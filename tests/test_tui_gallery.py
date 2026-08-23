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

from qbit_ops.tui.modals.base import MODAL_WIDTHS
from scripts.tui_gallery import GALLERY_SIZE, SCREENS, _capture
from scripts.tui_wireframe import Surface, capture_inventory
from scripts.tui_wireframe import capture as capture_wireframe

pytestmark = pytest.mark.tui


@pytest.fixture(scope="session")
def _inventory_cache() -> dict[str, Surface]:
    """One `Surface` per screen name, shared across the whole session:
    `capture_inventory()` is a pure function of `name`, safe to memoize
    across every test that asks for the same screen."""
    return {}


async def _inventory(name: str, cache: dict[str, Surface]) -> Surface:
    if name not in cache:
        cache[name] = await capture_inventory(name, SCREENS[name], max_depth=6)
    return cache[name]


# One marker per screen: text that screen renders and NO other screen
# in the gallery does.
#
# Derived by measurement, not chosen by eye: a plausible-looking word
# (a table column, a footer binding) can still appear on the screen
# underneath, letting the test pass on a capture of the wrong screen.
#
# To regenerate after adding a screen, print the set difference of each
# screen's rendered vocabulary against the union of the others.
SCREEN_MARKERS: dict[str, tuple[str, ...]] = {
    # Not the connection word: the fixture instance is firewalled, and
    # that is the whole point of the capture -- a marker naming one
    # status would go green only while the screen lied.
    "overview": ("All-time", "announce status not read here"),
    "torrents": ("uncategorized", "cross-seed"),
    "filters": ("Tag any", "Name re"),
    "sort": ("high-low", "A-Z"),
    "help": ("Navigate", "Deselect"),
    "details": ("Hash", "Size"),
    "actions": ("Reannounce", "Resume"),
    "explain": ("Evidence", "Consider"),
    "preview": ("Affected", "Snapshot"),
    "result": ("Submitted", "observable"),
    "setup": ("Password", "Host"),
    "value-category": ("Set category", "create it as well"),
    "value-tag-add": ("Add tags", "Existing"),
    "value-tag-remove": ("Remove tags", "On selection"),
    "value-throttle": ("Set transfer limits", "leaves it"),
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
    """Guards against a layout pass that silently never ran: an empty
    grid would otherwise render as a valid-looking wireframe, not an
    error."""
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
    from scripts.tui_gallery import GALLERY_SIZE
    from scripts.tui_wireframe import WIREFRAME_SIZE

    assert GALLERY_SIZE == WIREFRAME_SIZE


# --- The inventory measures what it claims to -----------------------------


@pytest.mark.parametrize("name", sorted(SCREENS))
async def test_the_inventory_measures_the_surface_not_the_screen(
    name: str,
    _inventory_cache: dict[str, Surface],
) -> None:
    """A `ModalScreen` is full-bleed and transparent: measuring it
    instead of its dialog would report every modal as 140 columns wide
    and hide the very divergence the inventory exists to expose."""
    surface = await _inventory(name, _inventory_cache)

    assert surface.screen == name
    assert surface.width >= 4 and surface.height >= 2
    assert surface.depth >= 1
    if surface.frame != "MainScreen":
        assert surface.width < GALLERY_SIZE[0], (
            f"{name}: measured the modal screen ({surface.container}), "
            "not the dialog inside it"
        )


async def test_the_inventory_counts_a_one_line_input_as_structural(
    _inventory_cache: dict[str, Surface],
) -> None:
    """A height filter meant to drop decorative one-line text must not
    also drop a real one-line control: `filters` nests `Input` five
    levels down (`FiltersScreen -> VerticalScroll -> FiltersPanel ->
    _Row -> Input`), verified by walking `.parent`."""
    surface = await _inventory("filters", _inventory_cache)

    assert surface.depth == 5


async def test_the_inventory_excludes_a_static_subclass_by_type(
    _inventory_cache: dict[str, Surface],
) -> None:
    """`BrandHeader`, `RateGraph`, `TrackersWindow` and `SessionWindow`
    all subclass `Static` under a different name. Matching content
    leaves by exact class name let them through as if they carried
    nesting, inflating `overview`'s measured depth with a passive
    renderer that has no children of its own."""
    surface = await _inventory("overview", _inventory_cache)

    assert surface.depth == 4


async def test_the_inventory_counts_only_css_a_screen_declares_itself() -> None:
    """Counting an inherited `CSS` against every subclass would report
    a shared base class as nine copies of the duplication it removes."""
    from scripts.tui_wireframe import _own_css_lines

    class _Base:
        CSS = "a\nb\nc\n"

    class _Child(_Base):
        pass

    class _App:
        screen = _Child()

    assert _own_css_lines(_App()) == 0  # type: ignore[arg-type]
    _App.screen = _Base()  # type: ignore[assignment]
    assert _own_css_lines(_App()) == 3  # type: ignore[arg-type]


# --- The measured result of the style system ------------------------------

# Every surface that is a modal: the two workspaces frame themselves and
# are measured as the screen, not as a dialog floating on one.
MODAL_SCREENS: tuple[str, ...] = tuple(
    name for name in SCREENS if name not in ("overview", "torrents")
)


async def _modal_surfaces(cache: dict[str, Surface]) -> list[Surface]:
    return [await _inventory(name, cache) for name in MODAL_SCREENS]


async def test_no_surface_declares_a_stylesheet_of_its_own(
    _inventory_cache: dict[str, Surface],
) -> None:
    """One sheet, one file: a class-level `CSS` block would let a
    screen's style drift out of the shared stylesheet."""
    for name in SCREENS:
        surface = await _inventory(name, _inventory_cache)
        assert surface.css_lines == 0, (
            f"{name} declares {surface.css_lines} lines of its own CSS; "
            "it belongs in src/qbit_ops/tui/qbit_ops.tcss"
        )


async def test_every_modal_is_measured_on_the_width_scale(
    _inventory_cache: dict[str, Surface],
) -> None:
    """Measured, not declared: a modal could name `medium` and still be
    squeezed by a stray rule. Only the rendered width proves the scale."""
    scale = set(MODAL_WIDTHS.values())
    for surface in await _modal_surfaces(_inventory_cache):
        assert surface.width in scale, (
            f"{surface.screen} renders {surface.width} columns wide, "
            f"outside the scale {sorted(scale)}"
        )


async def test_every_modal_is_centred_on_its_own_footprint(
    _inventory_cache: dict[str, Surface],
) -> None:
    """Every modal is centred on its own measured footprint, never
    placed by hand, at whatever width/height that footprint turns out
    to be -- `y`/`height` are not expected to match across modals."""
    surfaces = await _modal_surfaces(_inventory_cache)

    assert {s.container for s in surfaces} == {"VerticalScroll"}
    for surface in surfaces:
        assert surface.x == (GALLERY_SIZE[0] - surface.width) // 2, (
            surface.screen,
            surface.x,
        )
        assert surface.y == (GALLERY_SIZE[1] - surface.height) // 2, (
            surface.screen,
            surface.y,
        )
