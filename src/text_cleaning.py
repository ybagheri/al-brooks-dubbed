"""Deterministic transcript text cleaning.

Everything here is **formatting only**. The cleaning stage never changes what
was said: no spelling is corrected, no grammar is fixed, no word is added,
removed or reordered, and punctuation is preserved wherever it is meaningful.

The module deliberately provides a separate *comparison key* function used by
the QA detectors. That key lowercases, strips punctuation and collapses
whitespace so that two segments can be compared for equality - but it is never
written to an output artifact.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

#: Characters that are invisible in practice but break string comparisons.
_INVISIBLE = {
    "\u00a0": " ",  # no-break space
    "\u200b": None,  # zero-width space
    "\u200c": None,  # zero-width non-joiner
    "\u200d": None,  # zero-width joiner
    "\u2060": None,  # word joiner
    "\ufeff": None,  # byte order mark
}

_WHITESPACE_RUN = re.compile(r"\s+")

#: A space directly before closing or sentence punctuation is always an
#: artifact of segmentation, never meaningful.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%…])")

#: Brackets that bind tightly to their content.
_OPEN_BRACKETS = "([{"
_CLOSE_BRACKETS = ")]}"

#: Punctuation that should be followed by a single space.
_SPACE_AFTER_PUNCT = re.compile(r"([,;:])(?=[^\s\d])")

#: An ellipsis typed as three dots is normalised to the single character.
_TRIPLE_DOT = re.compile(r"\.{3,}")

_COMPARISON_STRIP = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE_RUN_COMPARISON = re.compile(r"\s+")


def normalize_invisible(text: str) -> str:
    """Replace invisible characters that break comparisons."""

    for char, replacement in _INVISIBLE.items():
        if char not in text:
            continue
        text = text.replace(char, "" if replacement is None else replacement)
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse every whitespace run into a single space and strip the ends."""

    if not text:
        return ""
    return _WHITESPACE_RUN.sub(" ", normalize_invisible(text)).strip()


def clean_text(text: str) -> str:
    """Deterministically clean a piece of transcript text.

    The transformation is intentionally conservative and reversible in spirit:

    * Unicode is normalised to NFKC so that look-alike characters agree;
    * runs of whitespace collapse to one space;
    * a stray space before ``, . ; : ! ?`` is removed;
    * brackets bind to their content (``( x )`` -> ``(x)``);
    * a single space is enforced after ``, ; :``;
    * an ellipsis becomes a single ``…``.

    Words, letter case, punctuation meaning and sentence boundaries are left
    exactly as the API returned them.
    """

    if not text:
        return ""

    cleaned = unicodedata.normalize("NFKC", normalize_invisible(text))
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned).strip()
    cleaned = _SPACE_BEFORE_PUNCT.sub(r"\1", cleaned)
    cleaned = _close_brackets(cleaned)
    cleaned = _open_brackets(cleaned)
    cleaned = _SPACE_AFTER_PUNCT.sub(r"\1 ", cleaned)
    cleaned = _TRIPLE_DOT.sub("\u2026", cleaned)
    return _WHITESPACE_RUN.sub(" ", cleaned).strip()


def _open_brackets(text: str) -> str:
    """Remove the space that follows an opening bracket, keeping the bracket."""

    result: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        result.append(char)
        if char in _OPEN_BRACKETS and text[index + 1 : index + 2] == " ":
            index += 2  # skip the space
            continue
        index += 1
    return "".join(result)


def _close_brackets(text: str) -> str:
    """Remove the space before a closing bracket."""

    result: list[str] = []
    for char in text:
        if char in _CLOSE_BRACKETS and result and result[-1] == " ":
            result.pop()
        result.append(char)
    return "".join(result)


def comparison_key(text: str) -> str:
    """Normalise text for equality and similarity comparisons only.

    Lowercases, removes punctuation and collapses whitespace. The result is
    never written to an output artifact - it exists so that a segment repeated
    with different punctuation is still recognised as a duplicate.
    """

    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", normalize_invisible(text)).lower()
    without_punctuation = _COMPARISON_STRIP.sub(" ", normalized)
    return _WHITESPACE_RUN_COMPARISON.sub(" ", without_punctuation).strip()


def word_tokens(text: str) -> list[str]:
    """Split text into comparable word tokens."""

    key = comparison_key(text)
    return key.split() if key else []


#: Above this many characters, similarity is measured on a truncated prefix.
#: ``SequenceMatcher`` is quadratic in the worst case, which is unacceptable for
#: a full-length lecture transcript.
MAX_SIMILARITY_CHARS = 20_000

#: A length ratio beyond this means the texts are certainly not the same.
_MAX_LENGTH_RATIO = 2.0


def similarity(left: str, right: str, max_chars: int = MAX_SIMILARITY_CHARS) -> float:
    """Return a 0.0-1.0 similarity ratio for two pieces of text.

    Very long inputs are compared on a truncated prefix after a cheap length
    check, so the cost stays bounded on full-length transcripts.
    """

    left_key = comparison_key(left)
    right_key = comparison_key(right)
    if not left_key or not right_key:
        return 1.0 if left_key == right_key else 0.0

    longer, shorter = max(len(left_key), len(right_key)), min(len(left_key), len(right_key))
    if longer / max(1, shorter) > _MAX_LENGTH_RATIO:
        return 0.0

    if len(left_key) > max_chars:
        left_key = left_key[:max_chars]
    if len(right_key) > max_chars:
        right_key = right_key[:max_chars]

    return SequenceMatcher(None, left_key, right_key).ratio()


def join_segment_texts(texts: list[str], separator: str = " ") -> str:
    """Join segment texts, skipping empties and avoiding double separators."""

    parts = [cleaned for cleaned in (clean_text(text) for text in texts) if cleaned]
    if not parts:
        return ""
    joined = separator.join(parts)
    # A segment often starts with the punctuation that ended the previous one.
    return re.sub(r"\s+([,.;:!?])", r"\1", joined)


def words_changed(original: str, cleaned: str) -> list[str]:
    """Words present in the cleaned text but not in the original.

    Used as a safety assertion: deterministic cleaning must not introduce
    tokens that were not in the source.
    """

    original_words = set(word_tokens(original))
    return sorted({word for word in word_tokens(cleaned) if word not in original_words})
