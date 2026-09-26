"""White-box tests for the per-stream reflow helpers in _format_display.

These pin down the internal tiers (one-line-per-stream -> combine same-day
-> pack into whatever rows exist) directly, including edge cases that no
*real* FiestaBoard geometry currently reaches (e.g. a coarser board than any
shipped device) but which the code is written to handle defensively rather
than assume away.
"""

import json
from pathlib import Path

from src.text_to_board import count_tiles

from plugins.trash_day import TrashDayPlugin, _due_in

MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "manifest.json").read_text())

TRASH = {"name": "Trash", "next_date": "Sep 15", "color": "red"}
RECYCLING = {"name": "Recycling", "next_date": "Sep 15", "color": "green"}
COMPOST = {"name": "Compost", "next_date": "Sep 17", "color": "blue"}


def _plugin() -> TrashDayPlugin:
    return TrashDayPlugin(MANIFEST)


def test_stream_lines_returns_nothing_when_no_rows_are_available():
    plugin = _plugin()

    assert plugin._stream_lines([dict(TRASH)], cols=22, available=0) == []


def test_group_line_of_one_stream_matches_the_single_stream_format():
    """A one-item date-group (mixed in with bigger groups) renders exactly
    like a standalone stream line, not the same-day combined format."""
    plugin = _plugin()

    assert plugin._group_line([dict(COMPOST)], cols=22) == plugin._single_stream_line(dict(COMPOST), 22)


def test_single_stream_line_drops_the_date_column_when_too_narrow_for_one():
    """Every real board is at least 15 tiles wide, wide enough for a date
    column -- this is the defensive floor for anything narrower."""
    line = TrashDayPlugin._single_stream_line(dict(TRASH), cols=10)

    assert line == "{red} TRASH"
    assert "SEP" not in line
    assert count_tiles(line) <= 10


def test_packed_lines_uses_every_available_row_before_cramming():
    """With 2 rows to pack 3 streams into, the packer should fill both rows
    (not collapse everything onto the first) and still keep every stream's
    tile represented somewhere in the output."""
    streams = [TRASH, RECYCLING, COMPOST]

    lines = TrashDayPlugin._packed_lines(streams, cols=15, available=2)

    assert len(lines) == 2
    assert all(count_tiles(line) <= 15 for line in lines)
    assert "TRASH" in lines[0]
    assert "RECYCL" in lines[1]


def test_due_in_phrases():
    assert _due_in("0") == "DUE TODAY"
    assert _due_in("1") == "DUE TOMORROW"
    assert _due_in("5") == "DUE IN 5 DAYS"
    assert _due_in("not-a-number") == ""


def test_packed_lines_never_exceeds_the_requested_row_count():
    """Even with far more streams than could ever fit, packing stops at
    `available` lines instead of growing past the board."""
    streams = [
        {"name": f"Stream {i}", "next_date": "Sep 15", "color": "red"}
        for i in range(4)
    ]

    lines = TrashDayPlugin._packed_lines(streams, cols=15, available=1)

    assert len(lines) == 1
    assert count_tiles(lines[0]) <= 15
