#!/usr/bin/env python3
"""Render every TUI screen to SVG, so a design change can be seen.

A redesign judged from source is judged on intent. This drives the real
app headlessly and exports what a terminal would actually paint --
colours, borders, spacing, alignment -- which is the whole subject when
the goal is a uniform style across every modal (`len(SCREENS)` below,
minus the two workspaces).

    python3 scripts/tui_gallery.py                  # -> docs/assets/tui/
    python3 scripts/tui_gallery.py --out /tmp/before
    python3 scripts/tui_gallery.py --only filters,sort

Textual exports SVG natively (`App.export_screenshot`), so this needs no
snapshot plugin. That mattered: the obvious one pins an older pytest,
and downgrading the whole suite's runner to look at pictures is a bad
trade.

Not a gate. It has no baseline to compare against and fails nothing --
during a redesign every screen changes on purpose, and a wall there
would only be climbed. It gives the eyes; judging is still human.

Never contacts qBittorrent and never reads `.env`.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import re
from pathlib import Path
from typing import Any

from qbit_ops.tui.app import QbitOpsTuiApp

REPO_ROOT = Path(__file__).resolve().parent.parent
# `tmp/` is gitignored: these are working artefacts of a design pass,
# regenerated in seconds, and 100 KB apiece.
DEFAULT_OUT = REPO_ROOT / "tmp" / "design" / "svg"

# Wide enough that the table shows its full column set: the narrow
# fallback is a different design and deserves its own pass, not a
# silent substitution here.
GALLERY_SIZE = (140, 40)

# Rich draws a macOS window around every exported terminal: three
# traffic-light circles above the content. They are pure decoration,
# they imitate an OS this tool does not target, and in a before/after
# pair they are the only part guaranteed identical. Dropped by string
# surgery rather than a custom template, because the alternative is
# re-implementing `export_screenshot` on top of Textual internals.
_CHROME_CIRCLE = re.compile(r'\s*<circle cx="\d+" cy="0" r="7"[^>]*/>')


# Enough distinct hosts that the Trackers window shows a real spread,
# plus the two shapes that name no host at all.
_TRACKERS: tuple[str, ...] = (
    "https://tracker.example.org/announce",
    "https://bt.private.tld/announce/passkey",
    "udp://open.demonii.si:1337/announce",
    "https://flaky.tracker.net/announce",
    "** [DHT] **",
    "",
)


def _torrent(index: int, **overrides: Any) -> dict[str, Any]:
    """One synthetic torrent, varied enough to exercise the renderers."""
    states = ["uploading", "downloading", "stalledUP", "error", "pausedUP"]
    base: dict[str, Any] = {
        "hash": f"{index:040x}",
        "name": f"Debian netinst {index} amd64 iso",
        "state": states[index % len(states)],
        "progress": min(1.0, 0.13 * index),
        "ratio": 0.4 * index,
        "size": 700_000_000 + index * 1_100_000_000,
        "category": ["", "sonarr", "radarr", "cross-seed"][index % 4],
        "tags": ["", "stale", "keep,stale"][index % 3],
        "dlspeed": 0 if index % 2 else 1_400_000,
        "upspeed": 220_000 if index % 3 else 0,
        "dl_limit": 500_000 if index % 5 == 0 else 0,
        "up_limit": 0,
        "downloaded": 500_000_000 * index,
        "uploaded": 320_000_000 * index,
        "seeding_time": 3_600 * 24 * index,
        "added_on": 1_700_000_000 - index * 86_400,
        "completion_on": 1_700_086_400 - index * 86_400,
        "last_activity": 1_700_090_000 - index * 3_600,
        "save_path": f"/downloads/{index}",
        # The Overview attributes each torrent to its working tracker,
        # so a fixture without one photographs a single "no tracker"
        # row instead of the window under test.
        "tracker": _TRACKERS[index % len(_TRACKERS)],
        "trackers_count": 1 + index % 3,
    }
    base.update(overrides)
    return base


class _GalleryClient:
    """Only the calls the refresh path makes, answered from fixtures."""

    def __init__(self) -> None:
        self.torrents = [_torrent(index) for index in range(1, 13)]

    def torrents_info(self, torrent_hashes: Any = None) -> list[dict[str, Any]]:
        if torrent_hashes is None:
            return self.torrents
        wanted = {value.lower() for value in torrent_hashes}
        return [t for t in self.torrents if t["hash"].lower() in wanted]

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        return [{"url": "https://tracker.example/announce", "status": "2"}]

    def transfer_info(self) -> dict[str, int]:
        return {"dl_info_speed": 4_200_000, "up_info_speed": 900_000}

    def sync_maindata(self, rid: str | int = 0) -> dict[str, Any]:
        # Every field the Session window reads, or the capture shows
        # blanks where the screen under review has values.
        return {
            "server_state": {
                "alltime_dl": 8_400_000_000_000,
                "alltime_ul": 12_900_000_000_000,
                "global_ratio": "1.54",
                "total_peer_connections": 214,
                "dl_info_data": 442_000_000_000,
                "up_info_data": 94_500_000_000,
                "dht_nodes": 387,
                "dl_rate_limit": 0,
                "up_rate_limit": 2_097_152,
                "use_alt_speed_limits": False,
                "free_space_on_disk": 1_319_413_953_331,
                "queueing": True,
                "connection_status": "firewalled",
            },
            "rid": 1,
        }

    def app_version(self) -> str:
        return "5.2.3"

    def app_web_api_version(self) -> str:
        return "2.11.4"

    def torrents_categories(self) -> dict[str, dict[str, Any]]:
        return {name: {"name": name} for name in ("sonarr", "radarr")}

    def torrents_tags(self) -> list[str]:
        return ["stale", "keep"]

    # The seven LOW-risk bulk actions the TUI can reach (see
    # `qbit_ops.tui.app`'s security-boundary docstring). Accepted and
    # discarded: without them the `result`/value-modal captures
    # photograph a client defect rather than the screen the gallery
    # claims to show.
    def torrents_pause(self, torrent_hashes: Any = None) -> None:
        return None

    def torrents_resume(self, torrent_hashes: Any = None) -> None:
        return None

    def torrents_reannounce(self, torrent_hashes: Any = None) -> None:
        return None

    def torrents_create_category(self, name: str, **kwargs: Any) -> None:
        return None

    def torrents_set_category(
        self, torrent_hashes: Any = None, category: str = ""
    ) -> None:
        return None

    def torrents_add_tags(
        self, torrent_hashes: Any = None, tags: Any = None
    ) -> None:
        return None

    def torrents_remove_tags(
        self, torrent_hashes: Any = None, tags: Any = None
    ) -> None:
        return None

    def torrents_set_download_limit(
        self, torrent_hashes: Any = None, limit: int = 0
    ) -> None:
        return None

    def torrents_set_upload_limit(
        self, torrent_hashes: Any = None, limit: int = 0
    ) -> None:
        return None


# Each entry drives the app to one screen. The keys are stable file
# names: a reviewer compares `before/filters.svg` with `after/filters.svg`
# by name, so renaming one silently breaks the comparison.
SCREENS: dict[str, list[str]] = {
    "overview": ["g"],
    "torrents": ["t"],
    "filters": ["t", "f"],
    "sort": ["t", "s"],
    "help": ["t", "question_mark"],
    "details": ["t", "enter"],
    "actions": ["t", "space", "a"],
    "explain": ["t", "e"],
    "preview": ["t", "space", "a", "enter"],
    "result": ["t", "space", "a", "enter", "enter"],
    "setup": [],
    # ActionsScreen's button order: Pause, Resume, Reannounce, Set
    # category, Add tags, Remove tags, Set limits, Cancel -- "down"
    # N times reaches the Nth value-action button from Pause.
    "value-category": ["t", "space", "a", "down", "down", "down", "enter"],
    "value-tag-add": [
        "t",
        "space",
        "a",
        "down",
        "down",
        "down",
        "down",
        "enter",
    ],
    "value-tag-remove": [
        "t",
        "space",
        "a",
        "down",
        "down",
        "down",
        "down",
        "down",
        "enter",
    ],
    "value-throttle": [
        "t",
        "space",
        "a",
        "down",
        "down",
        "down",
        "down",
        "down",
        "down",
        "enter",
    ],
}

# The one screen no key sequence can reach: it is shown *instead of*
# the dashboard, decided at construction, so it needs its own app.
NEEDS_SETUP = frozenset({"setup"})


def build_app(name: str) -> QbitOpsTuiApp:
    return QbitOpsTuiApp(
        client_factory=lambda: _GalleryClient(),
        host="http://localhost:8080",
        refresh_interval=3600.0,
        needs_setup=name in NEEDS_SETUP,
    )


# A synthetic couple of minutes of transfer, so the Overview capture
# shows the braille alphabet, the row floored against the axis, and the
# per-tracker sparklines. Left empty, the graph would photograph its
# warm-up state -- honest, and useless for judging what it draws.
_SEEDED_SAMPLES = 200


def _seed_rate_history(app: QbitOpsTuiApp) -> None:
    history = app.controller.state.rate_history
    breakdown = app.controller.state.tracker_breakdown
    rows = breakdown.rows if breakdown is not None else ()
    for index in range(_SEEDED_SAMPLES):
        history.record_transfer(
            download=int(3_000_000 * (1 + math.sin(index / 11)) + 200_000),
            upload=int(430_000 * (1 + math.cos(index / 7))),
        )
    for index in range(12):
        history.record_trackers(
            {
                row.key: row.total_rate * (1 + (index + position) % 4) // 4
                for position, row in enumerate(rows)
            }
        )


async def _capture(name: str, keys: list[str], out: Path) -> Path:
    app = build_app(name)
    async with app.run_test(size=GALLERY_SIZE) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        if name == "overview":
            await app.workers.wait_for_complete()
            await pilot.pause()
            _seed_rate_history(app)
            app._render_all()
        # A second settle: pushing a screen mounts widgets whose own
        # first paint lands on the next frame, and exporting between the
        # two captures a half-drawn modal.
        await pilot.pause()
        svg = _CHROME_CIRCLE.sub(
            "", app.export_screenshot(title=f"qbit-ops -- {name}")
        )

    target = out / f"{name}.svg"
    target.write_text(svg, encoding="utf-8")
    return target


async def _run(names: list[str], out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        try:
            target = await _capture(name, SCREENS[name], out)
        except Exception as error:  # noqa: BLE001 - one screen must not
            # take the gallery down: a broken screen is exactly what a
            # reviewer needs to see reported, by name.
            print(f"  {name:10} FAILED: {type(error).__name__}: {error}")
            continue
        try:
            shown = target.relative_to(REPO_ROOT)
        except ValueError:
            shown = target
        print(f"  {name:10} {shown}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--only", help="comma-separated screen names", default=""
    )
    args = parser.parse_args(argv)

    names = [n for n in args.only.split(",") if n] or list(SCREENS)
    unknown = [n for n in names if n not in SCREENS]
    if unknown:
        print(f"Unknown screen(s): {', '.join(unknown)}")
        print(f"Known: {', '.join(SCREENS)}")
        return 1

    return asyncio.run(_run(names, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
