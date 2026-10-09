"""Groq speech-to-text client.

Uses the official ``groq`` Python SDK and the dedicated audio transcription
endpoint (``/openai/v1/audio/transcriptions``) - never a chat completion model.

Error handling contract:

* permanent failures (bad key, unknown model, malformed request) raise
  :class:`~src.errors.TranscriptionAuthError` / :class:`TranscriptionError`
  immediately and are **never** retried;
* transient failures (timeouts, connection resets, HTTP 408/409/429/5xx) are
  retried with bounded exponential backoff;
* the API key is never placed in a message, log record or exception.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import (
    EmptyTranscriptError,
    TranscriptionAuthError,
    TranscriptionError,
    TranscriptionSizeError,
)

logger = logging.getLogger(__name__)

#: HTTP status codes worth retrying.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

#: Sentinel for "this error will never succeed on a retry".
_MAX_STATUS_SNIPPET = 400


@dataclass(frozen=True)
class TranscriptionSegment:
    """A single timed segment as returned by the API (never synthesised)."""

    id: int | str | None
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "text": self.text,
        }


@dataclass(frozen=True)
class TranscriptionResult:
    """Normalised transcription payload."""

    text: str
    model: str
    language: str | None = None
    duration_seconds: float | None = None
    segments: tuple[TranscriptionSegment, ...] = ()
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def has_segments(self) -> bool:
        return bool(self.segments)

    def to_metadata(self) -> dict[str, Any]:
        """Structured JSON body; contains only fields the API actually returned."""

        payload: dict[str, Any] = {
            "transcription_model": self.model,
            "requested_language": self.language,
            "transcript": self.text,
        }
        if self.duration_seconds is not None:
            payload["api_reported_duration_seconds"] = round(self.duration_seconds, 3)
        if self.segments:
            payload["segments"] = [segment.to_dict() for segment in self.segments]
        else:
            payload["segments"] = None
        return payload


class GroqTranscriber:
    """Thin, retrying client around the Groq speech-to-text endpoint."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        language: str | None = "en",
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 30.0,
        max_audio_bytes: int = 25 * 1024 * 1024,
        response_format: str = "verbose_json",
        client_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not api_key.strip():
            raise TranscriptionError(
                "A non-empty Groq API key is required to build a transcription client."
            )
        self._api_key = api_key.strip()
        self.model = model
        self.language = language
        self.max_retries = max(1, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.max_backoff_seconds = max(self.backoff_seconds, max_backoff_seconds)
        self.max_audio_bytes = max_audio_bytes
        self.response_format = response_format
        self._sleep = sleep
        self._client_factory = client_factory or _default_client_factory
        self._client: Any | None = None

    # ------------------------------------------------------------------
    @property
    def client(self) -> Any:
        """Lazily constructed SDK client."""

        if self._client is None:
            self._client = self._client_factory(self._api_key)
        return self._client

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe ``audio_path`` and return a normalised result."""

        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise TranscriptionError(f"Audio file to transcribe does not exist: {audio_path}")

        size = audio_path.stat().st_size
        self._check_size(size, audio_path)

        payload = self._request_with_retries(audio_path)
        return self._parse_response(payload)

    # ------------------------------------------------------------------
    def _check_size(self, size: int, audio_path: Path) -> None:
        if size > self.max_audio_bytes:
            raise TranscriptionSizeError(
                f"The prepared audio is {size / 1e6:.1f} MB which exceeds the "
                f"{self.max_audio_bytes / 1e6:.1f} MB upload limit of the Groq "
                f"transcription endpoint (file: {audio_path.name}).\n"
                "Re-run with a smaller --duration (e.g. --duration 30). "
                "The audio is deliberately NOT truncated automatically so that no "
                "speech is lost silently."
            )
        logger.debug("Uploading %.2f MB of audio for transcription.", size / 1e6)

    def _request_with_retries(self, audio_path: Path) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return self._call_api(audio_path)
            except _PermanentTranscriptionError:
                raise
            except _RetryableTranscriptionError as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                delay = min(self.max_backoff_seconds, self.backoff_seconds * (2 ** (attempt - 1)))
                logger.warning(
                    "Transient Groq error (attempt %d/%d): %s. Retrying in %.1fs.",
                    attempt,
                    self.max_retries,
                    exc,
                    delay,
                )
                self._sleep(delay)

        raise TranscriptionError(
            f"Groq transcription failed after {self.max_retries} attempt(s): {last_error}"
        ) from last_error

    def _call_api(self, audio_path: Path) -> Any:
        extra: dict[str, Any] = {}
        if self.response_format == "verbose_json":
            # Ask explicitly for segment timestamps; they are only present in
            # the verbose_json response format.
            extra["timestamp_granularities"] = ["segment"]
        try:
            with audio_path.open("rb") as handle:
                return self.client.audio.transcriptions.create(
                    file=(audio_path.name, handle),
                    model=self.model,
                    response_format=self.response_format,
                    language=self.language,
                    temperature=0,
                    **extra,
                )
        except Exception as exc:  # noqa: BLE001 - SDK raises a heterogeneous family
            raise _classify(exc) from None

    def _parse_response(self, payload: Any) -> TranscriptionResult:
        if payload is None:
            raise EmptyTranscriptError(
                "The transcription endpoint returned an empty response body."
            )

        data = _as_dict(payload)
        if not data:
            raise EmptyTranscriptError(
                "The transcription endpoint returned a response without any content."
            )

        text = data.get("text")
        if text is None and isinstance(payload, str):
            text = payload
        if not isinstance(text, str) or not text.strip():
            raise EmptyTranscriptError(
                "The transcription endpoint returned a successful response but no "
                "transcript text. The audio may contain no detectable speech."
            )

        segments = _parse_segments(data.get("segments"))
        language = data.get("language") or self.language
        duration = data.get("duration")
        duration_value = float(duration) if isinstance(duration, (int, float)) else None

        logger.info(
            "Transcription received: %d character(s), %d segment(s), language=%s",
            len(text),
            len(segments),
            language,
        )

        return TranscriptionResult(
            text=text.strip(),
            model=str(data.get("model") or self.model),
            language=str(language) if language else None,
            duration_seconds=duration_value,
            segments=segments,
            raw_response=data,
        )


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------
class _RetryableTranscriptionError(TranscriptionError):
    """Internal marker: the request may succeed if repeated."""


class _PermanentTranscriptionError(TranscriptionError):
    """Internal marker: repeating the request cannot help."""


def is_retryable_error(exc: BaseException) -> bool:
    """True when repeating the request could plausibly succeed.

    Used by higher-level loops (such as Phase 2 verification) so that they do
    not retry an invalid key, a forbidden model or an oversized upload.
    """

    if not isinstance(exc, Exception):  # pragma: no cover - defensive
        return False
    if isinstance(exc, (TranscriptionAuthError, TranscriptionSizeError, EmptyTranscriptError)):
        return False
    return isinstance(_classify(exc), _RetryableTranscriptionError)


def _classify(exc: Exception) -> TranscriptionError:
    """Map an SDK exception onto a retryable / permanent project error."""

    # Already-classified project errors pass through unchanged so that a
    # permanent failure is never re-classified as retryable further up.
    if isinstance(exc, (TranscriptionAuthError, TranscriptionSizeError)):
        return exc

    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)

    # Import lazily so unit tests can run without the SDK installed.
    auth_error: type[BaseException] | None
    rate_limit_error: type[BaseException] | None
    try:  # pragma: no cover - exercised only with the real SDK
        from groq import AuthenticationError, RateLimitError

        auth_error = AuthenticationError
        rate_limit_error = RateLimitError
    except Exception:  # pragma: no cover
        auth_error = rate_limit_error = None

    name = type(exc).__name__

    if auth_error is not None and isinstance(exc, auth_error):
        return TranscriptionAuthError(
            "Groq rejected the API key (HTTP 401 authentication failure).\n"
            "Check that GROQ_API_KEY holds a valid, active key from "
            "https://console.groq.com/keys and that the environment variable is set "
            "for the shell that runs this program. This is a permanent error and was "
            "not retried."
        )

    if status_code in (401, 403):
        return TranscriptionAuthError(
            f"Groq refused the request (HTTP {status_code}): {_short_detail(exc)}\n"
            "This is a permanent error and was not retried.\n"
            "A 403 with an otherwise valid key is most often a network routing "
            "problem rather than a key problem:\n"
            "  * If this PC routes traffic through a local proxy/VPN, make sure "
            "Python uses it too - set HTTPS_PROXY (e.g. $env:HTTPS_PROXY = "
            '"http://127.0.0.1:1080") or GROQ_PROXY.\n'
            "  * On Windows the application also auto-detects the proxy configured "
            "in Internet Settings; verify that detection is not being bypassed.\n"
            "  * Check that the key is active and that the account has API access "
            "enabled at https://console.groq.com/keys"
        )

    if rate_limit_error is not None and isinstance(exc, rate_limit_error):
        return _RetryableTranscriptionError(
            "Groq rate limit reached (HTTP 429). The free tier allows a limited "
            "number of audio minutes per day; wait and try again later."
        )

    if status_code == 413:
        return _PermanentTranscriptionError(
            "Groq rejected the upload because the file is too large (HTTP 413). "
            "Re-run with a smaller --duration."
        )

    if status_code in (400, 404, 422):
        detail = _short_detail(exc)
        return _PermanentTranscriptionError(
            f"Groq rejected the request (HTTP {status_code}) - permanent error, "
            f"not retried. Check the model name and the audio file. Detail: {detail}"
        )

    if status_code in RETRYABLE_STATUS_CODES or status_code is None:
        detail = _short_detail(exc)
        kind = f"HTTP {status_code}" if status_code else name
        return _RetryableTranscriptionError(f"{kind}: {detail}")

    return _PermanentTranscriptionError(f"{name} (HTTP {status_code}): {_short_detail(exc)}")


def _short_detail(exc: Exception) -> str:
    """Return a short, key-free description of an SDK exception."""

    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    # Never echo anything that looks like a credential.
    from .logging_utils import get_redacting_filter

    scrubbed = get_redacting_filter().scrub(message)
    return scrubbed[:_MAX_STATUS_SNIPPET]


# ----------------------------------------------------------------------
# Response helpers
# ----------------------------------------------------------------------
def _as_dict(payload: Any) -> dict[str, Any]:
    """Convert an SDK model / dict / string payload into a plain dict."""

    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, str):
        return {"text": payload}
    if hasattr(payload, "model_dump"):
        return dict(payload.model_dump())
    if hasattr(payload, "to_dict"):
        return dict(payload.to_dict())
    if hasattr(payload, "__dict__"):
        return {k: v for k, v in vars(payload).items() if not k.startswith("_")}
    return {}


def _parse_segments(raw_segments: Any) -> tuple[TranscriptionSegment, ...]:
    """Parse the API's ``segments`` array, skipping malformed entries."""

    if not raw_segments:
        return ()

    parsed: list[TranscriptionSegment] = []
    for index, item in enumerate(raw_segments):
        data = _as_dict(item)
        text = data.get("text")
        start = data.get("start")
        end = data.get("end")
        if not isinstance(text, str) or start is None or end is None:
            continue
        try:
            parsed.append(
                TranscriptionSegment(
                    id=data.get("id", index),
                    start=float(start),
                    end=float(end),
                    text=text,
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(parsed)


def _default_client_factory(api_key: str) -> Any:
    try:
        from groq import Groq
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise TranscriptionError(
            "The 'groq' package is not installed. Run: pip install -e .[dev]"
        ) from exc

    kwargs: dict[str, Any] = {}
    proxy = resolve_proxy()
    if proxy is not None:
        # Only needed when the proxy is not already visible through the
        # standard environment variables that httpx honours.
        try:
            import httpx

            kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=120.0)
        except Exception:  # pragma: no cover - fall back to the default client
            kwargs = {}
        else:
            logger.info("Using HTTP proxy for the Groq API: %s", proxy)

    return Groq(api_key=api_key, max_retries=0, **kwargs)


# ----------------------------------------------------------------------
# Proxy resolution
# ----------------------------------------------------------------------
def resolve_proxy() -> str | None:
    """Determine the proxy the Groq client should use, or ``None`` for a direct call.

    Order of precedence:

    1. ``GROQ_PROXY`` / ``HTTPS_PROXY`` / ``HTTP_PROXY`` (explicit, standard);
    2. the Windows "Internet Settings" proxy, which ``subprocess`` based tools
       and PowerShell honour but Python's ``httpx`` does not.

    The second step matters on machines that route traffic through a local
    proxy: without it every API call is rejected with ``403 Forbidden`` even
    though the key itself is perfectly valid.
    """

    for name in ("GROQ_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return _windows_system_proxy()


def _windows_system_proxy() -> str | None:
    """Read the per-user proxy from the Windows registry, if one is enabled."""

    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return None

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:  # pragma: no cover - registry not readable
        return None

    server = (server or "").strip()
    if not server:
        return None

    # ProxyServer may be "host:port" or "http=host:port;https=host:port"
    if "=" in server:
        entries = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
        server = entries.get("https") or entries.get("http") or ""
    server = server.strip()
    if not server:
        return None
    return server if "://" in server else f"http://{server}"
