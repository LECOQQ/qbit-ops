#!/usr/bin/env python3
"""Derive a true-scale wireframe of every TUI screen, from the running app.

    python3 scripts/tui_wireframe.py                 # every screen
    python3 scripts/tui_wireframe.py --only filters
    python3 scripts/tui_wireframe.py --out wireframes/

A terminal is a character grid, so a wireframe drawn in that grid is not
an approximation of the interface -- it is a projection of it, at 1:1.
That is what makes a hand-edited wireframe a *specification of layout*
rather than an illustration of one, and what lets the built result be
compared back against what was approved.

**Derived, never drawn.** Boxes come from each widget's computed
`region` after a real headless layout pass. A hand-drawn "before" would
be one person's reading of the code; this is a measurement, and it
disagrees with the reader when the reader is wrong.

Companion to `scripts/tui_gallery.py`, and not a substitute: the gallery
photographs the rendering (colour, density, what catches the eye), this
measures the structure (what is nested in what, aligned how). "Is it
pretty" and "is it consistent" are different questions.

Never contacts qBittorrent and never reads `.env`.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from qbit_ops.tui.app import QbitOpsTuiApp

REPO_ROOT = Path(__file__).resolve().parent.parent

# Run either as `python3 scripts/tui_wireframe.py` or imported as
# `scripts.tui_wireframe` by the test suite; only the first needs help
# finding its sibling.
if __package__ in (None, ""):  # pragma: no cover - entry-point plumbing
    import sys

    sys.path.insert(0, str(REPO_ROOT))

from scripts.tui_gallery import (  # noqa: E402
    GALLERY_SIZE,
    SCREENS,
    _GalleryClient,
)

# The gallery's size, so a wireframe and its screenshot describe the
# same layout. Two sizes would make the pair impossible to reason about.
WIREFRAME_SIZE = GALLERY_SIZE

# `tmp/` is gitignored: working artefacts of a design pass.
DEFAULT_OUT = REPO_ROOT / "tmp" / "design" / "wireframes"

# Leaves whose box carries no structural information: they are content,
# and drawing them turns the frame into noise at exactly the depth where
# structure stops being the subject.
CONTENT_WIDGETS = frozenset(
    {"RadioButton", "Label", "Static", "Rule", "Checkbox"}
)

DEFAULT_DEPTH = 6


def _depth(node: Any) -> int:
    depth, current = 0, node
    while getattr(current, "parent", None) is not None:
        current = current.parent
        depth += 1
    return depth


def _boxes(app: QbitOpsTuiApp, max_depth: int) -> list[tuple[int, str, Any]]:
    """Every structural widget worth a box, shallowest first."""
    found: list[tuple[int, str, Any]] = []
    for node in app.screen.walk_children(with_self=True):
        region = getattr(node, "region", None)
        if region is None or region.width < 4 or region.height < 2:
            continue
        name = type(node).__name__
        if name in CONTENT_WIDGETS:
            continue
        depth = _depth(node)
        if depth > max_depth:
            continue
        found.append((depth, name, region))
    return sorted(found, key=lambda entry: (entry[0], entry[2].y, entry[2].x))


def render(
    app: QbitOpsTuiApp,
    *,
    max_depth: int = DEFAULT_DEPTH,
    size: tuple[int, int] = WIREFRAME_SIZE,
) -> str:
    """Draw the current screen's structure into a character grid."""
    width, height = size
    grid = [[" "] * width for _ in range(height)]

    for _, name, region in _boxes(app, max_depth):
        left, top = max(0, region.x), max(0, region.y)
        right = min(width - 1, region.x + region.width - 1)
        bottom = min(height - 1, region.y + region.height - 1)
        if right <= left or bottom <= top:
            continue

        # Edges are only drawn onto blank cells. Nested widgets routinely
        # share an edge with their parent, and overwriting turns a
        # corner into a straight line -- which reads as a box that is
        # not there.
        for x in range(left, right + 1):
            for y in (top, bottom):
                if grid[y][x] == " ":
                    grid[y][x] = "-"
        for y in range(top, bottom + 1):
            for x in (left, right):
                if grid[y][x] == " ":
                    grid[y][x] = "|"
        corners = ((top, left), (top, right), (bottom, left), (bottom, right))
        for y, x in corners:
            if grid[y][x] in " -|":
                grid[y][x] = "+"

        label = f" {name} "
        if len(label) <= right - left - 1:
            for offset, character in enumerate(label):
                cell = grid[top][left + 1 + offset]
                # Never over a `|`: that pipe is a nested box's edge
                # starting on this same row, and hiding it would draw a
                # container that looks emptier than it is.
                if cell != "|":
                    grid[top][left + 1 + offset] = character

    frame = "\n".join("".join(row).rstrip() for row in grid)
    return f"{frame}\n\n{_legend(app, max_depth)}"


def _legend(app: QbitOpsTuiApp, max_depth: int) -> str:
    """Every box, with the region it was drawn from.

    The frame alone cannot say everything: a widget that shares all four
    edges with its parent is invisible in it -- which is itself the
    finding, since coincident edges mean no padding at all. The legend
    keeps that measurable instead of merely looked at.
    """
    lines = ["LEGEND  (depth  widget  x,y  w*h)"]
    for depth, name, region in _boxes(app, max_depth):
        lines.append(
            f"  {depth:<2}  {'  ' * min(depth, 6)}{name:<20} "
            f"{region.x},{region.y}  {region.width}*{region.height}"
        )
    return "\n".join(lines)


async def capture(
    name: str,
    keys: list[str],
    *,
    max_depth: int,
    size: tuple[int, int] = WIREFRAME_SIZE,
) -> str:
    app = QbitOpsTuiApp(
        client_factory=lambda: _GalleryClient(),
        host="http://localhost:8080",
        refresh_interval=3600.0,
    )
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        await pilot.pause()
        return render(app, max_depth=max_depth, size=size)


async def _run(
    names: list[str],
    out: Path | None,
    max_depth: int,
    size: tuple[int, int],
) -> int:
    for name in names:
        frame = await capture(
            name, SCREENS[name], max_depth=max_depth, size=size
        )
        if out is None:
            print(f"# {name}\n\n{frame}\n")
            continue
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{name}.txt"
        target.write_text(frame + "\n", encoding="utf-8")
        print(f"  {name:10} {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write one .txt per screen")
    parser.add_argument("--only", default="", help="comma-separated names")
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    parser.add_argument(
        "--size",
        default="x".join(str(value) for value in WIREFRAME_SIZE),
        help=(
            "terminal size as WxH. A narrower capture is not a smaller "
            "picture of the same layout -- the TUI drops columns below a "
            "threshold, so it measures a different design."
        ),
    )
    args = parser.parse_args(argv)

    try:
        width, height = (int(part) for part in args.size.lower().split("x"))
    except ValueError:
        print(f"Invalid --size {args.size!r}. Use WxH, e.g. 100x30.")
        return 1

    names = [n for n in args.only.split(",") if n] or list(SCREENS)
    unknown = [n for n in names if n not in SCREENS]
    if unknown:
        print(f"Unknown screen(s): {', '.join(unknown)}")
        print(f"Known: {', '.join(SCREENS)}")
        return 1

    return asyncio.run(_run(names, args.out, args.depth, (width, height)))


if __name__ == "__main__":
    raise SystemExit(main())
