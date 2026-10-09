"""Tests for :mod:`src.outputs`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.errors import OutputError, OutputExistsError
from src.outputs import OutputPaths, OutputWriter


# ----------------------------------------------------------------------
# Path derivation
# ----------------------------------------------------------------------
def test_output_paths_for_lecture() -> None:
    paths = OutputPaths.for_input(Path("lecture.mp4"), Path("output"), 30.0)
    assert paths.video.name == "lecture_test_30s.mp4"
    assert paths.transcript_text.name == "lecture_test_30s.en.txt"
    assert paths.transcript_json.name == "lecture_test_30s.en.json"
    assert paths.metadata_json.name == "lecture_test_30s.metadata.json"
    assert paths.video.parent == Path("output")


def test_output_paths_for_non_integer_duration() -> None:
    paths = OutputPaths.for_input(Path("lecture.mp4"), Path("output"), 12.5)
    assert paths.video.name == "lecture_test_12.5s.mp4"


def test_output_paths_replace_dots_in_duration() -> None:
    """``12.5s`` must not become ``12.5.mp4`` - the suffix would be lost."""

    paths = OutputPaths.for_input(Path("lecture.mp4"), Path("output"), 12.5)
    assert paths.video.suffix == ".mp4"
    assert paths.transcript_text.suffix == ".txt"
    assert paths.video.name.endswith("12.5s.mp4")


def test_output_paths_use_the_output_directory() -> None:
    paths = OutputPaths.for_input(Path("D:/a/b/lecture.mp4"), Path("D:/out"), 30.0)
    assert paths.video == Path("D:/out/lecture_test_30s.mp4")


def test_output_paths_sanitize_unsafe_stem() -> None:
    paths = OutputPaths.for_input(Path("we*ird:<name>.mp4"), Path("out"), 30.0)
    assert "*" not in paths.video.name
    assert "<" not in paths.video.name


def test_output_paths_windows_source_path() -> None:
    paths = OutputPaths.for_input(
        Path(r"D:\Projects\al-brooks-dubbed\data\clip.mp4"), Path("out"), 30.0
    )
    assert paths.video.name == "clip_test_30s.mp4"


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------
def test_write_transcript_text_is_utf8(tmp_path: Path) -> None:
    writer = OutputWriter(tmp_path)
    path = writer.write_transcript_text(tmp_path / "t.txt", "Hello world")
    assert path.read_bytes() == b"Hello world\n"
    assert path.read_text(encoding="utf-8").strip() == "Hello world"


def test_write_transcript_text_supports_non_ascii(tmp_path: Path) -> None:
    writer = OutputWriter(tmp_path)
    text = "Line one\nسلام دنیا\nLine three"
    path = writer.write_transcript_text(tmp_path / "fa.txt", text)
    assert "سلام دنیا" in path.read_text(encoding="utf-8")


def test_write_transcript_text_normalises_newlines(tmp_path: Path) -> None:
    writer = OutputWriter(tmp_path)
    path = writer.write_transcript_text(tmp_path / "t.txt", "a\r\nb\r\n")
    assert path.read_bytes() == b"a\nb\n"


def test_write_json_round_trip(tmp_path: Path) -> None:
    writer = OutputWriter(tmp_path)
    payload = {"transcript": "hello", "segments": None, "count": 2}
    path = writer.write_json(tmp_path / "t.json", payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_write_json_keeps_unicode_readable(tmp_path: Path) -> None:
    writer = OutputWriter(tmp_path)
    path = writer.write_json(tmp_path / "t.json", {"text": "متن"})
    assert "متن" in path.read_text(encoding="utf-8")


def test_write_json_leaves_no_temp_files(tmp_path: Path) -> None:
    writer = OutputWriter(tmp_path)
    writer.write_json(tmp_path / "t.json", {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["t.json"]


def test_write_to_unwritable_location_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    writer = OutputWriter(blocker / "sub")
    with pytest.raises(OutputError):
        writer.write_json(blocker / "sub" / "t.json", {"a": 1})


# ----------------------------------------------------------------------
# Collision protection
# ----------------------------------------------------------------------
def _touch(paths: OutputPaths, which: tuple[str, ...] = ("video", "transcript_text")) -> None:
    for name in which:
        getattr(paths, name).write_text("existing", encoding="utf-8")


def test_existing_outputs_are_protected(tmp_path: Path) -> None:
    paths = OutputPaths.for_input(Path("lecture.mp4"), tmp_path, 30.0)
    _touch(paths)
    writer = OutputWriter(tmp_path, overwrite=False)
    with pytest.raises(OutputExistsError) as excinfo:
        writer.check_collisions(paths)
    assert "lecture_test_30s.mp4" in str(excinfo.value)
    assert "--overwrite" in str(excinfo.value)


def test_existing_file_content_is_untouched_without_overwrite(tmp_path: Path) -> None:
    paths = OutputPaths.for_input(Path("lecture.mp4"), tmp_path, 30.0)
    paths.video.write_text("original", encoding="utf-8")
    writer = OutputWriter(tmp_path, overwrite=False)
    with pytest.raises(OutputExistsError):
        writer.check_collisions(paths)
    assert paths.video.read_text(encoding="utf-8") == "original"


def test_overwrite_allows_existing_outputs(tmp_path: Path) -> None:
    paths = OutputPaths.for_input(Path("lecture.mp4"), tmp_path, 30.0)
    _touch(paths)
    OutputWriter(tmp_path, overwrite=True).check_collisions(paths)


def test_no_collision_when_output_absent(tmp_path: Path) -> None:
    paths = OutputPaths.for_input(Path("lecture.mp4"), tmp_path, 30.0)
    OutputWriter(tmp_path).check_collisions(paths)


def test_existing_lists_only_present_files(tmp_path: Path) -> None:
    paths = OutputPaths.for_input(Path("lecture.mp4"), tmp_path, 30.0)
    assert paths.existing() == []
    paths.video.write_text("x", encoding="utf-8")
    assert paths.existing() == [paths.video]


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def _valid_artifacts(tmp_path: Path) -> OutputPaths:
    paths = OutputPaths.for_input(Path("lecture.mp4"), tmp_path, 30.0)
    paths.video.write_bytes(b"\x00" * 100)
    paths.transcript_text.write_text("Hello there.\n", encoding="utf-8")
    paths.transcript_json.write_text(
        json.dumps({"transcript": "Hello there.", "segments": None}), encoding="utf-8"
    )
    paths.metadata_json.write_text(json.dumps({"processing_status": "success"}), encoding="utf-8")
    return paths


def test_validate_outputs_accepts_complete_set(tmp_path: Path) -> None:
    paths = _valid_artifacts(tmp_path)
    report = OutputWriter(tmp_path).validate_outputs(paths)
    assert report["all_valid"] is True
    assert report["transcript_characters"] == len("Hello there.")


def test_validate_outputs_detects_missing_file(tmp_path: Path) -> None:
    paths = _valid_artifacts(tmp_path)
    paths.metadata_json.unlink()
    with pytest.raises(OutputError, match="metadata_json"):
        OutputWriter(tmp_path).validate_outputs(paths)


def test_validate_outputs_detects_empty_file(tmp_path: Path) -> None:
    paths = _valid_artifacts(tmp_path)
    paths.transcript_text.write_text("", encoding="utf-8")
    with pytest.raises(OutputError, match="empty"):
        OutputWriter(tmp_path).validate_outputs(paths)


def test_validate_outputs_detects_invalid_json(tmp_path: Path) -> None:
    paths = _valid_artifacts(tmp_path)
    paths.transcript_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(OutputError, match="not valid JSON"):
        OutputWriter(tmp_path).validate_outputs(paths)


def test_validate_outputs_detects_missing_transcript_field(tmp_path: Path) -> None:
    paths = _valid_artifacts(tmp_path)
    paths.transcript_json.write_text(json.dumps({"text": "wrong key"}), encoding="utf-8")
    with pytest.raises(OutputError, match="no usable transcript text"):
        OutputWriter(tmp_path).validate_outputs(paths)


def test_validate_outputs_detects_blank_transcript(tmp_path: Path) -> None:
    paths = _valid_artifacts(tmp_path)
    paths.transcript_json.write_text(json.dumps({"transcript": "   "}), encoding="utf-8")
    with pytest.raises(OutputError, match="no usable transcript text"):
        OutputWriter(tmp_path).validate_outputs(paths)


def test_prepare_creates_nested_directory(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "output"
    assert OutputWriter(target).prepare() == target
    assert target.is_dir()
