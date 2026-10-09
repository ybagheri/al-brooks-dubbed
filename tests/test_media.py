"""Tests for :mod:`src.media` (FFmpeg / FFprobe service)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from src.errors import ExtractionError, MediaProbeError
from src.media import MediaService, verify_executables

from .conftest import FFMPEG, FFPROBE, ffmpeg_path, requires_ffmpeg


def make_runner(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    """Return a fake ``subprocess.run``-compatible callable."""

    def _runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        _runner.calls.append(command)  # type: ignore[attr-defined]
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    _runner.calls = []  # type: ignore[attr-defined]
    return _runner


def make_service(runner: Any) -> MediaService:
    return MediaService(Path(FFMPEG or "ffmpeg"), Path(FFPROBE or "ffprobe"), runner=runner)


# ----------------------------------------------------------------------
# Pure parsing helpers (no external process)
# ----------------------------------------------------------------------
def test_probe_parses_json_payload(tmp_path: Path) -> None:
    payload = """
    {
      "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1368, "height": 736,
         "avg_frame_rate": "15/1", "bit_rate": "280760", "pix_fmt": "yuv420p"},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2,
         "sample_rate": "44100", "bit_rate": "129515"}
      ],
      "format": {"duration": "10170.066667", "size": "526839074",
                 "format_name": "mov,mp4,m4a", "bit_rate": "414423"}
    }
    """
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 100)
    service = make_service(make_runner(stdout=payload))

    info = service.probe(video)
    assert info.duration_seconds == pytest.approx(10170.066667, abs=0.001)
    assert info.has_video and info.has_audio
    assert info.video is not None and info.video.codec_name == "h264"
    assert info.resolution == "1368x736"
    assert info.video.frame_rate == pytest.approx(15.0)
    assert info.audio is not None and info.audio.channels == 2
    assert info.summary()["video_stream"]["pixel_format"] == "yuv420p"


def test_probe_skips_cover_art_video_stream(tmp_path: Path) -> None:
    payload = """
    {"streams": [
       {"codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 600,
        "avg_frame_rate": "0/0", "disposition": {"attached_pic": 1}},
       {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480,
        "avg_frame_rate": "25/1"}
     ],
     "format": {"duration": "5.0", "format_name": "mp4"}}
    """
    video = tmp_path / "with_cover.mp4"
    video.write_bytes(b"x" * 100)
    info = make_service(make_runner(stdout=payload)).probe(video)
    assert info.video is not None and info.video.codec_name == "h264"


def test_probe_reports_missing_duration(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 100)
    service = make_service(make_runner(stdout='{"streams": [], "format": {}}'))
    with pytest.raises(MediaProbeError, match="duration"):
        service.probe(video)


def test_probe_rejects_invalid_json(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 100)
    service = make_service(make_runner(stdout="not json at all"))
    with pytest.raises(MediaProbeError, match="not valid JSON"):
        service.probe(video)


def test_probe_reports_ffprobe_failure(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 100)
    runner = make_runner(returncode=1, stderr="moov atom not found")
    with pytest.raises(MediaProbeError, match="moov atom not found"):
        make_service(runner).probe(video)


def test_probe_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError, match="does not exist"):
        make_service(make_runner()).probe(tmp_path / "ghost.mp4")


def test_probe_handles_missing_executable(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 100)

    def _raise(command: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(command[0])

    with pytest.raises(ExtractionError, match="not found"):
        make_service(_raise).probe(video)


# ----------------------------------------------------------------------
# Codec strategy
# ----------------------------------------------------------------------
def _info_with(codecs: tuple[str | None, str | None], tmp_path: Path) -> Any:
    streams: list[dict[str, Any]] = []
    if codecs[0]:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": codecs[0],
                "width": 320,
                "height": 240,
                "avg_frame_rate": "25/1",
            }
        )
    if codecs[1]:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": codecs[1],
                "channels": 2,
                "sample_rate": "48000",
            }
        )
    payload = json.dumps(
        {"streams": streams, "format": {"duration": "60.0", "format_name": "matroska"}}
    )
    video = tmp_path / "clip.mkv"
    video.write_bytes(b"x" * 100)
    return make_service(make_runner(stdout=payload)).probe(video)


def test_mp4_compatible_codecs_use_stream_copy(tmp_path: Path) -> None:
    info = _info_with(("h264", "aac"), tmp_path)
    mode, args = make_service(make_runner())._plan_codec_strategy(info)
    assert mode == "copy"
    assert args == ["-c", "copy"]


def test_incompatible_audio_forces_audio_reencode_only(tmp_path: Path) -> None:
    info = _info_with(("h264", "pcm_s16le"), tmp_path)
    mode, args = make_service(make_runner())._plan_codec_strategy(info)
    assert mode == "copy-video/reencode-audio"
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"


def test_incompatible_video_keeps_resolution(tmp_path: Path) -> None:
    info = _info_with(("mpeg2video", "aac"), tmp_path)
    mode, args = make_service(make_runner())._plan_codec_strategy(info)
    assert mode == "reencode-video/copy-audio"
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-c:a") + 1] == "copy"
    # no scaling filter anywhere -> resolution is preserved
    assert not any("scale" in token for token in args)


# ----------------------------------------------------------------------
# Real FFmpeg integration
# ----------------------------------------------------------------------
@requires_ffmpeg
def test_effective_duration_is_clamped(
    tmp_path: Path, short_video: Path, media_service: MediaService
) -> None:
    info = media_service.probe(short_video)
    assert info.duration_seconds == pytest.approx(2.0, abs=0.3)
    assert media_service.effective_duration(info, 30.0) == pytest.approx(2.0, abs=0.3)
    assert media_service.effective_duration(info, 1.0) == pytest.approx(1.0)


@requires_ffmpeg
def test_probe_detects_video_and_audio(synthetic_video: Path, media_service: MediaService) -> None:
    info = media_service.probe(synthetic_video)
    assert info.has_video and info.has_audio
    assert info.duration_seconds == pytest.approx(12.0, abs=0.5)
    assert info.video is not None and info.video.codec_name == "h264"
    assert info.resolution == "320x180"


@requires_ffmpeg
def test_extract_video_segment(
    tmp_path: Path, synthetic_video: Path, media_service: MediaService
) -> None:
    destination = tmp_path / "out.mp4"
    source_info = media_service.probe(synthetic_video)
    result = media_service.extract_video_segment(
        synthetic_video, destination, 0.0, 5.0, source_info
    )

    assert destination.is_file()
    assert destination.stat().st_size > 0
    assert result.actual_duration_seconds == pytest.approx(5.0, abs=0.6)
    assert result.mode == "copy"
    assert result.media_info.resolution == "320x180"  # not downscaled
    # command was built as a list, never a shell string
    assert isinstance(result.command, list)
    assert result.command[0] == str(ffmpeg_path())


@requires_ffmpeg
def test_extract_audio_segment(
    tmp_path: Path, synthetic_video: Path, media_service: MediaService
) -> None:
    audio = tmp_path / "audio.flac"
    info = media_service.extract_audio_segment(
        synthetic_video, audio, 0.0, 5.0, sample_rate=16_000, channels=1, codec="flac"
    )
    assert audio.is_file() and audio.stat().st_size > 0
    assert info.audio is not None
    assert info.audio.channels == 1
    assert info.audio.sample_rate == 16_000
    assert info.duration_seconds == pytest.approx(5.0, abs=0.5)


@requires_ffmpeg
def test_validate_output_rejects_empty_file(tmp_path: Path, media_service: MediaService) -> None:
    empty = tmp_path / "empty.mp4"
    empty.touch()
    with pytest.raises(ExtractionError, match="empty"):
        media_service.validate_video_output(empty)


@requires_ffmpeg
def test_validate_output_rejects_missing_file(tmp_path: Path, media_service: MediaService) -> None:
    with pytest.raises(ExtractionError, match="not created"):
        media_service.validate_video_output(tmp_path / "ghost.mp4")


def test_extraction_failure_includes_diagnostics(tmp_path: Path) -> None:
    """ffprobe must succeed while ffmpeg fails, so the failure is reported as such."""

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x" * 100)
    probe_payload = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 8,
                    "height": 8,
                    "avg_frame_rate": "1/1",
                }
            ],
            "format": {"duration": "60.0"},
        }
    )

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if Path(command[0]).name.lower().startswith("ffprobe"):
            return subprocess.CompletedProcess(command, 0, probe_payload, "")
        return subprocess.CompletedProcess(
            command, 1, "", "Invalid data found when processing input"
        )

    service = MediaService(Path("ffmpeg"), Path("ffprobe"), runner=runner)

    with pytest.raises(ExtractionError) as excinfo:
        service.extract_video_segment(source, tmp_path / "out.mp4", 0.0, 1.0)

    message = str(excinfo.value)
    assert "FFmpeg failed" in message
    assert "Invalid data found" in message


@requires_ffmpeg
def test_verify_executables_reports_versions() -> None:
    versions = verify_executables(Path(str(FFMPEG)), Path(str(FFPROBE)))
    assert "ffmpeg version" in versions["ffmpeg"]
    assert "ffprobe version" in versions["ffprobe"]


def test_verify_executables_handles_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError):
        verify_executables(tmp_path / "nope.exe", tmp_path / "nope2.exe")


def test_audio_only_source_is_rejected(tmp_path: Path) -> None:
    """A source with no video stream cannot produce an extracted video."""

    source = tmp_path / "audio-only.mp4"
    source.write_bytes(b"x" * 100)
    payload = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "sample_rate": "44100",
                }
            ],
            "format": {"duration": "60.0"},
        }
    )
    service = MediaService(Path("ffmpeg"), Path("ffprobe"), runner=make_runner(stdout=payload))
    with pytest.raises(ExtractionError, match="no video stream"):
        service.extract_video_segment(source, tmp_path / "out.mp4", 0.0, 1.0)


def test_video_only_source_still_extracts(tmp_path: Path) -> None:
    """A silent source is extractable; only the pipeline requires audio later."""

    source = tmp_path / "silent.mp4"
    source.write_bytes(b"x" * 100)
    payload = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 8,
                    "height": 8,
                    "avg_frame_rate": "1/1",
                }
            ],
            "format": {"duration": "60.0"},
        }
    )
    service = MediaService(Path("ffmpeg"), Path("ffprobe"), runner=make_runner(stdout=payload))
    info = service.probe(source)
    assert info.has_video and not info.has_audio
