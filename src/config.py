"""Configuration objects and environment validation.

The Windows environment variable ``GROQ_API_KEY`` is the single source of
truth for authentication. A ``.env`` file is *optional* and only consulted as a
fallback so that users who keep the key in the Windows environment do not have
to duplicate it anywhere.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

from .errors import ConfigurationError, MissingApiKeyError, ToolNotFoundError

#: Repository root (the directory that contains ``src/``).
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: Default transcription model. Verified against the Groq model listing for this
#: account: ``whisper-large-v3`` and ``whisper-large-v3-turbo`` are available.
DEFAULT_MODEL: Final[str] = "whisper-large-v3-turbo"

#: Supported Groq speech-to-text model IDs (documentation / validation only).
KNOWN_TRANSCRIPTION_MODELS: Final[tuple[str, ...]] = (
    "whisper-large-v3-turbo",
    "whisper-large-v3",
)

#: Documented upload ceiling of the Groq speech-to-text endpoint.
DEFAULT_MAX_AUDIO_BYTES: Final[int] = 25 * 1024 * 1024

#: Extensions accepted as input, all of which FFmpeg/FFprobe can demux.
SUPPORTED_VIDEO_EXTENSIONS: Final[tuple[str, ...]] = (
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".ts",
    ".wmv",
    ".flv",
    ".ogv",
)

API_KEY_ENV_VAR: Final[str] = "GROQ_API_KEY"
MODEL_ENV_VAR: Final[str] = "GROQ_TRANSCRIPTION_MODEL"
FFMPEG_ENV_VAR: Final[str] = "FFMPEG_PATH"
FFPROBE_ENV_VAR: Final[str] = "FFPROBE_PATH"

#: Executable names probed on Windows (and POSIX) when resolving FFmpeg tools.
_FFMPEG_NAMES: Final[tuple[str, ...]] = ("ffmpeg.exe", "ffmpeg")
_FFPROBE_NAMES: Final[tuple[str, ...]] = ("ffprobe.exe", "ffprobe")


def _load_optional_dotenv() -> None:
    """Load ``.env`` into ``os.environ`` without overriding real env vars.

    Silently does nothing when ``python-dotenv`` is unavailable or no ``.env``
    file exists; the Windows environment variable remains sufficient.
    """

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is declared, be forgiving
        return
    dotenv_path = PROJECT_ROOT / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path, override=False)


def _resolve_executable(env_var: str, names: tuple[str, ...]) -> Path | None:
    """Resolve an executable from an explicit env var or from ``PATH``."""

    override = os.environ.get(env_var, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        found = shutil.which(override)
        if found:
            return Path(found).resolve()
        raise ToolNotFoundError(f"{env_var} points to '{override}', which is not an existing file.")

    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


@dataclass(frozen=True)
class AppConfig:
    """Fully validated runtime configuration."""

    project_root: Path
    data_dir: Path
    output_dir: Path
    log_dir: Path
    duration_seconds: float
    model: str
    language: str
    api_key: str
    ffmpeg_path: Path
    ffprobe_path: Path
    audio_sample_rate: int = 16_000
    audio_channels: int = 1
    audio_codec: str = "flac"
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    retry_max_backoff_seconds: float = 30.0
    overwrite: bool = False
    keep_temp: bool = False
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def transcript_language(self) -> str:
        """Language code requested from the transcription endpoint."""

        return self.language

    def with_overrides(self, **overrides: object) -> AppConfig:
        """Return a copy with the given (non-``None``) fields replaced."""

        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self  # type: ignore[arg-type]


@dataclass(frozen=True)
class EnvironmentInfo:
    """Raw, not-yet-validated values read from the process environment."""

    data_dir: Path
    output_dir: Path
    log_dir: Path
    duration_seconds: float
    model: str
    language: str
    api_key: str | None
    ffmpeg_path: Path | None
    ffprobe_path: Path | None
    overwrite: bool = False
    keep_temp: bool = False


def resolve_tool_paths() -> tuple[Path | None, Path | None]:
    """Resolve ffmpeg/ffprobe without raising. Used by ``--check-env``."""

    _load_optional_dotenv()
    return (
        _resolve_executable(FFMPEG_ENV_VAR, _FFMPEG_NAMES),
        _resolve_executable(FFPROBE_ENV_VAR, _FFPROBE_NAMES),
    )


def read_environment(
    *,
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    log_dir: Path | None = None,
    duration_seconds: float | None = None,
    model: str | None = None,
    language: str | None = None,
    overwrite: bool = False,
    keep_temp: bool = False,
) -> EnvironmentInfo:
    """Collect settings from CLI arguments and the process environment.

    Precedence: explicit CLI argument > environment variable > built-in default.
    The API key is *only* ever read from the environment.
    """

    _load_optional_dotenv()

    raw_duration = (
        duration_seconds
        if duration_seconds is not None
        else _float_from_env("AL_BROOKS_DURATION_SECONDS", 30.0)
    )

    return EnvironmentInfo(
        data_dir=Path(data_dir).resolve() if data_dir else PROJECT_ROOT / "data",
        output_dir=Path(output_dir).resolve() if output_dir else PROJECT_ROOT / "output",
        log_dir=Path(log_dir).resolve() if log_dir else PROJECT_ROOT / "logs",
        duration_seconds=raw_duration,
        model=(model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL).strip(),
        language=(language or os.environ.get("AL_BROOKS_LANGUAGE") or "en").strip(),
        api_key=os.environ.get(API_KEY_ENV_VAR) or None,
        ffmpeg_path=_resolve_executable(FFMPEG_ENV_VAR, _FFMPEG_NAMES),
        ffprobe_path=_resolve_executable(FFPROBE_ENV_VAR, _FFPROBE_NAMES),
        overwrite=overwrite,
        keep_temp=keep_temp,
    )


def build_config(env: EnvironmentInfo) -> AppConfig:
    """Validate an :class:`EnvironmentInfo` and produce an :class:`AppConfig`.

    Raises:
        MissingApiKeyError: when ``GROQ_API_KEY`` is absent or empty.
        ToolNotFoundError: when ffmpeg or ffprobe cannot be located.
        ConfigurationError: for any other invalid setting.
    """

    problems: list[str] = []

    if env.api_key is None or not env.api_key.strip():
        problems.append(_api_key_hint())

    if env.ffmpeg_path is None:
        problems.append(
            "ffmpeg was not found on PATH. Install FFmpeg and add its bin folder "
            "to PATH, or set FFMPEG_PATH to the full path of ffmpeg.exe."
        )
    if env.ffprobe_path is None:
        problems.append(
            "ffprobe was not found on PATH. Install FFmpeg (ffprobe ships with it) "
            "and add its bin folder to PATH, or set FFPROBE_PATH."
        )

    if env.duration_seconds <= 0:
        problems.append(f"--duration must be greater than 0 (got {env.duration_seconds}).")

    if not env.model:
        problems.append("The transcription model name must not be empty.")

    if not env.language:
        problems.append("The transcription language must not be empty.")

    if problems:
        raise ConfigurationError("\n".join(f"- {p}" for p in problems))

    assert env.api_key is not None  # narrowed by the validation above
    assert env.ffmpeg_path is not None
    assert env.ffprobe_path is not None

    return AppConfig(
        project_root=PROJECT_ROOT,
        data_dir=env.data_dir,
        output_dir=env.output_dir,
        log_dir=env.log_dir,
        duration_seconds=float(env.duration_seconds),
        model=env.model,
        language=env.language,
        api_key=env.api_key.strip(),
        ffmpeg_path=env.ffmpeg_path,
        ffprobe_path=env.ffprobe_path,
        overwrite=env.overwrite,
        keep_temp=env.keep_temp,
        extra={"api_key_env_var": API_KEY_ENV_VAR, "model_env_var": MODEL_ENV_VAR},
    )


def require_api_key() -> str:
    """Return the API key or raise :class:`MissingApiKeyError`.

    Used by the CLI in ``--check-env`` mode and by unit tests.
    """

    _load_optional_dotenv()
    key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not key:
        raise MissingApiKeyError(_api_key_hint())
    return key


def _api_key_hint() -> str:
    return (
        "GROQ_API_KEY is not set. Set it as a Windows environment variable:\n"
        '  PowerShell (current session):  $env:GROQ_API_KEY = "gsk_..."\n'
        "  PowerShell (persistent):       "
        '[Environment]::SetEnvironmentVariable("GROQ_API_KEY", "gsk_...", "User")\n'
        '  cmd.exe:                       setx GROQ_API_KEY "gsk_..."\n'
        "Check that it exists without printing the value:\n"
        '  if ($env:GROQ_API_KEY) { "GROQ_API_KEY is set" } else { "GROQ_API_KEY is missing" }\n'
        "The key is created at https://console.groq.com/keys and is never read "
        "from a file or a command line argument."
    )


def _float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number (got '{raw}').") from exc
