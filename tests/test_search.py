"""Test `qbit_core.shared.search`: the `torrents search` ranking engine."""

from __future__ import annotations

import string
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

import pytest

import qbit_core.shared.search as search_module
from qbit_core.shared.search import (
    FUZZY_MAX_TOKEN_LENGTH_GAP,
    FUZZY_SIMILARITY_THRESHOLD,
    MatchTier,
    SearchMode,
    SearchResults,
    _best_token_similarity,
    _search_snapshots_over_tiers,
    normalize_search_text,
    search_snapshots,
    tokenize_search_text,
)
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    build_torrent_snapshot,
)
from tests.support import make_torrent

# --- S1: normalization -------------------------------------------------


def test_normalization_folds_case() -> None:
    assert normalize_search_text("ABC") == normalize_search_text("abc")


def test_normalization_uses_casefold_not_lower() -> None:
    # The German sharp s casefolds to "ss"; `str.lower()` leaves it alone.
    assert normalize_search_text("straße") == "strasse"


def test_normalization_strips_accents_and_diacritics() -> None:
    assert normalize_search_text("Père Noël") == normalize_search_text(
        "Pere Noel"
    )


def test_normalization_decomposes_ligatures_and_exotic_digits() -> None:
    # NFKD: "ﬁ" -> "fi", superscript "²" -> "2".
    assert normalize_search_text("ﬁle²") == "file2"


def test_normalization_uses_unicode_word_class_not_ascii() -> None:
    """The character class is `[\\W_]`, never `[^0-9a-z]` -- CJK and
    Cyrillic text must stay tokenizable by more than embedded digits."""
    assert (
        normalize_search_text("千と千尋の神隠し.2001.1080p")
        == "千と千尋の神隠し 2001 1080p"
    )
    assert normalize_search_text("Война.и.мир.1966") == "воина и мир 1966"


def test_normalization_folds_separators_to_space() -> None:
    assert normalize_search_text("Père_Noël-2023.mkv") == normalize_search_text(
        "Pere Noel 2023 mkv"
    )


def test_normalization_folds_punctuation_and_brackets_to_space() -> None:
    assert normalize_search_text("L'Amour, c'est [DEFINITIF]!") == (
        "l amour c est definitif"
    )


def test_normalization_collapses_multiple_separators() -> None:
    assert normalize_search_text("a   b...c---d") == "a b c d"


def test_normalization_strips_leading_and_trailing_separators() -> None:
    assert normalize_search_text("  .hello.  ") == "hello"


def test_normalization_of_pure_punctuation_is_blank() -> None:
    assert normalize_search_text("...") == ""
    assert normalize_search_text("---") == ""


def test_normalization_does_not_split_episode_codes() -> None:
    """S01E02 is not decomposed into season/episode -- stays one token."""
    assert normalize_search_text("Show.S01E02") == "show s01e02"


# --- S1: tokenization ----------------------------------------------------


def test_tokenize_splits_on_normalized_whitespace() -> None:
    assert tokenize_search_text("amour est dans le pre") == (
        "amour",
        "est",
        "dans",
        "le",
        "pre",
    )


def test_tokenize_of_blank_text_is_empty() -> None:
    assert tokenize_search_text("") == ()


def test_tokenize_preserves_token_order() -> None:
    normalized = normalize_search_text("le pre est dans amour")
    assert tokenize_search_text(normalized) == (
        "le",
        "pre",
        "est",
        "dans",
        "amour",
    )


# --- S2: SearchHit / SearchResults / ladder / ranking ---------------------

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40
HASH_D = "d" * 40
HASH_E = "e" * 40
HASH_F = "f" * 40
HASH_TARGET = "3f2a1b9cdeadbeef0123456789abcdef01234567"


def _snapshot(**overrides: Any) -> TorrentSnapshot:
    """Build a central-model snapshot from a raw torrent, as production does."""
    return build_torrent_snapshot(make_torrent(**overrides))


def _search(
    corpus: Sequence[TorrentSnapshot],
    query: str,
    *,
    mode: SearchMode = "tokens",
    hash_min_length: int = 8,
    limit: int = 0,
) -> SearchResults:
    return search_snapshots(
        corpus, query, mode=mode, hash_min_length=hash_min_length, limit=limit
    )


def _tiers(results: SearchResults) -> tuple[MatchTier, ...]:
    return tuple(hit.tier for hit in results.hits)


def _hashes(results: SearchResults) -> tuple[str, ...]:
    return tuple(hit.torrent.hash for hit in results.hits)


# --- SearchHit / SearchResults: field contract ----------------------------


def test_search_hit_carries_a_torrent_snapshot_never_a_selected_torrent() -> (
    None
):
    snapshot = _snapshot(hash=HASH_A, name="Amour")
    results = _search([snapshot], "amour")
    assert results.hits[0].torrent is snapshot


def test_similarity_is_none_outside_the_fuzzy_tier() -> None:
    snapshot = _snapshot(hash=HASH_A, name="Amour")
    results = _search([snapshot], "amour")
    assert results.hits[0].tier == "exact"
    assert results.hits[0].similarity is None


def test_matched_counts_before_truncation_and_truncated_reflects_it() -> None:
    corpus = [
        _snapshot(hash=HASH_A, name="Amour Un"),
        _snapshot(hash=HASH_B, name="Amour Deux"),
        _snapshot(hash=HASH_C, name="Amour Trois"),
    ]
    results = _search(corpus, "amour", limit=2)
    assert results.matched == 3
    assert len(results.hits) == 2
    assert results.truncated is True


def test_zero_limit_means_no_truncation() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Amour Un")]
    results = _search(corpus, "amour", limit=0)
    assert results.matched == 1
    assert len(results.hits) == 1
    assert results.truncated is False


def test_scanned_is_an_explicit_input_not_derived_from_corpus_length() -> None:
    """`scanned` reports the pre-filter corpus size; a caller that
    pre-filters before scoring must be able to pass it explicitly."""
    corpus = [_snapshot(hash=HASH_A, name="Amour")]
    results = search_snapshots(
        corpus, "amour", mode="tokens", hash_min_length=8, limit=0, scanned=1105
    )
    assert results.scanned == 1105


def test_scanned_defaults_to_corpus_length() -> None:
    corpus = [
        _snapshot(hash=HASH_A, name="Amour"),
        _snapshot(hash=HASH_B, name="Unrelated"),
    ]
    results = _search(corpus, "amour")
    assert results.scanned == 2


def test_blank_normalizing_query_yields_zero_matches_not_an_error() -> None:
    """The engine itself never rejects a blank query -- rejection before
    any API call is `validate_search_query`'s job (S5's caller)."""
    corpus = [_snapshot(hash=HASH_A, name="Amour")]
    results = _search(corpus, "...")
    assert results.normalized_query == ""
    assert results.matched == 0
    assert results.hits == ()
    assert results.truncated is False


# --- Tier 0: hash -----------------------------------------------------------


def test_hash_tier_matches_hex_prefix_at_or_above_min_length() -> None:
    corpus = [_snapshot(hash=HASH_TARGET, name="Completely Unrelated Title")]
    results = _search(corpus, "3f2a1b9c", hash_min_length=4)
    assert _tiers(results) == ("hash",)
    assert results.hits[0].torrent.hash == HASH_TARGET


def test_hash_tier_does_not_match_below_min_length() -> None:
    corpus = [_snapshot(hash=HASH_TARGET, name="Completely Unrelated Title")]
    results = _search(corpus, "3f2", hash_min_length=8)
    assert results.matched == 0


def test_hash_tier_requires_a_purely_hexadecimal_query() -> None:
    """A query with a space is never hex, even if some words are hex-ish."""
    corpus = [_snapshot(hash=HASH_TARGET, name="Completely Unrelated Title")]
    results = _search(corpus, "3f2a 1b9c", hash_min_length=4)
    assert not any(hit.tier == "hash" for hit in results.hits)


def test_hash_tier_is_case_insensitive() -> None:
    corpus = [_snapshot(hash=HASH_TARGET.upper(), name="Unrelated")]
    results = _search(corpus, "3f2a1b9c", hash_min_length=4)
    assert _tiers(results) == ("hash",)


def test_hash_tier_secondary_order_is_hash_ascending_not_name() -> None:
    """The ladder's secondary order for tier 0 is "hash croissant" -- it
    must prime over `name.casefold()`, not the other way around. Two
    hits are required: with a single hit, the final tie-break alone
    would already pick the right (only) answer, which is exactly why
    the four other hash-tier tests above never caught this."""
    corpus = [
        _snapshot(hash="beef2000" + "0" * 32, name="Alpha Torrent"),
        _snapshot(hash="beef1000" + "0" * 32, name="Zebra Torrent"),
    ]
    results = _search(corpus, "beef", mode="tokens", hash_min_length=1)
    assert _tiers(results) == ("hash", "hash")
    assert _hashes(results) == (
        "beef1000" + "0" * 32,
        "beef2000" + "0" * 32,
    )


# --- Tiers 1-5: exact / prefix / substring / all_tokens / token_prefix ----


def test_tier_exact() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Amour Est")]
    results = _search(corpus, "amour est")
    assert _tiers(results) == ("exact",)


def test_tier_prefix() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Amour Est Toujours")]
    results = _search(corpus, "amour est")
    assert _tiers(results) == ("prefix",)


def test_tier_substring() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Mon Amour Est La")]
    results = _search(corpus, "amour est")
    assert _tiers(results) == ("substring",)


def test_tier_all_tokens_matches_out_of_order_tokens() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Est Amour Fidele")]
    results = _search(corpus, "amour est")
    assert _tiers(results) == ("all_tokens",)


def test_tier_token_prefix_matches_partial_words() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Amoureux Estival Jour")]
    results = _search(corpus, "amour est")
    assert _tiers(results) == ("token_prefix",)


def test_tier_token_prefix_requires_every_query_token_to_prefix_something() -> (
    None
):
    """One query token with no prefix match anywhere sinks the whole tier."""
    corpus = [_snapshot(hash=HASH_A, name="Amoureux Jour")]
    results = _search(corpus, "amour est")
    assert results.matched == 0


def test_tier_all_tokens_requires_every_query_token_present() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Amour Fidele")]
    results = _search(corpus, "amour est")
    assert results.matched == 0


def test_mode_ceiling_excludes_tiers_beyond_it() -> None:
    """`contains` mode (tiers 0-3) never reaches `all_tokens`."""
    corpus = [_snapshot(hash=HASH_A, name="Est Amour Fidele")]
    results = _search(corpus, "amour est", mode="contains")
    assert results.matched == 0


# --- Ranking: tier is the primary sort key, in ladder order ----------------


def test_ranking_orders_by_tier_first() -> None:
    corpus = [
        _snapshot(hash=HASH_F, name="Amoureux Estival Jour"),  # token_prefix
        _snapshot(hash=HASH_D, name="Est Amour Fidele"),  # all_tokens
        _snapshot(hash=HASH_C, name="Mon Amour Est La"),  # substring
        _snapshot(hash=HASH_B, name="Amour Est Toujours"),  # prefix
        _snapshot(hash=HASH_A, name="Amour Est"),  # exact
    ]
    results = _search(corpus, "amour est")
    assert _tiers(results) == (
        "exact",
        "prefix",
        "substring",
        "all_tokens",
        "token_prefix",
    )
    assert _hashes(results) == (HASH_A, HASH_B, HASH_C, HASH_D, HASH_F)


def test_prefix_tier_secondary_order_is_shortest_name_first() -> None:
    corpus = [
        _snapshot(hash=HASH_B, name="Amour Est Vraiment Toujours La"),
        _snapshot(hash=HASH_A, name="Amour Est Toujours"),
    ]
    results = _search(corpus, "amour est")
    assert _hashes(results) == (HASH_A, HASH_B)


def test_substring_tier_secondary_order_is_position_then_shortest_name() -> (
    None
):
    corpus = [
        _snapshot(hash=HASH_B, name="Un Peu Mon Amour Est La"),
        _snapshot(hash=HASH_A, name="Mon Amour Est La"),
    ]
    results = _search(corpus, "amour est")
    assert _hashes(results) == (HASH_A, HASH_B)


def test_all_tokens_tier_secondary_order_is_shortest_name_first() -> None:
    corpus = [
        _snapshot(hash=HASH_B, name="Est Amour Fidele Et Vrai"),
        _snapshot(hash=HASH_A, name="Est Amour Fidele"),
    ]
    results = _search(corpus, "amour est")
    assert _hashes(results) == (HASH_A, HASH_B)


def test_final_tie_break_is_name_casefold_then_hash() -> None:
    """Two torrents with the same name: name.casefold() ties, hash decides."""
    corpus = [
        _snapshot(hash=HASH_B, name="Amour Est"),
        _snapshot(hash=HASH_A, name="Amour Est"),
    ]
    results = _search(corpus, "amour est")
    assert _hashes(results) == (HASH_A, HASH_B)


def test_final_tie_break_uses_name_casefold_before_hash() -> None:
    corpus = [
        _snapshot(hash=HASH_B, name="Zebre Est Amour"),  # all_tokens
        _snapshot(hash=HASH_A, name="Alpha Est Amour"),  # same tier, same len
    ]
    results = _search(corpus, "amour est")
    assert _hashes(results) == (HASH_A, HASH_B)


# --- AC5: raising mode only adds results, never reorders or removes -------
# (fuzzy mode is added to this sequence at S3, once the tier is built)

_S2_MODE_SEQUENCE: tuple[SearchMode, ...] = ("exact", "contains", "tokens")


def test_raising_mode_only_adds_results_never_reorders_or_removes() -> None:
    corpus = [
        _snapshot(hash=HASH_A, name="Amour Est"),  # exact
        _snapshot(hash=HASH_B, name="Amour Est Toujours"),  # prefix
        _snapshot(hash=HASH_C, name="Mon Amour Est La"),  # substring
        _snapshot(hash=HASH_D, name="Est Amour Fidele"),  # all_tokens
    ]
    previous_order: tuple[str, ...] | None = None
    for mode in _S2_MODE_SEQUENCE:
        results = _search(corpus, "amour est", mode=mode, hash_min_length=8)
        order = _hashes(results)
        if previous_order is not None:
            assert order[: len(previous_order)] == previous_order, (
                mode,
                order,
                previous_order,
            )
        previous_order = order
    assert previous_order == (HASH_A, HASH_B, HASH_C, HASH_D)


# --- The monotonicity property test (prefixes typed forward) --------------

_MONOTONY_TIERS_0_5: frozenset[MatchTier] = frozenset(
    {"hash", "exact", "prefix", "substring", "all_tokens", "token_prefix"}
)

_MONOTONY_CORPUS = (
    _snapshot(hash=HASH_TARGET, name="Completely Unrelated Title"),
    _snapshot(hash=HASH_C, name="Amour Est Dans Le Pre"),
    _snapshot(hash=HASH_D, name="Le Grand Amour Ancien"),
    _snapshot(hash=HASH_E, name="Historic Preface Notes"),
)

# Piège 1: a hex hash-prefix query journey is *required* in this set --
# without it, tier 0 never fires and the test below is green on a false
# invariant.
_MONOTONY_QUERIES: tuple[str, ...] = ("3f2a1b9c", "amour est dans le pre")


def _prefix_journey_violations(
    corpus: Sequence[TorrentSnapshot],
    query: str,
    *,
    tiers: frozenset[MatchTier],
    hash_min_length: int,
) -> list[tuple[str, int, frozenset[str]]]:
    """Walk `query`'s successive prefixes and report every transition
    where the result set grows instead of only shrinking -- (prefix,
    position, newly-appeared hashes), never a bare `assert`."""
    violations: list[tuple[str, int, frozenset[str]]] = []
    previous_hashes: frozenset[str] | None = None
    for position in range(1, len(query) + 1):
        prefix = query[:position]
        results = _search_snapshots_over_tiers(
            corpus,
            prefix,
            tiers=tiers,
            hash_min_length=hash_min_length,
            limit=0,
            scanned=len(corpus),
        )
        current_hashes = frozenset(hit.torrent.hash for hit in results.hits)
        widened = previous_hashes is not None and not (
            current_hashes <= previous_hashes
        )
        if widened:
            assert previous_hashes is not None
            appeared = current_hashes - previous_hashes
            violations.append((prefix, position, appeared))
        previous_hashes = current_hashes
    return violations


def test_union_0_5_is_monotone_under_forward_typing() -> None:
    """AC6: `S(r[:i+1]) subseteq S(r[:i])` for every tested query."""
    for query in _MONOTONY_QUERIES:
        violations = _prefix_journey_violations(
            _MONOTONY_CORPUS,
            query,
            tiers=_MONOTONY_TIERS_0_5,
            hash_min_length=1,
        )
        assert (
            not violations
        ), f"query {query!r} narrowed then widened: {violations}"


def test_hash_tier_actually_fires_in_the_monotony_corpus() -> None:
    """Piège 1's positive half: the hex-prefix journey must actually
    reach tier 0 at least once, or the test above proves nothing."""
    results = _search_snapshots_over_tiers(
        _MONOTONY_CORPUS,
        "3f2a1b9c",
        tiers=_MONOTONY_TIERS_0_5,
        hash_min_length=1,
        limit=0,
        scanned=len(_MONOTONY_CORPUS),
    )
    hash_hits = [hit for hit in results.hits if hit.tier == "hash"]
    assert hash_hits, "expected at least one hash-tier hit in the journey"
    assert hash_hits[0].torrent.hash == HASH_TARGET


def test_all_tokens_tier_alone_is_not_monotone_under_forward_typing() -> None:
    """Piège 2: proves `_prefix_journey_violations` actually catches a
    violation -- tier 4 alone flickers on "amour est dans le pre"
    (absent at "amour e"/"amour es", present at "amour est", present at
    "amour" alone): a disappearance followed by a reappearance."""
    violations = _prefix_journey_violations(
        _MONOTONY_CORPUS,
        "amour est dans le pre",
        tiers=frozenset({"all_tokens"}),
        hash_min_length=1,
    )
    assert (
        violations
    ), "expected the isolated all_tokens tier to be non-monotone here"


# --- S3: the fuzzy tier -----------------------------------------------------


def test_fuzzy_similarity_threshold_is_the_named_constant() -> None:
    assert FUZZY_SIMILARITY_THRESHOLD == 0.75


def test_fuzzy_max_token_length_gap_is_the_named_constant() -> None:
    assert FUZZY_MAX_TOKEN_LENGTH_GAP == 2


# 15 named calibration cases, each independently verified against the
# implementation before being written down here -- a case that failed
# at the 0.75/2 constants would mean the constants are wrong, not that
# the case should be adjusted (none did).
_FUZZY_CALIBRATION_CASES: tuple[tuple[str, str, bool], ...] = (
    ("amour", "amuor", True),  # transposition
    ("amour", "amur", True),  # missing letter
    ("amour", "amourr", True),  # extra doubled letter
    ("premiere", "premeire", True),  # transposition, longer word
    ("torrent", "torent", True),  # missing letter
    ("torrent", "torrant", True),  # substitution
    ("season", "seasno", True),  # transposition
    ("episode", "epizode", True),  # substitution
    ("matrix", "matriks", True),  # substitution, near threshold (0.769)
    ("cat", "bat", False),  # short word, one substitution -- too destructive
    ("amour", "haine", False),  # unrelated words
    ("premiere", "pre", False),  # length gap 5, gated before ratio()
    ("torrent", "torrrrent", True),  # length gap 2, boundary, inclusive
    ("torrent", "torrrrrent", False),  # length gap 3: gate rejects ratio=0.82
    ("abcd", "abce", True),  # synthetic: ratio() == 0.75 exactly, inclusive
)


@pytest.mark.parametrize(
    ("name", "query", "expected_match"),
    _FUZZY_CALIBRATION_CASES,
    ids=[f"{name}-vs-{query}" for name, query, _ in _FUZZY_CALIBRATION_CASES],
)
def test_fuzzy_calibration_table(
    name: str, query: str, expected_match: bool
) -> None:
    similarity = _best_token_similarity(query, (name,))
    if expected_match:
        assert similarity is not None
        assert similarity >= FUZZY_SIMILARITY_THRESHOLD
    else:
        assert similarity is None


def test_fuzzy_requires_every_distinct_query_token_to_match() -> None:
    """Piège 5 of the fuzzy tier: one non-matching token sinks the
    whole match, even when every other token matches well."""
    corpus = [_snapshot(hash=HASH_A, name="Amour Vrai")]
    results = _search(corpus, "amuor xyzzyx", mode="fuzzy", hash_min_length=8)
    assert results.matched == 0


def test_fuzzy_matches_when_every_distinct_query_token_qualifies() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Amuor Et")]
    results = _search(corpus, "amour est", mode="fuzzy", hash_min_length=8)
    assert _tiers(results) == ("fuzzy",)


def test_fuzzy_populates_similarity_on_the_hit() -> None:
    corpus = [_snapshot(hash=HASH_A, name="Torrant")]
    results = _search(corpus, "torrent", mode="fuzzy", hash_min_length=8)
    assert results.hits[0].tier == "fuzzy"
    assert results.hits[0].similarity is not None
    assert results.hits[0].similarity >= FUZZY_SIMILARITY_THRESHOLD


def test_fuzzy_secondary_order_is_average_similarity_descending() -> None:
    corpus = [
        _snapshot(hash=HASH_B, name="Torrantt"),  # sim 0.800
        _snapshot(hash=HASH_A, name="Torrant"),  # sim 0.857 -- ranks first
    ]
    results = _search(corpus, "torrent", mode="fuzzy", hash_min_length=8)
    assert _tiers(results) == ("fuzzy", "fuzzy")
    assert _hashes(results) == (HASH_A, HASH_B)
    first, second = results.hits
    assert first.similarity is not None and second.similarity is not None
    assert first.similarity >= second.similarity


def test_fuzzy_only_evaluates_each_distinct_query_token_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen point 5 / "comparaison sur les tokens distincts"."""
    calls: list[str] = []
    original = search_module._best_token_similarity

    def spy(query_token: str, name_tokens: tuple[str, ...]) -> float | None:
        calls.append(query_token)
        return original(query_token, name_tokens)

    monkeypatch.setattr(search_module, "_best_token_similarity", spy)
    search_module._match_fuzzy(("amuor", "amuor"), ("amour",))
    assert calls == ["amuor"]


def test_fuzzy_only_evaluates_each_distinct_name_token_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original_set_seq1 = SequenceMatcher.set_seq1

    def spy_set_seq1(self: SequenceMatcher, value: str) -> None:  # type: ignore[type-arg]
        calls.append(value)
        original_set_seq1(self, value)

    monkeypatch.setattr(SequenceMatcher, "set_seq1", spy_set_seq1)
    _best_token_similarity("amuor", ("amour", "amour", "amour"))
    # `SequenceMatcher.__init__` itself calls `set_seq1("")` once for its
    # default empty sequence -- filter that construction-time call out.
    assert [value for value in calls if value != ""] == ["amour"]


# --- Frozen point 1: autojunk=False -----------------------------------


def test_autojunk_false_is_required_for_long_repetitive_tokens() -> None:
    """Frozen point 1: `autojunk=False` is explicit. `difflib`'s default
    autojunk heuristic only activates once `len(seq2) >= 200`, and only
    a token pair with length >= 200 (length gap <= 2) exercises it --
    every torrent-name token shorter than 200 chars never reaches this
    code path, which is exactly why this guard is easy to leave
    decorative."""
    base = "premiere" * 25  # 200 chars, highly repetitive
    typo = base[:100] + "x" + base[101:]  # one substituted char, gap 0
    assert len(base) == len(typo) == 200

    similarity = _best_token_similarity(typo, (base,))
    assert similarity is not None
    assert similarity >= FUZZY_SIMILARITY_THRESHOLD


def test_swapping_seq1_and_seq2_can_change_the_ratio() -> None:
    """Frozen point 2's second half: the query-as-seq2/corpus-as-seq1
    idiom is not an arbitrary stylistic choice -- `ratio()` is not
    always symmetric under which string is `seq1` vs `seq2`, even with
    `autojunk=False`, because `autojunk`'s popularity profiling only
    ever inspects `seq2`."""
    b = "premiere" * 25
    alphabet = string.ascii_lowercase + string.digits
    a = (
        "premiereremierepremiereremiere"
        + "".join(alphabet[i % len(alphabet)] for i in range(len(b)))
    )[: len(b)]
    assert len(a) == len(b) == 200

    forward = SequenceMatcher(None, autojunk=False)
    forward.set_seq2(b)
    forward.set_seq1(a)

    backward = SequenceMatcher(None, autojunk=False)
    backward.set_seq2(a)
    backward.set_seq1(b)

    assert forward.ratio() != backward.ratio()


# --- Frozen point 4: the length-gap gate is a real, independent guard -----


def test_length_gap_gate_rejects_a_pair_before_a_qualifying_ratio() -> None:
    """This pair's `ratio()` is ~0.82 -- well above threshold -- but its
    length gap is 3, one past `FUZZY_MAX_TOKEN_LENGTH_GAP`. If the gate
    were merely redundant with the threshold, this pair would match."""
    name = "torrent"
    query = "torrrrrent"
    assert abs(len(name) - len(query)) == FUZZY_MAX_TOKEN_LENGTH_GAP + 1

    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(query)
    matcher.set_seq1(name)
    # ratio() alone would pass -- proves the gate is not decorative.
    assert matcher.ratio() >= FUZZY_SIMILARITY_THRESHOLD

    assert _best_token_similarity(query, (name,)) is None


# --- AC5, extended to fuzzy mode -------------------------------------------

_S3_MODE_SEQUENCE: tuple[SearchMode, ...] = (
    "exact",
    "contains",
    "tokens",
    "fuzzy",
)


def test_raising_mode_to_fuzzy_only_adds_results_never_reorders() -> None:
    corpus = [
        _snapshot(hash=HASH_A, name="Amour Est"),  # exact
        _snapshot(hash=HASH_B, name="Amour Est Toujours"),  # prefix
        _snapshot(hash=HASH_C, name="Mon Amour Est La"),  # substring
        _snapshot(hash=HASH_D, name="Est Amour Fidele"),  # all_tokens
        _snapshot(hash=HASH_F, name="Amoureux Estival Jour"),  # token_prefix
        _snapshot(hash=HASH_TARGET, name="Amuor Et"),  # fuzzy only
    ]
    previous_order: tuple[str, ...] | None = None
    for mode in _S3_MODE_SEQUENCE:
        results = _search(corpus, "amour est", mode=mode, hash_min_length=8)
        order = _hashes(results)
        if previous_order is not None:
            assert order[: len(previous_order)] == previous_order, (
                mode,
                order,
                previous_order,
            )
        previous_order = order
    assert previous_order == (
        HASH_A,
        HASH_B,
        HASH_C,
        HASH_D,
        HASH_F,
        HASH_TARGET,
    )
