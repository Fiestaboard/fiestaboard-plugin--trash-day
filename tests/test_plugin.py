"""Tests for the Trash & Recycling Day plugin."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytz

from src.devices import BoardContext
from src.text_to_board import count_tiles

from plugins.trash_day import TrashDayPlugin

MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "manifest.json").read_text())

TRASH = {"name": "Trash", "weekday": "Tuesday", "frequency": "weekly", "color": "blue"}
RECYCLING = {
    "name": "Recycling",
    "weekday": "Tuesday",
    "frequency": "biweekly",
    "anchor_date": "2026-09-01",
    "color": "green",
}


def make_plugin(local_now="2026-09-13 10:00", tz="America/Los_Angeles", **config):
    """Plugin pinned to a wall-clock time in *tz*.

    2026-09-13 is a Sunday; 2026-09-15, -22 and -29 are Tuesdays.
    """
    plugin = TrashDayPlugin(MANIFEST)
    moment = pytz.timezone(tz).localize(datetime.strptime(local_now, "%Y-%m-%d %H:%M"))
    plugin._now = lambda: moment.astimezone(timezone.utc)
    plugin.config = {"streams": [dict(TRASH)], "timezone": tz, "enabled": True, **config}
    return plugin


def test_plugin_id():
    assert TrashDayPlugin(MANIFEST).plugin_id == MANIFEST["id"] == "trash_day"


def test_fetch_data_returns_every_declared_variable():
    result = make_plugin(streams=[dict(TRASH), dict(RECYCLING)]).fetch_data()

    assert result.available
    declared = set(MANIFEST["variables"]["simple"]) | set(MANIFEST["variables"]["arrays"])
    assert set(result.data) == declared

    item_fields = set(MANIFEST["variables"]["arrays"]["streams"]["item_fields"])
    for item in result.data["streams"]:
        assert set(item) == item_fields


def test_fetch_data_without_streams_is_unavailable():
    result = make_plugin(streams=[]).fetch_data()

    assert not result.available
    assert result.error
    assert result.data is None


def test_fetch_data_never_raises_on_bad_timezone():
    result = make_plugin(timezone="Not/AZone").fetch_data()

    assert not result.available
    assert "Not/AZone" in result.error


# ---------------------------------------------------------------------------
# Schedule maths
# ---------------------------------------------------------------------------


def test_weekly_picks_the_next_matching_weekday():
    data = make_plugin("2026-09-13 10:00").fetch_data().data

    assert data["next_date"] == "Sep 15"
    assert data["next_weekday"] == "Tuesday"
    assert data["days_until"] == "2"


def test_weekly_on_collection_day_stays_on_today():
    data = make_plugin("2026-09-15 07:00").fetch_data().data

    assert data["next_date"] == "Sep 15"
    assert data["days_until"] == "0"


@pytest.mark.parametrize(
    "anchor,expected",
    [
        ("2026-09-01", "Sep 15"),  # anchor in the past, same parity
        ("2026-09-08", "Sep 22"),  # anchor in the past, opposite parity
        ("2026-09-29", "Sep 15"),  # anchor in the future, same parity
        ("2026-09-22", "Sep 22"),  # anchor in the future, opposite parity
    ],
)
def test_biweekly_parity(anchor, expected):
    stream = {**RECYCLING, "anchor_date": anchor}
    data = make_plugin("2026-09-13 10:00", streams=[stream]).fetch_data().data

    assert data["next_date"] == expected


def test_streams_are_sorted_and_next_streams_groups_same_day():
    compost = {"name": "Compost", "weekday": "Friday", "frequency": "weekly", "color": "yellow"}
    data = make_plugin(
        "2026-09-13 10:00", streams=[compost, dict(TRASH), dict(RECYCLING)]
    ).fetch_data().data

    assert [s["name"] for s in data["streams"]] == ["Trash", "Recycling", "Compost"]
    assert data["next_streams"] == "Trash, Recycling"
    assert data["streams"][2]["next_date"] == "Sep 18"


def test_misconfigured_stream_is_skipped_not_fatal():
    data = make_plugin(
        "2026-09-13 10:00", streams=[{"name": "Bad", "weekday": "Someday"}, dict(TRASH)]
    ).fetch_data().data

    assert [s["name"] for s in data["streams"]] == ["Trash"]


# ---------------------------------------------------------------------------
# Timezone and reminder hour
# ---------------------------------------------------------------------------


def test_timezone_decides_the_day_across_midnight():
    """One instant, two zones: 06:30 UTC is still Monday in LA, already Tuesday in NY."""
    west = make_plugin("2026-09-14 23:30", tz="America/Los_Angeles").fetch_data().data
    east = make_plugin("2026-09-15 02:30", tz="America/New_York").fetch_data().data

    assert west["days_until"] == "1"
    assert west["is_today"] == "false"
    assert east["days_until"] == "0"
    assert east["is_today"] == "true"


@pytest.mark.parametrize(
    "local_now,expected",
    [
        ("2026-09-14 17:59", "false"),  # before the reminder hour
        ("2026-09-14 18:00", "true"),  # on the reminder hour
        ("2026-09-14 23:30", "true"),  # later that evening
    ],
)
def test_is_tomorrow_respects_reminder_hour(local_now, expected):
    data = make_plugin(local_now, reminder_hour=18).fetch_data().data

    assert data["days_until"] == "1"
    assert data["is_tomorrow"] == expected


def test_custom_reminder_hour():
    data = make_plugin("2026-09-14 15:00", reminder_hour=14).fetch_data().data

    assert data["is_tomorrow"] == "true"


@pytest.mark.parametrize(
    "local_now,streams,expected",
    [
        ("2026-09-15 08:00", [dict(TRASH)], "TRASH TODAY"),
        ("2026-09-14 19:00", [dict(TRASH)], "TRASH TOMORROW"),
        ("2026-09-13 10:00", [dict(TRASH)], "NO PICKUP FOR 2 DAYS"),
        # "TRASH + RECYCLING TOMORROW" is 26 tiles, so it falls back.
        ("2026-09-14 19:00", [dict(TRASH), dict(RECYCLING)], "PICKUP TOMORROW"),
    ],
)
def test_message_variants(local_now, streams, expected):
    data = make_plugin(local_now, streams=streams).fetch_data().data

    assert data["message"] == expected
    assert len(data["message"]) <= MANIFEST["variables"]["simple"]["message"]["max_length"]


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def test_triggers_off_by_default():
    assert make_plugin("2026-09-14 19:00").check_triggers() == []


def test_trigger_fires_once_per_collection_date():
    plugin = make_plugin("2026-09-14 19:00", enable_triggers=True)

    first = plugin.check_triggers()
    assert len(first) == 1
    assert first[0].triggered
    assert first[0].trigger_id == "trash_day_2026-09-15"
    assert first[0].formatted_lines[0].strip() == "BINS OUT TONIGHT"

    assert plugin.check_triggers() == []

    # A later collection is a new trigger id, so it fires again.
    plugin._now = lambda: pytz.timezone("America/Los_Angeles").localize(
        datetime(2026, 9, 21, 19, 0)
    ).astimezone(timezone.utc)
    plugin.clear_cache()
    second = plugin.check_triggers()
    assert len(second) == 1
    assert second[0].trigger_id == "trash_day_2026-09-22"


def test_trigger_does_not_fire_before_the_reminder_hour():
    plugin = make_plugin("2026-09-14 12:00", enable_triggers=True)

    assert plugin.check_triggers() == []


def test_trigger_survives_a_broken_config():
    plugin = make_plugin("2026-09-14 19:00", enable_triggers=True, streams=[])

    assert plugin.check_triggers() == []


def test_cleanup_clears_trigger_state():
    plugin = make_plugin("2026-09-14 19:00", enable_triggers=True)

    assert len(plugin.check_triggers()) == 1
    plugin.cleanup()
    assert len(plugin.check_triggers()) == 1


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def test_validate_config_accepts_a_good_config():
    plugin = TrashDayPlugin(MANIFEST)

    assert plugin.validate_config(
        {
            "streams": [dict(TRASH), dict(RECYCLING)],
            "timezone": "America/New_York",
            "reminder_hour": 20,
            "refresh_seconds": 900,
        }
    ) == []


@pytest.mark.parametrize(
    "stream,fragment",
    [
        ({**TRASH, "weekday": "Funday"}, "weekday must be one of"),
        ({**TRASH, "name": "  "}, "name is required"),
        ({**TRASH, "frequency": "monthly"}, "frequency must be"),
        ({**TRASH, "color": "chartreuse"}, "color must be one of"),
        ({**RECYCLING, "anchor_date": ""}, "known collection date"),
        ({**RECYCLING, "anchor_date": "2026-13-45"}, "known collection date"),
        ({**RECYCLING, "anchor_date": "not-a-date"}, "known collection date"),
        # 2026-09-02 is a Wednesday, not the stream's Tuesday.
        ({**RECYCLING, "anchor_date": "2026-09-02"}, "is not a Tuesday"),
        ("not-an-object", "must be an object"),
    ],
)
def test_validate_config_rejects_bad_streams(stream, fragment):
    errors = TrashDayPlugin(MANIFEST).validate_config({"streams": [stream]})

    assert any(fragment in e for e in errors), errors


@pytest.mark.parametrize(
    "config,fragment",
    [
        ({"streams": []}, "At least one collection stream"),
        ({"streams": [dict(TRASH)] * 5}, "At most 4"),
        ({"streams": [dict(TRASH)], "timezone": "Mars/Olympus"}, "Invalid timezone"),
        ({"streams": [dict(TRASH)], "reminder_hour": 24}, "between 0 and 23"),
        ({"streams": [dict(TRASH)], "reminder_hour": "six"}, "between 0 and 23"),
        ({"streams": [dict(TRASH)], "refresh_seconds": 60}, "at least 300"),
    ],
)
def test_validate_config_rejects_bad_settings(config, fragment):
    errors = TrashDayPlugin(MANIFEST).validate_config(config)

    assert any(fragment in e for e in errors), errors


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("device_type,rows,cols", [("flagship", 6, 22), ("note", 3, 15)])
def test_formatted_display_fits_the_board(device_type, rows, cols):
    plugin = make_plugin(
        "2026-09-13 10:00",
        streams=[
            dict(TRASH),
            dict(RECYCLING),
            {"name": "Yard Waste!!", "weekday": "Friday", "frequency": "weekly", "color": "orange"},
            {"name": "Compost", "weekday": "Thursday", "frequency": "weekly", "color": "white"},
        ],
    )

    with plugin._bound_board(BoardContext(device_type, rows=rows, cols=cols)):
        lines = plugin.get_formatted_display()

    assert len(lines) == rows
    for line in lines:
        assert count_tiles(line) <= cols, line


def test_formatted_display_abbreviates_the_weekday_on_a_note():
    plugin = make_plugin("2026-09-13 10:00", streams=[{**TRASH, "weekday": "Wednesday"}])

    with plugin._bound_board(BoardContext("note", rows=3, cols=15)):
        note = plugin.get_formatted_display()
    with plugin._bound_board(BoardContext("flagship", rows=6, cols=22)):
        flagship = plugin.get_formatted_display()

    assert note[1].strip() == "WED SEP 16"
    assert flagship[1].strip() == "WEDNESDAY SEP 16"


def test_formatted_display_without_a_board_assumes_flagship():
    lines = make_plugin("2026-09-13 10:00").get_formatted_display()

    assert len(lines) == 6
    assert lines[0].strip() == "NEXT PICKUP"
    assert lines[2].startswith("{blue} TRASH")


def test_get_formatted_display_returns_none_when_unavailable():
    assert make_plugin(streams=[]).get_formatted_display() is None
