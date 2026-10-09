"""Phase 2 CLI tests plus an integration run on the real 30 second artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from src.cli import build_parser, main
from src.media import MediaService
from src.outputs import PreparedPaths
from src.preparation import TranscriptPreparer
from src.qa import FindingType
from src.transcription import GroqTranscriber
from src.verification import VerificationStatus

from .conftest import FFMPEG, FFPROBE, known_duplicate_payload, requires_ffmpeg


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
def test_phase1_flags_still_parse() -> None:
    """Phase 2 must not break any Phase 1 option."""

    args = build_parser().parse_args(
        ["--input", "lecture.mp4", "--duration", "30", "--overwrite", "-v"]
    )
    assert args.input == "lecture.mp4"
    assert args.duration == 30.0
    assert args.overwrite is True
    assert args.verbose is True


def test_prepare_transcript_flag_parses() -> None:
    args = build_parser().parse_args(["--prepare-transcript", "out/lecture_test_30s.en.json"])
    assert args.prepare_transcript == "out/lecture_test_30s.en.json"
    assert args.verify_transcript is False
    assert args.verify_attempts == 1
    assert args.audio is None


def test_verification_flags_parse() -> None:
    args = build_parser().parse_args(
        [
            "--prepare-transcript",
            "out/a.en.json",
            "--verify-transcript",
            "--audio",
            "out/a.flac",
            "--verify-attempts",
            "3",
        ]
    )
    assert args.verify_transcript is True
    assert args.audio == "out/a.flac"
    assert args.verify_attempts == 3


def test_help_documents_phase_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_help_output_mentions_phase2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for token in (
        "--prepare-transcript",
        "--verify-transcript",
        "--audio",
        "--verify-attempts",
        ".en.clean.json",
        ".qa.json",
    ):
        assert token in out


# ----------------------------------------------------------------------
# Exit codes
# ----------------------------------------------------------------------
def test_verify_without_prepare_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--verify-transcript"]) == 2
    assert "--prepare-transcript" in capsys.readouterr().out


def test_missing_raw_artifact_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--prepare-transcript",
            str(tmp_path / "nope.en.json"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 7
    assert "not found" in capsys.readouterr().out


def test_malformed_raw_artifact_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.en.json"
    bad.write_text("{not json", encoding="utf-8")
    code = main(["--prepare-transcript", str(bad), "--log-dir", str(tmp_path / "logs")])
    assert code == 7


def test_prepare_works_without_api_key(tmp_path: Path) -> None:
    """Plain preparation must not require GROQ_API_KEY."""

    raw = tmp_path / "output" / "lecture_test_30s.en.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")

    code = main(["--prepare-transcript", str(raw), "--log-dir", str(tmp_path / "logs")])
    assert code == 0


def test_invalid_verify_attempts(tmp_path: Path) -> None:
    raw = tmp_path / "output" / "a.en.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")
    code = main(["--prepare-transcript", str(raw), "--verify-transcript", "--verify-attempts", "0"])
    assert code == 2


# ----------------------------------------------------------------------
# End-to-end through the CLI
# ----------------------------------------------------------------------
def test_cli_prepare_writes_all_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "output" / "lecture_test_30s.en.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")

    assert main(["--prepare-transcript", str(raw), "--log-dir", str(tmp_path / "logs")]) == 0

    out = capsys.readouterr().out
    assert "Phase 2 preparation complete" in out
    assert "NEEDS_REVIEW" in out

    paths = PreparedPaths.from_raw(raw)
    for path in paths.all:
        assert path.is_file()
        assert path.stat().st_size > 0


def test_cli_prepare_preserves_raw(tmp_path: Path) -> None:
    raw = tmp_path / "output" / "lecture_test_30s.en.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")
    before = raw.read_bytes()

    main(["--prepare-transcript", str(raw), "--log-dir", str(tmp_path / "logs")])
    assert raw.read_bytes() == before


def test_cli_prepare_second_run_needs_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "output" / "lecture_test_30s.en.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")
    args = ["--prepare-transcript", str(raw), "--log-dir", str(tmp_path / "logs")]

    assert main(args) == 0
    capsys.readouterr()
    assert main(args) == 6
    assert "--overwrite" in capsys.readouterr().out

    assert main([*args, "--overwrite"]) == 0


def test_cli_flagged_findings_reach_the_user(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "output" / "lecture_test_30s.en.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")
    main(["--prepare-transcript", str(raw), "--log-dir", str(tmp_path / "logs")])

    out = capsys.readouterr().out
    assert str(FindingType.EXACT_DUPLICATE_SEGMENT) in out
    assert str(FindingType.DUPLICATE_DURATION_MISMATCH) in out


def test_module_entry_point_runs_prepare(tmp_path: Path) -> None:
    import subprocess
    import sys

    from .conftest import PROJECT_ROOT

    raw = tmp_path / "output" / "lecture_test_30s.en.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps(known_duplicate_payload()), encoding="utf-8")

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "src.main", "--prepare-transcript", str(raw)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
        timeout=120,
    )
    assert result.returncode == 0
    assert "Phase 2 preparation complete" in result.stdout


# ----------------------------------------------------------------------
# Integration against the real 30 second artifacts
# ----------------------------------------------------------------------
def _real_artifacts() -> Path | None:
    """Locate the real Phase 1 output, if the user has generated it.

    The test is skipped when it is absent so the suite never depends on a
    particular user's video or on a prior API call.
    """

    from .conftest import PROJECT_ROOT

    candidate = PROJECT_ROOT / "output" / "BTR20140930-9439edit_test_30s.en.json"
    return candidate if candidate.is_file() else None


@pytest.mark.skipif(_real_artifacts() is None, reason="real Phase 1 artifact not present")
def test_integration_on_real_sample(tmp_path: Path) -> None:
    """Runs against the genuine duplicated transcript, with no API calls."""

    from src.config import PROJECT_ROOT, AppConfig
    from src.media import MediaService
    from src.preparation import TranscriptPreparer
    from src.verification import VerificationStatus

    from .conftest import FFMPEG, FFPROBE

    raw_path = _real_artifacts()
    assert raw_path is not None
    work = tmp_path / "work"
    work.mkdir()
    local_raw = work / raw_path.name
    shutil.copy(raw_path, local_raw)

    config = AppConfig(
        project_root=PROJECT_ROOT,
        data_dir=Path(PROJECT_ROOT / "data"),
        output_dir=work,
        log_dir=tmp_path / "logs",
        duration_seconds=30.0,
        model="whisper-large-v3-turbo",
        language="en",
        api_key="gsk_testONLYnotarealkey0000000000000000",
        ffmpeg_path=Path(FFMPEG or "ffmpeg"),
        ffprobe_path=Path(FFPROBE or "ffprobe"),
    )
    preparer = TranscriptPreparer(
        config, media_service=MediaService(Path(FFMPEG or "f"), Path(FFPROBE or "p"))
    )
    result = preparer.prepare(local_raw)

    assert result.status == "needs_review"
    assert result.report.has(FindingType.EXACT_DUPLICATE_SEGMENT)
    assert result.report.has(FindingType.DUPLICATE_DURATION_MISMATCH)
    assert result.report.has(FindingType.SEGMENT_TOO_LONG)
    assert result.verification.status is VerificationStatus.NOT_PERFORMED

    # The duplication is preserved, never removed on suspicion alone.
    cleaned = result.paths.cleaned_text.read_text(encoding="utf-8")
    assert cleaned.count("Sorry about being a couple of minutes late") == 2
    assert local_raw.read_bytes() == raw_path.read_bytes()


def _offline_client_factory(_api_key: str) -> Any:
    """Client factory that never touches the network."""

    class _OfflineTranscriptions:
        def create(self, **_kwargs: Any) -> Any:
            raise ConnectionError("offline test double")

    class _OfflineAudio:
        transcriptions = _OfflineTranscriptions()

    class _OfflineClient:
        audio = _OfflineAudio()

    return _OfflineClient()


@requires_ffmpeg
def test_integration_reports_no_audio_available_without_source(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any
) -> None:
    """With verification requested but no recoverable audio, say what is missing.

    The transcription client is an offline double, so this test performs no
    network request and cannot incur any API usage.
    """

    payload = known_duplicate_payload()
    payload.pop("source_path")
    payload.pop("source_file")
    raw_path = write_raw_transcript(payload)

    config = config_factory(data_dir=tmp_path / "empty-data")
    preparer = TranscriptPreparer(
        config,
        media_service=MediaService(Path("ffmpeg"), Path("ffprobe")),
        transcriber=GroqTranscriber(
            api_key=config.api_key,
            model=config.model,
            client_factory=_offline_client_factory,
            sleep=lambda _s: None,
        ),
    )

    result = preparer.prepare(raw_path, verify=True)

    assert result.verification.status is VerificationStatus.INCONCLUSIVE
    assert result.verification.status.supports_correction is False
    assert "--audio" in "\n".join(result.warnings)
    # The duplicate is still preserved.
    assert len(result.clean.segments) == 3


@requires_ffmpeg
def test_integration_reextracts_audio_and_reports_api_failure(
    tmp_path: Path, config_factory: Any, write_raw_transcript: Any, synthetic_video: Path
) -> None:
    """Audio for verification is re-derived from the source video with FFmpeg.

    The offline client double makes the transcription attempt fail, proving the
    audio path works without any network access.
    """

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(synthetic_video, data_dir / "clip.mp4")
    payload = known_duplicate_payload(source_file="clip.mp4")
    payload.pop("source_path")
    raw_path = write_raw_transcript(payload)

    config = config_factory(data_dir=data_dir)
    preparer = TranscriptPreparer(
        config,
        media_service=MediaService(Path(FFMPEG or "ffmpeg"), Path(FFPROBE or "ffprobe")),
        transcriber=GroqTranscriber(
            api_key=config.api_key,
            model=config.model,
            max_retries=1,
            client_factory=_offline_client_factory,
            sleep=lambda _s: None,
        ),
    )

    result = preparer.prepare(raw_path, verify=True, verify_attempts=1)

    # The audio was located (no "missing audio" warning) but the request failed.
    assert not result.warnings
    assert result.verification.status is VerificationStatus.FAILED
    assert "offline test double" in result.verification.detail
    assert len(result.clean.segments) == 3
