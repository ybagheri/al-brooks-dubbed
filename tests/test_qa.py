"""Tests for :mod:`src.qa` (Phase 2 quality assurance).

Everything here is deterministic and offline: no API calls, no media.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.qa import (
    DuplicateGroup,
    FindingType,
    QaReport,
    QaThresholds,
    Severity,
    TranscriptQaAnalyzer,
    max_run_length,
)
from src.transcript import load_raw_transcript

from .conftest import known_duplicate_payload


def write_transcript(tmp_path: Path, payload: dict[str, Any], name: str = "t.en.json") -> Any:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return load_raw_transcript(path)


def segment(index: int, start: Any, end: Any, text: str) -> dict[str, Any]:
    return {"id": index, "start": start, "end": end, "text": text}


def payload(
    segments: list[dict[str, Any]], text: str | None = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "transcription_model": "whisper-large-v3-turbo",
        "requested_language": "en",
        "processed_duration_seconds": 30.0,
        "transcript": text if text is not None else " ".join(s["text"] for s in segments),
        "segments": segments,
    }
    body.update(extra)
    return body


def analyze(tmp_path: Path, segments: list[dict[str, Any]], **kwargs: Any) -> QaReport:
    raw = write_transcript(tmp_path, payload(segments, **kwargs))
    return TranscriptQaAnalyzer().analyze(raw)


# ----------------------------------------------------------------------
# The known duplicate example from the project brief
# ----------------------------------------------------------------------
def test_known_example_is_flagged(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)

    assert report.has(FindingType.EXACT_DUPLICATE_SEGMENT)
    assert report.has(FindingType.DUPLICATE_DURATION_MISMATCH)
    assert report.has(FindingType.SEGMENT_TOO_LONG)
    assert report.needs_review
    assert report.max_severity() in (Severity.HIGH, Severity.CRITICAL)


def test_known_example_duplicate_names_both_segments(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)

    duplicate = report.by_type(FindingType.EXACT_DUPLICATE_SEGMENT)[0]
    assert set(duplicate.segment_ids) == {"0", "1"}
    assert duplicate.evidence["exact_match"] is True
    assert duplicate.evidence["adjacent"] is True
    assert duplicate.severity is Severity.HIGH


def test_known_example_unusually_long_segment_is_segment_one(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)

    long_finding = report.by_type(FindingType.SEGMENT_TOO_LONG)[0]
    assert long_finding.segment_ids == ("1",)
    assert long_finding.evidence["duration_seconds"] == pytest.approx(16.0)
    assert long_finding.evidence["median_duration_seconds"] == pytest.approx(7.0)


def test_known_example_duration_mismatch_is_reported(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)

    mismatch = report.by_type(FindingType.DUPLICATE_DURATION_MISMATCH)[0]
    assert mismatch.severity is Severity.HIGH
    assert mismatch.evidence["shortest_seconds"] == pytest.approx(7.0)
    assert mismatch.evidence["longest_seconds"] == pytest.approx(16.0)


def test_known_example_duplicate_group_detected(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)

    assert len(report.duplicate_groups) == 1
    group = report.duplicate_groups[0]
    assert group.segment_ids == ("0", "1")
    assert group.exact is True


# ----------------------------------------------------------------------
# Duplicates vs legitimate repetition
# ----------------------------------------------------------------------
def test_exact_duplicate_adjacent_segments_are_high(tmp_path: Path) -> None:
    text = "the bulls are in control of this market"
    report = analyze(tmp_path, [segment(0, 0.0, 5.0, text), segment(1, 5.0, 10.0, text)])
    assert report.has(FindingType.EXACT_DUPLICATE_SEGMENT)


def test_duplicate_with_different_punctuation_is_still_detected(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [
            segment(0, 0.0, 5.0, "A wedge bull flag."),
            segment(1, 5.0, 10.0, "a wedge bull flag"),
        ],
    )
    assert report.has(FindingType.EXACT_DUPLICATE_SEGMENT)


def test_non_adjacent_repeat_is_reported_at_lower_severity(tmp_path: Path) -> None:
    text = "we need to watch the breakout"
    report = analyze(
        tmp_path,
        [
            segment(0, 0.0, 5.0, text),
            segment(1, 5.0, 10.0, "something completely different here"),
            segment(2, 10.0, 15.0, text),
        ],
    )
    finding = report.by_type(FindingType.EXACT_DUPLICATE_SEGMENT)[0]
    assert finding.severity is Severity.MEDIUM
    assert finding.evidence["adjacent"] is False


def test_distinct_segments_are_not_duplicates(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [
            segment(0, 0.0, 5.0, "the bulls see a wedge bull flag"),
            segment(1, 5.0, 10.0, "and the bears reply with a bear flag"),
        ],
    )
    assert not report.has(FindingType.EXACT_DUPLICATE_SEGMENT)
    assert not report.has(FindingType.NEAR_DUPLICATE_SEGMENT)


def test_legitimate_repetition_of_terminology_is_only_low_severity(tmp_path: Path) -> None:
    """A speaker repeating a trading term must not look like a critical bug."""

    segments = [
        segment(0, 0.0, 10.0, "Let us look at the trading range and the breakout point."),
        segment(1, 10.0, 20.0, "The trading range gives us the breakout point we want."),
        segment(2, 20.0, 30.0, "That is why the trading range matters for the breakout."),
    ]
    report = analyze(tmp_path, segments)
    for finding in report.findings:
        assert finding.severity in (Severity.LOW, Severity.INFO)


def test_repeated_phrase_is_low_severity_and_not_removed(tmp_path: Path) -> None:
    phrase = "the sixty minute chart is forming a wedge"
    text = f"{phrase} and then {phrase} later on in the lecture"
    report = analyze(tmp_path, [segment(0, 0.0, 30.0, text)], text=text)

    assert report.has(FindingType.REPEATED_PHRASE)
    assert report.by_type(FindingType.REPEATED_PHRASE)[0].severity is Severity.LOW


def test_repeated_phrase_does_not_duplicate_one_passing(tmp_path: Path) -> None:
    """Overlapping n-grams of one repetition must collapse into one finding."""

    text = (
        "Sorry about being late. The bulls see the sixty minute chart as forming a "
        "wedge bull flag. Sorry about being late. The bulls see the sixty minute "
        "chart as forming a wedge bull flag."
    )
    report = analyze(tmp_path, [segment(0, 0.0, 30.0, text)], text=text)
    assert len(report.by_type(FindingType.REPEATED_PHRASE)) <= 2


def test_short_text_has_no_repeated_phrase_noise(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, 0.0, 5.0, "a gap a gap")], text="a gap a gap")
    assert not report.has(FindingType.REPEATED_PHRASE)


# ----------------------------------------------------------------------
# Empty segments
# ----------------------------------------------------------------------
@pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
def test_empty_segment_is_detected(tmp_path: Path, empty: str) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 5.0, "real speech here"), segment(1, 5.0, 10.0, empty)],
    )
    finding = report.by_type(FindingType.EMPTY_SEGMENT)[0]
    assert finding.segment_ids == ("1",)
    assert finding.severity is Severity.MEDIUM


def test_empty_transcript_is_critical(tmp_path: Path) -> None:
    path = tmp_path / "empty.en.json"
    path.write_text(json.dumps({"schema_version": 1, "transcript": "  "}), encoding="utf-8")
    report = TranscriptQaAnalyzer().analyze(load_raw_transcript(path))
    assert report.has(FindingType.NO_SEGMENTS)
    assert report.max_severity() is Severity.CRITICAL


def test_transcript_without_segments_is_high(tmp_path: Path) -> None:
    report = analyze(tmp_path, [], text="Some words were transcribed.")
    finding = report.by_type(FindingType.NO_SEGMENTS)[0]
    assert finding.severity is Severity.HIGH
    assert report.has_timestamps is False


# ----------------------------------------------------------------------
# Timestamps
# ----------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, "abc", "", True, [1, 2], {"a": 1}])
def test_invalid_timestamps_are_detected(tmp_path: Path, bad: Any) -> None:
    report = analyze(tmp_path, [segment(0, bad, 5.0, "some speech")])
    finding = report.by_type(FindingType.INVALID_TIMESTAMP)[0]
    assert finding.severity is Severity.HIGH
    assert finding.evidence["start_raw"] == bad


def test_invalid_end_timestamp_is_detected(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, 0.0, "nope", "some speech")])
    finding = report.by_type(FindingType.INVALID_TIMESTAMP)[0]
    assert "end" in finding.summary


def test_end_before_start_is_critical(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, 10.0, 5.0, "backwards in time")])
    assert report.by_type(FindingType.END_BEFORE_START)[0].severity is Severity.CRITICAL


def test_zero_length_segment_is_critical(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, 5.0, 5.0, "instantaneous")])
    assert report.by_type(FindingType.END_BEFORE_START)[0].severity is Severity.CRITICAL


def test_negative_start_is_reported(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, -2.0, 5.0, "negative start")])
    assert report.has(FindingType.INVALID_TIMESTAMP)


def test_out_of_order_segments_are_detected(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [
            segment(0, 10.0, 15.0, "the second one actually"),
            segment(1, 0.0, 5.0, "but comes with an earlier start"),
        ],
    )
    finding = report.by_type(FindingType.OUT_OF_ORDER)[0]
    assert finding.severity is Severity.HIGH
    assert finding.segment_ids == ("0", "1")
    assert finding.evidence["previous_start"] == pytest.approx(10.0)
    assert finding.evidence["current_start"] == pytest.approx(0.0)


def test_timestamp_beyond_audio_length_is_reported(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 45.0, "runs past the end")],
        processed_duration_seconds=30.0,
    )
    finding = report.by_type(FindingType.TIMESTAMP_OUT_OF_BOUNDS)[0]
    assert finding.severity is Severity.HIGH
    assert finding.evidence["reference_duration_seconds"] == pytest.approx(30.0)


def test_string_timestamps_are_parsed(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, "0.0", "7.0", "numeric strings work")])
    assert not report.has(FindingType.INVALID_TIMESTAMP)
    assert not report.has(FindingType.END_BEFORE_START)


def test_nan_timestamp_is_invalid(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, float("nan"), 5.0, "not a number")])
    assert report.has(FindingType.INVALID_TIMESTAMP)


# ----------------------------------------------------------------------
# Gaps and overlaps
# ----------------------------------------------------------------------
def test_large_gap_is_detected(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 5.0, "before the gap"), segment(1, 20.0, 25.0, "after the gap")],
    )
    gap = report.by_type(FindingType.SEGMENT_GAP)[0]
    assert gap.evidence["gap_seconds"] == pytest.approx(15.0)
    assert gap.segment_ids == ("0", "1")


def test_small_gap_is_not_reported(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 5.0, "first bit"), segment(1, 5.4, 10.0, "second bit")],
    )
    assert not report.has(FindingType.SEGMENT_GAP)


def test_overlap_is_detected(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 8.0, "first segment"), segment(1, 6.0, 12.0, "second segment")],
    )
    overlap = report.by_type(FindingType.SEGMENT_OVERLAP)[0]
    assert overlap.evidence["overlap_seconds"] == pytest.approx(2.0)


def test_tiny_overlap_is_tolerated(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 8.0, "first segment"), segment(1, 7.98, 12.0, "second")],
    )
    assert not report.has(FindingType.SEGMENT_OVERLAP)


# ----------------------------------------------------------------------
# Coverage of the interval
# ----------------------------------------------------------------------
def test_incomplete_start_is_detected(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 5.0, 10.0, "starts late in the clip")],
        processed_duration_seconds=30.0,
    )
    finding = report.by_type(FindingType.INCOMPLETE_SPEECH_START)[0]
    assert finding.severity is Severity.MEDIUM
    assert finding.time_range is not None
    assert finding.time_range.start == 0.0


def test_incomplete_end_is_detected(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [
            segment(0, 0.0, 5.0, "the beginning"),
            segment(1, 5.0, 10.0, "the middle bit"),
        ],
        processed_duration_seconds=30.0,
    )
    assert report.has(FindingType.INCOMPLETE_SPEECH_END)


def test_full_coverage_produces_no_coverage_findings(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 15.0, "first half"), segment(1, 15.0, 30.0, "second half")],
        processed_duration_seconds=30.0,
    )
    assert not report.has(FindingType.INCOMPLETE_SPEECH_START)
    assert not report.has(FindingType.INCOMPLETE_SPEECH_END)
    assert not report.has(FindingType.SEGMENT_GAP)


def test_no_bounds_check_without_duration_reference(tmp_path: Path) -> None:
    path = tmp_path / "nodur.en.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "transcript": "text",
                "segments": [segment(0, 0.0, 500.0, "long")],
            }
        ),
        encoding="utf-8",
    )
    report = TranscriptQaAnalyzer().analyze(load_raw_transcript(path))
    assert not report.has(FindingType.TIMESTAMP_OUT_OF_BOUNDS)


# ----------------------------------------------------------------------
# Transcript vs segments consistency
# ----------------------------------------------------------------------
def test_transcript_segment_mismatch_is_detected(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [segment(0, 0.0, 5.0, "the bulls are in control")],
        text="completely unrelated words about the weather forecast tomorrow",
    )
    finding = report.by_type(FindingType.TRANSCRIPT_SEGMENT_MISMATCH)[0]
    assert finding.evidence["similarity"] < 0.5
    assert finding.severity is Severity.HIGH


def test_consistent_transcript_and_segments_pass(tmp_path: Path) -> None:
    segments = [
        segment(0, 0.0, 5.0, "the bulls are in control"),
        segment(1, 5.0, 10.0, "and the bears are not"),
    ]
    report = analyze(tmp_path, segments, text="the bulls are in control and the bears are not")
    assert not report.has(FindingType.TRANSCRIPT_SEGMENT_MISMATCH)


def test_empty_transcript_with_segments_is_reported(tmp_path: Path) -> None:
    report = analyze(tmp_path, [segment(0, 0.0, 5.0, "there is text here")], text="")
    assert report.has(FindingType.TRANSCRIPT_SEGMENT_MISMATCH)


# ----------------------------------------------------------------------
# Segment length sanity
# ----------------------------------------------------------------------
def test_short_segment_is_low_severity(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [
            segment(0, 0.0, 8.0, "a normal length of speech here"),
            segment(1, 8.0, 8.05, "uh"),
            segment(2, 8.1, 16.0, "more normal length speech"),
            segment(3, 16.0, 24.0, "and yet more normal speech"),
        ],
    )
    finding = report.by_type(FindingType.SEGMENT_TOO_SHORT)[0]
    assert finding.severity is Severity.LOW
    assert finding.segment_ids == ("1",)


def test_uniform_segments_are_not_flagged_as_long(tmp_path: Path) -> None:
    report = analyze(
        tmp_path,
        [
            segment(index, index * 8.0, (index + 1) * 8.0, f"segment number {index}")
            for index in range(4)
        ],
    )
    assert not report.has(FindingType.SEGMENT_TOO_LONG)


# ----------------------------------------------------------------------
# Terminology
# ----------------------------------------------------------------------
def test_terminology_is_annotated_not_applied(tmp_path: Path) -> None:
    text = "The wedge bull flag is a bull flag after a spike and pullback."
    raw = write_transcript(tmp_path, payload([segment(0, 0.0, 10.0, text)], text=text))
    report = TranscriptQaAnalyzer().analyze(raw)

    finding = report.by_type(FindingType.TERMINOLOGY_DETECTED)[0]
    assert finding.severity is Severity.INFO
    assert "wedge_bull_flag" in finding.evidence["term_ids"]
    assert "bull_flag" in finding.evidence["term_ids"]


def test_terminology_never_changes_the_text(tmp_path: Path) -> None:
    text = "the gap was filled by a reversal"
    raw = write_transcript(tmp_path, payload([segment(0, 0.0, 10.0, text)], text=text))
    TranscriptQaAnalyzer().analyze(raw)
    assert raw.text == text


# ----------------------------------------------------------------------
# Report structure
# ----------------------------------------------------------------------
def test_findings_are_sorted_by_severity(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)
    ranks = [finding.severity.rank for finding in report.findings]
    assert ranks == sorted(ranks)


def test_every_finding_has_the_required_fields(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)

    for finding in report.findings:
        payload_dict = finding.to_dict()
        for field in (
            "type",
            "severity",
            "summary",
            "explanation",
            "suggested_action",
            "segment_ids",
            "time_range",
        ):
            assert field in payload_dict
        assert payload_dict["explanation"]
        assert payload_dict["suggested_action"]


def test_summary_counts_add_up(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)
    summary = report.summary()

    assert summary["total_findings"] == len(report.findings)
    assert sum(summary["by_severity"].values()) == len(report.findings)
    assert sum(summary["by_type"].values()) == len(report.findings)
    assert summary["duplicate_group_count"] == len(report.duplicate_groups)


def test_clean_report_has_no_actionable_findings(tmp_path: Path) -> None:
    segments = [
        segment(0, 0.0, 8.0, "the bulls are in control of this market"),
        segment(1, 8.0, 16.0, "and they push the price higher with conviction"),
        segment(2, 16.0, 24.0, "until we finally see a clear breakout point appear"),
        segment(3, 24.0, 32.0, "that is the setup we wanted to find today"),
    ]
    report = analyze(tmp_path, segments, processed_duration_seconds=32.0)
    # Terminology annotations are informational and always expected here.
    actionable = [f for f in report.findings if f.severity is not Severity.INFO]
    assert actionable == []
    assert report.needs_review is False


def test_segment_flags_are_collected(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    report = TranscriptQaAnalyzer().analyze(raw)

    assert "exact_duplicate_segment" in report.segment_flags["0"]
    assert "exact_duplicate_segment" in report.segment_flags["1"]
    assert "2" not in report.segment_flags
    # terminology is an annotation, not a per-segment quality flag
    assert all(
        str(FindingType.TERMINOLOGY_DETECTED) not in flags
        for flags in report.segment_flags.values()
    )


def test_thresholds_are_configurable(tmp_path: Path) -> None:
    raw = write_transcript(tmp_path, known_duplicate_payload())
    strict = TranscriptQaAnalyzer(
        thresholds=QaThresholds(long_segment_multiplier=1.1, long_segment_floor_seconds=1.0)
    ).analyze(raw)
    assert strict.by_type(FindingType.SEGMENT_TOO_LONG)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def test_full_length_transcript_is_analysed_quickly(tmp_path: Path) -> None:
    """A whole lecture must not blow up the QA stage.

    This guards the linear-ish algorithms: an earlier overlap check in the
    terminology matcher and an n-gram sweep made a full-length transcript take
    minutes, which would be unusable for the later full-video phase.
    """

    import time

    words = 60_000
    body = " ".join(
        f"the bulls see a wedge bull flag in the trading range number {index}"
        for index in range(words // 13)
    )
    segments = [
        segment(index, index * 10.0, (index + 1) * 10.0, f"chunk number {index} of the lecture")
        for index in range(200)
    ]
    document = payload(segments, text=body, processed_duration_seconds=2000.0)
    raw = write_transcript(tmp_path, document)

    started = time.perf_counter()
    report = TranscriptQaAnalyzer().analyze(raw)
    elapsed = time.perf_counter() - started

    assert report.findings  # the synthetic text is heavily repetitive
    assert elapsed < 30.0, f"analysis took {elapsed:.1f}s, expected well under 30s"


def test_overlapping_seeds_of_one_repetition_collapse_to_one_finding(tmp_path: Path) -> None:
    """A repetition separated by filler yields exactly one finding."""

    phrase = "the bulls see the sixty minute chart as forming a wedge bull flag"
    filler = "and now let us look at something else entirely for a while"
    text = f"{phrase}. {filler}. {phrase}."
    report = analyze(tmp_path, [segment(0, 0.0, 30.0, text)], text=text)

    repeated = report.by_type(FindingType.REPEATED_PHRASE)
    assert len(repeated) == 1
    assert repeated[0].evidence["occurrences"] == 2
    assert repeated[0].evidence["word_count"] == 13


def test_adjacent_repetition_is_not_double_reported(tmp_path: Path) -> None:
    """Back-to-back repeats belong to the duplicate-segment check, not this one."""

    phrase = "the bulls see the sixty minute chart as forming a wedge bull flag"
    text = f"{phrase}. {phrase}."
    report = analyze(tmp_path, [segment(0, 0.0, 30.0, text)], text=text)
    assert not report.has(FindingType.REPEATED_PHRASE)


def test_max_run_length() -> None:
    assert max_run_length(("the", "the", "the")) == 3
    assert max_run_length(("a", "b", "c")) == 1


def test_duplicate_group_size() -> None:
    group = DuplicateGroup(segment_ids=("0", "1"), normalized_text="x", exact=True)
    assert group.size == 2


def test_empty_report_is_consistent() -> None:
    report = QaReport()
    assert report.findings == ()
    assert report.needs_review is False
    assert report.max_severity() is None
    assert report.summary()["total_findings"] == 0


def test_severity_ordering() -> None:
    assert Severity.CRITICAL.rank < Severity.HIGH.rank < Severity.MEDIUM.rank
    assert Severity.MEDIUM.rank < Severity.LOW.rank < Severity.INFO.rank


def test_enum_stringifies_to_value() -> None:
    """Python 3.10 has no StrEnum; str() must still yield the plain value."""

    assert str(Severity.HIGH) == "high"
    assert str(FindingType.EXACT_DUPLICATE_SEGMENT) == "exact_duplicate_segment"
