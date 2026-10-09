"""Tests for confusable-term detection (Phase 2 QA addition).

The check must catch the speech-to-text substitutions actually heard in this
lecture series ("training range", "bare bar") while ignoring ordinary English
that merely looks similar ("clear breakout", "bull bar").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.errors import TerminologyError
from src.qa import FindingType, Severity, TranscriptQaAnalyzer
from src.terminology import (
    ANCHOR_MAX_DISTANCE,
    CONFUSABLE_STOPWORDS,
    KnownConfusion,
    Terminology,
    TerminologyEntry,
    _parse_anchors,
    _parse_known_confusions,
    load_terminology,
)
from src.text_cleaning import damerau_levenshtein, word_tokens
from src.transcript import load_raw_transcript


# ----------------------------------------------------------------------
# Edit distance
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("training", "trading", 2),
        ("bare", "bear", 2),
        ("bulls", "bull", 1),
        ("abc", "abd", 1),
        ("kitten", "sitting", 3),
        ("same", "same", 0),
        ("flag", "flags", 1),
    ],
)
def test_damerau_distance(left: str, right: str, expected: int) -> None:
    assert damerau_levenshtein(left, right, 5) == expected


def test_distance_returns_none_beyond_limit() -> None:
    assert damerau_levenshtein("training", "trading", 1) is None
    assert damerau_levenshtein("abc", "xyz", 1) is None


def test_distance_rejects_on_length_difference() -> None:
    assert damerau_levenshtein("a", "abcdefgh", 3) is None


def test_distance_is_symmetric() -> None:
    for left, right in (("bare", "bear"), ("training", "trading"), ("flag", "chlag")):
        assert damerau_levenshtein(left, right, 4) == damerau_levenshtein(right, left, 4)


# ----------------------------------------------------------------------
# Anchors
# ----------------------------------------------------------------------
def test_anchors_are_built_from_multiword_terms() -> None:
    anchors = load_terminology().confusable_anchors()
    assert ("trading", "range") in anchors
    assert ("wedge", "bull") in anchors


def test_weak_anchors_are_rejected() -> None:
    """An anchor whose second word is a stopword catches almost anything."""

    for first, second in load_terminology().confusable_anchors():
        assert second not in CONFUSABLE_STOPWORDS
        assert len(second) >= 3
        assert len(first) >= 4


def test_extra_anchors_are_loaded() -> None:
    anchors = load_terminology().confusable_anchors()
    assert ("bear", "bar") in anchors
    assert ("bull", "body") in anchors


def test_anchors_are_cached() -> None:
    terms = load_terminology()
    assert terms.confusable_anchors() is terms.confusable_anchors()


def test_anchor_distance_is_one() -> None:
    """Two edits is unsafe generically: 'clear breakout' looks like 'bear breakout'."""

    assert ANCHOR_MAX_DISTANCE == 1


# ----------------------------------------------------------------------
# Mechanism 1: curated known confusions
# ----------------------------------------------------------------------
def test_known_confusions_are_loaded() -> None:
    pairs = {(c.wrong, c.right) for c in load_terminology().known_confusions()}
    assert ("training", "trading") in pairs
    assert ("bare", "bear") in pairs


def test_known_confusion_notes_are_preserved() -> None:
    entries = load_terminology().known_confusions()
    assert all(entry.note for entry in entries)
    assert entries[0].to_dict()["note"]


@pytest.mark.parametrize(
    ("text", "wrong", "right"),
    [
        ("a tight training range here", "training", "trading"),
        ("body's neutral, bare bar", "bare", "bear"),
        ("bigger bare body indeed", "bare", "bear"),
    ],
)
def test_known_confusions_are_detected(text: str, wrong: str, right: str) -> None:
    confusions = load_terminology().find_confusables(text)
    assert [(c.word, c.suspected_word) for c in confusions] == [(wrong, right)]


def test_known_confusion_is_flagged_as_such() -> None:
    confusion = load_terminology().find_confusables("a tight training range here")[0]
    assert confusion.source == "known_confusion"
    assert "training range" in confusion.context


def test_each_known_confusion_reported_once() -> None:
    text = "training range and training range and training range"
    assert len(load_terminology().find_confusables(text)) == 1


# ----------------------------------------------------------------------
# Mechanism 2: anchored single-edit detection
# ----------------------------------------------------------------------
def test_custom_terminology_can_supply_anchors() -> None:
    """The first word of an anchor is what gets replaced; the second must be exact."""

    terms = Terminology(
        [TerminologyEntry(id="t", canonical="silver bullet", category="core")],
        extra_anchors=(("golden", "ratio"),),
    )
    assert [(c.word, c.suspected_word) for c in terms.find_confusables("a golen ratio")] == [
        ("golen", "golden")
    ]
    assert terms.find_confusables("a golden ratio pattern") == []


def test_custom_known_confusions_are_used() -> None:
    terms = Terminology(
        [TerminologyEntry(id="t", canonical="silver bullet", category="core")],
        known_confusions=(KnownConfusion(wrong="silber", right="silver"),),
    )
    assert [(c.word, c.suspected_word) for c in terms.find_confusables("a silber bullet")] == [
        ("silber", "silver")
    ]


def test_domain_vocabulary_is_never_flagged() -> None:
    """'bull' is not a misspelling of 'bear', even directly before 'bar'."""

    terms = load_terminology()
    assert ("bear", "bar") in terms.confusable_anchors()
    assert terms.find_confusables("a bull bar and a bear bar") == []
    assert "bull" in terms.domain_vocabulary()


def test_confusable_serialises() -> None:
    payload = load_terminology().find_confusables("a training range")[0].to_dict()
    assert payload["word"] == "training"
    assert payload["suggested_word"] == "trading"
    assert payload["source"] == "known_confusion"
    assert "training range" in payload["context"]


# ----------------------------------------------------------------------
# Precision: ordinary English must NOT be flagged
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "a clear breakout now",
        "a bull bar and a bear bar",
        "the bulls are strong today",
        "bears control this market",
        "he tried to reverse down here",
        "we are near the top of the range",
        "that is more than enough",
        "I like this setup",
        "put a tail on the bottom",
        "high of the day so far",
        "the market went back up",
        "a big chart with a channel",
        "the head and the shoulders",
        "trading range with a bull flag and a bear channel",
    ],
)
def test_ordinary_english_is_not_flagged(text: str) -> None:
    assert load_terminology().find_confusables(text) == []


def test_empty_text() -> None:
    assert load_terminology().find_confusables("") == []


def test_results_are_sorted_for_stability() -> None:
    text = "a bare bar and a bare body and a training range"
    confusions = load_terminology().find_confusables(text)
    assert [c.word for c in confusions] == sorted(c.word for c in confusions)


# ----------------------------------------------------------------------
# Resource validation
# ----------------------------------------------------------------------
def test_extra_anchors_must_be_two_words() -> None:
    with pytest.raises(TerminologyError, match="exactly two words"):
        _parse_anchors({"confusable_extra_anchors": ["bear"]})


def test_extra_anchors_must_be_strings() -> None:
    with pytest.raises(TerminologyError, match="not a string"):
        _parse_anchors({"confusable_extra_anchors": [{"a": 1}]})


def test_extra_anchors_default_to_empty() -> None:
    assert _parse_anchors({}) == ()


def test_known_confusions_default_to_empty() -> None:
    assert _parse_known_confusions({}) == ()


def test_known_confusions_must_be_objects() -> None:
    with pytest.raises(TerminologyError, match="not an object"):
        _parse_known_confusions({"known_confusions": ["training"]})


def test_known_confusion_needs_both_sides() -> None:
    with pytest.raises(TerminologyError, match="both 'wrong' and 'right'"):
        _parse_known_confusions({"known_confusions": [{"wrong": "training"}]})


def test_known_confusion_sides_must_differ() -> None:
    with pytest.raises(TerminologyError, match="identical"):
        _parse_known_confusions({"known_confusions": [{"wrong": "trend", "right": "trend"}]})


# ----------------------------------------------------------------------
# Integration with the QA stage
# ----------------------------------------------------------------------
def _analyze(tmp_path: Path, text: str) -> Any:
    payload = {
        "schema_version": 1,
        "transcription_model": "whisper-large-v3-turbo",
        "requested_language": "en",
        "processed_duration_seconds": 60.0,
        "transcript": text,
        "segments": [
            {"id": 0, "start": 0.0, "end": 30.0, "text": text},
            {"id": 1, "start": 30.0, "end": 60.0, "text": "the bulls are in control"},
        ],
    }
    path = tmp_path / "t.en.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return TranscriptQaAnalyzer().analyze(load_raw_transcript(path))


def test_qa_reports_confusable_term(tmp_path: Path) -> None:
    report = _analyze(tmp_path, "a tight training range in a bear channel today")

    findings = report.by_type(FindingType.CONFUSABLE_TERM)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity is Severity.MEDIUM
    assert "training" in finding.summary
    assert "trading" in finding.summary
    assert finding.segment_ids == ("0",)
    assert "Listen" in finding.suggested_action


def test_qa_clean_text_has_no_confusable_findings(tmp_path: Path) -> None:
    report = _analyze(tmp_path, "a tight trading range in a bear channel today")
    assert not report.has(FindingType.CONFUSABLE_TERM)


def test_qa_confusable_flag_attaches_to_segment(tmp_path: Path) -> None:
    report = _analyze(tmp_path, "a tight training range in a bear channel today")
    assert "confusable_term" in report.segment_flags["0"]
    assert "2" not in report.segment_flags


def test_qa_confusable_is_advisory_only(tmp_path: Path) -> None:
    """A confusable must never cause a merge or mark wording as corrected."""

    text = "a tight training range in a bear channel today"
    report = _analyze(tmp_path, text)
    assert report.duplicate_groups == ()


def test_gap_severity_is_low_for_a_live_speaker(tmp_path: Path) -> None:
    """Multi-second pauses are normal for a live trader, so they must not alarm."""

    report = _analyze(tmp_path, "the bulls are in control of this market today")
    gap_findings = report.by_type(FindingType.SEGMENT_GAP)
    for finding in gap_findings:
        assert finding.severity is Severity.LOW


def test_token_helpers_agree_on_normalisation() -> None:
    assert word_tokens("body's neutral") == ["body", "s", "neutral"]
