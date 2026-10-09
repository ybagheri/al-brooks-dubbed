"""Tests for :mod:`src.discovery`."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.discovery import InputResolver
from src.errors import (
    AmbiguousInputError,
    InputDirectoryMissingError,
    InvalidInputError,
    NoInputVideoError,
)

from .conftest import FFMPEG


def make_video(directory: Path, name: str, size: int = 4096) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


def test_missing_data_directory(tmp_path: Path) -> None:
    resolver = InputResolver(tmp_path / "does-not-exist")
    with pytest.raises(InputDirectoryMissingError) as excinfo:
        resolver.list_candidates()
    assert "does-not-exist" in str(excinfo.value)


def test_missing_data_directory_message_explains_placement(tmp_path: Path) -> None:
    resolver = InputResolver(tmp_path / "nope")
    with pytest.raises(InputDirectoryMissingError, match=r"lecture\.mp4"):
        resolver.resolve()


def test_no_video_found(tmp_path: Path) -> None:
    resolver = InputResolver(tmp_path)
    with pytest.raises(NoInputVideoError) as excinfo:
        resolver.resolve()
    assert str(tmp_path) in str(excinfo.value)


def test_ignores_non_video_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "cover.jpg").write_bytes(b"\xff" * 2000)
    resolver = InputResolver(tmp_path)
    assert resolver.list_candidates() == []


def test_ignores_subdirectories(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    make_video(tmp_path / "nested", "inner.mp4")
    resolver = InputResolver(tmp_path)
    assert resolver.list_candidates() == []


def test_single_video_is_selected_automatically(tmp_path: Path) -> None:
    video = make_video(tmp_path, "lecture.mp4")
    resolver = InputResolver(tmp_path)
    assert resolver.resolve().path == video.resolve()


@pytest.mark.parametrize("suffix", [".mp4", ".MKV", ".Mov", ".avi", ".webm", ".mp4"])
def test_supported_extensions(tmp_path: Path, suffix: str) -> None:
    make_video(tmp_path, f"clip{suffix}")
    resolver = InputResolver(tmp_path)
    assert len(resolver.list_candidates()) == 1


def test_multiple_videos_raise_and_list_names(tmp_path: Path) -> None:
    make_video(tmp_path, "alpha.mp4")
    make_video(tmp_path, "beta.mkv")
    resolver = InputResolver(tmp_path)
    with pytest.raises(AmbiguousInputError) as excinfo:
        resolver.resolve()
    message = str(excinfo.value)
    assert "alpha.mp4" in message
    assert "beta.mkv" in message
    assert "--input" in message


def test_explicit_input_selection(tmp_path: Path) -> None:
    make_video(tmp_path, "alpha.mp4")
    wanted = make_video(tmp_path, "beta.mkv")
    resolver = InputResolver(tmp_path)
    assert resolver.resolve("beta.mkv").path == wanted.resolve()


def test_explicit_selection_among_many(tmp_path: Path) -> None:
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        make_video(tmp_path, name)
    resolver = InputResolver(tmp_path)
    assert resolver.resolve("c.mp4").name == "c.mp4"


def test_explicit_input_not_found(tmp_path: Path) -> None:
    make_video(tmp_path, "alpha.mp4")
    resolver = InputResolver(tmp_path)
    with pytest.raises(InvalidInputError) as excinfo:
        resolver.resolve("missing.mp4")
    assert "missing.mp4" in str(excinfo.value)
    assert "alpha.mp4" in str(excinfo.value)


def test_explicit_input_with_wrong_extension_explains(tmp_path: Path) -> None:
    make_video(tmp_path, "alpha.mp4")
    resolver = InputResolver(tmp_path)
    with pytest.raises(InvalidInputError, match="not a supported video extension"):
        resolver.resolve("notes.txt")


def test_empty_video_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "empty.mp4").write_bytes(b"")
    resolver = InputResolver(tmp_path)
    with pytest.raises(InvalidInputError, match="empty or truncated"):
        resolver.resolve()


def test_filename_with_spaces(tmp_path: Path) -> None:
    video = make_video(tmp_path, "my lecture part 1.mp4")
    resolver = InputResolver(tmp_path)
    assert resolver.resolve("my lecture part 1.mp4").path == video.resolve()


def test_non_ascii_filename(tmp_path: Path) -> None:
    video = make_video(tmp_path, " lecture-برس- lecture.mp4".strip())
    resolver = InputResolver(tmp_path)
    assert resolver.resolve(video.name).path == video.resolve()


def test_absolute_path_selection(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    video = make_video(outside, "absolute.mp4")
    resolver = InputResolver(tmp_path)
    assert resolver.resolve(str(video)).path == video.resolve()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")
def test_candidates_are_sorted_case_insensitively(tmp_path: Path) -> None:
    for name in ("Zeta.mp4", "alpha.mp4", "Beta.mp4"):
        make_video(tmp_path, name)
    names = [c.name for c in InputResolver(tmp_path).list_candidates()]
    assert names == ["alpha.mp4", "Beta.mp4", "Zeta.mp4"]


def test_data_dir_with_spaces(tmp_path: Path) -> None:
    data_dir = tmp_path / "my data dir"
    data_dir.mkdir()
    video = make_video(data_dir, "clip.mp4")
    resolver = InputResolver(data_dir)
    assert resolver.resolve().path == video.resolve()
