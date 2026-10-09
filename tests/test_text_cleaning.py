"""Tests for :mod:`src.text_cleaning` (Phase 2 deterministic cleanup)."""

from __future__ import annotations

import pytest
from src.text_cleaning import (
    clean_text,
    comparison_key,
    join_segment_texts,
    similarity,
    word_tokens,
    words_changed,
)


# ----------------------------------------------------------------------
# Whitespace and invisible characters
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  leading and trailing  ", "leading and trailing"),
        ("multiple   internal   spaces", "multiple internal spaces"),
        ("tabs\tand\nnewlines\r\nhere", "tabs and newlines here"),
        ("non\u00a0breaking\u00a0spaces", "non breaking spaces"),
        ("zero\u200bwidth\u200djoiners", "zerowidthjoiners"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_whitespace_normalisation(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_word_count_is_preserved_by_whitespace_cleaning() -> None:
    raw = "  A   market   with    a  gap . "
    cleaned = clean_text(raw)
    assert len(word_tokens(raw)) == len(word_tokens(cleaned))


# ----------------------------------------------------------------------
# Punctuation handling
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hello , world", "hello, world"),
        ("wait . what", "wait. what"),
        ("yes !", "yes!"),
        ("really ?", "really?"),
        ("50 %", "50%"),
        ("( spaced )", "(spaced)"),
        ("[ spaced ]", "[spaced]"),
        ("word ,word", "word, word"),
        ("word;word", "word; word"),
        ("end...", "end…"),
    ],
)
def test_punctuation_spacing_is_repaired(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_punctuation_is_preserved_not_removed() -> None:
    raw = "A wedge bull flag - isn't it? Yes: certainly (really)!"
    cleaned = clean_text(raw)
    for char in "-'?!:()!":
        assert char in cleaned


# ----------------------------------------------------------------------
# Conservative guarantees - the crucial ones
# ----------------------------------------------------------------------
def test_words_are_never_rewritten() -> None:
    raw = "So what I mean is-- it's basically, you know, a gap, right?"
    assert clean_text(raw) == raw


def test_grammar_errors_are_preserved() -> None:
    raw = "He dont know nothing about the the breakouts and reversals"
    assert clean_text(raw) == raw


def test_fillers_and_disfluencies_are_kept() -> None:
    raw = "Um, so -- like I was saying, the-- the gap was, uh, not filled."
    assert clean_text(raw) == raw


def test_case_is_preserved() -> None:
    assert clean_text("Wedge Bull Flag") == "Wedge Bull Flag"


def test_unicode_is_normalised_but_words_kept() -> None:
    # Fullwidth digits normalise, but no token is invented.
    cleaned = clean_text("The ６０ minute chart")
    assert "60" in cleaned
    assert set(word_tokens(cleaned)) <= set(word_tokens("The 60 minute chart"))


def test_no_unexpected_new_words() -> None:
    raw = "a wedge bull flag , respecting the gap"
    assert words_changed(raw, clean_text(raw)) == []


def test_cleaning_is_idempotent() -> None:
    raw = "  a  wedge  ,  bull flag ( really ) ... "
    once = clean_text(raw)
    assert clean_text(once) == once


# ----------------------------------------------------------------------
# Comparison key
# ----------------------------------------------------------------------
def test_comparison_key_ignores_case_and_punctuation() -> None:
    assert comparison_key("Wedge, bull flag!") == comparison_key("wedge bull flag")


def test_comparison_key_collapses_whitespace() -> None:
    assert comparison_key("wedge  bull\nflag") == "wedge bull flag"


def test_comparison_key_of_empty() -> None:
    assert comparison_key("") == ""
    assert comparison_key("   ") == ""


def test_comparison_key_is_not_used_for_output() -> None:
    """The key is punctuation-free; the cleaned text must not be."""

    raw = "Wedge, bull flag!"
    assert "," not in comparison_key(raw)
    assert "," in clean_text(raw)


# ----------------------------------------------------------------------
# Similarity
# ----------------------------------------------------------------------
def test_identical_text_similarity_is_one() -> None:
    assert similarity("same words here", "Same words here!") == pytest.approx(1.0)


def test_unrelated_text_similarity_is_low() -> None:
    assert similarity("a bull flag", "buy the dip") < 0.4


def test_empty_similarity() -> None:
    assert similarity("", "") == 1.0
    assert similarity("", "something") == 0.0


# ----------------------------------------------------------------------
# Joining segments
# ----------------------------------------------------------------------
def test_join_segment_texts_avoids_double_punctuation() -> None:
    joined = join_segment_texts(["First part", "and the second."])
    assert joined == "First part and the second."


def test_join_segment_texts_skips_empties() -> None:
    assert join_segment_texts(["one", "", "   ", "two"]) == "one two"


def test_join_segment_texts_of_all_empty() -> None:
    assert join_segment_texts(["", "  "]) == ""


def test_join_preserves_repeated_content() -> None:
    """Joining must never collapse a genuine repetition."""

    joined = join_segment_texts(["the gap", "the gap"])
    assert joined == "the gap the gap"
