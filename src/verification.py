"""Optional, evidence-based verification of a suspicious transcript.

When the QA stage finds a suspected duplicated segment, the only honest way to
resolve it is to transcribe the *same audio again* and compare. This module
performs that bounded, explicitly opt-in check and turns the comparison into a
verdict.

It never claims a transcript was verified unless a real transcription request
actually completed: :class:`VerificationOutcome` records the method and the
outcome, and anything short of a successful comparison stays ``inconclusive``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .enums import StringEnum
from .errors import VerificationError, VerificationUnavailableError
from .qa import DuplicateGroup
from .text_cleaning import comparison_key, join_segment_texts
from .transcript import RawTranscript
from .transcription import GroqTranscriber, TranscriptionResult, is_retryable_error

logger = logging.getLogger(__name__)


class VerificationStatus(StringEnum):
    """Outcome of the optional cross-check."""

    NOT_PERFORMED = "not_performed"
    #: A fresh transcription did not repeat the suspect text.
    DUPLICATE_CONTRADICTED = "duplicate_contradicted"
    #: A fresh transcription repeated the suspect text: it is in the audio.
    DUPLICATE_CONFIRMED = "duplicate_confirmed"
    #: The check ran but the evidence was not decisive.
    INCONCLUSIVE = "inconclusive"
    #: The check could not be completed.
    FAILED = "failed"

    @property
    def supports_correction(self) -> bool:
        """True only when evidence justifies changing the transcript."""

        return self is VerificationStatus.DUPLICATE_CONTRADICTED


@dataclass(frozen=True)
class VerificationOutcome:
    """Result of one verification attempt chain."""

    status: VerificationStatus
    method: str
    detail: str
    attempts: int = 0
    model: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    alternative: TranscriptionResult | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "performed": self.status is not VerificationStatus.NOT_PERFORMED,
            "status": str(self.status),
            "method": self.method,
            "detail": self.detail,
            "attempts": self.attempts,
            "model": self.model,
            "supports_correction": self.status.supports_correction,
            "evidence": self.evidence,
        }


def not_performed(reason: str) -> VerificationOutcome:
    """Build the outcome used whenever verification was not requested."""

    return VerificationOutcome(
        status=VerificationStatus.NOT_PERFORMED,
        method="none",
        detail=reason,
    )


def verify_duplicate_groups(
    raw: RawTranscript,
    groups: Sequence[DuplicateGroup],
    audio_path: Path,
    transcriber: GroqTranscriber,
    *,
    max_attempts: int = 1,
) -> VerificationOutcome:
    """Re-transcribe ``audio_path`` and compare it against ``raw``.

    Args:
        raw: the original transcript under suspicion.
        groups: duplicate groups flagged by the QA stage.
        audio_path: audio for the *same* interval as the original transcript.
        transcriber: configured Groq client (its own retries are bounded).
        max_attempts: how many independent transcription passes to allow.

    Returns:
        A :class:`VerificationOutcome`. A failed request never raises; it is
        reported as :attr:`VerificationStatus.FAILED` so the caller can still
        produce a preserved, flagged transcript.
    """

    if not groups:
        return not_performed("No duplicate groups required verification.")

    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise VerificationUnavailableError(
            f"Verification needs the audio for the transcribed interval, but it was "
            f"not found: {audio_path}"
        )

    attempts = max(1, max_attempts)
    alternative: TranscriptionResult | None = None
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            alternative = transcriber.transcribe(audio_path)
            break
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            last_error = f"{type(exc).__name__}: {exc}"
            if not is_retryable_error(exc):
                logger.warning(
                    "Verification attempt %d/%d failed permanently: %s",
                    attempt,
                    attempts,
                    last_error,
                )
                return VerificationOutcome(
                    status=VerificationStatus.FAILED,
                    method="grok_retranscription",
                    detail=(
                        "The verification transcription failed with a permanent error "
                        f"after {attempt} attempt(s), so it was not retried. The "
                        "original transcript is preserved unchanged. "
                        f"Last error: {last_error}"
                    ),
                    attempts=attempt,
                    model=transcriber.model,
                    evidence={"last_error": last_error, "retryable": False},
                )
            logger.warning("Verification attempt %d/%d failed: %s", attempt, attempts, last_error)

    if alternative is None:
        return VerificationOutcome(
            status=VerificationStatus.FAILED,
            method="grok_retranscription",
            detail=(
                f"The verification transcription did not succeed after {attempts} "
                f"attempt(s); the original transcript is preserved unchanged. "
                f"Last error: {last_error}"
            ),
            attempts=attempts,
            model=transcriber.model,
            evidence={"last_error": last_error},
        )

    return compare_with_alternative(raw, groups, alternative, attempts=attempts)


def compare_with_alternative(
    raw: RawTranscript,
    groups: Sequence[DuplicateGroup],
    alternative: TranscriptionResult,
    *,
    attempts: int = 1,
) -> VerificationOutcome:
    """Compare a fresh transcription with the original and derive a verdict.

    The rule is intentionally asymmetric: the transcript is only changed when
    the fresh transcription is *positive evidence* that the suspect repetition
    does not occur in the audio.
    """

    original_text = comparison_key(raw.text)
    alternative_text = comparison_key(alternative.text)

    if not alternative_text:
        return VerificationOutcome(
            status=VerificationStatus.INCONCLUSIVE,
            method="grok_retranscription",
            detail=(
                "The verification transcription returned no text, so it provides no "
                "evidence either way. The original transcript is preserved unchanged."
            ),
            attempts=attempts,
            model=alternative.model,
            alternative=alternative,
        )

    per_group: list[dict[str, Any]] = []
    contradicted = 0
    confirmed = 0

    for group in groups:
        phrase = group.normalized_text
        original_count = _count_occurrences(original_text, phrase)
        alternative_count = _count_occurrences(alternative_text, phrase)
        alternative_has_own_duplicates = _has_adjacent_duplicates(alternative)

        if alternative_count == 0 and original_count > 0:
            verdict = "absent_from_alternative"
            contradicted += 1
        elif alternative_count < original_count:
            verdict = "less_frequent_in_alternative"
            contradicted += 1
        elif alternative_count >= original_count:
            verdict = "present_in_alternative"
            confirmed += 1
        else:  # pragma: no cover - defensive
            verdict = "unknown"

        per_group.append(
            {
                "segment_ids": list(group.segment_ids),
                "phrase": phrase[:200],
                "occurrences_in_original": original_count,
                "occurrences_in_alternative": alternative_count,
                "alternative_has_duplicate_segments": alternative_has_own_duplicates,
                "verdict": verdict,
            }
        )

    evidence = {
        "groups": per_group,
        "original_characters": len(original_text),
        "alternative_characters": len(alternative_text),
        "alternative_segments": len(alternative.segments),
        "alternative_language": alternative.language,
    }

    if confirmed and contradicted:
        return VerificationOutcome(
            status=VerificationStatus.INCONCLUSIVE,
            method="grok_retranscription",
            detail=(
                "The verification transcription agreed with some duplicated text but "
                "not others. No correction is applied; every affected segment is "
                "flagged for manual review."
            ),
            attempts=attempts,
            model=alternative.model,
            evidence=evidence,
            alternative=alternative,
        )

    if contradicted and not confirmed:
        return VerificationOutcome(
            status=VerificationStatus.DUPLICATE_CONTRADICTED,
            method="grok_retranscription",
            detail=(
                "An independent transcription of the same audio does not contain the "
                "duplicated wording, which is direct evidence that the repetition is a "
                "transcription artefact rather than speech."
            ),
            attempts=attempts,
            model=alternative.model,
            evidence=evidence,
            alternative=alternative,
        )

    return VerificationOutcome(
        status=VerificationStatus.DUPLICATE_CONFIRMED,
        method="grok_retranscription",
        detail=(
            "An independent transcription of the same audio contains the same repeated "
            "wording, which indicates the speaker really said it. Nothing is removed."
        ),
        attempts=attempts,
        model=alternative.model,
        evidence=evidence,
        alternative=alternative,
    )


def build_alternative_segments(
    outcome: VerificationOutcome,
) -> list[dict[str, Any]] | None:
    """Expose the alternative segment timings for the report, if available."""

    if outcome.alternative is None:
        return None
    return [segment.to_dict() for segment in outcome.alternative.segments]


def summarize_for_review(groups: Sequence[DuplicateGroup]) -> str:
    """Human readable summary of what a reviewer still has to decide."""

    if not groups:
        return "No duplicated text requires review."
    lines = ["The following repeated text could not be resolved automatically:"]
    for group in groups:
        lines.append(f"  - segments {', '.join(group.segment_ids)}: {group.normalized_text[:120]}")
    lines.append(
        "Listen to the audio for these ranges and decide whether each occurrence was "
        "actually spoken before translating."
    )
    return "\n".join(lines)


def _count_occurrences(haystack: str, needle: str) -> int:
    """Count non-overlapping occurrences of a normalised phrase."""

    if not haystack or not needle:
        return 0
    count = 0
    position = haystack.find(needle)
    while position != -1:
        count += 1
        position = haystack.find(needle, position + len(needle))
    return count


def _has_adjacent_duplicates(alternative: TranscriptionResult) -> bool:
    keys = [comparison_key(segment.text) for segment in alternative.segments]
    non_empty = [key for key in keys if key]
    return any(
        previous == current for previous, current in zip(non_empty, non_empty[1:], strict=False)
    )


def merged_text_of(alternative: TranscriptionResult) -> str:
    """Combined text of the alternative transcription."""

    return join_segment_texts([segment.text for segment in alternative.segments])


__all__ = [
    "VerificationError",
    "VerificationOutcome",
    "VerificationStatus",
    "VerificationUnavailableError",
    "build_alternative_segments",
    "compare_with_alternative",
    "merged_text_of",
    "not_performed",
    "summarize_for_review",
    "verify_duplicate_groups",
]
