"""Tests for :mod:`src.verification` (Phase 2 optional cross-check).

All API access is mocked. Nothing here requires a real key or spends credits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.errors import TranscriptionError, VerificationUnavailableError
from src.qa import DuplicateGroup
from src.transcript import RawTranscript, load_raw_transcript
from src.transcription import (
    GroqTranscriber,
    TranscriptionResult,
    TranscriptionSegment,
)
from src.verification import (
    VerificationOutcome,
    VerificationStatus,
    build_alternative_segments,
    compare_with_alternative,
    merged_text_of,
    not_performed,
    summarize_for_review,
    verify_duplicate_groups,
)

from .conftest import (
    FakeFactory,
    known_duplicate_payload,
    make_transcriber,
)

DUP_TEXT = (
    "Sorry about being a couple of minutes late. The bulls see the 60 minute chart as forming"
)
UNIQUE_TEXT = "a wedge bull flag respecting the gap back here in August."


class FakeStatusError(Exception):
    def __init__(self, status_code: int, message: str = "api error") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "verify.flac"
    path.write_bytes(b"\x00" * 1024)
    return path


@pytest.fixture
def raw(tmp_path: Path) -> RawTranscript:
    path = tmp_path / "lecture_test_30s.en.json"
    path.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")
    return load_raw_transcript(path)


@pytest.fixture
def groups(raw: RawTranscript) -> tuple[DuplicateGroup, ...]:
    from src.qa import TranscriptQaAnalyzer

    report = TranscriptQaAnalyzer().analyze(raw)
    return report.duplicate_groups


def alternative(text: str, segments: list[tuple[float, float, str]]) -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        model="whisper-large-v3-turbo",
        language="en",
        duration_seconds=30.0,
        segments=tuple(
            TranscriptionSegment(id=index, start=start, end=end, text=segment_text)
            for index, (start, end, segment_text) in enumerate(segments)
        ),
    )


# ----------------------------------------------------------------------
# Comparison logic - the heart of the cautious recovery strategy
# ----------------------------------------------------------------------
def test_alternative_without_duplicate_contradicts_it(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...]
) -> None:
    alt = alternative(UNIQUE_TEXT, [(0.0, 30.0, UNIQUE_TEXT)])
    outcome = compare_with_alternative(raw, groups, alt)

    assert outcome.status is VerificationStatus.DUPLICATE_CONTRADICTED
    assert outcome.status.supports_correction is True


def test_alternative_with_duplicate_confirms_it(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...]
) -> None:
    """The real-world case: a second transcription repeats the same wording."""

    text = f"{DUP_TEXT} {DUP_TEXT} {UNIQUE_TEXT}"
    alt = alternative(
        text,
        [(0.0, 7.0, DUP_TEXT), (7.0, 23.0, DUP_TEXT), (23.0, 30.0, UNIQUE_TEXT)],
    )
    outcome = compare_with_alternative(raw, groups, alt)

    assert outcome.status is VerificationStatus.DUPLICATE_CONFIRMED
    assert outcome.status.supports_correction is False


def test_alternative_with_fewer_repetitions_contradicts(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...]
) -> None:
    alt = alternative(
        f"{DUP_TEXT} {UNIQUE_TEXT}",
        [(0.0, 7.0, DUP_TEXT), (7.0, 30.0, UNIQUE_TEXT)],
    )
    outcome = compare_with_alternative(raw, groups, alt)
    assert outcome.status is VerificationStatus.DUPLICATE_CONTRADICTED


def test_mixed_evidence_is_inconclusive(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...]
) -> None:
    other = DuplicateGroup(segment_ids=("2",), normalized_text="a wedge bull flag", exact=True)
    alt = alternative(
        f"{DUP_TEXT} {UNIQUE_TEXT}",
        [(0.0, 7.0, DUP_TEXT), (7.0, 30.0, UNIQUE_TEXT)],
    )
    outcome = compare_with_alternative(raw, (groups[0], other), alt)

    assert outcome.status is VerificationStatus.INCONCLUSIVE
    assert outcome.status.supports_correction is False


def test_empty_alternative_is_inconclusive(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...]
) -> None:
    outcome = compare_with_alternative(raw, groups, alternative("", []))
    assert outcome.status is VerificationStatus.INCONCLUSIVE


def test_evidence_records_counts(raw: RawTranscript, groups: tuple[DuplicateGroup, ...]) -> None:
    alt = alternative(
        f"{DUP_TEXT} {DUP_TEXT} {UNIQUE_TEXT}",
        [(0.0, 7.0, DUP_TEXT), (7.0, 23.0, DUP_TEXT), (23.0, 30.0, UNIQUE_TEXT)],
    )
    outcome = compare_with_alternative(raw, groups, alt)
    entry = outcome.evidence["groups"][0]

    assert entry["occurrences_in_original"] == 2
    assert entry["occurrences_in_alternative"] == 2
    assert entry["verdict"] == "present_in_alternative"
    assert entry["alternative_has_duplicate_segments"] is True


def test_outcome_serialisation(raw: RawTranscript, groups: tuple[DuplicateGroup, ...]) -> None:
    alt = alternative(UNIQUE_TEXT, [(0.0, 30.0, UNIQUE_TEXT)])
    payload = compare_with_alternative(raw, groups, alt).to_dict()

    assert payload["performed"] is True
    assert payload["status"] == "duplicate_contradicted"
    assert payload["supports_correction"] is True
    assert payload["method"] == "grok_retranscription"


def test_not_performed_is_not_a_verification_claim() -> None:
    outcome = not_performed("not requested")
    assert outcome.status is VerificationStatus.NOT_PERFORMED
    assert outcome.to_dict()["performed"] is False
    assert outcome.to_dict()["supports_correction"] is False


# ----------------------------------------------------------------------
# Bounded retries against a mocked API
# ----------------------------------------------------------------------
def test_verification_succeeds_with_mocked_api(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...], audio: Path, groq_factory: FakeFactory
) -> None:
    transcriber = make_transcriber(
        groq_factory, responses=[{"text": UNIQUE_TEXT, "language": "en"}]
    )
    outcome = verify_duplicate_groups(raw, groups, audio, transcriber, max_attempts=2)

    assert outcome.status is VerificationStatus.DUPLICATE_CONTRADICTED
    assert len(groq_factory.calls) == 1
    assert len(groq_factory.last_upload) == audio.stat().st_size


def test_verification_retries_are_bounded(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...], audio: Path, groq_factory: FakeFactory
) -> None:
    groq_factory.responses = [FakeStatusError(500, "boom")] * 10
    transcriber = make_transcriber(groq_factory, max_retries=1)

    outcome = verify_duplicate_groups(raw, groups, audio, transcriber, max_attempts=3)

    assert outcome.status is VerificationStatus.FAILED
    assert outcome.attempts == 3
    assert len(groq_factory.calls) == 3


def test_verification_succeeds_after_transient_failure(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...], audio: Path, groq_factory: FakeFactory
) -> None:
    groq_factory.responses = [
        FakeStatusError(503, "unavailable"),
        {"text": UNIQUE_TEXT, "language": "en"},
    ]
    transcriber = make_transcriber(groq_factory, max_retries=1)
    outcome = verify_duplicate_groups(raw, groups, audio, transcriber, max_attempts=3)

    assert outcome.status is VerificationStatus.DUPLICATE_CONTRADICTED
    assert len(groq_factory.calls) == 2


def test_auth_failure_during_verification_is_reported_not_raised(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...], audio: Path, groq_factory: FakeFactory
) -> None:
    groq_factory.responses = [FakeStatusError(401, "invalid api key")] * 5
    transcriber = make_transcriber(groq_factory, max_retries=1)

    outcome = verify_duplicate_groups(raw, groups, audio, transcriber, max_attempts=2)

    assert outcome.status is VerificationStatus.FAILED
    assert "401" in outcome.detail
    assert outcome.evidence["retryable"] is False
    assert len(groq_factory.calls) == 1  # permanent error never retried
    assert outcome.attempts == 1


def test_no_groups_means_no_api_call(
    raw: RawTranscript, audio: Path, groq_factory: FakeFactory
) -> None:
    transcriber = make_transcriber(groq_factory, responses=[{"text": "x"}])
    outcome = verify_duplicate_groups(raw, (), audio, transcriber)

    assert outcome.status is VerificationStatus.NOT_PERFORMED
    assert groq_factory.calls == []


def test_missing_audio_raises(
    tmp_path: Path,
    raw: RawTranscript,
    groups: tuple[DuplicateGroup, ...],
    groq_factory: FakeFactory,
) -> None:
    transcriber = make_transcriber(groq_factory, responses=[{"text": "x"}])
    with pytest.raises(VerificationUnavailableError, match="not found"):
        verify_duplicate_groups(raw, groups, tmp_path / "ghost.flac", transcriber)


# ----------------------------------------------------------------------
# Reporting helpers
# ----------------------------------------------------------------------
def test_alternative_segments_are_exposed(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...]
) -> None:
    alt = alternative(UNIQUE_TEXT, [(0.0, 10.0, "part one"), (10.0, 30.0, "part two")])
    outcome = compare_with_alternative(raw, groups, alt)
    segments = build_alternative_segments(outcome)

    assert segments is not None
    assert len(segments) == 2
    assert segments[0]["start"] == 0.0


def test_alternative_segments_absent_when_not_performed() -> None:
    assert build_alternative_segments(not_performed("no")) is None


def test_summary_for_review_lists_groups() -> None:
    group = DuplicateGroup(segment_ids=("0", "1"), normalized_text=DUP_TEXT.lower(), exact=True)
    summary = summarize_for_review([group])
    assert "segments 0, 1" in summary
    assert "Listen to the audio" in summary


def test_summary_for_review_without_groups() -> None:
    assert summarize_for_review(()) == "No duplicated text requires review."


def test_merged_text_of_alternative() -> None:
    alt = alternative("a b", [(0.0, 5.0, "a"), (5.0, 10.0, "b")])
    assert merged_text_of(alt) == "a b"


def test_verification_never_claims_success_without_a_response(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...]
) -> None:
    """An empty alternative must not be treated as a correction."""

    outcome = compare_with_alternative(raw, groups, alternative("   ", []))
    assert outcome.status.supports_correction is False
    assert "no text" in outcome.detail


def test_outcome_repr_excludes_alternative(groq_factory: FakeFactory) -> None:
    outcome = VerificationOutcome(
        status=VerificationStatus.INCONCLUSIVE,
        method="m",
        detail="d",
        alternative=alternative("x", []),
    )
    assert "alternative" not in repr(outcome)


def test_transcriber_model_is_recorded(
    raw: RawTranscript, groups: tuple[DuplicateGroup, ...], audio: Path, groq_factory: FakeFactory
) -> None:
    transcriber = make_transcriber(
        groq_factory, responses=[{"text": UNIQUE_TEXT}], model="whisper-large-v3"
    )
    outcome = verify_duplicate_groups(raw, groups, audio, transcriber)
    assert outcome.model == "whisper-large-v3"


def test_transcriber_requires_api_key() -> None:
    with pytest.raises(TranscriptionError, match="non-empty Groq API key"):
        GroqTranscriber(api_key="", model="whisper-large-v3-turbo")
