"""Measure the real cadence of the Overview's per-second rate sampler.

Records the timer firing and the moment a sample actually enters the
history, so timer drift, contention with the periodic refresh, and
network jitter can be told apart instead of guessed at.
"""

import asyncio
import random
import statistics
import sys
import time

sys.path.insert(0, ".")
from typing import Any

from qbit_ops.tui.app import QbitOpsTuiApp
from qbit_ops.tui.widgets.rate_graph import RateGraph


class Instance:
    """Only what the refresh and the sampler call, with latency knobs."""

    def __init__(
        self,
        *,
        torrents: int,
        list_latency: float,
        rate_latency: float,
        rate_jitter: float,
    ) -> None:
        self.list_latency = list_latency
        self.rate_latency = rate_latency
        self.rate_jitter = rate_jitter
        self.torrents = [
            {
                "hash": f"{i:040x}",
                "name": f"t{i}",
                "state": "uploading",
                "progress": 1.0,
                "ratio": 1.0,
                "size": 10**9,
                "category": "",
                "tags": "",
                "dlspeed": 0,
                "upspeed": 0,
                "tracker": f"http://t{i % 4}.tld/a",
                "trackers_count": 1,
            }
            for i in range(torrents)
        ]
        self.n = 0

    def torrents_info(self, torrent_hashes: Any = None) -> list[dict[str, Any]]:
        time.sleep(self.list_latency)
        return self.torrents

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        return []

    def transfer_info(self) -> dict[str, int]:
        delay = self.rate_latency
        if self.rate_jitter:
            delay += random.uniform(0, self.rate_jitter)
        if delay:
            time.sleep(delay)
        self.n += 1
        return {"dl_info_speed": 1_000_000 + self.n, "up_info_speed": 200_000}

    def sync_maindata(self) -> dict[str, Any]:
        return {
            "server_state": {
                "alltime_dl": 1,
                "alltime_ul": 1,
                "global_ratio": "1.0",
                "total_peer_connections": 3,
                "connection_status": "connected",
            }
        }

    def app_version(self) -> str:
        return "5.2.3"

    def app_web_api_version(self) -> str:
        return "2.11.4"


async def run(
    label: str,
    *,
    torrents: int,
    list_latency: float,
    rate_latency: float,
    rate_jitter: float,
    refresh_interval: float,
    seconds: float,
) -> None:
    random.seed(1)
    client = Instance(
        torrents=torrents,
        list_latency=list_latency,
        rate_latency=rate_latency,
        rate_jitter=rate_jitter,
    )
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=refresh_interval,
    )
    fired: list[float] = []
    recorded: list[float] = []

    original_start = app._start_rate_sample
    original_apply = app.controller.open_rate_slot

    def start() -> None:
        fired.append(time.monotonic())
        original_start()

    def apply() -> int:
        recorded.append(time.monotonic())
        return original_apply()

    app._start_rate_sample = start  # type: ignore[method-assign]
    app.controller.open_rate_slot = apply  # type: ignore[method-assign]

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await asyncio.sleep(seconds)
        panel = app.query_one(RateGraph).size.width

    def spread(name: str, stamps: list[float]) -> str:
        if len(stamps) < 3:
            return f"{name}: too few ({len(stamps)})"
        d = [b - a for a, b in zip(stamps, stamps[1:], strict=True)]
        return (
            f"{name:<16} n={len(d):<3} min={min(d):.2f} "
            f"p50={statistics.median(d):.2f} max={max(d):.2f} "
            f"gaps>1.5s={sum(1 for x in d if x > 1.5)}"
        )

    print(f"--- {label}  ({torrents} torrents, panel {panel} cols)")
    print("   ", spread("timer fired", fired))
    print("   ", spread("column opened", recorded))
    print(
        f"     fired={len(fired)} recorded={len(recorded)} "
        f"dropped={len(fired) - len(recorded)} "
        f"({100 * (len(fired) - len(recorded)) / max(len(fired), 1):.0f}%)"
    )
    print()


async def main() -> None:
    await run(
        "ideal: instant instance",
        torrents=12,
        list_latency=0.0,
        rate_latency=0.0,
        rate_jitter=0.0,
        refresh_interval=5.0,
        seconds=20.0,
    )
    await run(
        "big library, slow list",
        torrents=1147,
        list_latency=0.9,
        rate_latency=0.02,
        rate_jitter=0.0,
        refresh_interval=5.0,
        seconds=20.0,
    )
    await run(
        "network jitter on rates",
        torrents=12,
        list_latency=0.0,
        rate_latency=0.05,
        rate_jitter=0.6,
        refresh_interval=5.0,
        seconds=20.0,
    )


asyncio.run(main())
