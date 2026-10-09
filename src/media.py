"""FFmpeg / FFprobe media inspection and extraction service.

All external processes are launched with argument *lists* (never shell
strings), and FFmpeg streams directly from disk so that neither the video nor
the audio is ever fully loaded into memory.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ExtractionError, MediaProbeError

logger = logging.getLogger(__name__)

#: Signature of the injectable subprocess runner used by this module.
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

#: Video codecs that can be stream-copied into an MP4 container losslessly.
MP4_SAFE_VIDEO_CODECS: frozenset[str] = frozenset(
    {"h264", "hevc", "h265", "av1", "vp9", "mpeg4", "mjpeg"}
)

#: Audio codecs that can be stream-copied into an MP4 container losslessly.
MP4_SAFE_AUDIO_CODECS: frozenset[str] = frozenset({"aac", "mp3", "mp4a", "opus", "alac"})

_MIN_PLAUSIBLE_DURATION_SECONDS: float = 0.05


@dataclass(frozen=True)
class VideoStreamInfo:
    """Video stream summary extracted by FFprobe."""

    codec_name: str
    width: int
    height: int
    frame_rate: float
    bit_rate: int | None = None
    pix_fmt: str | None = None


@dataclass(frozen=True)
class AudioStreamInfo:
    """Audio stream summary extracted by FFprobe."""

    codec_name: str
    channels: int
    sample_rate: int
    bit_rate: int | None = None


@dataclass(frozen=True)
class MediaInfo:
    """Result of inspecting a media file with FFprobe."""

    path: Path
    duration_seconds: float
    size_bytes: int
    format_name: str
    bit_rate: int | None
    video: VideoStreamInfo | None
    audio: AudioStreamInfo | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def resolution(self) -> str | None:
        if self.video is None:
            return None
        return f"{self.video.width}x{self.video.height}"

    def summary(self) -> dict[str, Any]:
        """JSON-serialisable summary suitable for the metadata artifact."""

        return {
            "path": str(self.path),
            "file_name": self.path.name,
            "duration_seconds": round(self.duration_seconds, 3),
            "size_bytes": self.size_bytes,
            "format_name": self.format_name,
            "bit_rate": self.bit_rate,
            "video_stream": (
                None
                if self.video is None
                else {
                    "codec": self.video.codec_name,
                    "width": self.video.width,
                    "height": self.video.height,
                    "frame_rate": round(self.video.frame_rate, 3),
                    "bit_rate": self.video.bit_rate,
                    "pixel_format": self.video.pix_fmt,
                }
            ),
            "audio_stream": (
                None
                if self.audio is None
                else {
                    "codec": self.audio.codec_name,
                    "channels": self.audio.channels,
                    "sample_rate": self.audio.sample_rate,
                    "bit_rate": self.audio.bit_rate,
                }
            ),
        }


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of a successful extraction."""

    output_path: Path
    mode: str
    requested_duration_seconds: float
    actual_duration_seconds: float
    media_info: MediaInfo
    command: list[str] = field(default_factory=list, repr=False)


def _subprocess_kwargs() -> dict[str, Any]:
    """Platform-specific keyword arguments for :func:`subprocess.run`."""

    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    if sys.platform == "win32":
        # Avoid flashing a console window for every FFmpeg invocation.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


class MediaService:
    """Thin, testable wrapper around the ``ffprobe`` and ``ffmpeg`` binaries."""

    def __init__(
        self,
        ffmpeg_path: Path,
        ffprobe_path: Path,
        runner: CommandRunner | None = None,
    ) -> None:
        self.ffmpeg_path = Path(ffmpeg_path)
        self.ffprobe_path = Path(ffprobe_path)
        self._runner: CommandRunner = runner or subprocess.run

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------
    def probe(self, path: Path) -> MediaInfo:
        """Inspect ``path`` with FFprobe and return a structured summary."""

        path = Path(path)
        if not path.is_file():
            raise MediaProbeError(f"Cannot probe a file that does not exist: {path}")

        command = [
            str(self.ffprobe_path),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        completed = self._run(command, what="ffprobe")
        if completed.returncode != 0:
            raise MediaProbeError(
                f"ffprobe failed for {path} (exit code {completed.returncode}):\n"
                f"{_tail(completed.stderr)}"
            )

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise MediaProbeError(
                f"ffprobe returned output that is not valid JSON for {path}: {exc}"
            ) from exc

        return self._parse_probe_payload(path, payload)

    def _parse_probe_payload(self, path: Path, payload: dict[str, Any]) -> MediaInfo:
        streams = payload.get("streams") or []
        fmt = payload.get("format") or {}

        video_raw = _first_stream(streams, "video")
        audio_raw = _first_stream(streams, "audio")

        duration = _parse_float(_first_available(fmt, ("duration",)))
        if duration is None and video_raw is not None:
            duration = _parse_float(video_raw.get("duration"))
        if duration is None and audio_raw is not None:
            duration = _parse_float(audio_raw.get("duration"))
        if duration is None or duration <= 0:
            raise MediaProbeError(
                f"Could not determine a positive duration for {path}. "
                "The file may be corrupt or still being written."
            )

        video = (
            VideoStreamInfo(
                codec_name=str(video_raw.get("codec_name", "unknown")),
                width=int(video_raw.get("width") or 0),
                height=int(video_raw.get("height") or 0),
                frame_rate=(
                    _parse_frame_rate(video_raw.get("avg_frame_rate"))
                    or _parse_frame_rate(video_raw.get("r_frame_rate"))
                    or 0.0
                ),
                bit_rate=_parse_int(video_raw.get("bit_rate")),
                pix_fmt=_optional_str(video_raw.get("pix_fmt")),
            )
            if video_raw is not None
            else None
        )

        audio = (
            AudioStreamInfo(
                codec_name=str(audio_raw.get("codec_name", "unknown")),
                channels=int(audio_raw.get("channels") or 0),
                sample_rate=int(audio_raw.get("sample_rate") or 0),
                bit_rate=_parse_int(audio_raw.get("bit_rate")),
            )
            if audio_raw is not None
            else None
        )

        return MediaInfo(
            path=path,
            duration_seconds=duration,
            size_bytes=int(_parse_float(fmt.get("size")) or path.stat().st_size),
            format_name=str(fmt.get("format_name", "unknown")),
            bit_rate=_parse_int(fmt.get("bit_rate")),
            video=video,
            audio=audio,
            raw=payload,
        )

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------
    def effective_duration(self, info: MediaInfo, requested: float) -> float:
        """Return the duration that will really be extracted."""

        return max(0.0, min(requested, info.duration_seconds))

    def extract_video_segment(
        self,
        source: Path,
        destination: Path,
        start_seconds: float,
        duration_seconds: float,
        source_info: MediaInfo | None = None,
    ) -> ExtractionResult:
        """Extract ``[start, start + duration)`` from ``source`` into ``destination``.

        Stream copying is used when the codecs fit the MP4 container, otherwise
        only the incompatible stream is re-encoded. Resolution is never reduced.
        """

        source = Path(source)
        destination = Path(destination)
        info = source_info or self.probe(source)
        if not info.has_video:
            raise ExtractionError(
                f"The source file has no video stream, nothing to extract: {source}"
            )

        target = min(duration_seconds, info.duration_seconds)
        mode, ffmpeg_codec_args = self._plan_codec_strategy(info)

        command: list[str] = [
            str(self.ffmpeg_path),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if destination.exists() else "-n",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(source),
            "-t",
            f"{target:.3f}",
        ]
        if info.has_video:
            command += ["-map", "0:v:0"]
        if info.has_audio:
            command += ["-map", "0:a:0?"]
        command += ["-avoid_negative_ts", "make_zero"]
        command += ffmpeg_codec_args
        command += ["-movflags", "+faststart", str(destination)]

        destination.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Extracting %.3fs from %s using %s (output: %s)",
            target,
            source.name,
            mode,
            destination.name,
        )
        logger.debug("ffmpeg command: %s", " ".join(command))

        completed = self._run(command, what="ffmpeg")
        if completed.returncode != 0:
            raise ExtractionError(
                f"FFmpeg failed to extract the video segment (exit code "
                f"{completed.returncode}).\n"
                f"Source : {source}\n"
                f"Output : {destination}\n"
                f"Mode   : {mode}\n"
                f"Command: {' '.join(command)}\n"
                f"FFmpeg said:\n{_tail(completed.stderr)}"
            )

        output_info = self.validate_video_output(destination, expected_duration=target)
        return ExtractionResult(
            output_path=destination,
            mode=mode,
            requested_duration_seconds=duration_seconds,
            actual_duration_seconds=output_info.duration_seconds,
            media_info=output_info,
            command=command,
        )

    def extract_audio_segment(
        self,
        source: Path,
        destination: Path,
        start_seconds: float,
        duration_seconds: float,
        sample_rate: int = 16_000,
        channels: int = 1,
        codec: str = "flac",
    ) -> MediaInfo:
        """Extract the given interval as mono, downsampled audio for STT."""

        source = Path(source)
        destination = Path(destination)
        if not source.is_file():
            raise ExtractionError(f"Cannot extract audio from a missing file: {source}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.ffmpeg_path),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration_seconds:.3f}",
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            codec,
            str(destination),
        ]
        logger.debug("ffmpeg audio command: %s", " ".join(command))

        completed = self._run(command, what="ffmpeg")
        if completed.returncode != 0:
            raise ExtractionError(
                f"FFmpeg failed to prepare the audio for transcription "
                f"(exit code {completed.returncode}).\n"
                f"Source : {source}\n"
                f"Audio  : {destination}\n"
                f"FFmpeg said:\n{_tail(completed.stderr)}"
            )

        if not destination.is_file() or destination.stat().st_size == 0:
            raise ExtractionError(
                f"FFmpeg produced no usable audio file: {destination}. "
                "The selected interval may be silent or contain no audio stream."
            )
        return self.probe(destination)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def validate_video_output(
        self, path: Path, expected_duration: float | None = None
    ) -> MediaInfo:
        """Validate that ``path`` is a playable MP4 with a plausible duration."""

        path = Path(path)
        if not path.is_file():
            raise ExtractionError(f"Expected output file was not created: {path}")
        size = path.stat().st_size
        if size == 0:
            raise ExtractionError(f"Extracted video file is empty: {path}")

        info = self.probe(path)
        if not info.has_video:
            raise ExtractionError(f"Extracted file contains no video stream: {path}")
        if info.duration_seconds < _MIN_PLAUSIBLE_DURATION_SECONDS:
            raise ExtractionError(
                f"Extracted video has an implausible duration "
                f"({info.duration_seconds:.3f}s): {path}"
            )
        if expected_duration is not None:
            # MP4 container duration is derived from timestamps; allow a
            # generous tolerance for keyframe snapping during stream copy.
            tolerance = max(1.0, expected_duration * 0.1)
            if abs(info.duration_seconds - expected_duration) > tolerance:
                raise ExtractionError(
                    f"Extracted duration {info.duration_seconds:.3f}s does not match the "
                    f"requested {expected_duration:.3f}s (tolerance {tolerance:.2f}s): {path}"
                )
        return info

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _plan_codec_strategy(self, info: MediaInfo) -> tuple[str, list[str]]:
        """Decide between stream copy and selective re-encoding."""

        video_codec = info.video.codec_name if info.video else ""
        audio_codec = info.audio.codec_name if info.audio else ""

        video_ok = video_codec in MP4_SAFE_VIDEO_CODECS
        audio_ok = audio_codec in MP4_SAFE_AUDIO_CODECS or not info.has_audio

        if video_ok and audio_ok:
            return "copy", ["-c", "copy"]

        args: list[str] = []
        if video_ok:
            args += ["-c:v", "copy"]
        else:
            # CRF/preset chosen for a good quality/size trade-off on CPU; the
            # original resolution is preserved.
            args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]

        if info.has_audio:
            if audio_ok:
                args += ["-c:a", "copy"]
            else:
                args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]

        if video_ok and not audio_ok:
            mode = "copy-video/reencode-audio"
        elif audio_ok and not video_ok:
            mode = "reencode-video/copy-audio"
        else:
            mode = "reencode"
        return mode, args

    def _run(self, command: Sequence[str], *, what: str) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(list(command), **_subprocess_kwargs())
        except FileNotFoundError as exc:
            raise ExtractionError(
                f"{what} executable not found: {command[0]}. "
                "Install FFmpeg or set FFMPEG_PATH / FFPROBE_PATH."
            ) from exc
        except OSError as exc:
            raise ExtractionError(f"Could not start {what} ({command[0]}): {exc}") from exc


# ----------------------------------------------------------------------
# Module level parsing helpers
# ----------------------------------------------------------------------
def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") == codec_type and not _is_cover_art(stream):
            return stream
    return None


def _is_cover_art(stream: dict[str, Any]) -> bool:
    disposition = stream.get("disposition") or {}
    return bool(disposition.get("attached_pic"))


def _first_available(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", "N/A"):
            return value
    return None


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


def _parse_frame_rate(value: Any) -> float | None:
    """Parse FFprobe's ``"num/den"`` frame rate notation."""

    if value in (None, "", "N/A", "0/0"):
        return None
    text = str(value)
    if "/" in text:
        num_text, _, den_text = text.partition("/")
        num, den = _parse_float(num_text), _parse_float(den_text)
        if num is None or den in (None, 0):
            return None
        return num / den
    return _parse_float(text)


def _optional_str(value: Any) -> str | None:
    return None if value in (None, "", "N/A") else str(value)


def _tail(text: str | None, max_lines: int = 25) -> str:
    """Return the last ``max_lines`` of a tool's output, for readable errors."""

    if not text:
        return "(no output)"
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "(no output)"
    return "\n".join(lines[-max_lines:])


def verify_executables(ffmpeg_path: Path, ffprobe_path: Path) -> dict[str, str]:
    """Return the version banner of both tools; used by ``--check-env``."""

    versions: dict[str, str] = {}
    for label, executable in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path)):
        kwargs = _subprocess_kwargs()
        kwargs.pop("check", None)
        try:
            result = subprocess.run([str(executable), "-version"], **kwargs)  # noqa: S603
        except OSError as exc:
            raise ExtractionError(f"{label} is installed but not runnable: {exc}") from exc
        first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
        versions[label] = first_line or f"exit code {result.returncode}"
    return versions


def readable_size(path: Path) -> str:
    """Human readable file size used in log messages."""

    try:
        return f"{path.stat().st_size / 1e6:.1f} MB"
    except OSError:  # pragma: no cover
        return "unknown size"


def ensure_writable(path: Path) -> None:
    """Create the parent directory and fail early when it is not writable."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExtractionError(f"Cannot create output directory {path.parent}: {exc}") from exc
    if not os.access(path.parent, os.W_OK):  # pragma: no cover - Windows/ACL edge
        raise ExtractionError(f"Output directory is not writable: {path.parent}")
