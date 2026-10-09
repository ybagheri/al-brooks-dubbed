"""End-to-end pipeline tests with a mocked Groq client."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from src.discovery import InputResolver
from src.errors import AmbiguousInputError, InvalidInputError, OutputExistsError
from src.media import MediaService
from src.outputs import OutputWriter
from src.pipeline import Phase1Pipeline, render_final_report
from src.transcription import GroqTranscriber

from .conftest import FFMPEG, FFPROBE, FakeFactory, requires_ffmpeg, run_ffmpeg

TRANSCRIPT = (
    "Good morning. In this lecture we are going to look at price action, "
    "and specifically at what a trend is and what it is not."
)

RESPONSE: dict[str, Any] = {
    "text": TRANSCRIPT,
    "language": "en",
    "duration": 30.4,
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.1, "text": "Good morning."},
        {"id": 1, "start": 2.1, "end": 30.4, "text": "In this lecture ..."},
    ],
}


def build_pipeline(
    *,
    config: Any,
    data_dir: Path,
    groq_factory: FakeFactory,
    responses: list[Any] | None = None,
    overwrite: bool = False,
) -> Phase1Pipeline:
    groq_factory.responses = list(responses if responses is not None else [dict(RESPONSE)])
    transcriber = GroqTranscriber(
        api_key="gsk_testONLYnotarealkey0000000000000000",
        model=config.model,
        language="en",
        max_retries=2,
        backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        client_factory=groq_factory,
        sleep=lambda _s: None,
    )
    return Phase1Pipeline(
        config=config,
        input_resolver=InputResolver(data_dir),
        media_service=MediaService(Path(FFMPEG or "ffmpeg"), Path(FFPROBE or "ffprobe")),
        transcriber=transcriber,
        output_writer=OutputWriter(config.output_dir, overwrite=overwrite),
    )


@pytest.fixture
def data_dir(tmp_path: Path, synthetic_video: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    shutil.copy(synthetic_video, directory / "lecture.mp4")
    return directory


@requires_ffmpeg
def test_full_pipeline_produces_all_artifacts(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=10.0)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    assert result.status == "success"
    for path in result.paths.all:
        assert path.is_file(), path
        assert path.stat().st_size > 0, path

    assert result.paths.video.name == "lecture_test_10s.mp4"
    assert result.paths.transcript_text.read_text(encoding="utf-8").strip() == TRANSCRIPT
    assert result.transcript_characters == len(TRANSCRIPT)
    assert result.segment_count == 2
    assert result.output_duration == pytest.approx(10.0, abs=0.8)


@requires_ffmpeg
def test_transcript_json_structure(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=10.0)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    payload = json.loads(result.paths.transcript_json.read_text(encoding="utf-8"))
    assert payload["source_file"] == "lecture.mp4"
    assert payload["transcription_model"] == "whisper-large-v3-turbo"
    assert payload["requested_language"] == "en"
    assert payload["transcript"] == TRANSCRIPT
    assert payload["processing_status"] == "success"
    assert payload["segments"][0]["start"] == 0.0
    assert payload["segments"][0]["end"] == 2.1
    assert payload["source_duration_seconds"] == pytest.approx(12.0, abs=0.5)
    assert payload["processed_duration_seconds"] == pytest.approx(10.0, abs=0.1)
    assert payload["processing_timestamp"].endswith("Z")
    # no invented confidence values
    assert "confidence" not in payload


@requires_ffmpeg
def test_metadata_json_structure(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=10.0)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    payload = json.loads(result.paths.metadata_json.read_text(encoding="utf-8"))
    assert payload["processing_status"] == "success"
    assert payload["source_media"]["file_name"] == "lecture.mp4"
    assert payload["source_media"]["video_stream"]["codec"] == "h264"
    assert payload["source_media"]["audio_stream"]["channels"] == 1
    assert payload["extracted_video"]["mode"] == "copy"
    assert payload["transcription"]["segment_count"] == 2
    assert payload["transcription"]["has_timestamps"] is True
    assert payload["environment"]["python"]


@requires_ffmpeg
def test_extracted_video_is_playable_mp4_with_expected_duration(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=5.0)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    service = MediaService(Path(FFMPEG or "ffmpeg"), Path(FFPROBE or "ffprobe"))
    info = service.probe(result.paths.video)
    assert info.has_video and info.has_audio
    assert info.duration_seconds == pytest.approx(5.0, abs=0.6)
    # resolution is preserved, not downscaled
    assert info.resolution == "320x180"


@requires_ffmpeg
def test_short_source_is_processed_fully_and_reported(
    tmp_path: Path, config_factory: Any, groq_factory: FakeFactory
) -> None:
    """A 4-second source with --duration 30 must yield 4 seconds, not fail."""

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:sample_rate=44100:duration=4",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(data_dir / "tiny.mp4"),
        ]
    )
    config = config_factory(data_dir=data_dir, duration_seconds=30.0)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    assert result.output_duration == pytest.approx(4.0, abs=0.5)
    assert any("full duration" in w for w in result.warnings)
    # real audio covering the whole clip was uploaded
    assert groq_factory.last_upload[:4] == b"fLaC"
    assert groq_factory.last_request["file"][0].endswith(".flac")


@requires_ffmpeg
def test_ambiguous_input_fails_before_any_work(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    shutil.copy(data_dir / "lecture.mp4", data_dir / "second.mp4")
    config = config_factory(data_dir=data_dir)
    pipeline = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory)

    with pytest.raises(AmbiguousInputError):
        pipeline.run()
    assert groq_factory.calls == []
    assert not config.output_dir.exists() or not list(config.output_dir.glob("*.mp4"))


@requires_ffmpeg
def test_explicit_input_resolves_ambiguity(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    shutil.copy(data_dir / "lecture.mp4", data_dir / "second.mp4")
    config = config_factory(data_dir=data_dir, duration_seconds=5.0)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run(
        input_name="second.mp4"
    )
    assert result.source.name == "second.mp4"
    assert result.paths.video.name == "second_test_5s.mp4"


@requires_ffmpeg
def test_second_run_is_protected_then_overwritten(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=5.0)
    first = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    with pytest.raises(OutputExistsError):
        build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()
    assert first.paths.transcript_text.read_text(encoding="utf-8").strip() == TRANSCRIPT

    rerun_config = config_factory(data_dir=data_dir, duration_seconds=5.0, overwrite=True)
    second = build_pipeline(
        config=rerun_config, data_dir=data_dir, groq_factory=groq_factory, overwrite=True
    ).run()
    assert second.status == "success"


@requires_ffmpeg
def test_audio_corresponds_to_extracted_interval(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=6.0)
    build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    payload = groq_factory.last_upload
    assert payload[:4] == b"fLaC"  # real FLAC audio, not an empty placeholder
    assert len(payload) > 0
    assert groq_factory.last_request["file"][0].endswith(".flac")


@requires_ffmpeg
def test_temporary_audio_is_cleaned_up(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=5.0)
    build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    import tempfile

    leftovers = [
        Path(p)
        for p in Path(tempfile.gettempdir()).glob("al-brooks-phase1-*")
        if p.is_dir() and any(p.glob("*.flac"))
    ]
    assert leftovers == []


@requires_ffmpeg
def test_temp_audio_kept_with_keep_temp_flag(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=5.0, keep_temp=True)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()

    import tempfile

    kept = list(Path(tempfile.gettempdir()).glob("al-brooks-phase1-*/*.flac"))
    for path in kept:
        shutil.rmtree(path.parent, ignore_errors=True)
    assert any("kept" in w for w in result.warnings) or kept == []


@requires_ffmpeg
def test_transcription_failure_leaves_no_half_written_json(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    from src.errors import TranscriptionAuthError

    class StatusError(Exception):
        status_code = 401

    config = config_factory(data_dir=data_dir, duration_seconds=5.0)
    pipeline = build_pipeline(
        config=config,
        data_dir=data_dir,
        groq_factory=groq_factory,
        responses=[StatusError("invalid api key")],
    )
    with pytest.raises(TranscriptionAuthError):
        pipeline.run()

    assert not (config.output_dir / "lecture_test_5s.en.json").exists()
    assert not (config.output_dir / "lecture_test_5s.en.txt").exists()
    # the extracted video is still there and is valid
    assert (config.output_dir / "lecture_test_5s.mp4").is_file()


@requires_ffmpeg
def test_source_without_audio_is_rejected(
    tmp_path: Path, config_factory: Any, groq_factory: FakeFactory
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(data_dir / "silent.mp4"),
        ]
    )
    config = config_factory(data_dir=data_dir)
    with pytest.raises(InvalidInputError, match="no audio stream"):
        build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()


@requires_ffmpeg
def test_final_report_lists_all_paths(
    tmp_path: Path, config_factory: Any, data_dir: Path, groq_factory: FakeFactory
) -> None:
    config = config_factory(data_dir=data_dir, duration_seconds=5.0)
    result = build_pipeline(config=config, data_dir=data_dir, groq_factory=groq_factory).run()
    report = render_final_report(result)

    assert "STATUS: SUCCESS" in report
    assert str(result.paths.video) in report
    assert str(result.paths.transcript_text) in report
    assert str(result.paths.transcript_json) in report
    assert str(result.paths.metadata_json) in report
