"""Trash & Recycling Day plugin for FiestaBoard.

Purely schedule-driven: the user describes up to four collection "streams"
(Trash, Recycling, Compost, ...) with a weekday and a weekly/biweekly cadence,
and the plugin works out the next pickup for each. No network access.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytz

from src.plugins.base import PluginBase, PluginResult, TriggerResult
from src.text_to_board import count_tiles

logger = logging.getLogger(__name__)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
FREQUENCIES = ("weekly", "biweekly")
COLORS = ("red", "orange", "yellow", "green", "blue", "violet", "white")

DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_REMINDER_HOUR = 18
MAX_STREAMS = 4

# Layout constants for the per-stream display lines. These are formatting
# choices (how wide the right-aligned date column is), not board geometry --
# actual widths/row counts are always derived from ``self.board`` at render
# time, never hardcoded to a specific device.
_DATE_FIELD_WIDTH = 7  # e.g. "SEP 16" right-aligned, with a little slack
_MIN_NAME_WIDTH = 4

_TRIGGER_PRIORITY = 5
_TRIGGER_DURATION_SECONDS = 1800


def _parse_date(value: Any) -> Optional[date]:
    """Parse a YYYY-MM-DD string, returning None when it is not a valid date."""
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def _next_collection(stream: Dict[str, Any], today: date) -> date:
    """Return the next collection date (today or later) for one stream."""
    target = WEEKDAYS.index(stream["weekday"])
    candidate = today + timedelta(days=(target - today.weekday()) % 7)
    if stream.get("frequency") == "biweekly":
        anchor = _parse_date(stream.get("anchor_date"))
        # Works for anchors before or after today: Python's % is never negative.
        if anchor is not None and (candidate - anchor).days % 14 != 0:
            candidate += timedelta(days=7)
    return candidate


def _format_date(d: date) -> str:
    """'Sep 16' style date."""
    return d.strftime("%b %-d")


def _center(text: str, cols: int) -> str:
    """Center *text* on a board *cols* wide, trimming anything that will not fit."""
    return text[:cols].center(cols).rstrip()


def _build_message(names: List[str], days: int, cols: int) -> str:
    """One-line summary that always fits in *cols* tiles.

    ``message`` is a template variable a user can drop into a custom page on
    *any* board, so it cannot assume the 22-tile Flagship -- the narrowest
    board is a 15-tile Note, and every fallback below is sized to still fit
    there.
    """
    if days == 0:
        when = "TODAY"
    elif days == 1:
        when = "TOMORROW"
    else:
        full = f"NO PICKUP FOR {days} DAYS"
        if len(full) <= cols:
            return full
        short = f"{days}D TO PICKUP"
        return short if len(short) <= cols else short[:cols]

    message = " + ".join(n.upper() for n in names) + f" {when}"
    if len(message) <= cols:
        return message
    fallback = f"PICKUP {when}"
    return fallback if len(fallback) <= cols else fallback[:cols]


def _fit_names(names: List[str], budget: int) -> List[str]:
    """Truncate the longest name(s) first until *names* plus their 1-space
    separators fit within *budget* characters.

    Used to combine multiple same-day streams onto one line: shrinking the
    longest name(s) first keeps short names (e.g. "Trash") fully legible
    instead of every name being cut by the same fixed amount.
    """
    lengths = [len(n) for n in names]
    sep_total = max(len(names) - 1, 0)
    while sum(lengths) + sep_total > budget and any(length > 1 for length in lengths):
        i = max(range(len(lengths)), key=lengths.__getitem__)
        lengths[i] -= 1
    return [name[:max(length, 0)] for name, length in zip(names, lengths)]


def _fit_to_cols(text: str, cols: int) -> str:
    """Trim *text* (which may contain ``{color}`` tile markers) to *cols* tiles."""
    while text and count_tiles(text) > cols:
        text = text[:-1]
    return text


def _due_in(days_until: str) -> str:
    """Short "how soon" phrase for a stream's ``days_until``, for the
    surplus-row expansion in ``_expand_to_fill``."""
    try:
        n = int(days_until)
    except (TypeError, ValueError):
        return ""
    if n == 0:
        return "DUE TODAY"
    if n == 1:
        return "DUE TOMORROW"
    return f"DUE IN {n} DAYS"


class TrashDayPlugin(PluginBase):
    """Trash & Recycling Day plugin."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._last_trigger_id: Optional[str] = None

    @property
    def plugin_id(self) -> str:
        return "trash_day"

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """Current UTC time. Overridden in tests."""
        return datetime.now(timezone.utc)

    def _local_now(self) -> datetime:
        tz = pytz.timezone(self.config.get("timezone", DEFAULT_TIMEZONE))
        return self._now().astimezone(tz)

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []

        tz_name = config.get("timezone", DEFAULT_TIMEZONE)
        try:
            pytz.timezone(tz_name)
        except pytz.exceptions.UnknownTimeZoneError:
            errors.append(f"Invalid timezone: {tz_name}")

        streams = config.get("streams", [])
        if not isinstance(streams, list) or not streams:
            errors.append("At least one collection stream is required")
        elif len(streams) > MAX_STREAMS:
            errors.append(f"At most {MAX_STREAMS} collection streams are supported")
        else:
            for i, stream in enumerate(streams, start=1):
                errors.extend(self._validate_stream(i, stream))

        hour = config.get("reminder_hour", DEFAULT_REMINDER_HOUR)
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            errors.append("Reminder hour must be a whole number between 0 and 23")

        errors.extend(self._validate_refresh_seconds(config))
        return errors

    @staticmethod
    def _validate_stream(index: int, stream: Any) -> List[str]:
        errors: List[str] = []
        if not isinstance(stream, dict):
            return [f"Stream {index}: must be an object"]

        if not str(stream.get("name", "")).strip():
            errors.append(f"Stream {index}: name is required")

        weekday = stream.get("weekday")
        if weekday not in WEEKDAYS:
            errors.append(f"Stream {index}: weekday must be one of {', '.join(WEEKDAYS)}")

        frequency = stream.get("frequency", "weekly")
        if frequency not in FREQUENCIES:
            errors.append(f"Stream {index}: frequency must be 'weekly' or 'biweekly'")
        elif frequency == "biweekly":
            anchor = _parse_date(stream.get("anchor_date"))
            if anchor is None:
                errors.append(
                    f"Stream {index}: a known collection date (YYYY-MM-DD) is required for biweekly pickups"
                )
            elif weekday in WEEKDAYS and anchor.weekday() != WEEKDAYS.index(weekday):
                errors.append(f"Stream {index}: known collection date {anchor.isoformat()} is not a {weekday}")

        if stream.get("color", "blue") not in COLORS:
            errors.append(f"Stream {index}: color must be one of {', '.join(COLORS)}")

        return errors

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def _compute(self) -> Tuple[Dict[str, Any], date]:
        """Build the template data dict; also return the next collection date."""
        now = self._local_now()
        today = now.date()

        items = []
        for stream in self.config.get("streams") or []:
            if not isinstance(stream, dict):
                continue
            name = str(stream.get("name", "")).strip()
            if not name or stream.get("weekday") not in WEEKDAYS:
                logger.warning("Skipping misconfigured stream: %r", stream)
                continue
            next_day = _next_collection(stream, today)
            color = stream.get("color", "blue")
            items.append({
                "name": name,
                "next_date": _format_date(next_day),
                "days_until": str((next_day - today).days),
                "color": color if color in COLORS else "blue",
                "_date": next_day,
            })

        if not items:
            raise ValueError("No collection streams configured")

        items.sort(key=lambda item: item["_date"])
        next_day = items[0]["_date"]
        days = (next_day - today).days
        next_names = [item["name"] for item in items if item["_date"] == next_day]

        reminder_hour = int(self.config.get("reminder_hour", DEFAULT_REMINDER_HOUR))
        is_today = days == 0
        is_tomorrow = days == 1 and now.hour >= reminder_hour

        data = {
            "next_date": _format_date(next_day),
            "next_weekday": WEEKDAYS[next_day.weekday()],
            "days_until": str(days),
            "is_today": "true" if is_today else "false",
            "is_tomorrow": "true" if is_tomorrow else "false",
            "next_streams": ", ".join(next_names)[:44],
            "message": _build_message(next_names, days, self._cols()),
            "streams": [
                {k: v for k, v in item.items() if k != "_date"}
                for item in items
            ],
        }
        return data, next_day

    # ------------------------------------------------------------------
    # PluginBase API
    # ------------------------------------------------------------------

    def fetch_data(self) -> PluginResult:
        try:
            data, _ = self._compute()
            return PluginResult(
                available=True,
                data=data,
                formatted_lines=self._format_display(data),
            )
        except Exception as e:
            logger.exception("Error computing collection schedule")
            return PluginResult(available=False, error=str(e))

    def get_formatted_display(self) -> Optional[List[str]]:
        # Pass the bound board through: get_data(None) would unbind self.board
        # and re-render the lines at the default Flagship width.
        result = self.get_data(self.board)
        if not result.available:
            return None
        return result.formatted_lines

    def check_triggers(self) -> List[TriggerResult]:
        """Fire one board takeover per collection, the evening before."""
        if not self.config.get("enable_triggers", False):
            return []

        try:
            data, next_day = self._compute()
        except Exception:
            logger.warning("Could not compute schedule for trigger check", exc_info=True)
            return []

        if data["is_tomorrow"] != "true":
            return []

        trigger_id = f"trash_day_{next_day.isoformat()}"
        if trigger_id == self._last_trigger_id:
            return []
        self._last_trigger_id = trigger_id

        lines = self._format_display(data)
        cols = self._cols()
        lines[0] = _center("BINS OUT TONIGHT" if cols >= 16 else "BINS OUT", cols)
        return [
            TriggerResult(
                triggered=True,
                trigger_id=trigger_id,
                formatted_lines=lines,
                priority=_TRIGGER_PRIORITY,
                duration_seconds=_TRIGGER_DURATION_SECONDS,
                data=data,
            )
        ]

    def cleanup(self) -> None:
        self._last_trigger_id = None

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _cols(self) -> int:
        return self.board.cols if self.board else 22

    def _format_display(self, data: Dict[str, Any]) -> List[str]:
        rows = self.board.rows if self.board else 6
        cols = self._cols()

        header = f"{data['next_weekday']} {data['next_date']}".upper()
        if len(header) > cols:
            header = f"{data['next_weekday'][:3]} {data['next_date']}".upper()
        lines = [_center("NEXT PICKUP", cols), _center(header, cols)]

        available = max(rows - len(lines), 0)
        lines.extend(self._stream_lines(data["streams"], cols, available))

        while len(lines) < rows:
            lines.append("")
        return lines[:rows]

    def _stream_lines(self, streams: List[Dict[str, Any]], cols: int, available: int) -> List[str]:
        """Render *streams* (already sorted by date) into at most *available* lines.

        Three tiers, tried in order of how much each degrades the ideal
        one-line-per-stream layout:

        1. Room for every stream on its own line (as before) -- used
           whenever it fits, so same-day streams still get their own full
           line with its own date on a Flagship or any board with room.
        2. Not enough rows for one-per-stream, but enough for one line per
           *distinct pickup date* -- same-day streams are combined onto a
           shared line instead of the extras being dropped.
        3. Not even one line per date (e.g. four different collection days
           on a 15x3 Note) -- every stream's tile + name is packed into the
           rows available rather than silently vanishing.

        Tiers 1 and 2 then get expanded to use any *surplus* rows (a big
        note-array panel with only a couple of streams to show): each item
        gains a second "due in N days" line instead of the panel showing a
        handful of lines up top and going blank for the rest.
        """
        if available <= 0 or not streams:
            return []

        if len(streams) <= available:
            pairs = [(self._single_stream_line(stream, cols), stream["days_until"]) for stream in streams]
            return self._expand_to_fill(pairs, cols, available)

        groups = self._group_by_date(streams)
        if len(groups) <= available:
            pairs = [(self._group_line(group, cols), group[0]["days_until"]) for group in groups]
            return self._expand_to_fill(pairs, cols, available)

        return self._packed_lines(streams, cols, available)

    @staticmethod
    def _expand_to_fill(pairs: List[Tuple[str, str]], cols: int, available: int) -> List[str]:
        """Stretch a compact one-line-per-item layout to fill *available* rows.

        Used when a board has far more rows than the compact layout needs
        (e.g. a 120x24 panel with a handful of streams): rather than a
        couple of lines at the top and blank for the rest, every item gets
        an extra "due in N days" line for each whole multiple of itself
        that fits in the surplus.
        """
        lines = [line for line, _ in pairs]
        count = len(pairs)
        if count == 0 or available < 2 * count:
            return lines

        per_item = available // count
        expanded: List[str] = []
        for line, days_until in pairs:
            expanded.append(line)
            expanded.extend(_center(_due_in(days_until), cols) for _ in range(per_item - 1))
        return expanded[:available]

    @staticmethod
    def _group_by_date(streams: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Cluster consecutive streams (already date-sorted) sharing a next_date."""
        groups: List[List[Dict[str, Any]]] = []
        for stream in streams:
            if groups and groups[-1][0]["next_date"] == stream["next_date"]:
                groups[-1].append(stream)
            else:
                groups.append([stream])
        return groups

    def _group_line(self, group: List[Dict[str, Any]], cols: int) -> str:
        if len(group) == 1:
            return self._single_stream_line(group[0], cols)
        return self._combined_stream_line(group, cols)

    @staticmethod
    def _single_stream_line(stream: Dict[str, Any], cols: int) -> str:
        """One stream, own line: tile + name, with a right-aligned date when there's room."""
        tile = f"{{{stream['color']}}}"
        name = stream["name"].upper()
        date_str = stream["next_date"].upper()

        budget = max(cols - 2, 0)  # tile (1 tile) + 1 space
        if budget >= _DATE_FIELD_WIDTH + _MIN_NAME_WIDTH:
            name_width = budget - _DATE_FIELD_WIDTH
            return f"{tile} {name[:name_width]:<{name_width}}{date_str:>{_DATE_FIELD_WIDTH}}"
        return f"{tile} {name[:budget]}"

    @staticmethod
    def _combined_stream_line(group: List[Dict[str, Any]], cols: int) -> str:
        """Multiple streams sharing a pickup date, combined onto one line.

        No date column here -- it is the same date for every entry, and
        showing it once (in the header) leaves more width for names.
        """
        tiles = [f"{{{s['color']}}}" for s in group]
        names = [s["name"].upper() for s in group]
        budget = max(cols - len(group) - (len(group) - 1), 0)  # 1 tile/marker + separators
        fitted = _fit_names(names, budget)
        return " ".join(f"{tile}{name}" for tile, name in zip(tiles, fitted))

    @staticmethod
    def _packed_lines(streams: List[Dict[str, Any]], cols: int, available: int) -> List[str]:
        """Word-wrap every stream's tile + name into exactly *available* lines.

        Used only when there are more distinct pickup dates than rows
        (e.g. four different collection days on a 15x3 Note): rather than
        keeping the old one-stream-per-line layout and truncating the list
        to `available` entries -- which drops streams outright -- every
        stream contributes at least a truncated tile+name to the output.
        """
        tokens = [f"{{{s['color']}}}{s['name'].upper()}" for s in streams]
        lines: List[str] = []
        current = ""
        for token in tokens:
            candidate = f"{current} {token}" if current else token
            if count_tiles(candidate) <= cols:
                current = candidate
                continue
            if len(lines) + 1 < available:
                lines.append(current)
                current = token if count_tiles(token) <= cols else _fit_to_cols(token, cols)
            else:
                # No more lines left to open: keep cramming into this one.
                current = _fit_to_cols(candidate, cols)
        if current:
            lines.append(current)
        return lines[:available]


# Export the plugin class
Plugin = TrashDayPlugin
