"""Tests for :mod:`src.transcription`.

The Groq API is fully mocked: no test requires a real key or spends credits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.errors import (
    EmptyTranscriptError,
    TranscriptionAuthError,
    TranscriptionError,
    TranscriptionSizeError,
)
from src.logging_utils import get_redacting_filter, register_secret
from src.transcription import GroqTranscriber

from .conftest import FakeFactory, make_transcriber

SEGMENTED_RESPONSE: dict[str, Any] = {
    "text": "Good morning. Today we look at price action.",
    "language": "en",
    "duration": 5.0,
    "segments": [
        {"id": 0, "start": 0.0, "end": 1.5, "text": "Good morning."},
        {"id": 1, "start": 1.5, "end": 5.0, "text": "Today we look at price action."},
    ],
}


def make_audio(tmp_path: Path, size: int = 2048) -> Path:
    audio = tmp_path / "clip.flac"
    audio.write_bytes(b"\x00" * size)
    return audio


# ----------------------------------------------------------------------
# Happy paths
# ----------------------------------------------------------------------
def test_transcribe_returns_text_and_segments(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[dict(SEGMENTED_RESPONSE)])
    result = transcriber.transcribe(make_audio(tmp_path))

    assert result.text == SEGMENTED_RESPONSE["text"]
    assert result.language == "en"
    assert result.duration_seconds == 5.0
    assert len(result.segments) == 2
    assert result.segments[1].start == 1.5
    assert result.has_segments


def test_request_uses_transcription_endpoint_options(
    tmp_path: Path, groq_factory: FakeFactory
) -> None:
    transcriber = make_transcriber(groq_factory, responses=[dict(SEGMENTED_RESPONSE)])
    transcriber.transcribe(make_audio(tmp_path))

    request = groq_factory.last_request
    assert request["model"] == "whisper-large-v3-turbo"
    assert request["response_format"] == "verbose_json"
    assert request["language"] == "en"  # English is requested explicitly
    assert request["temperature"] == 0
    assert request["timestamp_granularities"] == ["segment"]
    # the audio is uploaded as a (filename, file object) tuple
    filename, handle = request["file"]
    assert filename.endswith(".flac")
    assert hasattr(handle, "read")


def test_api_key_is_passed_to_client_factory(tmp_path: Path) -> None:
    factory = FakeFactory([dict(SEGMENTED_RESPONSE)])
    key = "gsk_testONLYnotarealkey0000000000000000"
    transcriber = GroqTranscriber(
        api_key=key, model="whisper-large-v3-turbo", client_factory=factory
    )
    transcriber.transcribe(make_audio(tmp_path))
    assert factory.keys == [key]


def test_wording_is_preserved_verbatim(tmp_path: Path, groq_factory: FakeFactory) -> None:
    """The client must not normalise, summarise or correct the transcript."""

    raw = "Um, so what I mean is-- it's basically, you know, a gap, right?"
    transcriber = make_transcriber(groq_factory, responses=[{"text": raw, "language": "en"}])
    result = transcriber.transcribe(make_audio(tmp_path))
    assert result.text == raw


def test_response_without_segments(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(
        groq_factory, responses=[{"text": "Hello there.", "language": "en"}]
    )
    result = transcriber.transcribe(make_audio(tmp_path))
    assert result.segments == ()
    assert result.has_segments is False
    # timestamps are not invented
    assert result.to_metadata()["segments"] is None


def test_string_response_is_accepted(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=["plain text transcript"])
    result = transcriber.transcribe(make_audio(tmp_path))
    assert result.text == "plain text transcript"


def test_object_response_is_accepted(tmp_path: Path, groq_factory: FakeFactory) -> None:
    class PydanticLike:
        def model_dump(self) -> dict[str, Any]:
            return {"text": "From an object.", "language": "en"}

    transcriber = make_transcriber(groq_factory, responses=[PydanticLike()])
    assert transcriber.transcribe(make_audio(tmp_path)).text == "From an object."


def test_malformed_segments_are_skipped(tmp_path: Path, groq_factory: FakeFactory) -> None:
    payload = {
        "text": "Mixed quality.",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "good"},
            {"start": "x", "end": 2.0, "text": "bad start"},
            {"start": 1.0, "end": None, "text": "bad end"},
            {"start": 1.0, "end": 2.0, "text": "good too"},
        ],
    }
    transcriber = make_transcriber(groq_factory, responses=[payload])
    result = transcriber.transcribe(make_audio(tmp_path))
    assert len(result.segments) == 2


# ----------------------------------------------------------------------
# Failures
# ----------------------------------------------------------------------
def test_missing_audio_file(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[dict(SEGMENTED_RESPONSE)])
    with pytest.raises(TranscriptionError, match="does not exist"):
        transcriber.transcribe(tmp_path / "ghost.flac")


def test_oversized_audio_is_rejected_explicitly(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[], max_audio_bytes=1024)
    audio = make_audio(tmp_path, size=4096)
    with pytest.raises(TranscriptionSizeError) as excinfo:
        transcriber.transcribe(audio)
    assert "--duration" in str(excinfo.value)
    # audio was never silently truncated or sent
    assert groq_factory.calls == []


def test_empty_response_object(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[{}])
    with pytest.raises(EmptyTranscriptError, match="without any content"):
        transcriber.transcribe(make_audio(tmp_path))


def test_response_without_text_field(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[{"language": "en"}])
    with pytest.raises(EmptyTranscriptError, match="no transcript text"):
        transcriber.transcribe(make_audio(tmp_path))


def test_whitespace_only_response(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[{"text": "   \n  "}])
    with pytest.raises(EmptyTranscriptError):
        transcriber.transcribe(make_audio(tmp_path))


def test_none_response(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[None])
    with pytest.raises(EmptyTranscriptError):
        transcriber.transcribe(make_audio(tmp_path))


def test_empty_api_key_rejected(groq_factory: FakeFactory) -> None:
    with pytest.raises(TranscriptionError, match="non-empty Groq API key"):
        GroqTranscriber(api_key="  ", model="whisper-large-v3-turbo")


# ----------------------------------------------------------------------
# Error classification / retries
# ----------------------------------------------------------------------
class FakeStatusError(Exception):
    """Mimics ``groq.APIStatusError`` closely enough for the classifier."""

    def __init__(self, status_code: int, message: str = "api error") -> None:
        super().__init__(message)
        self.status_code = status_code


def test_authentication_error_is_not_retried(tmp_path: Path, groq_factory: FakeFactory) -> None:
    responses = [FakeStatusError(401, "invalid api key")] * 5
    transcriber = make_transcriber(groq_factory, responses=responses, max_retries=5)

    with pytest.raises(TranscriptionAuthError) as excinfo:
        transcriber.transcribe(make_audio(tmp_path))

    assert len(groq_factory.calls) == 1  # exactly one attempt
    assert "not retried" in str(excinfo.value)


def test_permission_error_is_not_retried(tmp_path: Path, groq_factory: FakeFactory) -> None:
    responses = [FakeStatusError(403, "forbidden")] * 4
    transcriber = make_transcriber(groq_factory, responses=responses, max_retries=4)
    with pytest.raises(TranscriptionAuthError):
        transcriber.transcribe(make_audio(tmp_path))
    assert len(groq_factory.calls) == 1


def test_bad_request_is_not_retried(tmp_path: Path, groq_factory: FakeFactory) -> None:
    responses = [FakeStatusError(400, "unknown model")] * 3
    transcriber = make_transcriber(groq_factory, responses=responses, max_retries=3)
    with pytest.raises(TranscriptionError, match="permanent"):
        transcriber.transcribe(make_audio(tmp_path))
    assert len(groq_factory.calls) == 1


def test_payload_too_large_is_not_retried(tmp_path: Path, groq_factory: FakeFactory) -> None:
    responses = [FakeStatusError(413, "file too large")] * 3
    transcriber = make_transcriber(groq_factory, responses=responses, max_retries=3)
    with pytest.raises(TranscriptionError, match="413"):
        transcriber.transcribe(make_audio(tmp_path))
    assert len(groq_factory.calls) == 1


def test_rate_limit_is_retried_then_succeeds(tmp_path: Path, groq_factory: FakeFactory) -> None:
    responses = [
        FakeStatusError(429, "rate limit exceeded"),
        FakeStatusError(500, "internal server error"),
        dict(SEGMENTED_RESPONSE),
    ]
    transcriber = make_transcriber(groq_factory, responses=responses, max_retries=3)

    result = transcriber.transcribe(make_audio(tmp_path))
    assert result.text == SEGMENTED_RESPONSE["text"]
    assert len(groq_factory.calls) == 3


def test_retries_are_bounded(tmp_path: Path, groq_factory: FakeFactory) -> None:
    responses = [FakeStatusError(503, "unavailable")] * 10
    transcriber = make_transcriber(groq_factory, responses=responses, max_retries=3)
    with pytest.raises(TranscriptionError, match="after 3 attempt"):
        transcriber.transcribe(make_audio(tmp_path))
    assert len(groq_factory.calls) == 3


def test_retryable_errors_use_backoff(tmp_path: Path, groq_factory: FakeFactory) -> None:
    delays: list[float] = []
    responses = [FakeStatusError(429, "slow down"), dict(SEGMENTED_RESPONSE)]
    transcriber = GroqTranscriber(
        api_key="gsk_testONLYnotarealkey0000000000000000",
        model="whisper-large-v3-turbo",
        max_retries=3,
        backoff_seconds=1.0,
        max_backoff_seconds=30.0,
        client_factory=groq_factory,
        sleep=delays.append,
    )
    groq_factory.responses = responses

    transcriber.transcribe(make_audio(tmp_path))
    assert delays == [1.0]


def test_backoff_is_exponential_and_capped(tmp_path: Path, groq_factory: FakeFactory) -> None:
    delays: list[float] = []
    groq_factory.responses = [FakeStatusError(500, "boom")] * 6
    transcriber = GroqTranscriber(
        api_key="gsk_testONLYnotarealkey0000000000000000",
        model="whisper-large-v3-turbo",
        max_retries=5,
        backoff_seconds=2.0,
        max_backoff_seconds=4.0,
        client_factory=groq_factory,
        sleep=delays.append,
    )
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(make_audio(tmp_path))
    assert delays == [2.0, 4.0, 4.0, 4.0]


def test_connection_error_is_retryable(tmp_path: Path, groq_factory: FakeFactory) -> None:
    class ConnectionError_(Exception):
        pass

    groq_factory.responses = [ConnectionError_("connection reset"), dict(SEGMENTED_RESPONSE)]
    transcriber = make_transcriber(groq_factory, max_retries=2)
    assert transcriber.transcribe(make_audio(tmp_path)).text


# ----------------------------------------------------------------------
# Secret hygiene
# ----------------------------------------------------------------------
def test_api_key_never_appears_in_error_messages(tmp_path: Path, groq_factory: FakeFactory) -> None:
    secret = "gsk_realtestingsecret0123456789abcdef"
    groq_factory.responses = [FakeStatusError(500, f"failed using key {secret}")]
    transcriber = GroqTranscriber(
        api_key=secret,
        model="whisper-large-v3-turbo",
        max_retries=1,
        client_factory=groq_factory,
        sleep=lambda _s: None,
    )
    with pytest.raises(TranscriptionError) as excinfo:
        transcriber.transcribe(make_audio(tmp_path))
    assert secret not in str(excinfo.value)


def test_redacting_filter_scrubs_registered_and_lookalike_secrets() -> None:
    register_secret("my-plain-secret")
    scrubber = get_redacting_filter()
    text = scrubber.scrub("key=my-plain-secret other=gsk_abcdefghijklmnopqrstuvwx")
    assert "my-plain-secret" not in text
    assert "gsk_abcdefghijklmnopqrstuvwx" not in text
    assert "REDACTED" in text


def test_metadata_contains_only_api_fields(tmp_path: Path, groq_factory: FakeFactory) -> None:
    transcriber = make_transcriber(groq_factory, responses=[dict(SEGMENTED_RESPONSE)])
    metadata = transcriber.transcribe(make_audio(tmp_path)).to_metadata()

    assert metadata["transcription_model"] == "whisper-large-v3-turbo"
    assert set(metadata) == {
        "transcription_model",
        "requested_language",
        "transcript",
        "api_reported_duration_seconds",
        "segments",
    }
    # no fabricated confidence scores
    assert "confidence" not in json.dumps(metadata)
