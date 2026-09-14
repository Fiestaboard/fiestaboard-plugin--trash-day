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

logger = logging.getLogger(__name__)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
FREQUENCIES = ("weekly", "biweekly")
COLORS = ("red", "orange", "yellow", "green", "blue", "violet", "white")

DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_REMINDER_HOUR = 18
MAX_STREAMS = 4

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


def _build_message(names: List[str], days: int) -> str:
    """One-line summary that always fits in 22 tiles."""
    if days == 0:
        when = "TODAY"
    elif days == 1:
        when = "TOMORROW"
    else:
        return f"NO PICKUP FOR {days} DAYS"
    message = " + ".join(n.upper() for n in names) + f" {when}"
    return message if len(message) <= 22 else f"PICKUP {when}"


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
            "message": _build_message(next_names, days),
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

        for stream in data["streams"]:
            name = stream["name"].upper()
            tile = f"{{{stream['color']}}}"
            if cols >= 22:
                # tile + space + 12-char name + right-aligned 7-char date = 21 tiles
                lines.append(f"{tile} {name[:12]:<12}{stream['next_date'].upper():>7}")
            else:
                lines.append(f"{tile} {name[:cols - 2]}")

        while len(lines) < rows:
            lines.append("")
        return lines[:rows]


# Export the plugin class
Plugin = TrashDayPlugin
