"""Tests for the CLI surface (argument parsing and exit codes)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from src.cli import build_parser, main
from src.config import PROJECT_ROOT

from .conftest import requires_ffmpeg


def test_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--input", "--duration", "--overwrite", "--check-env", "--version"):
        assert flag in out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    from src import __version__

    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert __version__ == "0.3.1"


def test_default_duration_is_documented() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.duration is None  # applied later, from env or default 30


def test_parses_input_and_duration() -> None:
    args = build_parser().parse_args(["--input", "lecture.mp4", "--duration", "45"])
    assert args.input == "lecture.mp4"
    assert args.duration == 45.0


def test_parses_short_flags() -> None:
    args = build_parser().parse_args(["-i", "a.mp4", "-d", "10", "-v"])
    assert args.input == "a.mp4"
    assert args.duration == 10.0
    assert args.verbose is True


def test_unknown_flag_fails() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--nope"])
    assert excinfo.value.code == 2


# ----------------------------------------------------------------------
# Exit codes
# ----------------------------------------------------------------------
def test_missing_api_key_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "clip.mp4").write_bytes(b"\x00" * 4096)

    code = main(
        [
            "--input",
            "clip.mp4",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 2
    assert "GROQ_API_KEY" in capsys.readouterr().out


def test_missing_data_dir_exit_code(tmp_path: Path, fake_api_key: str) -> None:
    code = main(
        [
            "--data-dir",
            str(tmp_path / "absent"),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 3


def test_no_video_exit_code(tmp_path: Path, fake_api_key: str) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    code = main(
        [
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 3


def test_ambiguous_input_exit_code(tmp_path: Path, fake_api_key: str) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("a.mp4", "b.mp4"):
        (data_dir / name).write_bytes(b"\x00" * 4096)
    code = main(
        [
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 3


def test_unknown_input_exit_code(tmp_path: Path, fake_api_key: str) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "a.mp4").write_bytes(b"\x00" * 4096)
    code = main(
        [
            "--input",
            "zzz.mp4",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 3


def test_invalid_duration_exit_code(tmp_path: Path, fake_api_key: str) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    code = main(
        [
            "--duration",
            "0",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 2


# ----------------------------------------------------------------------
# Informational commands
# ----------------------------------------------------------------------
@requires_ffmpeg
def test_check_env_passes(fake_api_key: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check-env"]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg version" in out
    assert "GROQ_API_KEY is set" in out
    assert fake_api_key not in out  # the value is never printed


def test_check_env_fails_without_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check-env"]) == 2
    assert "Environment check FAILED" in capsys.readouterr().out


def test_check_env_reports_missing_ffmpeg(
    fake_api_key: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("src.cli.resolve_tool_paths", lambda: (None, None))
    assert main(["--check-env"]) == 2
    assert "ffmpeg: not found" in capsys.readouterr().out


def test_list_inputs_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert main(["--list-inputs", "--data-dir", str(data_dir)]) == 0
    assert "No supported video" in capsys.readouterr().out


def test_list_inputs_single(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "solo.mp4").write_bytes(b"\x00" * 4096)
    assert main(["--list-inputs", "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "solo.mp4" in out
    assert "processed automatically" in out


def test_list_inputs_multiple(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("one.mp4", "two.mp4"):
        (data_dir / name).write_bytes(b"\x00" * 4096)
    assert main(["--list-inputs", "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "one.mp4" in out and "two.mp4" in out
    assert "--input" in out


def test_list_inputs_missing_directory(tmp_path: Path) -> None:
    assert main(["--list-inputs", "--data-dir", str(tmp_path / "gone")]) == 3


# ----------------------------------------------------------------------
# Integration through the CLI
# ----------------------------------------------------------------------
@requires_ffmpeg
def test_cli_runs_full_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_video: Path,
    fake_api_key: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(synthetic_video, data_dir / "lecture.mp4")

    monkeypatch.setattr(
        "src.pipeline.GroqTranscriber._call_api",
        lambda self, audio_path: {
            "text": "Good morning, this is a test transcript.",
            "language": "en",
            "duration": 8.0,
            "segments": [{"id": 0, "start": 0.0, "end": 8.0, "text": "Good morning, ..."}],
        },
    )

    output_dir = tmp_path / "out"
    code = main(
        [
            "--input",
            "lecture.mp4",
            "--duration",
            "8",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert code == 0

    out = capsys.readouterr().out
    assert "Stage 1/7" in out and "Stage 7/7" in out
    assert "STATUS: SUCCESS" in out
    assert (output_dir / "lecture_test_8s.mp4").is_file()
    assert "test transcript" in (output_dir / "lecture_test_8s.en.txt").read_text(encoding="utf-8")
    assert list((tmp_path / "logs").glob("run-*.log"))


@requires_ffmpeg
def test_cli_key_is_never_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_video: Path,
    fake_api_key: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(synthetic_video, data_dir / "lecture.mp4")
    monkeypatch.setattr(
        "src.pipeline.GroqTranscriber._call_api",
        lambda self, audio_path: {"text": "hi", "language": "en"},
    )

    log_dir = tmp_path / "logs"
    main(
        [
            "--duration",
            "5",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--log-dir",
            str(log_dir),
        ]
    )
    captured = capsys.readouterr().out
    log_text = "".join(p.read_text(encoding="utf-8") for p in log_dir.glob("run-*.log"))
    assert fake_api_key not in captured
    assert fake_api_key not in log_text


def test_module_entry_point_exists() -> None:
    assert (PROJECT_ROOT / "src" / "main.py").is_file()


def test_run_as_module_reports_usage() -> None:
    """``python -m src.main`` must be wired to the same entry point."""

    import subprocess
    import sys

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "src.main", "--help"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
        timeout=120,
    )
    assert result.returncode == 0
    assert "--duration" in result.stdout
