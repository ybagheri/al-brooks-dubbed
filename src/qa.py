"""Transcript quality assurance.

The analyzer is deliberately **conservative**. Natural speech contains
repetitions, self-corrections and repeated terminology, so a repeated phrase
is reported as a finding with an explanation, never silently removed. Nothing
in this module modifies the transcript; it only reports what it finds.

Findings are ordered by severity, and each one carries the affected segment
ids, the time range when known, and an actionable explanation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .enums import StringEnum
from .terminology import Terminology, load_terminology
from .text_cleaning import (
    clean_text,
    comparison_key,
    join_segment_texts,
    similarity,
    word_tokens,
)
from .transcript import RawTranscript

logger = logging.getLogger(__name__)


class Severity(StringEnum):
    """How strongly a finding suggests a problem with the transcript."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


class FindingType(StringEnum):
    """Stable identifiers for every check, used in reports and flags."""

    EMPTY_SEGMENT = "empty_segment"
    EXACT_DUPLICATE_SEGMENT = "exact_duplicate_segment"
    NEAR_DUPLICATE_SEGMENT = "near_duplicate_segment"
    DUPLICATE_DURATION_MISMATCH = "duplicate_segment_duration_mismatch"
    REPEATED_PHRASE = "repeated_phrase"
    INVALID_TIMESTAMP = "invalid_timestamp"
    END_BEFORE_START = "segment_end_before_start"
    OUT_OF_ORDER = "segment_out_of_order"
    TIMESTAMP_OUT_OF_BOUNDS = "segment_timestamp_out_of_bounds"
    SEGMENT_GAP = "segment_gap"
    SEGMENT_OVERLAP = "segment_overlap"
    TRANSCRIPT_SEGMENT_MISMATCH = "transcript_segment_mismatch"
    SEGMENT_TOO_SHORT = "segment_suspiciously_short"
    SEGMENT_TOO_LONG = "segment_suspiciously_long"
    INCOMPLETE_SPEECH_START = "incomplete_speech_at_start"
    INCOMPLETE_SPEECH_END = "incomplete_speech_at_end"
    TERMINOLOGY_DETECTED = "terminology_detected"
    NO_SEGMENTS = "no_segments"


@dataclass(frozen=True)
class TimeRange:
    start: float
    end: float

    def to_dict(self) -> dict[str, float]:
        return {"start": round(self.start, 3), "end": round(self.end, 3)}


@dataclass(frozen=True)
class QAFinding:
    """One reported quality issue."""

    type: FindingType
    severity: Severity
    summary: str
    explanation: str
    suggested_action: str
    segment_ids: tuple[str, ...] = ()
    time_range: TimeRange | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": str(self.type),
            "severity": str(self.severity),
            "summary": self.summary,
            "explanation": self.explanation,
            "suggested_action": self.suggested_action,
            "segment_ids": list(self.segment_ids),
            "time_range": self.time_range.to_dict() if self.time_range else None,
        }
        if self.evidence:
            payload["evidence"] = self.evidence
        return payload


@dataclass(frozen=True)
class DuplicateGroup:
    """Raw segments believed to repeat one another."""

    segment_ids: tuple[str, ...]
    normalized_text: str
    exact: bool

    @property
    def size(self) -> int:
        return len(self.segment_ids)


@dataclass(frozen=True)
class QaReport:
    """All findings for one transcript."""

    findings: tuple[QAFinding, ...] = ()
    duplicate_groups: tuple[DuplicateGroup, ...] = ()
    segment_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    duration_reference_seconds: float | None = None
    has_timestamps: bool = False

    def by_type(self, finding_type: FindingType) -> list[QAFinding]:
        return [finding for finding in self.findings if finding.type is finding_type]

    def has(self, finding_type: FindingType) -> bool:
        return any(finding.type is finding_type for finding in self.findings)

    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return min((finding.severity for finding in self.findings), key=lambda s: s.rank)

    def counts_by_severity(self) -> dict[str, int]:
        counts = {str(severity): 0 for severity in Severity}
        for finding in self.findings:
            counts[str(finding.severity)] += 1
        return counts

    def counts_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[str(finding.type)] = counts.get(str(finding.type), 0) + 1
        return counts

    @property
    def needs_review(self) -> bool:
        """True when a human should look at this transcript."""

        return any(
            finding.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)
            for finding in self.findings
        )

    def summary(self) -> dict[str, Any]:
        return {
            "total_findings": len(self.findings),
            "by_severity": self.counts_by_severity(),
            "by_type": self.counts_by_type(),
            "duplicate_group_count": len(self.duplicate_groups),
            "max_severity": str(self.max_severity()) if self.max_severity() else None,
            "needs_review": self.needs_review,
            "has_timestamps": self.has_timestamps,
            "duration_reference_seconds": (
                round(self.duration_reference_seconds, 3)
                if self.duration_reference_seconds is not None
                else None
            ),
        }


@dataclass(frozen=True)
class QaThresholds:
    """Tunable limits. Exposed so they can be adjusted without code edits."""

    duplicate_similarity: float = 0.90
    repeated_phrase_min_words: int = 6
    #: Upper bound on the n-gram window, keeping detection linear in length.
    repeated_phrase_max_words: int = 12
    segment_gap_seconds: float = 1.5
    overlap_tolerance_seconds: float = 0.15
    bounds_tolerance_seconds: float = 0.5
    min_segment_seconds: float = 0.2
    #: A segment more than this multiple of the median duration is suspicious.
    long_segment_multiplier: float = 2.0
    long_segment_floor_seconds: float = 10.0
    transcript_match_min: float = 0.80
    start_silence_seconds: float = 1.0
    end_silence_seconds: float = 2.0


@dataclass(frozen=True)
class _TimedSegment:
    """A segment with best-effort parsed timestamps."""

    key: str
    index: int
    text: str
    cleaned_text: str
    start: float | None
    end: float | None
    start_valid: bool
    end_valid: bool
    start_raw_repr: Any
    end_raw_repr: Any

    @property
    def duration(self) -> float | None:
        if self.start is None or self.end is None or self.end < self.start:
            return None
        return self.end - self.start

    @property
    def normalized(self) -> str:
        return comparison_key(self.text)


class TranscriptQaAnalyzer:
    """Runs every quality check over one raw transcript."""

    def __init__(
        self,
        thresholds: QaThresholds | None = None,
        terminology: Terminology | None = None,
    ) -> None:
        self.thresholds = thresholds or QaThresholds()
        self.terminology = terminology or load_terminology()

    # ------------------------------------------------------------------
    def analyze(self, raw: RawTranscript) -> QaReport:
        """Run all checks and return a consolidated report."""

        segments = _prepare_segments(raw)
        reference = raw.reference_duration_seconds

        findings: list[QAFinding] = []
        findings.extend(self._check_structure(raw))
        findings.extend(self._check_empty_segments(segments))
        findings.extend(self._check_timestamps(segments))
        findings.extend(self._check_ordering(segments))
        findings.extend(self._check_coverage(segments, reference))
        findings.extend(self._check_segment_lengths(segments))
        findings.extend(self._check_transcript_consistency(raw, segments))

        duplicate_groups = self._find_duplicate_groups(segments)
        findings.extend(self._duplicate_findings(segments, duplicate_groups))
        findings.extend(self._check_repeated_phrases(raw, segments))
        findings.extend(self._check_terminology(raw, segments))

        segment_flags = _collect_segment_flags(findings)
        ordered = tuple(sorted(findings, key=_finding_sort_key))
        return QaReport(
            findings=ordered,
            duplicate_groups=duplicate_groups,
            segment_flags=segment_flags,
            duration_reference_seconds=reference,
            has_timestamps=raw.has_timestamps,
        )

    # ------------------------------------------------------------------
    # Structural
    # ------------------------------------------------------------------
    def _check_structure(self, raw: RawTranscript) -> list[QAFinding]:
        if raw.segments:
            return []
        if not raw.text.strip():
            return [
                QAFinding(
                    type=FindingType.NO_SEGMENTS,
                    severity=Severity.CRITICAL,
                    summary="Transcript is empty",
                    explanation=(
                        "The artifact contains neither segment data nor transcript text, "
                        "so there is nothing to clean or translate."
                    ),
                    suggested_action="Re-run Phase 1 transcription on this interval.",
                )
            ]
        return [
            QAFinding(
                type=FindingType.NO_SEGMENTS,
                severity=Severity.HIGH,
                summary="Transcript text has no segment timestamps",
                explanation=(
                    "The transcript is present but the API returned no segments, so no "
                    "word or phrase can be aligned to the audio. Downstream timing "
                    "alignment would have to treat the whole text as a single unit."
                ),
                suggested_action=(
                    "Re-run with --keep-temp and inspect the audio, or accept that "
                    "timing alignment is unavailable for this interval."
                ),
            )
        ]

    def _check_empty_segments(self, segments: Sequence[_TimedSegment]) -> list[QAFinding]:
        findings: list[QAFinding] = []
        for segment in segments:
            if segment.cleaned_text.strip():
                continue
            findings.append(
                QAFinding(
                    type=FindingType.EMPTY_SEGMENT,
                    severity=Severity.MEDIUM,
                    summary=f"Segment {segment.key} has no text",
                    explanation=(
                        "The API returned a segment with empty or whitespace-only text. "
                        "It may cover silence, or the speech in that slot was lost."
                    ),
                    suggested_action=(
                        "Check the audio for that time range; leave the segment in place "
                        "so the time reference is preserved."
                    ),
                    segment_ids=(segment.key,),
                    time_range=_range_of(segment),
                    evidence={"raw_text_repr": repr(segment.text)},
                )
            )
        return findings

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------
    def _check_timestamps(self, segments: Sequence[_TimedSegment]) -> list[QAFinding]:
        findings: list[QAFinding] = []
        for segment in segments:
            problems: list[str] = []
            if not segment.start_valid:
                problems.append("start")
            if not segment.end_valid:
                problems.append("end")
            if problems:
                findings.append(
                    QAFinding(
                        type=FindingType.INVALID_TIMESTAMP,
                        severity=Severity.HIGH,
                        summary=(
                            f"Segment {segment.key} has an unusable "
                            f"{' and '.join(problems)} timestamp"
                        ),
                        explanation=(
                            f"The {', '.join(problems)} value "
                            f"(start={segment.start_raw_repr}, end={segment.end_raw_repr}) "
                            "is missing, non-numeric or not a finite number. Timing "
                            "alignment for this segment is impossible."
                        ),
                        suggested_action=(
                            "Treat this segment as untimed; do not guess a time range."
                        ),
                        segment_ids=(segment.key,),
                        time_range=_range_of(segment),
                        evidence={
                            "start_raw": segment.start_raw_repr,
                            "end_raw": segment.end_raw_repr,
                        },
                    )
                )
                continue

            assert segment.start is not None and segment.end is not None
            if segment.end <= segment.start:
                findings.append(
                    QAFinding(
                        type=FindingType.END_BEFORE_START,
                        severity=Severity.CRITICAL,
                        summary=f"Segment {segment.key} ends at or before it starts",
                        explanation=(
                            f"start={segment.start}, end={segment.end}. A segment with "
                            "non-positive duration cannot be aligned and usually "
                            "indicates a broken timestamp."
                        ),
                        suggested_action=(
                            "Regenerate the segment timing before translating this range."
                        ),
                        segment_ids=(segment.key,),
                        time_range=_range_of(segment),
                    )
                )
            elif segment.start < 0:
                findings.append(
                    QAFinding(
                        type=FindingType.INVALID_TIMESTAMP,
                        severity=Severity.HIGH,
                        summary=f"Segment {segment.key} has a negative start time",
                        explanation=f"start={segment.start} is before the audio begins.",
                        suggested_action="Clamp to 0 only if independently confirmed.",
                        segment_ids=(segment.key,),
                        time_range=_range_of(segment),
                    )
                )
        return findings

    def _check_coverage(
        self, segments: Sequence[_TimedSegment], reference: float | None
    ) -> list[QAFinding]:
        """Gaps, overlaps, out-of-bounds times and incomplete coverage.

        Gaps and overlaps need at least two timed segments, but the bounds and
        completeness checks must also run for a single-segment transcript.
        """

        findings: list[QAFinding] = []
        timed = [segment for segment in segments if segment.duration is not None]
        if not timed:
            return findings

        if len(timed) >= 2:
            findings.extend(self._check_adjacent_pairs(timed))

        if reference is not None:
            findings.extend(self._check_bounds(timed, reference))
            findings.extend(self._check_completeness(timed, reference))
        return findings

    def _check_ordering(self, segments: Sequence[_TimedSegment]) -> list[QAFinding]:
        """Report segments whose start times go backwards in document order."""

        findings: list[QAFinding] = []
        previous: _TimedSegment | None = None
        for segment in segments:
            if segment.start is None:
                previous = None
                continue
            if (
                previous is not None
                and previous.start is not None
                and segment.start < previous.start
            ):
                findings.append(
                    QAFinding(
                        type=FindingType.OUT_OF_ORDER,
                        severity=Severity.HIGH,
                        summary=(
                            f"Segment {segment.key} starts at {segment.start}s, before "
                            f"segment {previous.key} at {previous.start}s"
                        ),
                        explanation=(
                            "Segments are listed out of chronological order, so a "
                            "translation stage that renders them in sequence would "
                            "produce dubbed audio in the wrong order."
                        ),
                        suggested_action=(
                            "Sort by start time before translating, and verify the "
                            "boundaries against the audio."
                        ),
                        segment_ids=(previous.key, segment.key),
                        evidence={
                            "previous_start": previous.start,
                            "current_start": segment.start,
                        },
                    )
                )
            previous = segment
        return findings

    def _check_adjacent_pairs(self, timed: Sequence[_TimedSegment]) -> list[QAFinding]:
        findings: list[QAFinding] = []
        for previous, current in zip(timed, timed[1:], strict=False):
            assert previous.end is not None and current.start is not None
            delta = current.start - previous.end
            if delta > self.thresholds.segment_gap_seconds:
                findings.append(
                    QAFinding(
                        type=FindingType.SEGMENT_GAP,
                        severity=Severity.MEDIUM,
                        summary=(
                            f"{delta:.2f}s gap between segments {previous.key} and {current.key}"
                        ),
                        explanation=(
                            "No transcript covers this interval. It may be silence, but "
                            "it may also be speech the API skipped."
                        ),
                        suggested_action=(
                            "Listen to the gap; a later translation stage should keep the "
                            "pause explicit rather than merge across it."
                        ),
                        segment_ids=(previous.key, current.key),
                        time_range=TimeRange(previous.end, current.start),
                        evidence={"gap_seconds": round(delta, 3)},
                    )
                )
            elif delta < -self.thresholds.overlap_tolerance_seconds:
                overlap = -delta
                findings.append(
                    QAFinding(
                        type=FindingType.SEGMENT_OVERLAP,
                        severity=Severity.MEDIUM,
                        summary=(
                            f"{overlap:.2f}s overlap between segments "
                            f"{previous.key} and {current.key}"
                        ),
                        explanation=(
                            "Segments overlap in time, so they cannot be translated as "
                            "independent units without the same audio being rendered twice."
                        ),
                        suggested_action=(
                            "Re-time the segments so each audio range is owned by exactly "
                            "one segment."
                        ),
                        segment_ids=(previous.key, current.key),
                        time_range=TimeRange(current.start, previous.end),
                        evidence={"overlap_seconds": round(overlap, 3)},
                    )
                )
        return findings

    def _check_bounds(self, timed: Sequence[_TimedSegment], reference: float) -> list[QAFinding]:
        findings: list[QAFinding] = []
        tolerance = self.thresholds.bounds_tolerance_seconds
        for segment in timed:
            assert segment.start is not None and segment.end is not None
            if segment.start > reference + tolerance or segment.end > reference + tolerance:
                findings.append(
                    QAFinding(
                        type=FindingType.TIMESTAMP_OUT_OF_BOUNDS,
                        severity=Severity.HIGH,
                        summary=f"Segment {segment.key} lies outside the processed audio",
                        explanation=(
                            f"Segment spans {segment.start}-{segment.end}s but the "
                            f"transcribed interval is only {reference:.3f}s long. The "
                            "timestamps cannot belong to this audio."
                        ),
                        suggested_action=(
                            "Re-transcribe; do not clamp timestamps to the audio length "
                            "because that would fabricate timing."
                        ),
                        segment_ids=(segment.key,),
                        time_range=_range_of(segment),
                        evidence={"reference_duration_seconds": round(reference, 3)},
                    )
                )
        return findings

    def _check_completeness(
        self, timed: Sequence[_TimedSegment], reference: float
    ) -> list[QAFinding]:
        findings: list[QAFinding] = []
        first, last = timed[0], timed[-1]
        assert first.start is not None and last.end is not None

        if first.start > self.thresholds.start_silence_seconds:
            findings.append(
                QAFinding(
                    type=FindingType.INCOMPLETE_SPEECH_START,
                    severity=Severity.MEDIUM,
                    summary=f"Transcription starts {first.start:.2f}s into the audio",
                    explanation=(
                        "The first transcript segment does not begin at 0s. Speech before "
                        "it may have been missed, which often happens when a segment "
                        "boundary is mis-detected."
                    ),
                    suggested_action=(
                        "Verify the opening of the sample before translating; the start "
                        "may be truncated mid-sentence."
                    ),
                    segment_ids=(first.key,),
                    time_range=TimeRange(0.0, first.start),
                )
            )

        remaining = reference - last.end
        if remaining > self.thresholds.end_silence_seconds:
            findings.append(
                QAFinding(
                    type=FindingType.INCOMPLETE_SPEECH_END,
                    severity=Severity.MEDIUM,
                    summary=f"Transcription ends {remaining:.2f}s before the audio ends",
                    explanation=(
                        "The transcript stops before the end of the processed interval, so "
                        "the closing words may be missing."
                    ),
                    suggested_action=(
                        "Check the end of the sample; the final sentence may be cut off."
                    ),
                    segment_ids=(last.key,),
                    time_range=TimeRange(last.end, reference),
                    evidence={"uncovered_seconds": round(remaining, 3)},
                )
            )
        return findings

    # Segment length sanity
    # ------------------------------------------------------------------
    def _check_segment_lengths(self, segments: Sequence[_TimedSegment]) -> list[QAFinding]:
        durations = [segment.duration for segment in segments if segment.duration is not None]
        if not durations:
            return []

        findings: list[QAFinding] = []
        ordered = sorted(durations)
        median = _median(ordered)
        long_limit = max(
            self.thresholds.long_segment_floor_seconds,
            median * self.thresholds.long_segment_multiplier,
        )

        for segment in segments:
            duration = segment.duration
            if duration is None:
                continue
            if duration < self.thresholds.min_segment_seconds:
                findings.append(
                    QAFinding(
                        type=FindingType.SEGMENT_TOO_SHORT,
                        severity=Severity.LOW,
                        summary=f"Segment {segment.key} lasts only {duration:.2f}s",
                        explanation=(
                            "Very short segments often contain a filler sound or a "
                            "mis-detected boundary rather than a complete utterance."
                        ),
                        suggested_action=("Consider merging with a neighbour before translating."),
                        segment_ids=(segment.key,),
                        time_range=_range_of(segment),
                        evidence={"duration_seconds": round(duration, 3)},
                    )
                )
            elif duration > long_limit:
                findings.append(
                    QAFinding(
                        type=FindingType.SEGMENT_TOO_LONG,
                        severity=Severity.MEDIUM,
                        summary=(
                            f"Segment {segment.key} lasts {duration:.2f}s, far longer than "
                            f"the {median:.2f}s median"
                        ),
                        explanation=(
                            "A single segment covering much more audio than its "
                            "neighbours is a common symptom of a duplicated or merged "
                            "segment, which is hard to split correctly for dubbing."
                        ),
                        suggested_action=(
                            "Verify this range against the audio before translating; it "
                            "may need to be split."
                        ),
                        segment_ids=(segment.key,),
                        time_range=_range_of(segment),
                        evidence={
                            "duration_seconds": round(duration, 3),
                            "median_duration_seconds": round(median, 3),
                            "long_segment_threshold_seconds": round(long_limit, 3),
                        },
                    )
                )
        return findings

    # ------------------------------------------------------------------
    # Duplicates
    # ------------------------------------------------------------------
    def _find_duplicate_groups(
        self, segments: Sequence[_TimedSegment]
    ) -> tuple[DuplicateGroup, ...]:
        groups: list[DuplicateGroup] = []
        by_key: dict[str, list[str]] = {}
        for segment in segments:
            if not segment.normalized:
                continue
            by_key.setdefault(segment.normalized, []).append(segment.key)

        for normalized, keys in by_key.items():
            if len(keys) < 2:
                continue
            texts = {segment.normalized for segment in segments if segment.key in keys}
            groups.append(
                DuplicateGroup(
                    segment_ids=tuple(keys),
                    normalized_text=normalized,
                    exact=len(texts) == 1,
                )
            )
        return tuple(groups)

    def _duplicate_findings(
        self,
        segments: Sequence[_TimedSegment],
        duplicate_groups: Sequence[DuplicateGroup],
    ) -> list[QAFinding]:
        findings: list[QAFinding] = []
        by_key = {segment.key: segment for segment in segments}

        for group in duplicate_groups:
            involved = [by_key[key] for key in group.segment_ids if key in by_key]
            if len(involved) < 2:
                continue
            exact = group.exact and len({s.normalized for s in involved}) == 1
            # Adjacent repeats are the strongest signal of a duplication artefact.
            indexes = sorted(s.index for s in involved)
            adjacent = all(indexes[i + 1] == indexes[i] + 1 for i in range(len(indexes) - 1))

            time_range = None
            valid = [s for s in involved if s.duration is not None]
            starts = [s.start for s in valid if s.start is not None]
            ends = [s.end for s in valid if s.end is not None]
            if starts and ends:
                time_range = TimeRange(min(starts), max(ends))

            ratio = similarity(involved[0].text, involved[-1].text)
            findings.append(
                QAFinding(
                    type=(
                        FindingType.EXACT_DUPLICATE_SEGMENT
                        if exact
                        else FindingType.NEAR_DUPLICATE_SEGMENT
                    ),
                    severity=Severity.HIGH if (exact and adjacent) else Severity.MEDIUM,
                    summary=(
                        f"{'Identical' if exact else 'Near-identical'} text in "
                        f"segments {', '.join(group.segment_ids)}"
                    ),
                    explanation=(
                        "Adjacent segments carry the same words"
                        f"{' with a similarity of ' + format(ratio, '.2f') if not exact else ''}. "
                        "This is either a genuine repetition by the speaker, a "
                        "self-correction, or a duplication artefact from the "
                        "transcription API. Repeating terminology is normal speech and "
                        "must not be removed on this evidence alone."
                    ),
                    suggested_action=(
                        "Listen to the range. If the words are spoken only once, re-run "
                        "transcription with --verify-transcript; otherwise keep both "
                        "segments."
                    ),
                    segment_ids=group.segment_ids,
                    time_range=time_range,
                    evidence={
                        "exact_match": exact,
                        "adjacent": adjacent,
                        "similarity": round(ratio, 4),
                        "normalized_text": group.normalized_text[:200],
                        "segment_count": group.size,
                    },
                )
            )

            durations = [s.duration for s in involved if s.duration is not None]
            if exact and len(durations) >= 2:
                shortest, longest = min(durations), max(durations)
                if shortest > 0 and longest / shortest >= 2.0:
                    findings.append(
                        QAFinding(
                            type=FindingType.DUPLICATE_DURATION_MISMATCH,
                            severity=Severity.HIGH,
                            summary=(
                                f"Duplicated text spans very different durations "
                                f"({shortest:.2f}s vs {longest:.2f}s)"
                            ),
                            explanation=(
                                "The same words cannot plausibly occupy both a short and "
                                "a long span unless one segment absorbed neighbouring "
                                "speech. This points to a duplicated segment that also "
                                "swallowed other content, and its time range is unreliable."
                            ),
                            suggested_action=(
                                "Do not merge these segments automatically. Verify the "
                                "audio and re-transcribe before trusting either boundary."
                            ),
                            segment_ids=group.segment_ids,
                            time_range=time_range,
                            evidence={
                                "shortest_seconds": round(shortest, 3),
                                "longest_seconds": round(longest, 3),
                            },
                        )
                    )
        return findings

    # ------------------------------------------------------------------
    # Repeated phrases inside the combined transcript
    # ------------------------------------------------------------------
    def _check_repeated_phrases(
        self, raw: RawTranscript, segments: Sequence[_TimedSegment]
    ) -> list[QAFinding]:
        """Report phrases that occur more than once, conservatively.

        Repetition is normal in teaching material, so this only produces ``low``
        severity findings, and sub-phrases of a longer repetition are dropped so
        that a single stutter is not reported repeatedly.

        The scan uses a seed-and-extend strategy: every minimum-length n-gram is
        indexed, and only the few that actually repeat are grown into their
        maximal form. That keeps the cost linear in the transcript length, which
        matters because a full lecture runs to tens of thousands of words.
        """

        tokens = word_tokens(raw.text)
        smallest = self.thresholds.repeated_phrase_min_words
        if len(tokens) < smallest * 2:
            return []

        seeds: dict[tuple[str, ...], list[int]] = {}
        for index in range(len(tokens) - smallest + 1):
            seeds.setdefault(tuple(tokens[index : index + smallest]), []).append(index)

        repeated_seeds = [positions for positions in seeds.values() if len(positions) > 1]
        if not repeated_seeds:
            return []

        grown = [_extend_repetition(tokens, positions, smallest) for positions in repeated_seeds]
        maximal = _maximal_phrases(grown)

        findings: list[QAFinding] = []
        for phrase, positions, word_count in maximal:
            phrase_text = " ".join(phrase)
            gaps = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
            if min(gaps) <= word_count:
                # Directly adjacent repetition: already covered by the
                # duplicate-segment checks, so do not report it twice.
                continue
            findings.append(
                QAFinding(
                    type=FindingType.REPEATED_PHRASE,
                    severity=Severity.LOW,
                    summary=f'Phrase repeated {len(positions)} times: "{phrase_text}"',
                    explanation=(
                        f"The same {word_count}-word phrase occurs {len(positions)} times "
                        "in the transcript. In trading lectures this is usually a "
                        "deliberate restatement or a repeated example rather than an "
                        "error, so it is reported for review only and is never removed."
                    ),
                    suggested_action=(
                        "Keep the repetition if the speaker really repeated it; only "
                        "remove it if the audio proves it was said once."
                    ),
                    segment_ids=_segments_covering(segments, positions),
                    evidence={
                        "phrase": phrase_text,
                        "word_count": word_count,
                        "occurrences": len(positions),
                    },
                )
            )
        return findings

    def _check_transcript_consistency(
        self, raw: RawTranscript, segments: Sequence[_TimedSegment]
    ) -> list[QAFinding]:
        if not segments:
            return []
        joined = join_segment_texts([segment.text for segment in segments])
        raw_text = clean_text(raw.text)
        if not raw_text.strip():
            return [
                QAFinding(
                    type=FindingType.TRANSCRIPT_SEGMENT_MISMATCH,
                    severity=Severity.HIGH,
                    summary="Transcript text is empty although segments carry text",
                    explanation=(
                        "The combined segment text is non-empty but the top-level "
                        "'transcript' field is blank, so the two views of the same "
                        "recording disagree."
                    ),
                    suggested_action="Use the segment text and re-generate the artifact.",
                    segment_ids=tuple(segment.key for segment in segments),
                )
            ]

        ratio = similarity(raw_text, joined)
        if ratio >= self.thresholds.transcript_match_min:
            return []

        return [
            QAFinding(
                type=FindingType.TRANSCRIPT_SEGMENT_MISMATCH,
                severity=Severity.HIGH if ratio < 0.5 else Severity.MEDIUM,
                summary=(f"Transcript text and segment texts disagree (similarity {ratio:.2f})"),
                explanation=(
                    "The combined segment text differs substantially from the "
                    "top-level transcript. One of them is incomplete or was produced "
                    "from a different transcription, so they cannot both be trusted."
                ),
                suggested_action=(
                    "Re-transcribe, or decide explicitly which view is authoritative "
                    "before translating."
                ),
                segment_ids=tuple(segment.key for segment in segments),
                evidence={
                    "similarity": round(ratio, 4),
                    "transcript_characters": len(raw_text),
                    "segment_characters": len(joined),
                },
            )
        ]

    # ------------------------------------------------------------------
    # Terminology (annotation only)
    # ------------------------------------------------------------------
    def _check_terminology(
        self, raw: RawTranscript, segments: Sequence[_TimedSegment]
    ) -> list[QAFinding]:
        term_ids = self.terminology.term_ids_in(raw.text)
        if not term_ids:
            return []
        return [
            QAFinding(
                type=FindingType.TERMINOLOGY_DETECTED,
                severity=Severity.INFO,
                summary=f"{len(term_ids)} Al Brooks term(s) detected",
                explanation=(
                    "Domain vocabulary was found and annotated so the translation phase "
                    "can apply approved Persian equivalents. Detection never changes the "
                    "source text."
                ),
                suggested_action="No action required; review during translation.",
                segment_ids=tuple(
                    segment.key for segment in segments if self.terminology.find(segment.text)
                ),
                evidence={"term_ids": term_ids},
            )
        ]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _prepare_segments(raw: RawTranscript) -> list[_TimedSegment]:
    prepared: list[_TimedSegment] = []
    for segment in raw.segments:
        start = _parse_timestamp(segment.start_raw)
        end = _parse_timestamp(segment.end_raw)
        prepared.append(
            _TimedSegment(
                key=segment.key,
                index=segment.index,
                text=segment.text,
                cleaned_text=clean_text(segment.text),
                start=start.value,
                end=end.value,
                start_valid=start.valid,
                end_valid=end.valid,
                start_raw_repr=start.raw_repr,
                end_raw_repr=end.raw_repr,
            )
        )
    return prepared


@dataclass(frozen=True)
class _Timestamp:
    value: float | None
    valid: bool
    raw_repr: Any


def _parse_timestamp(raw: Any) -> _Timestamp:
    """Parse a timestamp, reporting validity without ever inventing a value."""

    if raw is None:
        return _Timestamp(None, False, None)
    if isinstance(raw, bool):
        return _Timestamp(None, False, raw)
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value != value or value in (float("inf"), float("-inf")):
            return _Timestamp(None, False, raw)
        return _Timestamp(value, value >= 0, raw)
    if isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            return _Timestamp(None, False, raw)
        if value != value:
            return _Timestamp(None, False, raw)
        return _Timestamp(value, value >= 0, raw)
    return _Timestamp(None, False, raw)


def _range_of(segment: _TimedSegment) -> TimeRange | None:
    if segment.start is None or segment.end is None:
        return None
    return TimeRange(segment.start, segment.end)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _finding_sort_key(finding: QAFinding) -> tuple[int, float, str]:
    start = finding.time_range.start if finding.time_range else 0.0
    return (finding.severity.rank, start, str(finding.type))


def _collect_segment_flags(findings: Iterable[QAFinding]) -> dict[str, tuple[str, ...]]:
    flags: dict[str, list[str]] = {}
    for finding in findings:
        if finding.type is FindingType.TERMINOLOGY_DETECTED:
            continue
        for segment_id in finding.segment_ids:
            flags.setdefault(segment_id, []).append(str(finding.type))
    return {key: tuple(sorted(set(value))) for key, value in sorted(flags.items())}


def max_run_length(gram: tuple[str, ...]) -> int:
    """Length of the longest run of identical words inside a repeated phrase.

    ``"the the"`` is a filler artefact and should not force a wide window.
    """

    longest = 1
    current = 1
    for previous, word in zip(gram, gram[1:], strict=False):
        current = current + 1 if word == previous else 1
        longest = max(longest, current)
    return longest


def _extend_repetition(
    tokens: Sequence[str], positions: Sequence[int], minimum: int
) -> tuple[tuple[str, ...], list[int], int]:
    """Grow a repeated seed into the longest phrase that still repeats.

    The first two occurrences are compared and extended left and right for as
    long as their words agree; every other occurrence of the same seed is then
    re-aligned to that same extent.
    """

    first, second = positions[0], positions[1]

    left = 0
    while (
        first - left - 1 >= 0
        and second - left - 1 >= 0
        and tokens[first - left - 1] == tokens[second - left - 1]
    ):
        left += 1

    right = minimum
    while (
        first + right < len(tokens)
        and second + right < len(tokens)
        and tokens[first + right] == tokens[second + right]
    ):
        right += 1

    length = left + right
    aligned: list[int] = []
    for position in positions:
        start = position - left
        if start >= 0 and start + length <= len(tokens):
            aligned.append(start)
    if len(aligned) < 2:
        return (), [], 0
    return tuple(tokens[aligned[0] : aligned[0] + length]), aligned, length


def _maximal_phrases(
    candidates: Sequence[tuple[tuple[str, ...], list[int], int]],
) -> list[tuple[tuple[str, ...], list[int], int]]:
    """Keep the longest phrase for each distinct repetition.

    A single repeated passage is discovered from several overlapping seeds;
    they are collapsed by span overlap so that one repetition yields one
    finding.
    """

    ordered = sorted(candidates, key=lambda item: len(item[0]), reverse=True)
    accepted: list[tuple[tuple[str, ...], list[int], int]] = []
    accepted_spans: list[tuple[int, int]] = []

    for phrase, positions, _ in ordered:
        if not phrase:
            continue
        width = len(phrase)
        spans = [(position, position + width) for position in positions]
        if any(
            _span_overlap(spans[index], other) >= 0.5
            for index in range(len(spans))
            for other in accepted_spans
        ):
            continue
        accepted.append((phrase, positions, width))
        accepted_spans.extend(spans)

    return accepted


def _span_overlap(span: tuple[int, int], other: tuple[int, int]) -> float:
    """Fraction of ``span`` that falls inside ``other``."""

    start, end = span
    other_start, other_end = other
    overlap = min(end, other_end) - max(start, other_start)
    return overlap / max(1, end - start)


def _segments_covering(
    segments: Sequence[_TimedSegment], positions: Sequence[int]
) -> tuple[str, ...]:
    """Map word positions in the transcript back onto segment ids.

    Segment word counts are accumulated in transcript order, which matches the
    transcript because joining segments only normalises whitespace.
    """

    if not segments:
        return ()

    boundaries: list[tuple[str, int, int]] = []
    cumulative = 0
    for segment in segments:
        count = len(word_tokens(segment.text))
        boundaries.append((segment.key, cumulative, cumulative + count))
        cumulative += count

    ids: list[str] = []
    for position in positions:
        for key, start, end in boundaries:
            if start <= position < end:
                if key not in ids:
                    ids.append(key)
                break
    return tuple(ids)
