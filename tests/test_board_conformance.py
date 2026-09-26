"""Board-geometry conformance for the Trash & Recycling Day plugin.

Runs the shared FiestaBoard suite (every Flagship/Note/Note-array shape from
15x3 up to 120x24) against this plugin, plus a couple of scenario-specific
regressions for the bugs that suite doesn't pin down by name.
"""

import json
from pathlib import Path

from src.devices import BoardContext
from src.plugins.geometry_conformance import assert_board_conformance
from src.text_to_board import count_tiles

from tests.test_plugin import RECYCLING, TRASH, make_plugin

MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "manifest.json").read_text())


def _make_plugin():
    """Fresh, ready-to-render plugin with enough streams to exercise growth
    and same-day combining: Trash and Recycling both land on 2026-09-15
    (see test_plugin.RECYCLING's anchor), Compost on the 17th, Yard Waste
    on the 18th -- three distinct pickup dates, one of them shared by two
    streams.
    """
    return make_plugin(
        "2026-09-13 10:00",
        streams=[
            dict(TRASH),
            dict(RECYCLING),
            {"name": "Yard Waste", "weekday": "Friday", "frequency": "weekly", "color": "orange"},
            {"name": "Compost", "weekday": "Thursday", "frequency": "weekly", "color": "white"},
        ],
    )


def test_renders_on_every_board_shape():
    """The shared conformance suite: every standard geometry, forwards and
    backwards, plus the growth ladder and the manifest checks.

    strict_growth=True because this plugin renders a list of streams -- a
    taller board must show strictly more of it when the shorter one was
    full. require_note_array_preview=True now that the manifest has one.
    """
    report = assert_board_conformance(
        _make_plugin,
        manifest=MANIFEST,
        strict_growth=True,
        require_note_array_preview=True,
    )
    assert report.ok


def test_same_day_streams_are_combined_not_dropped_on_a_note():
    """Regression for the data-loss bug: on a 15x3 Note only one row is left
    for streams after the two header lines. Trash and Recycling are both due
    2026-09-15, so both must appear -- combined onto that one row -- instead
    of Recycling silently vanishing because ``lines[:rows]`` used to drop
    everything past the first stream line.
    """
    plugin = make_plugin("2026-09-13 10:00", streams=[dict(TRASH), dict(RECYCLING)])

    with plugin._bound_board(BoardContext("note", rows=3, cols=15)):
        lines = plugin.get_formatted_display()

    assert len(lines) == 3
    stream_line = lines[2]
    assert "TRASH" in stream_line
    assert "RECYCL" in stream_line  # combined line abbreviates to fit 15 tiles
    assert count_tiles(stream_line) <= 15


def test_same_day_streams_get_their_own_full_lines_on_a_bigger_board():
    """The same two same-day streams, given the room a Flagship has, still
    show up as two distinct, fully-legible lines (each with its own line to
    itself) rather than being forced into the combined format a Note needs.
    """
    plugin = make_plugin("2026-09-13 10:00", streams=[dict(TRASH), dict(RECYCLING)])

    with plugin._bound_board(BoardContext("flagship", rows=6, cols=22)):
        lines = plugin.get_formatted_display()

    joined = "\n".join(lines[2:])
    assert "TRASH" in joined
    assert "RECYCLING" in joined
    # A Flagship has exactly enough rows for a name line per stream plus one
    # surplus "due in" line each -- confirms the surplus is spent on real
    # per-stream content rather than being left blank.
    assert any("DUE" in line for line in lines)


def test_message_fits_a_note_when_bound_to_one():
    """`message` is a template variable users can drop onto any board -- it
    used to assume a 22-tile Flagship unconditionally and could overflow a
    15-tile Note (e.g. "NO PICKUP FOR 5 DAYS" is 20 characters)."""
    plugin = make_plugin("2026-09-13 10:00", streams=[dict(TRASH)])

    with plugin._bound_board(BoardContext("note", rows=3, cols=15)):
        data = plugin.fetch_data().data

    assert len(data["message"]) <= 15
