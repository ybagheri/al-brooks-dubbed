"""Tests for :mod:`src.preparation` (Phase 2 orchestration).

The Groq client is always mocked, so no test needs a real key.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from src.errors import OutputExistsError, TranscriptFormatError
from src.outputs import PreparedPaths
from src.preparation import (
    CONFIDENCE_MISSING,
    CONFIDENCE_RELIABLE,
    STATUS_UNCERTAIN,
    STATUS_VERIFIED,
    TranscriptPreparer,
    render_preparation_report,
)
from src.qa import FindingType, Severity
from src.transcription import GroqTranscriber
from src.verification import VerificationStatus

from .conftest import (
    KNOWN_DUPLICATE_SEGMENTS,
    KNOWN_DUPLICATE_TRANSCRIPT,
    known_duplicate_payload,
)
from .test_verification import DUP_TEXT, UNIQUE_TEXT, FakeStatusError, alternative


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audio_path_for(raw_path: Path) -> Path:
    """Path the preparer looks for when an audio export sits beside the artifact."""

    return raw_path.with_name(f"{raw_path.stem}.flac")


def make_preparer(
    config_factory: Any,
    data_dir: Path,
    groq_factory: Any = None,
    **config_kwargs: Any,
) -> TranscriptPreparer:
    config = config_factory(data_dir=data_dir, **config_kwargs)
    transcriber = None
    if groq_factory is not None:
        transcriber = GroqTranscriber(
            api_key=config.api_key,
            model=config.model,
            language="en",
            max_retries=1,
            backoff_seconds=0.0,
            max_backoff_seconds=0.0,
            client_factory=groq_factory,
            sleep=lambda _s: None,
        )
    return TranscriptPreparer(config, transcriber=transcriber)


# ----------------------------------------------------------------------
# Output paths
# ----------------------------------------------------------------------
def test_prepared_paths_from_raw() -> None:
    paths = PreparedPaths.from_raw(Path("output/lecture_test_30s.en.json"))
    assert paths.cleaned_text.name == "lecture_test_30s.en.clean.txt"
    assert paths.cleaned_json.name == "lecture_test_30s.en.clean.json"
    assert paths.qa_json.name == "lecture_test_30s.qa.json"
    assert paths.cleaned_text.parent == Path("output")


def test_prepared_paths_windows_path() -> None:
    paths = PreparedPaths.from_raw(Path(r"D:\proj\output\lecture_test_30s.en.json"))
    assert paths.qa_json == Path(r"D:\proj\output\lecture_test_30s.qa.json")


def test_prepared_paths_without_en_tag() -> None:
    paths = PreparedPaths.from_raw(Path("out/plain.json"))
    assert paths.qa_json.name == "plain.qa.json"


def test_prepared_paths_existing_and_dict(tmp_path: Path) -> None:
    paths = PreparedPaths.from_raw(tmp_path / "a.en.json")
    assert paths.existing() == []
    paths.qa_json.write_text("{}", encoding="utf-8")
    assert paths.existing() == [paths.qa_json]
    assert set(paths.as_dict()) == {"cleaned_transcript", "prepared_transcript", "qa_report"}


# ----------------------------------------------------------------------
# Raw artifact preservation
# ----------------------------------------------------------------------
def test_raw_files_are_never_modified(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    before_json = digest(raw_path)
    before_text = raw_path.with_name(raw_path.name.replace(".en.json", ".en.txt"))
    before_text.write_text(KNOWN_DUPLICATE_TRANSCRIPT, encoding="utf-8")
    before_text_digest = digest(before_text)

    preparer = make_preparer(config_factory, tmp_path)
    preparer.prepare(raw_path)

    assert digest(raw_path) == before_json
    assert digest(before_text) == before_text_digest


def test_duplicate_is_preserved_without_verification(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    """The central conservative guarantee: no speech is dropped on suspicion."""

    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)

    cleaned = result.paths.cleaned_text.read_text(encoding="utf-8")
    assert cleaned.count("Sorry about being a couple of minutes late") == 2
    assert len(result.clean.segments) == 3
    assert result.verification.status is VerificationStatus.NOT_PERFORMED


def test_uncertain_segments_are_marked(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)

    assert result.clean.segments[0].correction_status == STATUS_UNCERTAIN
    assert result.clean.segments[1].correction_status == STATUS_UNCERTAIN
    assert result.clean.segments[2].correction_status == "unchanged"
    assert str(FindingType.EXACT_DUPLICATE_SEGMENT) in result.clean.segments[0].quality_flags


def test_status_is_needs_review(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    assert result.status == "needs_review"
    assert result.needs_review is True


def test_review_notes_mention_the_duplicates(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    joined = "\n".join(result.review_notes)
    assert "segments 0, 1" in joined
    assert "Listen to the audio" in joined


# ----------------------------------------------------------------------
# Artifacts and schema
# ----------------------------------------------------------------------
def test_all_three_artifacts_are_written(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)

    for path in result.paths.all:
        assert path.is_file()
        assert path.stat().st_size > 0


def test_cleaned_json_schema(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    payload = json.loads(result.paths.cleaned_json.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["artifact_type"] == "prepared_transcript"
    assert payload["source_artifact"] == str(raw_path)
    assert payload["transcription_model"] == "whisper-large-v3-turbo"
    assert payload["raw_transcript"] == KNOWN_DUPLICATE_TRANSCRIPT
    assert payload["cleaned_transcript"]
    assert payload["processed_duration_seconds"] == pytest.approx(30.0)
    assert payload["generated_at"].endswith("Z")


def test_cleaned_segment_fields(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    payload = json.loads(result.paths.cleaned_json.read_text(encoding="utf-8"))

    segment = payload["segments"][0]
    for field in (
        "segment_id",
        "source_segment_ids",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
        "raw_text",
        "cleaned_text",
        "quality_flags",
        "correction_status",
        "timestamp_confidence",
        "terminology",
    ):
        assert field in segment

    assert segment["segment_id"] == "0"
    assert segment["source_segment_ids"] == ["0"]
    assert segment["start_seconds"] == 0.0
    assert segment["end_seconds"] == pytest.approx(7.0)
    assert segment["timestamp_confidence"] == CONFIDENCE_RELIABLE
    assert segment["raw_text"] == KNOWN_DUPLICATE_SEGMENTS[0]["text"]


def test_cleaned_json_keeps_raw_transcript_verbatim(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    payload = json.loads(result.paths.cleaned_json.read_text(encoding="utf-8"))
    assert payload["raw_transcript"] == KNOWN_DUPLICATE_TRANSCRIPT


def test_qa_report_schema(tmp_path: Path, config_factory: Any, write_raw_transcript: Any) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    payload = json.loads(result.paths.qa_json.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["artifact_type"] == "transcript_quality_report"
    assert payload["summary"]["total_findings"] > 0
    assert payload["summary"]["needs_review"] is True
    assert payload["duplicate_groups"]
    assert payload["segment_flags"]
    assert payload["severity_scale"] == ["critical", "high", "medium", "low", "info"]

    types = {finding["type"] for finding in payload["findings"]}
    assert str(FindingType.EXACT_DUPLICATE_SEGMENT) in types
    assert str(FindingType.DUPLICATE_DURATION_MISMATCH) in types
    assert str(FindingType.SEGMENT_TOO_LONG) in types


def test_no_confidence_scores_are_invented(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    body = result.paths.qa_json.read_text(encoding="utf-8")
    assert "confidence_score" not in body
    assert '"confidence"' not in body


def test_terminology_is_annotated_on_segments(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    payload = json.loads(result.paths.cleaned_json.read_text(encoding="utf-8"))

    ids = payload["terminology_summary"]["term_ids_detected"]
    assert "wedge_bull_flag" in ids
    assert "gap" in ids
    assert payload["segments"][2]["terminology"]


def test_cleaning_summary_states_no_rewriting(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    cleaning = result.clean.cleaning

    assert cleaning["rewrote_words"] is False
    assert cleaning["grammar_corrected"] is False
    assert cleaning["translated"] is False
    assert cleaning["removed_content"] is False
    assert cleaning["unexpected_new_words"] == []
    assert cleaning["raw_word_count"] == cleaning["cleaned_word_count"]


# ----------------------------------------------------------------------
# Overwrite protection
# ----------------------------------------------------------------------
def test_existing_prepared_output_is_protected(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    paths = PreparedPaths.from_raw(raw_path)
    paths.cleaned_json.write_text("{}", encoding="utf-8")

    with pytest.raises(OutputExistsError, match="--overwrite"):
        make_preparer(config_factory, tmp_path).prepare(raw_path)


def test_overwrite_replaces_prepared_output(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(known_duplicate_payload())
    paths = PreparedPaths.from_raw(raw_path)
    paths.cleaned_json.write_text("{}", encoding="utf-8")

    result = make_preparer(config_factory, tmp_path, overwrite=True).prepare(raw_path)
    assert result.paths.cleaned_json.read_text(encoding="utf-8") != "{}"


def test_failed_run_does_not_leave_partial_prepared_output(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    """A schema error must not leave half-written cleaned artifacts behind."""

    bad = known_duplicate_payload()
    bad["segments"] = ["not an object"]
    raw_path = write_raw_transcript(bad)

    with pytest.raises(TranscriptFormatError):
        make_preparer(config_factory, tmp_path).prepare(raw_path)

    assert not PreparedPaths.from_raw(raw_path).cleaned_json.exists()


# ----------------------------------------------------------------------
# Timestamp handling
# ----------------------------------------------------------------------
def test_missing_timestamps_are_marked_not_invented(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    payload = known_duplicate_payload(
        segments=[{"id": 0, "start": None, "end": None, "text": "untimed speech"}]
    )
    raw_path = write_raw_transcript(payload)
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)

    segment = result.clean.segments[0]
    assert segment.start_seconds is None
    assert segment.end_seconds is None
    assert segment.timestamp_confidence == CONFIDENCE_MISSING
    assert str(FindingType.INVALID_TIMESTAMP) in segment.quality_flags


def test_no_segments_keeps_the_whole_transcript(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    payload = known_duplicate_payload(segments=None, transcript="just some words")
    raw_path = write_raw_transcript(payload)
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)

    assert len(result.clean.segments) == 1
    segment = result.clean.segments[0]
    assert segment.cleaned_text == "just some words"
    assert segment.timestamp_confidence == CONFIDENCE_MISSING
    assert "no_segments" in segment.quality_flags


# ----------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------
def _dup_payload() -> dict[str, Any]:
    return known_duplicate_payload()


def test_verification_not_requested_makes_no_api_call(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(_dup_payload())
    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(raw_path)

    assert groq_factory.calls == []
    assert result.verification.status is VerificationStatus.NOT_PERFORMED
    assert result.verification.to_dict()["performed"] is False


def test_verification_preserves_when_duplicate_confirmed(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    text = f"{DUP_TEXT} {DUP_TEXT} {UNIQUE_TEXT}"
    groq_factory.responses = [
        {
            "text": text,
            "language": "en",
            "segments": [
                {"id": 0, "start": 0.0, "end": 7.0, "text": DUP_TEXT},
                {"id": 1, "start": 7.0, "end": 23.0, "text": DUP_TEXT},
                {"id": 2, "start": 23.0, "end": 30.0, "text": UNIQUE_TEXT},
            ],
        }
    ]
    raw_path = write_raw_transcript(_dup_payload())
    audio = audio_path_for(raw_path)
    audio.write_bytes(b"\x00" * 512)

    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(
        raw_path, verify=True, audio_path=audio
    )

    assert result.verification.status is VerificationStatus.DUPLICATE_CONFIRMED
    assert len(result.clean.segments) == 3
    assert (
        result.paths.cleaned_text.read_text(encoding="utf-8").count(
            "Sorry about being a couple of minutes late"
        )
        == 2
    )


def test_verification_merges_when_contradicted(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    """Positive evidence may justify collapsing the duplicate."""

    groq_factory.responses = [{"text": UNIQUE_TEXT, "language": "en"}]
    raw_path = write_raw_transcript(_dup_payload())
    audio = audio_path_for(raw_path)
    audio.write_bytes(b"\x00" * 512)

    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(
        raw_path, verify=True, audio_path=audio
    )

    assert result.verification.status is VerificationStatus.DUPLICATE_CONTRADICTED
    assert len(result.clean.segments) == 2  # 0 and 1 merged
    merged = result.clean.segments[0]
    assert merged.source_segment_ids == ("0", "1")
    assert merged.segment_id == "0-1"
    assert merged.correction_status == STATUS_VERIFIED
    assert "merged_verbatim" in merged.quality_flags


def test_verification_without_duplicates_makes_no_api_call(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    payload = {
        "schema_version": 1,
        "transcription_model": "whisper-large-v3-turbo",
        "requested_language": "en",
        "processed_duration_seconds": 20.0,
        "transcript": "the bulls are in control of this market today",
        "segments": [
            {"id": 0, "start": 0.0, "end": 10.0, "text": "the bulls are in control"},
            {"id": 1, "start": 10.0, "end": 20.0, "text": "of this market today"},
        ],
    }
    raw_path = write_raw_transcript(payload)
    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(raw_path, verify=True)

    assert groq_factory.calls == []
    assert result.verification.status is VerificationStatus.NOT_PERFORMED


def test_verification_failure_is_reported_and_preserves(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    groq_factory.responses = [FakeStatusError(401, "invalid key")] * 3
    raw_path = write_raw_transcript(_dup_payload())
    audio = audio_path_for(raw_path)
    audio.write_bytes(b"\x00" * 512)

    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(
        raw_path, verify=True, audio_path=audio
    )

    assert result.verification.status is VerificationStatus.FAILED
    assert len(result.clean.segments) == 3
    assert result.status == "needs_review"


def test_missing_audio_is_reported_clearly(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    """Without --audio and without a recoverable source, say exactly what is missing."""

    payload = known_duplicate_payload()
    payload.pop("source_path")
    payload.pop("source_file")
    raw_path = write_raw_transcript(payload)

    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(raw_path, verify=True)

    assert result.verification.status is VerificationStatus.INCONCLUSIVE
    assert result.warnings
    warning = "\n".join(result.warnings)
    assert "--audio" in warning
    assert "ffmpeg" in warning.lower() or "source video" in warning.lower()


def test_missing_audio_file_is_reported(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(_dup_payload())
    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(
        raw_path, verify=True, audio_path=tmp_path / "ghost.flac"
    )
    assert "--audio does not exist" in "\n".join(result.warnings)


def test_verification_uses_audio_beside_artifact(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(_dup_payload())
    audio_path_for(raw_path).write_bytes(b"\x00" * 512)
    groq_factory.responses = [{"text": UNIQUE_TEXT, "language": "en"}]

    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(raw_path, verify=True)
    assert result.verification.status is VerificationStatus.DUPLICATE_CONTRADICTED


def test_verification_attempts_are_bounded(
    tmp_path: Path, config_factory: Any, groq_factory: Any, write_raw_transcript: Any
) -> None:
    groq_factory.responses = [FakeStatusError(503, "unavailable")] * 6
    raw_path = write_raw_transcript(_dup_payload())
    audio = audio_path_for(raw_path)
    audio.write_bytes(b"\x00" * 512)

    result = make_preparer(config_factory, tmp_path, groq_factory).prepare(
        raw_path, verify=True, audio_path=audio, verify_attempts=2
    )
    assert result.verification.attempts == 2
    assert len(groq_factory.calls) == 2


# ----------------------------------------------------------------------
# Report rendering
# ----------------------------------------------------------------------
def test_render_preparation_report(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    raw_path = write_raw_transcript(_dup_payload())
    result = make_preparer(config_factory, tmp_path).prepare(raw_path)
    report = render_preparation_report(result)

    assert "Phase 2 preparation complete" in report
    assert "NEEDS_REVIEW" in report
    assert str(result.paths.qa_json) in report
    assert "Review needed" in report


def test_alternative_helper_is_available() -> None:
    """Keeps the shared helper import meaningful for readers of this module."""

    assert alternative("x", [(0.0, 1.0, "x")]).text == "x"


def test_severity_enum_is_shared() -> None:
    assert Severity.HIGH.rank == 1
