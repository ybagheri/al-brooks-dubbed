"""Shared pytest fixtures.

Unit tests never touch the real Groq API, the real ``data/`` directory or the
user's environment: every test runs against ``tmp_path`` and mocked clients.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from src.config import PROJECT_ROOT, AppConfig
from src.discovery import InputResolver
from src.media import MediaService
from src.outputs import OutputWriter
from src.transcription import GroqTranscriber

FFMPEG = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe.exe") or shutil.which("ffprobe")

requires_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg/ffprobe are not available on PATH",
)


# ----------------------------------------------------------------------
# Environment isolation
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ambient configuration so tests never depend on the developer PC."""

    for name in (
        "GROQ_API_KEY",
        "GROQ_TRANSCRIPTION_MODEL",
        "FFMPEG_PATH",
        "FFPROBE_PATH",
        "AL_BROOKS_DURATION_SECONDS",
        "AL_BROOKS_LANGUAGE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def fake_api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A syntactically valid but fake key - never a real credential."""

    key = "gsk_testONLYnotarealkey0000000000000000"
    monkeypatch.setenv("GROQ_API_KEY", key)
    return key


# ----------------------------------------------------------------------
# Fake Groq client
# ----------------------------------------------------------------------
class FakeTranscriptions:
    """Stand-in for ``client.audio.transcriptions``."""

    def __init__(
        self, recorder: list[dict[str, Any]], responses: list[Any], uploads: list[bytes]
    ) -> None:
        self._recorder = recorder
        self._responses = list(responses)
        self._uploads = uploads

    def create(self, **kwargs: Any) -> Any:
        # Snapshot the payload eagerly: the real SDK closes the handle as soon
        # as the request completes, exactly like our client does.
        payload = kwargs.get("file")
        if isinstance(payload, tuple) and len(payload) == 2 and hasattr(payload[1], "read"):
            self._uploads.append(payload[1].read())
        self._recorder.append(kwargs)
        if not self._responses:
            raise AssertionError("Groq fake called more times than the test expected")
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAudio:
    def __init__(self, transcriptions: FakeTranscriptions) -> None:
        self.transcriptions = transcriptions


class FakeClient:
    def __init__(
        self, responses: list[Any], recorder: list[dict[str, Any]], uploads: list[bytes]
    ) -> None:
        self.audio = FakeAudio(FakeTranscriptions(recorder, responses, uploads))


class FakeFactory:
    """Callable client factory that records the key it was handed."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.uploads: list[bytes] = []
        self.keys: list[str] = []

    def __call__(self, api_key: str) -> Any:
        self.keys.append(api_key)
        return FakeClient(self.responses, self.calls, self.uploads)

    @property
    def last_request(self) -> dict[str, Any]:
        return self.calls[-1]

    @property
    def last_upload(self) -> bytes:
        return self.uploads[-1]


@pytest.fixture
def groq_factory() -> FakeFactory:
    return FakeFactory([])


def make_transcriber(
    groq_factory: FakeFactory,
    *,
    responses: list[Any] | None = None,
    model: str = "whisper-large-v3-turbo",
    max_retries: int = 3,
    backoff_seconds: float = 0.0,
    max_audio_bytes: int = 25 * 1024 * 1024,
) -> GroqTranscriber:
    """Build a :class:`GroqTranscriber` wired to the fake client."""

    if responses is not None:
        groq_factory.responses = list(responses)
    return GroqTranscriber(
        api_key="gsk_testONLYnotarealkey0000000000000000",
        model=model,
        language="en",
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        max_backoff_seconds=backoff_seconds,
        max_audio_bytes=max_audio_bytes,
        client_factory=groq_factory,
        sleep=lambda _seconds: None,
    )


# ----------------------------------------------------------------------
# Media helpers
# ----------------------------------------------------------------------
def ffmpeg_path() -> Path:
    """Absolute path of the ffmpeg executable (asserts availability)."""

    assert FFMPEG is not None, "ffmpeg is required for this test"
    return Path(FFMPEG).resolve()


def ffprobe_path() -> Path:
    """Absolute path of the ffprobe executable (asserts availability)."""

    assert FFPROBE is not None, "ffprobe is required for this test"
    return Path(FFPROBE).resolve()


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with a fixed argument list (no shell)."""

    assert FFMPEG is not None
    return subprocess.run(  # noqa: S603
        [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a tiny 12-second H.264/AAC test video with a spoken sine tone.

    Session scoped because encoding is slow on this hardware; the file is only
    a few hundred kilobytes and is deleted with the temporary directory.
    """

    if FFMPEG is None:
        pytest.skip("ffmpeg is not available")

    directory = tmp_path_factory.mktemp("synthetic-media")
    path = directory / "synthetic.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=15:duration=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=12",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(path),
        ]
    )
    return path


@pytest.fixture
def short_video(tmp_path: Path) -> Path:
    """A per-test 2-second video, used for duration-clamping behaviour."""

    path = tmp_path / "short.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=44100:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    return path


@pytest.fixture
def media_service() -> MediaService:
    assert FFMPEG is not None and FFPROBE is not None, "ffmpeg/ffprobe required"
    return MediaService(ffmpeg_path(), ffprobe_path())


# ----------------------------------------------------------------------
# Configuration / pipeline wiring
# ----------------------------------------------------------------------
@pytest.fixture
def config_factory(tmp_path: Path) -> Any:
    """Build an :class:`AppConfig` rooted inside ``tmp_path``."""

    assert FFMPEG is not None and FFPROBE is not None

    def _factory(
        *,
        data_dir: Path,
        output_dir: Path | None = None,
        duration_seconds: float = 30.0,
        model: str = "whisper-large-v3-turbo",
        overwrite: bool = False,
        keep_temp: bool = False,
    ) -> AppConfig:
        return AppConfig(
            project_root=PROJECT_ROOT,
            data_dir=data_dir,
            output_dir=output_dir or (tmp_path / "output"),
            log_dir=tmp_path / "logs",
            duration_seconds=duration_seconds,
            model=model,
            language="en",
            api_key="gsk_testONLYnotarealkey0000000000000000",
            ffmpeg_path=ffmpeg_path(),
            ffprobe_path=ffprobe_path(),
            overwrite=overwrite,
            keep_temp=keep_temp,
        )

    return _factory


@pytest.fixture
def resolver_factory() -> Any:
    def _factory(data_dir: Path) -> InputResolver:
        return InputResolver(data_dir)

    return _factory


@pytest.fixture
def writer_factory() -> Any:
    def _factory(output_dir: Path, overwrite: bool = False) -> OutputWriter:
        return OutputWriter(output_dir, overwrite=overwrite)

    return _factory


@pytest.fixture
def on_pythonpath() -> Iterator[None]:
    """Guarantee ``import src`` works regardless of how pytest was started."""

    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    yield
