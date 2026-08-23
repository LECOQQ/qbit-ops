"""Rank a torrent snapshot corpus against a free-text query -- read-only.

The seven-tier ladder from `MatchTier` through `SearchResults`: pure,
`TorrentSnapshot`-in/`SearchHit`-out, no qBittorrent client, no
`SelectionRequest`, no `TorrentFilter`. A search result is a cul-de-sac
by construction -- see `SearchResults` -- never a step towards a
mutation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from qbit_core.errors import InvalidInputError
from qbit_core.shared.torrent_states import TorrentSnapshot

MatchTier = Literal[
    "hash",
    "exact",
    "prefix",
    "substring",
    "all_tokens",
    "token_prefix",
    "fuzzy",
]

SearchMode = Literal["exact", "contains", "tokens", "fuzzy"]

_TIER_ORDER: tuple[MatchTier, ...] = (
    "hash",
    "exact",
    "prefix",
    "substring",
    "all_tokens",
    "token_prefix",
    "fuzzy",
)
_TIER_INDEX: dict[MatchTier, int] = {
    tier: index for index, tier in enumerate(_TIER_ORDER)
}

# Each mode's ladder, "up to and including this tier" -- a slice of
# `_TIER_ORDER`, never a hand-maintained duplicate of it.
_MODE_TIERS: dict[SearchMode, frozenset[MatchTier]] = {
    "exact": frozenset(_TIER_ORDER[:2]),
    "contains": frozenset(_TIER_ORDER[:4]),
    "tokens": frozenset(_TIER_ORDER[:6]),
    "fuzzy": frozenset(_TIER_ORDER),
}

# The fuzzy tier's two figured points (see `.agents/features/search/SPEC.md`):
# never a `_score_name_match`-style parameter default, since the floor
# lives with the caller, not the callee.
FUZZY_SIMILARITY_THRESHOLD = 0.75
FUZZY_MAX_TOKEN_LENGTH_GAP = 2

_HEX_DIGITS = frozenset("0123456789abcdef")
_SEPARATOR_PATTERN = re.compile(r"[\W_]+")


def normalize_search_text(text: str) -> str:
    """Fold text into the comparable form the whole ladder matches on.

    NFKD decomposition before stripping combining marks handles accents,
    ligatures, and exotic digit forms in one pass. The character class
    is `[\\W_]`, never an ASCII `[^0-9a-z]` -- that would reduce a CJK or
    Cyrillic title to whatever digits it happens to contain.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    folded = without_marks.casefold()
    return _SEPARATOR_PATTERN.sub(" ", folded).strip()


def tokenize_search_text(normalized_text: str) -> tuple[str, ...]:
    """Split already-normalized text on its collapsed whitespace."""
    return tuple(normalized_text.split())


def validate_search_query(query: str) -> str:
    """Reject a query that is empty or normalizes to empty.

    Raises `InvalidInputError` -- callers must run this before any
    qBittorrent call. `"..."` and `"---"` normalize to blank and must
    fail the same way as `""`. Returns the normalized query.
    """
    normalized = normalize_search_text(query)
    if normalized == "":
        raise InvalidInputError(
            "Provide a search query with at least one letter, digit, or "
            "word -- punctuation alone does not match anything."
        )
    return normalized


@dataclass(frozen=True)
class SearchHit:
    """One ranked match. `similarity` is set only for the `fuzzy` tier."""

    torrent: TorrentSnapshot
    tier: MatchTier
    similarity: float | None = None


@dataclass(frozen=True)
class SearchResults:
    """A ranked search outcome, and the type that keeps it a cul-de-sac.

    `matched` counts every match *before* truncation; `truncated` is
    `matched > len(hits)`. Deliberately exposes no hash set and no
    `to_selection_request()` -- see `.agents/features/search/SPEC.md`.
    """

    query: str
    normalized_query: str
    scanned: int
    matched: int
    hits: tuple[SearchHit, ...]
    truncated: bool


def search_snapshots(
    corpus: Sequence[TorrentSnapshot],
    query: str,
    *,
    mode: SearchMode,
    hash_min_length: int,
    limit: int,
    scanned: int | None = None,
) -> SearchResults:
    """Rank `corpus` against `query` up to `mode`'s tier ceiling.

    `scanned` defaults to `len(corpus)`; a caller that pre-filters the
    corpus before scoring (composable `search` filters narrow the
    corpus, never the other way around) passes the pre-filter count
    explicitly, so `scanned` still reports the size before filtering.
    """
    return _search_snapshots_over_tiers(
        corpus,
        query,
        tiers=_MODE_TIERS[mode],
        hash_min_length=hash_min_length,
        limit=limit,
        scanned=len(corpus) if scanned is None else scanned,
    )


def _search_snapshots_over_tiers(
    corpus: Sequence[TorrentSnapshot],
    query: str,
    *,
    tiers: frozenset[MatchTier],
    hash_min_length: int,
    limit: int,
    scanned: int,
) -> SearchResults:
    """`search_snapshots`'s engine, over an explicit tier set rather than
    a mode -- the knob property tests need to isolate one tier."""
    normalized_query = normalize_search_text(query)
    if normalized_query == "":
        return SearchResults(
            query=query,
            normalized_query=normalized_query,
            scanned=scanned,
            matched=0,
            hits=(),
            truncated=False,
        )

    query_tokens = tokenize_search_text(normalized_query)

    scored: list[tuple[tuple[int, float, float, str, str], SearchHit]] = []
    for torrent in corpus:
        classified = _classify(
            torrent,
            normalized_query=normalized_query,
            query_tokens=query_tokens,
            tiers=tiers,
            hash_min_length=hash_min_length,
        )
        if classified is None:
            continue
        tier, secondary_a, secondary_b, similarity = classified
        # The final tie-break is (tier, secondary, name.casefold(), hash)
        # -- except tier 0 itself, whose *secondary* order is "hash
        # ascending" (see the ladder table): the hash must prime over
        # the name there, not merely serve as the last-resort split.
        # Both slots stay `str` either way, so the sort key's shape
        # never depends on which tier produced it. Hashes compare
        # lowercased, same casing `_matches_hash` already normalizes to.
        hash_key = torrent.hash.lower()
        name_key = torrent.name.casefold()
        primary_tie_break, secondary_tie_break = (
            (hash_key, name_key) if tier == "hash" else (name_key, hash_key)
        )
        sort_key = (
            _TIER_INDEX[tier],
            secondary_a,
            secondary_b,
            primary_tie_break,
            secondary_tie_break,
        )
        hit = SearchHit(torrent=torrent, tier=tier, similarity=similarity)
        scored.append((sort_key, hit))

    scored.sort(key=lambda item: item[0])
    matched = len(scored)
    hits = tuple(hit for _, hit in scored)
    if limit > 0:
        hits = hits[:limit]

    return SearchResults(
        query=query,
        normalized_query=normalized_query,
        scanned=scanned,
        matched=matched,
        hits=hits,
        truncated=matched > len(hits),
    )


def _classify(
    torrent: TorrentSnapshot,
    *,
    normalized_query: str,
    query_tokens: tuple[str, ...],
    tiers: frozenset[MatchTier],
    hash_min_length: int,
) -> tuple[MatchTier, float, float, float | None] | None:
    """Return the first ladder tier `torrent` matches, or `None`.

    Tested in the fixed 0-6 order regardless of which tiers are active;
    `tiers` only decides which of them are eligible to fire, so a mode
    can only ever narrow which tier wins, never which one is tried
    first among the tiers it allows.
    """
    if "hash" in tiers and _matches_hash(
        torrent.hash, normalized_query, hash_min_length
    ):
        # Tier 0's own secondary order ("hash ascending") is not encoded
        # here -- a 160-bit hash cannot survive a float64 secondary slot
        # without truncation/collisions. It is assembled in the sort key
        # instead, in `_search_snapshots_over_tiers`.
        return "hash", 0.0, 0.0, None

    normalized_name = normalize_search_text(torrent.name)

    if "exact" in tiers and normalized_name == normalized_query:
        return "exact", 0.0, 0.0, None

    if "prefix" in tiers and normalized_name.startswith(normalized_query):
        return "prefix", float(len(normalized_name)), 0.0, None

    if "substring" in tiers:
        position = normalized_name.find(normalized_query)
        if position != -1:
            return (
                "substring",
                float(position),
                float(len(normalized_name)),
                None,
            )

    name_tokens = tokenize_search_text(normalized_name)

    if "all_tokens" in tiers and _all_tokens_present(query_tokens, name_tokens):
        return "all_tokens", float(len(normalized_name)), 0.0, None

    if "token_prefix" in tiers and _all_tokens_prefixed(
        query_tokens, name_tokens
    ):
        return "token_prefix", float(len(normalized_name)), 0.0, None

    if "fuzzy" in tiers:
        similarity = _match_fuzzy(query_tokens, name_tokens)
        if similarity is not None:
            return "fuzzy", -similarity, 0.0, similarity

    return None


def _matches_hash(
    torrent_hash: str, normalized_query: str, hash_min_length: int
) -> bool:
    """Tier 0: `normalized_query` is a long-enough hex prefix of the hash."""
    if len(normalized_query) < hash_min_length:
        return False
    if not all(char in _HEX_DIGITS for char in normalized_query):
        return False
    return torrent_hash.lower().startswith(normalized_query)


def _all_tokens_present(
    query_tokens: tuple[str, ...], name_tokens: tuple[str, ...]
) -> bool:
    """Tier 4: every query token is a literal token of the name."""
    name_token_set = set(name_tokens)
    return all(token in name_token_set for token in query_tokens)


def _all_tokens_prefixed(
    query_tokens: tuple[str, ...], name_tokens: tuple[str, ...]
) -> bool:
    """Tier 5: every query token prefixes at least one token of the name."""
    return all(
        any(name_token.startswith(query_token) for name_token in name_tokens)
        for query_token in query_tokens
    )


def _best_token_similarity(
    query_token: str, name_tokens: Sequence[str]
) -> float | None:
    """Best `ratio()` between `query_token` and a distinct name token
    within `FUZZY_MAX_TOKEN_LENGTH_GAP`, at or above
    `FUZZY_SIMILARITY_THRESHOLD` -- or `None` if none qualifies.

    The query token is always `seq2` (`set_seq2`, once); each candidate
    name token is `seq1` (`set_seq1`) -- difflib's own idiom for
    comparing one sequence against many, and the side `autojunk`'s 1%
    popularity heuristic profiles. `autojunk=False` is explicit: the
    default silently misclassifies a long repetitive token as mostly
    "junk" once `len(seq2) >= 200`.
    """
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(query_token)
    best: float | None = None
    for name_token in dict.fromkeys(name_tokens):
        if abs(len(name_token) - len(query_token)) > FUZZY_MAX_TOKEN_LENGTH_GAP:
            continue
        matcher.set_seq1(name_token)
        ratio = matcher.ratio()
        if ratio < FUZZY_SIMILARITY_THRESHOLD:
            continue
        if best is None or ratio > best:
            best = ratio
    return best


def _match_fuzzy(
    query_tokens: tuple[str, ...], name_tokens: tuple[str, ...]
) -> float | None:
    """Tier 6: `None` unless every distinct query token finds a
    qualifying name token; otherwise the average of their best
    similarities (the tier's secondary sort key, descending)."""
    best_similarities: list[float] = []
    for query_token in dict.fromkeys(query_tokens):
        best = _best_token_similarity(query_token, name_tokens)
        if best is None:
            return None
        best_similarities.append(best)
    return sum(best_similarities) / len(best_similarities)
