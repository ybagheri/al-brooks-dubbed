"""Stage orchestration for the Phase 1 pipeline.

Stage order (each one is reported to the console):

1. Input validation
2. Media inspection
3. Video extraction
4. Audio preparation
5. Groq transcription
6. Output validation
7. Final result locations
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AppConfig
from .discovery import InputResolver
from .errors import InvalidInputError
from .logging_utils import register_secret
from .media import MediaService
from .outputs import OutputPaths, OutputWriter, utc_timestamp
from .transcription import GroqTranscriber, TranscriptionResult

logger = logging.getLogger(__name__)

_STAGE_WIDTH = 66


@dataclass(frozen=True)
class PipelineResult:
    """Everything the CLI needs to render the final report."""

    source: Path
    paths: OutputPaths
    status: str
    source_duration: float
    processed_duration: float
    requested_duration: float
    extraction_mode: str
    output_duration: float
    model: str
    language: str | None
    transcript_characters: int
    segment_count: int
    validation: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def announce(stage_number: int, title: str) -> None:
    """Print a clearly delimited stage banner."""

    logger.info("")
    logger.info("=" * _STAGE_WIDTH)
    logger.info("Stage %d/7  %s", stage_number, title)
    logger.info("=" * _STAGE_WIDTH)


class Phase1Pipeline:
    """Runs the seven Phase 1 stages end to end."""

    def __init__(
        self,
        config: AppConfig,
        input_resolver: InputResolver | None = None,
        media_service: MediaService | None = None,
        transcriber: GroqTranscriber | None = None,
        output_writer: OutputWriter | None = None,
    ) -> None:
        self.config = config
        self.resolver = input_resolver or InputResolver(config.data_dir)
        self.media = media_service or MediaService(config.ffmpeg_path, config.ffprobe_path)
        self.transcriber = transcriber or self._build_transcriber()
        self.writer = output_writer or OutputWriter(config.output_dir, config.overwrite)
        register_secret(config.api_key)

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
    def run(self, input_name: str | None = None) -> PipelineResult:
        """Execute all stages. Raises :class:`AlBrooksError` on failure."""

        warnings: list[str] = []

        # --- Stage 1: input validation -------------------------------
        announce(1, "Input validation")
        candidate = self.resolver.resolve(input_name)
        source = candidate.path
        logger.info("Source video : %s", source)
        logger.info("Size         : %.1f MB", candidate.size_bytes / 1e6)
        logger.info("Data dir     : %s", self.config.data_dir)
        if not self.config.data_dir.exists():
            warnings.append(f"Data directory was created: {self.config.data_dir}")

        # --- Stage 2: media inspection ------------------------------
        announce(2, "Media inspection (ffprobe)")
        source_info = self.media.probe(source)
        self._log_media(source_info)

        effective = self.media.effective_duration(source_info, self.config.duration_seconds)
        if source_info.duration_seconds < self.config.duration_seconds:
            warnings.append(
                f"Source is only {source_info.duration_seconds:.2f}s long; "
                f"processed its full duration instead of {self.config.duration_seconds:g}s."
            )

        paths = OutputPaths.for_input(source, self.config.output_dir, self.config.duration_seconds)
        self.writer.prepare()
        self.writer.check_collisions(paths)

        # --- Stage 3: video extraction ------------------------------
        announce(3, f"Video extraction (first {effective:.2f}s)")
        extraction = self.media.extract_video_segment(
            source=source,
            destination=paths.video,
            start_seconds=0.0,
            duration_seconds=effective,
            source_info=source_info,
        )
        logger.info("Extracted video : %s", extraction.output_path)
        logger.info(
            "Duration        : %.3fs (mode: %s)",
            extraction.actual_duration_seconds,
            extraction.mode,
        )
        logger.info(
            "Resolution      : %s (unchanged from source)", extraction.media_info.resolution
        )

        # --- Stage 4: audio preparation (temporary, cleaned up below) --
        temp_dir: Path | None = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="al-brooks-phase1-"))
            audio_path = temp_dir / f"{paths.video.stem}.{self.config.audio_codec}"
            announce(4, f"Audio preparation (ffmpeg, mono {self.config.audio_sample_rate} Hz)")
            audio_info = self.media.extract_audio_segment(
                source=source,
                destination=audio_path,
                start_seconds=0.0,
                duration_seconds=effective,
                sample_rate=self.config.audio_sample_rate,
                channels=self.config.audio_channels,
                codec=self.config.audio_codec,
            )
            logger.info(
                "Prepared audio : %s (%.1f KB, %.2fs)",
                audio_path.name,
                audio_path.stat().st_size / 1e3,
                audio_info.duration_seconds,
            )

            # --- Stage 5: transcription -----------------------------
            announce(5, f"Groq transcription (model: {self.config.model})")
            result: TranscriptionResult = self.transcriber.transcribe(audio_path)
        finally:
            temp_dir = self._cleanup_temp(temp_dir, warnings)

        # --- Output artifacts ---------------------------------------
        self._write_artifacts(
            paths=paths,
            source=source,
            source_info=source_info,
            extraction=extraction,
            audio_duration=result.duration_seconds,
            result=result,
            effective=effective,
        )

        # --- Stage 6: output validation -----------------------------
        announce(6, "Output validation")
        validation = self.writer.validate_outputs(paths)
        logger.info("All %d artifacts are present, non-empty and parseable.", 4)

        return PipelineResult(
            source=source,
            paths=paths,
            status="success",
            source_duration=source_info.duration_seconds,
            processed_duration=result.duration_seconds or effective,
            requested_duration=self.config.duration_seconds,
            extraction_mode=extraction.mode,
            output_duration=extraction.actual_duration_seconds,
            model=result.model,
            language=result.language,
            transcript_characters=len(result.text),
            segment_count=len(result.segments),
            validation=validation,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------
    def _write_artifacts(
        self,
        *,
        paths: OutputPaths,
        source: Path,
        source_info: Any,
        extraction: Any,
        audio_duration: float | None,
        result: TranscriptionResult,
        effective: float,
    ) -> None:
        announce(7, "Writing output artifacts")

        transcript_payload: dict[str, Any] = {
            "schema_version": 1,
            "source_file": source.name,
            "source_path": str(source),
            "source_duration_seconds": round(source_info.duration_seconds, 3),
            "requested_duration_seconds": round(self.config.duration_seconds, 3),
            "processed_duration_seconds": round(effective, 3),
            "processing_timestamp": utc_timestamp(),
            "processing_status": "success",
            "api_reported_audio_duration_seconds": (
                round(audio_duration, 3) if audio_duration is not None else None
            ),
        }
        transcript_payload.update(result.to_metadata())
        self.writer.write_json(paths.transcript_json, transcript_payload)
        self.writer.write_transcript_text(paths.transcript_text, result.text)
        logger.info("Transcript text: %s", paths.transcript_text)
        logger.info("Transcript JSON: %s", paths.transcript_json)

        metadata_payload = {
            "schema_version": 1,
            "processing_timestamp": utc_timestamp(),
            "processing_status": "success",
            "source_media": source_info.summary(),
            "extracted_video": {
                "path": str(paths.video),
                "mode": extraction.mode,
                "requested_duration_seconds": round(extraction.requested_duration_seconds, 3),
                "actual_duration_seconds": round(extraction.actual_duration_seconds, 3),
                "media": extraction.media_info.summary(),
            },
            "transcription": {
                "model": result.model,
                "language": result.language,
                "segment_count": len(result.segments),
                "has_timestamps": result.has_segments,
            },
            "artifacts": paths.as_dict(),
            "environment": {
                "ffmpeg": str(self.config.ffmpeg_path),
                "ffprobe": str(self.config.ffprobe_path),
                "python": _python_version(),
                "platform": _platform_name(),
            },
        }
        self.writer.write_json(paths.metadata_json, metadata_payload)
        logger.info("Metadata JSON : %s", paths.metadata_json)

    def _cleanup_temp(self, temp_dir: Path | None, warnings: list[str]) -> Path | None:
        """Remove the temporary audio directory unless retention was requested."""

        if temp_dir is None or not temp_dir.exists():
            return None
        if self.config.keep_temp:
            logger.info("Keeping temporary audio (--keep-temp): %s", temp_dir)
            warnings.append(f"Temporary audio kept at {temp_dir}")
            return temp_dir
        try:
            shutil.rmtree(temp_dir, ignore_errors=False)
            logger.debug("Removed temporary directory %s", temp_dir)
        except OSError as exc:  # pragma: no cover - Windows file locking edge case
            logger.warning("Could not remove temporary directory %s: %s", temp_dir, exc)
            warnings.append(f"Temporary directory left behind at {temp_dir}")
        return None

    def _log_media(self, info: Any) -> None:
        logger.info(
            "Duration   : %.3fs (%.2f minutes)", info.duration_seconds, info.duration_seconds / 60
        )
        logger.info("Format     : %s", info.format_name)
        logger.info("Video      : %s", self._describe_video(info))
        logger.info("Audio      : %s", self._describe_audio(info))
        if not info.has_audio:
            raise InvalidInputError(
                f"The source file has no audio stream, so it cannot be transcribed: "
                f"{info.path.name}"
            )

    @staticmethod
    def _describe_video(info: Any) -> str:
        if not info.has_video:
            return "none"
        return f"{info.video.codec_name} {info.resolution} @ {info.video.frame_rate:.3f} fps"

    @staticmethod
    def _describe_audio(info: Any) -> str:
        if not info.has_audio:
            return "none"
        return f"{info.audio.codec_name} {info.audio.channels}ch @ {info.audio.sample_rate} Hz"


def _python_version() -> str:
    import platform

    return platform.python_version()


def _platform_name() -> str:
    import platform

    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def render_final_report(result: PipelineResult) -> str:
    """Build the human readable closing block (stage 7 of the CLI output)."""

    lines = [
        "",
        "=" * 66,
        "Phase 1 complete - STATUS: SUCCESS",
        "=" * 66,
        f"  Source video          : {result.source}",
        f"  Source duration       : {result.source_duration:.2f}s",
        f"  Requested duration    : {result.requested_duration:g}s",
        f"  Extracted video       : {result.paths.video}",
        f"  Extracted duration    : {result.output_duration:.2f}s ({result.extraction_mode})",
        f"  Transcript (plain)    : {result.paths.transcript_text}",
        f"  Transcript (JSON)     : {result.paths.transcript_json}",
        f"  Media metadata        : {result.paths.metadata_json}",
        f"  Transcription model   : {result.model}",
        f"  Language              : {result.language or 'unknown'}",
        f"  Transcript characters : {result.transcript_characters}",
        f"  Timed segments        : {result.segment_count}",
    ]
    if result.warnings:
        lines.append("  Warnings:")
        lines.extend(f"    - {warning}" for warning in result.warnings)
    lines.append("=" * 66)
    return "\n".join(lines)
