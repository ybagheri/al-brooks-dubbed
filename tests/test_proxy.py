"""Tests for proxy resolution used by the Groq client.

On Windows, PowerShell and FFmpeg honour the "Internet Settings" proxy while
Python's httpx does not, which produces a misleading ``403 Forbidden`` from the
Groq API even when the API key is perfectly valid.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from src.transcription import _windows_system_proxy, resolve_proxy

PROXY_VARS = ("GROQ_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


@pytest.fixture(autouse=True)
def no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)


def test_no_proxy_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.transcription._windows_system_proxy", lambda: None)
    assert resolve_proxy() is None


def test_explicit_groq_proxy_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_PROXY", "http://127.0.0.1:9999")
    assert resolve_proxy() == "http://127.0.0.1:9999"


def test_https_proxy_env_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:8080")
    assert resolve_proxy() == "http://proxy.local:8080"


def test_environment_beats_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://from-env:1")
    monkeypatch.setattr("src.transcription._windows_system_proxy", lambda: "http://from-reg:2")
    assert resolve_proxy() == "http://from-env:1"


def test_registry_is_used_as_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.transcription._windows_system_proxy", lambda: "http://from-reg:2")
    assert resolve_proxy() == "http://from-reg:2"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry only")
class TestWindowsRegistryProxy:
    """Exercise the WinINET parsing logic with a stubbed ``winreg`` module."""

    def _with_registry(
        self, monkeypatch: pytest.MonkeyPatch, enabled: int, server: str
    ) -> str | None:
        import types

        fake = types.ModuleType("winreg")
        fake.HKEY_CURRENT_USER = object()  # type: ignore[attr-defined]

        class _Key:
            def __enter__(self) -> _Key:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        values = {"ProxyEnable": enabled, "ProxyServer": server}

        def _query_value_ex(_key: object, name: str) -> tuple[Any, int]:
            return values[name], 1

        fake.OpenKey = lambda *_a, **_k: _Key()  # type: ignore[attr-defined]
        fake.QueryValueEx = _query_value_ex  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "winreg", fake)
        return _windows_system_proxy()

    def test_proxy_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._with_registry(monkeypatch, 0, "127.0.0.1:1080") is None

    def test_simple_host_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._with_registry(monkeypatch, 1, "127.0.0.1:1080") == "http://127.0.0.1:1080"

    def test_scheme_is_not_duplicated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert (
            self._with_registry(monkeypatch, 1, "http://127.0.0.1:1080") == "http://127.0.0.1:1080"
        )

    def test_per_protocol_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = "http=proxy.local:3128;https=secure.local:3129"
        assert self._with_registry(monkeypatch, 1, server) == "http://secure.local:3129"

    def test_empty_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._with_registry(monkeypatch, 1, "") is None
