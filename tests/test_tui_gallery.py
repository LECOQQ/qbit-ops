"""Prove the TUI gallery captures the screen it claims to.

`scripts/tui_gallery.py` is the instrument the redesign is judged with:
it drives the real app and exports what a terminal would paint. A
modal that silently fails to open still produces a perfectly valid SVG
-- of the screen underneath. The gallery would then report seven
successes and show the same view seven times, and a reviewer comparing
`before/` with `after/` would be comparing the wrong pictures.

So each entry is pinned to text only its own screen renders. These
assertions are deliberately about *identity*, never about layout or
wording: the redesign is expected to change how every screen looks, and
a test that broke on that would be a wall to climb rather than a
signal.
"""

import pytest

from scripts.tui_gallery import SCREENS, _capture
from scripts.tui_wireframe import capture as capture_wireframe

pytestmark = pytest.mark.tui

# One marker per screen: text that screen renders and NO other screen
# in the gallery does.
#
# Derived by measurement, not chosen by eye. The first attempt used
# plausible words -- `Category` for the filters modal, `Sort` for the
# sort modal -- and both also appear on the screen underneath: a table
# column, and a footer binding. Removing the keypress that opens the
# modal then changed nothing, so the test passed on a capture of the
# wrong screen. It was decorative for exactly as long as nobody tried
# to break it.
#
# To regenerate after adding a screen, print the set difference of each
# screen's rendered vocabulary against the union of the others.
SCREEN_MARKERS: dict[str, tuple[str, ...]] = {
    "overview": ("All-time", "Connected"),
    "torrents": ("uncategorized", "cross-seed"),
    "filters": ("Completion", "Errored"),
    "sort": ("high-low", "A-Z"),
    "help": ("Navigate", "Deselect"),
    "details": ("Hash", "Size"),
    "actions": ("Reannounce", "Resume"),
}


def _rendered_text(svg: str) -> str:
    """The text a terminal would show, out of the exported SVG.

    The stylesheet is dropped first: it carries font names and URLs that
    would match a marker by accident and turn this test green on an
    empty screen.
    """
    import html
    import re

    body = svg.split("</style>", 1)[-1]
    words = (html.unescape(w) for w in re.findall(r">([^<>]+)<", body))
    return re.sub(r"\s+", " ", " ".join(words))


def test_every_gallery_screen_declares_a_marker() -> None:
    """A screen added to the gallery without one would be captured but
    never checked -- the exact blind spot this file exists to close."""
    assert set(SCREEN_MARKERS) == set(SCREENS)


@pytest.mark.parametrize("name", sorted(SCREENS))
async def test_the_gallery_reaches_the_screen_it_names(
    name: str,
    tmp_path,
) -> None:
    target = await _capture(name, SCREENS[name], tmp_path)
    text = _rendered_text(target.read_text(encoding="utf-8"))

    for marker in SCREEN_MARKERS[name]:
        assert marker.lower() in text.lower(), (
            f"{name}.svg does not show {marker!r}. The keys in SCREENS "
            "no longer reach that screen, so the gallery is capturing "
            "whatever sits underneath it."
        )


# --- The wireframe measures the same screens ------------------------------


@pytest.mark.parametrize("name", sorted(SCREENS))
async def test_the_wireframe_measures_a_real_layout(name: str) -> None:
    """An empty grid is a valid wireframe of nothing. Without this, a
    layout pass that never ran would render as a screen with no
    structure -- and read as a finding rather than a broken tool."""
    frame = await capture_wireframe(name, SCREENS[name], max_depth=6)

    assert "LEGEND" in frame
    boxes = [
        line
        for line in frame.splitlines()
        if line.startswith("  ") and "*" in line
    ]
    assert len(boxes) >= 2, f"{name}: only {len(boxes)} box(es) measured"
    assert "+" in frame, f"{name}: no box was drawn"


async def test_the_wireframe_and_the_gallery_agree_on_size() -> None:
    """They describe the same layout. Two sizes would make a screenshot
    and its wireframe impossible to read against each other."""
    from scripts.tui_gallery import GALLERY_SIZE
    from scripts.tui_wireframe import WIREFRAME_SIZE

    assert GALLERY_SIZE == WIREFRAME_SIZE
