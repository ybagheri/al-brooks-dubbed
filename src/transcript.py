"""Transcript domain model for Phase 2.

Two shapes exist:

* :class:`RawTranscript` - the *unmodified* Phase 1 artifact, loaded from
  ``*.en.json``. It is the source of truth for what the API returned and is
  never rewritten.
* :class:`CleanTranscript` - the prepared, translation-ready artifact written
  to ``*.en.clean.json`` (schema version 2).

Every cleaned segment keeps a link back to the raw segment(s) it came from, so
a later translation stage can always reconcile its output against the audio.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import TranscriptFormatError

logger = logging.getLogger(__name__)

#: Schema version of the cleaned transcript produced by Phase 2.
CLEAN_SCHEMA_VERSION: int = 2

#: Schema version of the QA report.
QA_SCHEMA_VERSION: int = 1

SUPPORTED_RAW_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


@dataclass(frozen=True)
class RawSegment:
    """A segment exactly as returned by the transcription API."""

    index: int
    id: int | str | None
    start_raw: Any
    end_raw: Any
    text: str
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        """Stable identifier used across reports and cleaned artifacts."""

        return str(self.id) if self.id is not None else str(self.index)


@dataclass(frozen=True)
class RawTranscript:
    """The parsed Phase 1 transcription artifact."""

    path: Path
    schema_version: int | None
    text: str
    segments: tuple[RawSegment, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_file(self) -> str | None:
        value = self.metadata.get("source_file")
        return str(value) if isinstance(value, str) else None

    @property
    def source_path(self) -> Path | None:
        value = self.metadata.get("source_path")
        if isinstance(value, str) and value:
            candidate = Path(value)
            if candidate.exists():
                return candidate
        return None

    @property
    def model(self) -> str | None:
        value = self.metadata.get("transcription_model")
        return str(value) if isinstance(value, str) else None

    @property
    def processed_duration_seconds(self) -> float | None:
        return _as_float(self.metadata.get("processed_duration_seconds"))

    @property
    def api_reported_duration_seconds(self) -> float | None:
        return _as_float(self.metadata.get("api_reported_duration_seconds"))

    @property
    def language(self) -> str | None:
        value = self.metadata.get("requested_language")
        return str(value) if isinstance(value, str) else None

    @property
    def has_timestamps(self) -> bool:
        return any(
            _as_float(segment.start_raw) is not None and _as_float(segment.end_raw) is not None
            for segment in self.segments
        )

    @property
    def reference_duration_seconds(self) -> float | None:
        """Best available duration for bounds checking.

        Prefers the interval actually transcribed, then what the API reported
        for the audio it received.
        """

        return self.processed_duration_seconds or self.api_reported_duration_seconds


def load_raw_transcript(path: Path) -> RawTranscript:
    """Read a Phase 1 ``*.en.json`` artifact without altering it."""

    path = Path(path)
    if not path.is_file():
        raise TranscriptFormatError(f"Transcription JSON not found: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise TranscriptFormatError(f"Could not read {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise TranscriptFormatError(f"{path} must contain a JSON object.")

    schema_version = payload.get("schema_version")
    if isinstance(schema_version, int) and schema_version not in SUPPORTED_RAW_SCHEMA_VERSIONS:
        raise TranscriptFormatError(
            f"{path} has schema_version {schema_version}, which this version of the "
            f"tool does not understand (supported: "
            f"{sorted(SUPPORTED_RAW_SCHEMA_VERSIONS)})."
        )

    text = payload.get("transcript")
    if text is None:
        raise TranscriptFormatError(
            f"{path} has no 'transcript' field. Run Phase 1 first "
            "(python -m src.main) to create the transcription artifact."
        )
    if not isinstance(text, str):
        raise TranscriptFormatError(f"{path} has a non-string 'transcript' field.")

    segments = _parse_segments(payload.get("segments"), path)

    # Everything except the segment array is carried through verbatim so the
    # cleaned artifact can echo the original processing metadata.
    metadata = {key: value for key, value in payload.items() if key != "segments"}

    return RawTranscript(
        path=path,
        schema_version=schema_version if isinstance(schema_version, int) else None,
        text=text,
        segments=segments,
        metadata=metadata,
    )


def _parse_segments(raw_segments: Any, path: Path) -> tuple[RawSegment, ...]:
    if raw_segments is None:
        return ()
    if not isinstance(raw_segments, list):
        raise TranscriptFormatError(f"{path} has a non-list 'segments' field.")

    parsed: list[RawSegment] = []
    for position, raw in enumerate(raw_segments):
        if not isinstance(raw, dict):
            raise TranscriptFormatError(
                f"{path} segment #{position} is not an object; the QA stage cannot "
                "trust malformed segment data."
            )
        text = raw.get("text")
        parsed.append(
            RawSegment(
                index=position,
                id=raw.get("id"),
                start_raw=raw.get("start"),
                end_raw=raw.get("end"),
                text=text if isinstance(text, str) else "",
                extra={k: v for k, v in raw.items() if k not in {"id", "start", "end", "text"}},
            )
        )
    return tuple(parsed)


# ----------------------------------------------------------------------
# Cleaned (translation-ready) model
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class CleanSegment:
    """A prepared segment ready for translation."""

    segment_id: str
    source_segment_ids: tuple[str, ...]
    start_seconds: float | None
    end_seconds: float | None
    raw_text: str
    cleaned_text: str
    quality_flags: tuple[str, ...]
    correction_status: str
    timestamp_confidence: str
    terminology: tuple[dict[str, Any], ...] = ()

    @property
    def duration_seconds(self) -> float | None:
        if self.start_seconds is None or self.end_seconds is None:
            return None
        return max(0.0, self.end_seconds - self.start_seconds)

    @property
    def is_empty(self) -> bool:
        return not self.cleaned_text.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "source_segment_ids": list(self.source_segment_ids),
            "start_seconds": _round(self.start_seconds),
            "end_seconds": _round(self.end_seconds),
            "duration_seconds": _round(self.duration_seconds),
            "raw_text": self.raw_text,
            "cleaned_text": self.cleaned_text,
            "quality_flags": list(self.quality_flags),
            "correction_status": self.correction_status,
            "timestamp_confidence": self.timestamp_confidence,
            "terminology": [dict(item) for item in self.terminology],
        }


@dataclass(frozen=True)
class CleanTranscript:
    """The prepared transcript written to ``*.en.clean.json``."""

    source_artifact: Path
    segments: tuple[CleanSegment, ...]
    cleaned_text: str
    raw_text: str
    model: str | None
    language: str | None
    processed_duration_seconds: float | None
    qa_summary: dict[str, Any]
    verification: dict[str, Any]
    cleaning: dict[str, Any]
    terminology_summary: dict[str, Any]
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        return bool(self.qa_summary.get("needs_review"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CLEAN_SCHEMA_VERSION,
            "artifact_type": "prepared_transcript",
            "generated_at": _utc_timestamp(),
            "source_artifact": str(self.source_artifact),
            "source_file": self.source_metadata.get("source_file"),
            "source_path": self.source_metadata.get("source_path"),
            "transcription_model": self.model,
            "language": self.language,
            "processed_duration_seconds": _round(self.processed_duration_seconds),
            "cleaning": self.cleaning,
            "verification": self.verification,
            "qa_summary": self.qa_summary,
            "terminology_summary": self.terminology_summary,
            "cleaned_transcript": self.cleaned_text,
            "raw_transcript": self.raw_text,
            "segments": [segment.to_dict() for segment in self.segments],
        }


#: Human readable description of the cleaned JSON schema, mirrored in the docs.
CLEAN_SCHEMA_FIELDS: dict[str, str] = {
    "schema_version": "Version of this cleaned artifact (currently 2).",
    "artifact_type": "Always 'prepared_transcript' for this artifact.",
    "generated_at": "UTC timestamp of the preparation run.",
    "source_artifact": "Path of the raw *.en.json this was derived from.",
    "source_file": "Original source video file name, if recorded.",
    "source_path": "Original source video path, if recorded.",
    "transcription_model": "Model that produced the raw transcription.",
    "language": "Language reported/requested by the API.",
    "processed_duration_seconds": "Length of the transcribed interval.",
    "cleaning": "What the deterministic cleaning stage changed.",
    "verification": "Whether and how the transcript was cross-checked.",
    "qa_summary": "Counts of quality findings by severity and type.",
    "terminology_summary": "Domain terms detected in the transcript.",
    "cleaned_transcript": "Full cleaned text, ready for translation.",
    "raw_transcript": "Verbatim text returned by the API, unmodified.",
    "segments[].segment_id": "Stable id of the prepared segment.",
    "segments[].source_segment_ids": "Raw segment ids this was built from.",
    "segments[].start_seconds": "Start time in the source audio, null if unknown.",
    "segments[].end_seconds": "End time in the source audio, null if unknown.",
    "segments[].duration_seconds": "End minus start, null if either is unknown.",
    "segments[].raw_text": "Original segment text, unmodified.",
    "segments[].cleaned_text": "Formatting-cleaned English text.",
    "segments[].quality_flags": "QA finding types attached to this segment.",
    "segments[].correction_status": (
        "unchanged | uncertain_review_required | verified_by_retranscription | merged_verbatim"
    ),
    "segments[].timestamp_confidence": "reliable | suspect | missing.",
    "segments[].terminology": "Domain terms detected in this segment.",
}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return None if result != result else result  # drop NaN
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
