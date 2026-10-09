"""Logging setup with built-in redaction of sensitive values.

The API key is registered as a secret as soon as it is read from the
environment, so that even an unexpected third-party traceback cannot leak it
into ``logs/`` or the console.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

_SECRET_PLACEHOLDER = "***REDACTED***"

# Matches anything that looks like a Groq key, used as a belt-and-braces filter
# for values that never passed through the secret registry (e.g. a key echoed
# back inside an API error message).
_API_KEY_PATTERN = re.compile(r"\b(?:gsk_|sk_)[A-Za-z0-9_\-]{16,}")

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class SecretRedactingFilter(logging.Filter):
    """Replace registered secrets and Groq-key-like tokens with a placeholder."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def add_secret(self, secret: str | None) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, _SECRET_PLACEHOLDER)
        return _API_KEY_PATTERN.sub(_SECRET_PLACEHOLDER, text)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.scrub(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(self.scrub(a) if isinstance(a, str) else a for a in record.args)
        return True


_redacting_filter = SecretRedactingFilter()
_handler_configured = False


def get_redacting_filter() -> SecretRedactingFilter:
    """Return the process-wide redaction filter."""

    return _redacting_filter


def register_secret(secret: str | None) -> None:
    """Register a value that must never appear in logs or error messages."""

    _redacting_filter.add_secret(secret)


def setup_logging(log_dir: Path | None = None, verbose: bool = False) -> Path | None:
    """Configure console (and optional file) logging.

    Returns the path of the log file, or ``None`` when file logging could not
    be enabled. Failures to create the log directory are non-fatal: the
    pipeline must still be able to report errors on the console.
    """

    global _handler_configured

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    console.addFilter(_redacting_filter)
    root.addHandler(console)

    log_file: Path | None = None
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
            file_handler.addFilter(_redacting_filter)
            root.addHandler(file_handler)
        except OSError as exc:  # pragma: no cover - depends on the filesystem
            root.warning("File logging disabled (%s). Continuing with console only.", exc)
            log_file = None

    # Third-party loggers are extremely chatty at DEBUG level.
    for noisy in ("httpx", "httpcore", "groq._base_client", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _handler_configured = True
    return log_file


def logging_is_configured() -> bool:
    """Return ``True`` once :func:`setup_logging` has run."""

    return _handler_configured
