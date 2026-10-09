"""Tests for :mod:`src.config`."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.config import (
    DEFAULT_MODEL,
    PROJECT_ROOT,
    build_config,
    read_environment,
    require_api_key,
    resolve_tool_paths,
)
from src.errors import ConfigurationError, MissingApiKeyError, ToolNotFoundError

from .conftest import FFMPEG, FFPROBE


@pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg/ffprobe required")
def test_build_config_uses_defaults(tmp_path: Path, fake_api_key: str) -> None:
    env = read_environment(data_dir=tmp_path)
    config = build_config(env)

    assert config.data_dir == tmp_path.resolve()
    assert config.output_dir == (PROJECT_ROOT / "output")
    assert config.duration_seconds == 30.0
    assert config.model == DEFAULT_MODEL
    assert config.language == "en"
    assert config.api_key == fake_api_key
    assert config.ffmpeg_path == resolve_tool_paths()[0]


def test_missing_api_key_is_rejected(tmp_path: Path) -> None:
    env = read_environment(data_dir=tmp_path)
    with pytest.raises(ConfigurationError) as excinfo:
        build_config(env)

    message = str(excinfo.value)
    assert "GROQ_API_KEY" in message
    assert "EnvironmentVariable" in message


def test_empty_api_key_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    env = read_environment(data_dir=tmp_path)
    with pytest.raises(ConfigurationError):
        build_config(env)


def test_require_api_key_reports_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MissingApiKeyError):
        require_api_key()


def test_require_api_key_returns_value(fake_api_key: str) -> None:
    assert require_api_key() == fake_api_key


def test_invalid_duration_is_rejected(tmp_path: Path, fake_api_key: str) -> None:
    env = read_environment(data_dir=tmp_path, duration_seconds=0)
    with pytest.raises(ConfigurationError, match="duration"):
        build_config(env)


def test_negative_duration_is_rejected(tmp_path: Path, fake_api_key: str) -> None:
    env = read_environment(data_dir=tmp_path, duration_seconds=-5)
    with pytest.raises(ConfigurationError, match="duration"):
        build_config(env)


def test_missing_ffmpeg_is_reported(
    tmp_path: Path, fake_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.config._resolve_executable", lambda env_var, names: None)
    env = read_environment(data_dir=tmp_path)
    with pytest.raises(ConfigurationError) as excinfo:
        build_config(env)
    message = str(excinfo.value)
    assert "ffmpeg" in message and "ffprobe" in message


def test_bad_ffmpeg_override_raises(
    tmp_path: Path, fake_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FFMPEG_PATH", str(tmp_path / "does-not-exist.exe"))
    with pytest.raises(ToolNotFoundError):
        read_environment(data_dir=tmp_path)


def test_model_env_override(
    tmp_path: Path, fake_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3")
    env = read_environment(data_dir=tmp_path)
    assert build_config(env).model == "whisper-large-v3"


def test_cli_argument_beats_environment(
    tmp_path: Path, fake_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3")
    env = read_environment(data_dir=tmp_path, model="whisper-large-v3-turbo")
    assert build_config(env).model == "whisper-large-v3-turbo"


def test_duration_env_override(
    tmp_path: Path, fake_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AL_BROOKS_DURATION_SECONDS", "12.5")
    env = read_environment(data_dir=tmp_path)
    assert build_config(env).duration_seconds == 12.5


def test_duration_env_override_must_be_numeric(
    tmp_path: Path, fake_api_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AL_BROOKS_DURATION_SECONDS", "thirty")
    with pytest.raises(ConfigurationError, match="AL_BROOKS_DURATION_SECONDS"):
        read_environment(data_dir=tmp_path)


def test_windows_style_paths_are_resolved(tmp_path: Path, fake_api_key: str) -> None:
    """Backslash paths must survive Path() handling unchanged in meaning."""

    env = read_environment(data_dir=Path(r"D:\Projects\al-brooks-dubbed\data"))
    assert env.data_dir.name == "data"
    assert env.data_dir.parent.name == "al-brooks-dubbed"
    assert env.data_dir.drive == "D:"


def test_config_with_overrides_is_immutable(tmp_path: Path, fake_api_key: str) -> None:
    config = build_config(read_environment(data_dir=tmp_path))
    updated = config.with_overrides(overwrite=True)
    assert updated.overwrite is True
    assert config.overwrite is False


def test_error_message_does_not_leak_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "gsk_supersecretvalue0123456789"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    monkeypatch.setattr("src.config._resolve_executable", lambda env_var, names: None)
    with pytest.raises(ConfigurationError) as excinfo:
        build_config(read_environment(data_dir=tmp_path))
    assert secret not in str(excinfo.value)
