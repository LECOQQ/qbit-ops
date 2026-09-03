"""Shell-completion callbacks for options whose values live on the instance.

The counterpart to `qbit_ops.cli.completion.complete_choices`: same
Typer `autocompletion` contract, but a different, weaker guarantee.
`complete_choices` is pure and network-free *by construction* -- its
values are baked in at declaration time, so completion itself cannot
reach qBittorrent. `--category`/`--tag`/`--tracker` have no such closed
set to bake in: their values live on the instance (`.agents/specs/
completion-cache.md`). Kept in a separate module rather than merged
into `completion.py` so that module's own docstring -- "only an
enumeration knowable without talking to qBittorrent belongs here" --
stays literally true.

What this module *does* still guarantee, mechanically:

- a cache hit never touches the network, at all;
- `tracker_hosts` never probes the network, ever -- see `_values_for`;
- `categories`/`tags` probe the network at most once per otherwise-empty
  cache, budgeted to `COLD_START_PROBE_BUDGET_SECONDS`, and never again
  once that probe has failed (`completion_cache`'s witness);
- `_complete` itself never raises: a shell-completion callback that
  does breaks the user's shell, not just the command.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Literal

from qbit_core.features.torrents import known_tags
from qbit_ops.cli import error_boundary
from qbit_ops.cli.completion_cache import (
    mark_cold_start_probe_failed,
    merge_instance_vocabulary,
    read_instance_vocabulary,
)

__all__ = ["complete_live_choices"]

COLD_START_PROBE_BUDGET_SECONDS = 0.3

Vocabulary = Literal["categories", "tags", "tracker_hosts"]

# `tracker_hosts` is deliberately excluded: filling it costs one
# `torrents_trackers()` call per torrent (there is no single cheap API
# call for "every tracker host in use"), which cannot be bounded to the
# budget independent of library size. It fills exclusively through
# `trackers list`/`trackers status` usage instead.
_PROBED_VOCABULARIES: frozenset[Vocabulary] = frozenset({"categories", "tags"})


def complete_live_choices(vocabulary: Vocabulary) -> Callable[[str], list[str]]:
    """Build a Typer `autocompletion` callback for one live vocabulary."""

    def _complete(incomplete: str) -> list[str]:
        try:
            values = _values_for(vocabulary)
        except Exception:  # noqa: BLE001 - never break the user's shell
            return []
        return [value for value in values if value.startswith(incomplete)]

    return _complete


def _values_for(vocabulary: Vocabulary) -> tuple[str, ...]:
    try:
        host = error_boundary.load_qbit_config().host
    except Exception:  # noqa: BLE001 - no usable config, nothing to complete
        return ()

    entry = read_instance_vocabulary(host)
    cached = getattr(entry, vocabulary)
    if cached:
        return cached
    if vocabulary not in _PROBED_VOCABULARIES or entry.cold_start_probe_failed:
        return ()

    probed = _cold_start_probe()
    if probed is None:
        mark_cold_start_probe_failed(host)
        return ()

    categories, tags = probed
    merge_instance_vocabulary(host, categories=categories, tags=tags)
    return categories if vocabulary == "categories" else tags


def _cold_start_probe() -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Attempt once to fetch categories and tags, bounded to the budget.

    The attempt runs on a background daemon thread so a hung connection
    can be abandoned at the budget instead of blocking the shell.
    `outcome` is only ever read by this (the calling) thread, and only
    after `join()` -- the background thread never touches the cache or
    any shared state beyond writing its own result into `outcome`, so
    a probe that finishes late (after the budget) has nothing left to
    race: its result is simply never read.
    """
    outcome: dict[str, Any] = {}

    def _attempt() -> None:
        try:
            client = error_boundary.create_qbit_client()
            categories = tuple(sorted(client.torrents_categories()))
            tags = known_tags(client)
        except Exception as error:  # noqa: BLE001 - any failure -> probe failed
            outcome["error"] = error
        else:
            outcome["categories"] = categories
            outcome["tags"] = tags

    thread = threading.Thread(target=_attempt, daemon=True)
    thread.start()
    thread.join(timeout=COLD_START_PROBE_BUDGET_SECONDS)

    if thread.is_alive() or "categories" not in outcome:
        return None
    return outcome["categories"], outcome["tags"]
