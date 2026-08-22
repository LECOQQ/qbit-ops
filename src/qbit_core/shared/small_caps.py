"""Unicode small capitals, and the three blocks they are scattered over.

Unicode never designed a small-capital alphabet. The 25 letters that
exist were added to three unrelated blocks, decades apart, for phonetic
notation -- so a terminal font routinely covers one block, misses
another, and substitutes the missing letters from a fallback face at a
different size. That is not a rendering bug to fix in software: it is
what the letters are.

This module is where the alphabet and its provenance live, so the
renderer that uses it and the diagnostic that explains it read the same
table.
"""

from __future__ import annotations

PHONETIC_EXTENSIONS = "Phonetic Extensions"
IPA_EXTENSIONS = "IPA Extensions"
LATIN_EXTENDED_D = "Latin Extended-D"

# letter -> (small capital, the block it was added to).
_SMALL_CAPS_BY_LETTER: dict[str, tuple[str, str]] = {
    "a": ("ᴀ", PHONETIC_EXTENSIONS),
    "b": ("ʙ", IPA_EXTENSIONS),
    "c": ("ᴄ", PHONETIC_EXTENSIONS),
    "d": ("ᴅ", PHONETIC_EXTENSIONS),
    "e": ("ᴇ", PHONETIC_EXTENSIONS),
    "f": ("ꜰ", LATIN_EXTENDED_D),
    "g": ("ɢ", IPA_EXTENSIONS),
    "h": ("ʜ", IPA_EXTENSIONS),
    "i": ("ɪ", IPA_EXTENSIONS),
    "j": ("ᴊ", PHONETIC_EXTENSIONS),
    "k": ("ᴋ", PHONETIC_EXTENSIONS),
    "l": ("ʟ", IPA_EXTENSIONS),
    "m": ("ᴍ", PHONETIC_EXTENSIONS),
    "n": ("ɴ", IPA_EXTENSIONS),
    "o": ("ᴏ", PHONETIC_EXTENSIONS),
    "p": ("ᴘ", PHONETIC_EXTENSIONS),
    "q": ("ꞯ", LATIN_EXTENDED_D),
    "r": ("ʀ", IPA_EXTENSIONS),
    "s": ("ꜱ", LATIN_EXTENDED_D),
    "t": ("ᴛ", PHONETIC_EXTENSIONS),
    "u": ("ᴜ", PHONETIC_EXTENSIONS),
    "v": ("ᴠ", PHONETIC_EXTENSIONS),
    "w": ("ᴡ", PHONETIC_EXTENSIONS),
    "y": ("ʏ", IPA_EXTENSIONS),
    "z": ("ᴢ", PHONETIC_EXTENSIONS),
}

SMALL_CAPS: dict[str, str] = {
    letter: glyph for letter, (glyph, _) in _SMALL_CAPS_BY_LETTER.items()
}

# Unicode has no LATIN LETTER SMALL CAPITAL X. Not a theoretical gap:
# the `Explain` screen exists, and a half-converted word would read
# worse than an ordinary one.
UNMAPPABLE_LETTERS = frozenset("x")

# Ordered widest-coverage first: a font that misses one of these misses
# the last far more often than the first.
BLOCK_ORDER: tuple[str, ...] = (
    PHONETIC_EXTENSIONS,
    IPA_EXTENSIONS,
    LATIN_EXTENDED_D,
)


def to_small_caps(word: str) -> str:
    """Render `word` in small capitals, or unchanged when it cannot be.

    All-or-nothing on purpose: one letter without a small capital makes
    the whole word fall back, rather than shipping a word that is
    small-caps everywhere except one full-height letter.
    """
    if any(char in UNMAPPABLE_LETTERS for char in word.lower()):
        return word
    return "".join(SMALL_CAPS.get(char, char) for char in word.lower())


def blocks_used(word: str) -> dict[str, tuple[str, ...]]:
    """The small capitals `word` draws, grouped by the block they live in.

    Empty when the word cannot be converted at all.
    """
    if to_small_caps(word) == word and any(
        char in UNMAPPABLE_LETTERS for char in word.lower()
    ):
        return {}

    grouped: dict[str, list[str]] = {}
    for char in word.lower():
        entry = _SMALL_CAPS_BY_LETTER.get(char)
        if entry is None:
            continue
        glyph, block = entry
        grouped.setdefault(block, []).append(glyph)
    return {
        block: tuple(grouped[block])
        for block in BLOCK_ORDER
        if block in grouped
    }


def describe_coverage(words: tuple[str, ...]) -> str:
    """One line per block, naming the glyphs `words` need from it."""
    needed: dict[str, set[str]] = {}
    for word in words:
        for block, glyphs in blocks_used(word).items():
            needed.setdefault(block, set()).update(glyphs)
    return "; ".join(
        f"{block}: {' '.join(sorted(needed[block]))}"
        for block in BLOCK_ORDER
        if block in needed
    )
