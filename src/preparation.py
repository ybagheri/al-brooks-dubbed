"""Transcript preparation orchestration (Phase 2).

The pipeline has three clearly separated stages:

1. **Deterministic cleanup** - formatting only, never a rewrite.
2. **Quality assurance** - detection and reporting (see :mod:`src.qa`).
3. **Optional verification** - an explicitly requested, bounded re-transcription
   that can turn *evidence* into a correction.

The raw Phase 1 artifacts are opened read-only and are never modified.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .errors import OutputExistsError, VerificationUnavailableError
from .media import MediaService
from .outputs import OutputWriter, PreparedPaths, utc_timestamp
from .qa import (
    DuplicateGroup,
    QaReport,
    QaThresholds,
    Severity,
    TranscriptQaAnalyzer,
)
from .terminology import Terminology, load_terminology
from .text_cleaning import (
    clean_text,
    comparison_key,
    join_segment_texts,
    word_tokens,
    words_changed,
)
from .transcript import (
    QA_SCHEMA_VERSION,
    CleanSegment,
    CleanTranscript,
    RawSegment,
    RawTranscript,
    load_raw_transcript,
)
from .transcription import GroqTranscriber
from .verification import (
    VerificationOutcome,
    VerificationStatus,
    build_alternative_segments,
    not_performed,
    summarize_for_review,
    verify_duplicate_groups,
)

logger = logging.getLogger(__name__)

#: Correction status values used in the prepared artifact.
STATUS_UNCHANGED = "unchanged"
STATUS_UNCERTAIN = "uncertain_review_required"
STATUS_VERIFIED = "verified_by_retranscription"

#: Timestamp confidence values used in the prepared artifact.
CONFIDENCE_RELIABLE = "reliable"
CONFIDENCE_SUSPECT = "suspect"
CONFIDENCE_MISSING = "missing"

#: Findings that make a segment's wording doubtful.
DUPLICATE_FINDING_TYPES = frozenset(
    {
        "exact_duplicate_segment",
        "near_duplicate_segment",
        "duplicate_segment_duration_mismatch",
    }
)

#: Verdict values from the verification evidence.
_VERDICT_CONTRADICTED = frozenset({"absent_from_alternative", "less_frequent_in_alternative"})


@dataclass(frozen=True)
class PreparationResult:
    """Everything the CLI needs to report a preparation run."""

    raw_path: Path
    paths: PreparedPaths
    status: str
    report: QaReport
    clean: CleanTranscript
    verification: VerificationOutcome
    warnings: tuple[str, ...] = ()
    review_notes: tuple[str, ...] = ()

    @property
    def needs_review(self) -> bool:
        return self.status == "needs_review"


class TranscriptPreparer:
    """Runs QA and preparation for one raw transcription artifact."""

    def __init__(
        self,
        config: AppConfig,
        *,
        media_service: MediaService | None = None,
        transcriber: GroqTranscriber | None = None,
        writer: OutputWriter | None = None,
        terminology: Terminology | None = None,
        thresholds: QaThresholds | None = None,
    ) -> None:
        self.config = config
        self.media = media_service
        self.transcriber = transcriber
        self.writer = writer or OutputWriter(config.output_dir, config.overwrite)
        self.terminology = terminology or load_terminology()
        self.analyzer = TranscriptQaAnalyzer(thresholds=thresholds, terminology=self.terminology)

    # ------------------------------------------------------------------
    def prepare(
        self,
        raw_json_path: Path,
        *,
        verify: bool = False,
        audio_path: Path | None = None,
        verify_attempts: int = 1,
    ) -> PreparationResult:
        """Analyse, clean and (optionally) verify one transcription artifact."""

        raw_path = Path(raw_json_path)
        raw = load_raw_transcript(raw_path)
        paths = PreparedPaths.from_raw(raw_path)
        warnings: list[str] = []

        logger.info("Raw artifact  : %s", raw_path)
        logger.info("Segments      : %d (model: %s)", len(raw.segments), raw.model or "unknown")

        # --- Stage 1 + 2: deterministic cleanup and quality analysis ----
        report = self.analyzer.analyze(raw)
        self._log_report(report)
        duplicate_groups = report.duplicate_groups

        # --- Stage 3: optional, explicitly requested verification -------
        verification = self._maybe_verify(
            raw=raw,
            groups=duplicate_groups,
            verify=verify,
            audio_path=audio_path,
            attempts=verify_attempts,
            warnings=warnings,
        )

        # --- Build the prepared transcript ------------------------------
        suspect = self._suspect_keys(report)
        merge_groups = self._merge_groups(raw, verification, suspect)
        segments, merge_decisions = self._build_segments(raw, report, suspect, merge_groups)
        cleaned_text = self._assemble_text(raw, segments)

        clean = CleanTranscript(
            source_artifact=raw_path,
            segments=segments,
            cleaned_text=cleaned_text,
            raw_text=raw.text,
            model=raw.model,
            language=raw.language,
            processed_duration_seconds=raw.reference_duration_seconds,
            qa_summary=report.summary(),
            verification=verification.to_dict(),
            cleaning=self._cleaning_summary(raw, segments),
            terminology_summary={
                **self.terminology.summary(),
                "term_ids_detected": self.terminology.term_ids_in(cleaned_text),
                "known_confusions_detected": self._confusion_summary(cleaned_text),
            },
            source_metadata={
                "source_file": raw.source_file,
                "source_path": str(raw.source_path) if raw.source_path else None,
                "processing_timestamp": raw.metadata.get("processing_timestamp"),
                "processing_status": raw.metadata.get("processing_status"),
            },
        )

        self._write(paths, clean, report, verification)

        status = "needs_review" if self._needs_review(report, verification) else "ok"
        logger.info("Preparation status: %s", status)

        return PreparationResult(
            raw_path=raw_path,
            paths=paths,
            status=status,
            report=report,
            clean=clean,
            verification=verification,
            warnings=tuple(warnings),
            review_notes=tuple(
                self._review_notes(report, verification, duplicate_groups, merge_decisions)
            ),
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def _maybe_verify(
        self,
        *,
        raw: RawTranscript,
        groups: tuple[DuplicateGroup, ...],
        verify: bool,
        audio_path: Path | None,
        attempts: int,
        warnings: list[str],
    ) -> VerificationOutcome:
        if not verify:
            return not_performed(
                "Verification was not requested. Suspect wording is reported and "
                "preserved exactly as the API returned it."
            )

        if not groups:
            return not_performed(
                "Verification was requested but no duplicated text was found, so no "
                "additional API call was made."
            )

        transcriber = self.transcriber or self._build_transcriber()
        temp_dir: Path | None = None
        try:
            resolved, temp_dir = self._resolve_audio(raw, audio_path)
            logger.info(
                "Verifying %d duplicate group(s) against %s (max %d attempt(s))",
                len(groups),
                resolved.name,
                max(1, attempts),
            )
            return verify_duplicate_groups(
                raw=raw,
                groups=groups,
                audio_path=resolved,
                transcriber=transcriber,
                max_attempts=attempts,
            )
        except VerificationUnavailableError as exc:
            warnings.append(str(exc))
            return VerificationOutcome(
                status=VerificationStatus.INCONCLUSIVE,
                method="grok_retranscription",
                detail=str(exc),
                attempts=0,
            )
        finally:
            self._cleanup(temp_dir)

    def _resolve_audio(
        self, raw: RawTranscript, audio_path: Path | None
    ) -> tuple[Path, Path | None]:
        """Find audio for the transcribed interval, extracting it if required.

        Returns the audio path and the temporary directory to clean up (or
        ``None`` when nothing temporary was created).
        """

        if audio_path is not None:
            candidate = Path(audio_path)
            if not candidate.is_file():
                raise VerificationUnavailableError(
                    f"The audio file given with --audio does not exist: {candidate}"
                )
            return candidate, None

        # An audio export already sitting next to the artifact is used as is.
        stem = raw.path.name
        if stem.endswith(".json"):
            stem = stem[: -len(".json")]
        for suffix in (".flac", ".wav", ".mp3", ".m4a", ".ogg"):
            sibling = raw.path.parent / f"{stem}{suffix}"
            if sibling.is_file():
                logger.info("Using existing audio for verification: %s", sibling.name)
                return sibling, None

        source = self._resolve_source_media(raw)
        if source is None:
            raise VerificationUnavailableError(
                "Verification needs the audio for the transcribed interval, but none "
                "is available. Provide it in one of these ways:\n"
                "  * pass --audio <file>;\n"
                f"  * place an audio export next to {raw.path.name} with the same "
                "name (.flac/.wav/.mp3/.m4a/.ogg);\n"
                "  * keep the source video recorded in the artifact ('source_path') "
                "so the interval can be re-extracted with FFmpeg."
            )

        if self.media is None:
            raise VerificationUnavailableError(
                "Re-extracting the audio requires FFmpeg, but no media service is "
                "available. Pass --audio <file> instead."
            )

        duration = raw.reference_duration_seconds
        if duration is None:
            raise VerificationUnavailableError(
                f"{raw.path.name} does not record the processed duration, so the "
                "interval to verify cannot be determined. Pass --audio <file>."
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="al-brooks-verify-"))
        audio_out = temp_dir / "verify.flac"
        self.media.extract_audio_segment(
            source=source,
            destination=audio_out,
            start_seconds=0.0,
            duration_seconds=duration,
        )
        logger.info("Re-extracted %.2fs of audio from %s", duration, source.name)
        return audio_out, temp_dir

    def _resolve_source_media(self, raw: RawTranscript) -> Path | None:
        """Locate the extracted video next to the artifact, or the original."""

        raw_path = raw.path
        stem = raw_path.name[: -len(".json")] if raw_path.name.endswith(".json") else raw_path.stem
        for suffix in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
            sibling = raw_path.parent / f"{stem}{suffix}"
            if sibling.is_file():
                return sibling

        if raw.source_path is not None:
            return raw.source_path

        source_file = raw.source_file
        if source_file:
            for directory in (raw_path.parent, self.config.data_dir):
                candidate = Path(directory) / source_file
                if candidate.is_file():
                    return candidate
        return None

    def _build_transcriber(self) -> GroqTranscriber:
        return GroqTranscriber(
            api_key=self.config.api_key,
            model=self.config.model,
            language=self.config.language,
            max_retries=self.config.max_retries,
            backoff_seconds=self.config.retry_backoff_seconds,
            max_backoff_seconds=self.config.retry_max_backoff_seconds,
            max_audio_bytes=self.config.max_audio_bytes,
        )

    # ------------------------------------------------------------------
    # Segment construction
    # ------------------------------------------------------------------
    def _merge_groups(
        self,
        raw: RawTranscript,
        verification: VerificationOutcome,
        suspect: set[str],
    ) -> dict[str, list[int]]:
        """Decide which suspect segments may be merged.

        A merge happens **only** when verification contradicted the duplication
        and the alternative transcription does not contain the same wording.
        Every other case leaves the transcript untouched.
        """

        alternative = verification.alternative
        if alternative is None or not verification.status.supports_correction:
            return {}

        groups = verification.evidence.get("groups")
        if not isinstance(groups, list):
            return {}

        alternative_keys = {comparison_key(s.text) for s in alternative.segments}
        index_by_key: dict[str, int] = {
            segment.key: position for position, segment in enumerate(raw.segments)
        }

        merges: dict[str, list[int]] = {}
        for entry in groups:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("verdict")) not in _VERDICT_CONTRADICTED:
                continue
            phrase = str(entry.get("phrase", ""))
            if not phrase or phrase in alternative_keys:
                continue

            ids = entry.get("segment_ids")
            if not isinstance(ids, list):
                continue
            indexes = sorted(
                index_by_key[str(key)]
                for key in ids
                if str(key) in index_by_key and str(key) in suspect
            )
            if len(indexes) >= 2:
                merges[raw.segments[indexes[0]].key] = indexes
        return merges

    def _build_segments(
        self,
        raw: RawTranscript,
        report: QaReport,
        suspect: set[str],
        merge_groups: dict[str, list[int]],
    ) -> tuple[tuple[CleanSegment, ...], dict[str, list[str]]]:
        """Build prepared segments, honouring verified merges only."""

        merge_decisions: dict[str, list[str]] = {}
        prepared: list[CleanSegment] = []
        index = 0
        while index < len(raw.segments):
            key = raw.segments[index].key
            group = merge_groups.get(key)
            if group and len(group) >= 2:
                sources = tuple(
                    raw.segments[position]
                    for position in group
                    if 0 <= position < len(raw.segments)
                )
                merged = self._merge_segment(sources, report)
                prepared.append(merged)
                merge_decisions[merged.segment_id] = [s.key for s in sources]
                index = group[-1] + 1
                continue

            prepared.append(self._single_segment(raw.segments[index], report, suspect))
            index += 1

        if not prepared and raw.text.strip():
            # No segment data at all: keep the whole transcript as one untimed
            # unit so that no speech is lost, and say so explicitly.
            prepared.append(
                CleanSegment(
                    segment_id="0",
                    source_segment_ids=(),
                    start_seconds=None,
                    end_seconds=None,
                    raw_text=raw.text,
                    cleaned_text=clean_text(raw.text),
                    quality_flags=("no_segments",),
                    correction_status=STATUS_UNCERTAIN,
                    timestamp_confidence=CONFIDENCE_MISSING,
                    terminology=tuple(m.to_dict() for m in self.terminology.find(raw.text)),
                )
            )

        return tuple(prepared), merge_decisions

    def _merge_segment(self, sources: tuple[RawSegment, ...], report: QaReport) -> CleanSegment:
        """Join duplicate segments verbatim, recording the merge explicitly."""

        keys = tuple(segment.key for segment in sources)
        raw_text = " ".join(segment.text.strip() for segment in sources if segment.text.strip())
        cleaned = join_segment_texts([segment.text for segment in sources])

        starts = [
            value
            for value in (_parse_timestamp_value(s.start_raw)[0] for s in sources)
            if value is not None
        ]
        ends = [
            value
            for value in (_parse_timestamp_value(s.end_raw)[0] for s in sources)
            if value is not None
        ]
        flags = sorted({flag for key in keys for flag in report.segment_flags.get(key, ())})

        return CleanSegment(
            segment_id=f"{keys[0]}-{keys[-1]}" if len(keys) > 1 else keys[0],
            source_segment_ids=keys,
            start_seconds=min(starts) if starts else None,
            end_seconds=max(ends) if ends else None,
            raw_text=raw_text,
            cleaned_text=cleaned,
            quality_flags=(*flags, "merged_verbatim"),
            correction_status=STATUS_VERIFIED,
            timestamp_confidence=CONFIDENCE_SUSPECT,
            terminology=tuple(m.to_dict() for m in self.terminology.find(cleaned)),
        )

    def _single_segment(
        self, segment: RawSegment, report: QaReport, suspect: set[str]
    ) -> CleanSegment:
        start, start_valid = _parse_timestamp_value(segment.start_raw)
        end, end_valid = _parse_timestamp_value(segment.end_raw)
        cleaned = clean_text(segment.text)

        flags = list(report.segment_flags.get(segment.key, ()))
        status = STATUS_UNCERTAIN if segment.key in suspect else STATUS_UNCHANGED

        if not (start_valid and end_valid) or start is None or end is None:
            confidence = CONFIDENCE_MISSING
        elif end <= start or start < 0:
            confidence = CONFIDENCE_SUSPECT
        else:
            confidence = CONFIDENCE_RELIABLE

        return CleanSegment(
            segment_id=segment.key,
            source_segment_ids=(segment.key,),
            start_seconds=start,
            end_seconds=end,
            raw_text=segment.text,
            cleaned_text=cleaned,
            quality_flags=tuple(flags),
            correction_status=status,
            timestamp_confidence=confidence,
            terminology=tuple(m.to_dict() for m in self.terminology.find(cleaned)),
        )

    def _suspect_keys(self, report: QaReport) -> set[str]:
        """Segments whose wording is doubtful and must not be silently changed."""

        suspect: set[str] = set()
        for finding in report.findings:
            if finding.type in DUPLICATE_FINDING_TYPES and finding.severity in (
                Severity.CRITICAL,
                Severity.HIGH,
            ):
                suspect.update(finding.segment_ids)
        return suspect

    def _assemble_text(self, raw: RawTranscript, segments: tuple[CleanSegment, ...]) -> str:
        """Build the cleaned transcript from the prepared segments.

        Segment text is preferred over the top-level transcript field because
        it is the only view that carries timing. When segments and transcript
        disagree, the QA report has already flagged it for review.
        """

        if segments:
            joined = join_segment_texts([segment.cleaned_text for segment in segments])
            if joined.strip():
                return joined
        return clean_text(raw.text)

    def _confusion_summary(self, text: str) -> list[dict[str, Any]]:
        """Words that are probably mistranscriptions, for the translation stage.

        These are passed through so a later translator can refuse to translate
        them literally. Nothing is corrected here: only the audio can settle
        whether the speaker actually said the wrong word.
        """

        confusions = self.terminology.find_confusables(text)
        if not confusions:
            return []

        tokens = word_tokens(text)
        summary: list[dict[str, Any]] = []
        for confusion in confusions:
            # find_confusables de-duplicates by (wrong, right), so the real
            # occurrence count has to come from the text itself.
            summary.append(
                {
                    "word": confusion.word,
                    "suspected_word": confusion.suspected_word,
                    "occurrences": sum(1 for token in tokens if token == confusion.word),
                    "source": confusion.source,
                    "example_context": confusion.context,
                }
            )
        return summary

    def _cleaning_summary(
        self, raw: RawTranscript, segments: tuple[CleanSegment, ...]
    ) -> dict[str, Any]:
        """Describe exactly what the deterministic stage changed."""

        combined = "\n".join(segment.cleaned_text for segment in segments)
        return {
            "applied": [
                "unicode_nfkc",
                "whitespace_collapse",
                "space_before_punctuation",
                "bracket_spacing",
                "space_after_clause_punctuation",
                "ellipsis_normalisation",
            ],
            "rewrote_words": False,
            "grammar_corrected": False,
            "translated": False,
            "removed_content": False,
            "raw_word_count": len(word_tokens(raw.text)),
            "cleaned_word_count": len(word_tokens(combined)),
            "unexpected_new_words": words_changed(raw.text, combined),
        }

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def _write(
        self,
        paths: PreparedPaths,
        clean: CleanTranscript,
        report: QaReport,
        verification: VerificationOutcome,
    ) -> None:
        """Write the three Phase 2 artifacts, protecting existing ones."""

        self.writer.prepare()
        self._check_collisions(paths)

        self.writer.write_json(paths.cleaned_json, clean.to_dict())
        self.writer.write_transcript_text(paths.cleaned_text, clean.cleaned_text)
        self.writer.write_json(paths.qa_json, self._qa_payload(report, verification))

        logger.info("Cleaned text : %s", paths.cleaned_text)
        logger.info("Prepared JSON: %s", paths.cleaned_json)
        logger.info("QA report    : %s", paths.qa_json)

    def _check_collisions(self, paths: PreparedPaths) -> None:
        existing = paths.existing()
        if not existing:
            return
        if self.config.overwrite:
            logger.warning(
                "--overwrite is active: replacing %d prepared artifact(s)", len(existing)
            )
            return
        listing = "\n".join(f"  - {path.name}" for path in existing)
        raise OutputExistsError(
            f"Prepared output already exists and --overwrite was not given:\n{listing}\n"
            f"Full path: {existing[0].parent}\n"
            "Re-run with --overwrite to replace them, or delete them first."
        )

    def _qa_payload(self, report: QaReport, verification: VerificationOutcome) -> dict[str, Any]:
        return {
            "schema_version": QA_SCHEMA_VERSION,
            "artifact_type": "transcript_quality_report",
            "generated_at": utc_timestamp(),
            "summary": report.summary(),
            "severity_scale": [str(severity) for severity in Severity],
            "findings": [finding.to_dict() for finding in report.findings],
            "duplicate_groups": [
                {
                    "segment_ids": list(group.segment_ids),
                    "exact": group.exact,
                    "normalized_text": group.normalized_text[:200],
                }
                for group in report.duplicate_groups
            ],
            "verification": verification.to_dict(),
            "alternative_segments": build_alternative_segments(verification),
            "segment_flags": {key: list(value) for key, value in report.segment_flags.items()},
        }

    # ------------------------------------------------------------------
    def _cleanup(self, temp_dir: Path | None) -> None:
        if temp_dir is None or not temp_dir.exists():
            return
        shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _needs_review(report: QaReport, verification: VerificationOutcome) -> bool:
        if report.needs_review:
            return True
        return verification.status in (
            VerificationStatus.DUPLICATE_CONFIRMED,
            VerificationStatus.INCONCLUSIVE,
            VerificationStatus.FAILED,
        )

    @staticmethod
    def _review_notes(
        report: QaReport,
        verification: VerificationOutcome,
        groups: tuple[DuplicateGroup, ...],
        merge_decisions: dict[str, list[str]],
    ) -> list[str]:
        notes: list[str] = []
        if merge_decisions:
            notes.append(
                "Merged duplicated segments after verification: "
                + ", ".join(
                    f"{segment_id} <- {', '.join(keys)}"
                    for segment_id, keys in merge_decisions.items()
                )
            )
        if groups and not verification.status.supports_correction:
            notes.append(summarize_for_review(groups))
        for finding in report.findings:
            if finding.severity in (Severity.CRITICAL, Severity.HIGH):
                notes.append(f"[{finding.severity}] {finding.summary}")
        return notes

    @staticmethod
    def _log_report(report: QaReport) -> None:
        summary = report.summary()
        logger.info(
            "QA findings  : %d (max severity: %s)",
            summary["total_findings"],
            summary["max_severity"] or "none",
        )
        for finding in report.findings:
            logger.info("  [%-8s] %-34s %s", finding.severity, finding.type, finding.summary)
        if not report.findings:
            logger.info("No quality problems detected.")


def _parse_timestamp_value(raw: Any) -> tuple[float | None, bool]:
    """Parse a timestamp without ever inventing a value (see ``qa._parse_timestamp``)."""

    from .qa import _parse_timestamp

    parsed = _parse_timestamp(raw)
    return parsed.value, parsed.valid


def render_preparation_report(result: PreparationResult) -> str:
    """Human readable closing block for the CLI."""

    summary = result.report.summary()
    lines = [
        "",
        "=" * 70,
        f"Phase 2 preparation complete - STATUS: {result.status.upper()}",
        "=" * 70,
        f"  Raw artifact       : {result.raw_path}",
        f"  Source model       : {result.clean.model or 'unknown'}",
        f"  Segments prepared  : {len(result.clean.segments)}",
        f"  QA findings        : {summary['total_findings']}"
        f" (max severity: {summary['max_severity'] or 'none'})",
        f"  Verification       : {result.verification.status}",
        f"  Cleaned text       : {result.paths.cleaned_text}",
        f"  Prepared transcript: {result.paths.cleaned_json}",
        f"  QA report          : {result.paths.qa_json}",
    ]
    counts = ", ".join(f"{name}={count}" for name, count in summary["by_severity"].items() if count)
    if counts:
        lines.append(f"  Findings by severity: {counts}")
    for note in result.review_notes:
        lines.append("  Review needed:")
        lines.extend(f"    {line}" for line in note.splitlines())
    for warning in result.warnings:
        lines.append(f"  Warning: {warning}")
    lines.append("=" * 70)
    return "\n".join(lines)
