"""Output artifact paths, serialization and validation."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import OutputError, OutputExistsError

logger = logging.getLogger(__name__)

ENCODING = "utf-8"
JSON_INDENT = 2


@dataclass(frozen=True)
class OutputPaths:
    """The four artifacts produced for one input video."""

    video: Path
    transcript_text: Path
    transcript_json: Path
    metadata_json: Path

    @classmethod
    def for_input(cls, source: Path, output_dir: Path, duration_seconds: float) -> OutputPaths:
        """Derive predictable output paths from the source file name.

        ``lecture.mp4`` + 30s ->
        ``lecture_test_30s.mp4`` / ``.en.txt`` / ``.en.json`` / ``.metadata.json``
        """

        source = Path(source)
        output_dir = Path(output_dir)
        stem = _sanitize_stem(source.stem)
        base = f"{stem}_test_{_format_duration(duration_seconds)}s"
        # Names are built by concatenation: ``Path.with_suffix`` would eat a
        # fractional duration such as the ".5s" in "lecture_test_12.5s".
        return cls(
            video=output_dir / f"{base}.mp4",
            transcript_text=output_dir / f"{base}.en.txt",
            transcript_json=output_dir / f"{base}.en.json",
            metadata_json=output_dir / f"{base}.metadata.json",
        )

    @property
    def all(self) -> tuple[Path, ...]:
        return (self.video, self.transcript_text, self.transcript_json, self.metadata_json)

    def existing(self) -> list[Path]:
        return [path for path in self.all if path.exists()]

    def as_dict(self) -> dict[str, str]:
        return {
            "extracted_video": str(self.video),
            "transcript_text": str(self.transcript_text),
            "transcript_json": str(self.transcript_json),
            "metadata_json": str(self.metadata_json),
        }


@dataclass(frozen=True)
class PreparedPaths:
    """The three artifacts produced by the Phase 2 preparation stage."""

    cleaned_text: Path
    cleaned_json: Path
    qa_json: Path

    @classmethod
    def from_raw(cls, raw_json: Path) -> PreparedPaths:
        """Derive prepared-output names from a Phase 1 ``*.en.json`` path.

        ``lecture_test_30s.en.json`` ->
        ``lecture_test_30s.en.clean.txt`` / ``.en.clean.json`` /
        ``lecture_test_30s.qa.json``
        """

        raw_json = Path(raw_json)
        name = raw_json.name
        stem = name[: -len(".json")] if name.endswith(".json") else name
        output_dir = raw_json.parent
        # The QA report describes the raw transcript, so it drops the language
        # tag: "lecture_test_30s.en" -> "lecture_test_30s".
        base = stem[: -len(".en")] if stem.endswith(".en") else stem
        return cls(
            cleaned_text=output_dir / f"{stem}.clean.txt",
            cleaned_json=output_dir / f"{stem}.clean.json",
            qa_json=output_dir / f"{base}.qa.json",
        )

    @property
    def all(self) -> tuple[Path, ...]:
        return (self.cleaned_text, self.cleaned_json, self.qa_json)

    def existing(self) -> list[Path]:
        return [path for path in self.all if path.exists()]

    def as_dict(self) -> dict[str, str]:
        return {
            "cleaned_transcript": str(self.cleaned_text),
            "prepared_transcript": str(self.cleaned_json),
            "qa_report": str(self.qa_json),
        }


def atomic_write_text(path: Path, text: str) -> Path:
    """Public wrapper around the atomic UTF-8 writer used by all artifacts."""

    return _atomic_write_text(Path(path), text)


class OutputWriter:
    """Writes the Phase 1 artifacts and validates them afterwards."""

    def __init__(self, output_dir: Path, overwrite: bool = False) -> None:
        self.output_dir = Path(output_dir)
        self.overwrite = overwrite

    def prepare(self) -> Path:
        """Create the output directory, failing with a clear message on error."""

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputError(
                f"Could not create the output directory {self.output_dir}: {exc}"
            ) from exc
        if not os.access(self.output_dir, os.W_OK):
            raise OutputError(f"Output directory is not writable: {self.output_dir}")
        return self.output_dir

    def check_collisions(self, paths: OutputPaths) -> None:
        """Refuse to clobber existing artifacts unless ``overwrite`` is set."""

        if self.overwrite:
            if paths.existing():
                logger.warning(
                    "--overwrite is active: replacing %d existing artifact(s) in %s",
                    len(paths.existing()),
                    self.output_dir,
                )
            return

        existing = paths.existing()
        if existing:
            listing = "\n".join(f"  - {path.name}" for path in existing)
            raise OutputExistsError(
                f"Output already exists and --overwrite was not given:\n{listing}\n"
                f"Full path: {existing[0].parent}\n"
                "Re-run with --overwrite to replace the existing results, or choose "
                "a different --output-dir."
            )

    # ------------------------------------------------------------------
    def write_transcript_text(self, path: Path, text: str) -> Path:
        """Write the plain UTF-8 transcript, newline-normalised for portability."""

        normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
        if normalized and not normalized.endswith("\n"):
            normalized += "\n"
        return _atomic_write_text(path, normalized)

    def write_json(self, path: Path, payload: dict[str, Any]) -> Path:
        """Write a UTF-8 JSON document with stable key order."""

        text = json.dumps(payload, ensure_ascii=False, indent=JSON_INDENT, sort_keys=False)
        return _atomic_write_text(path, text + "\n")

    # ------------------------------------------------------------------
    def validate_outputs(self, paths: OutputPaths, min_transcript_chars: int = 1) -> dict[str, Any]:
        """Verify every artifact exists, is non-empty and is parseable.

        Returns a small report used for the final summary and for the metadata
        artifact. Raises :class:`OutputError` on the first problem found.
        """

        report: dict[str, Any] = {}

        for label, path in (
            ("extracted_video", paths.video),
            ("transcript_text", paths.transcript_text),
            ("transcript_json", paths.transcript_json),
            ("metadata_json", paths.metadata_json),
        ):
            if not path.is_file():
                raise OutputError(f"Expected {label} was not created: {path}")
            size = path.stat().st_size
            if size == 0:
                raise OutputError(f"{label} is empty: {path}")
            report[label] = {"path": str(path), "size_bytes": size}

        try:
            transcript_payload = _read_json(paths.transcript_json)
        except (json.JSONDecodeError, OSError) as exc:
            raise OutputError(
                f"transcript_json is not valid JSON ({exc}): {paths.transcript_json}"
            ) from exc
        if not isinstance(transcript_payload, dict):
            raise OutputError(
                f"transcript_json must contain a JSON object: {paths.transcript_json}"
            )
        transcript_text = transcript_payload.get("transcript")
        if (
            not isinstance(transcript_text, str)
            or len(transcript_text.strip()) < min_transcript_chars
        ):
            raise OutputError(
                f"transcript_json contains no usable transcript text: {paths.transcript_json}"
            )

        try:
            _read_json(paths.metadata_json)
        except (json.JSONDecodeError, OSError) as exc:
            raise OutputError(
                f"metadata_json is not valid JSON ({exc}): {paths.metadata_json}"
            ) from exc

        try:
            plain = paths.transcript_text.read_text(encoding=ENCODING)
        except (UnicodeDecodeError, OSError) as exc:
            raise OutputError(
                f"transcript_text is not readable as UTF-8 ({exc}): {paths.transcript_text}"
            ) from exc
        if len(plain.strip()) < min_transcript_chars:
            raise OutputError(f"transcript_text is empty: {paths.transcript_text}")

        report["transcript_characters"] = len(transcript_text.strip())
        report["all_valid"] = True
        logger.info("Validated %d output artifact(s).", len(report) - 2)
        return report


def utc_timestamp() -> str:
    """ISO-8601 UTC timestamp used in every JSON artifact."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _atomic_write_text(path: Path, text: str) -> Path:
    """Write UTF-8 text via a temporary file + replace to avoid partial files."""

    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The handle name is needed for the atomic replace below, so the file
        # cannot be opened directly in a ``with`` statement.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            encoding=ENCODING,
            newline="\n",
            delete=False,
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
        )
        try:
            with handle:
                handle.write(text)
            os.replace(handle.name, path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise OutputError(f"Could not write {path}: {exc}") from exc
    return path


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding=ENCODING))


def _sanitize_stem(stem: str) -> str:
    """Make a file stem safe to use as an output name."""

    cleaned = "".join(char if char.isalnum() or char in "-_." else "_" for char in stem)
    return cleaned.strip("._") or "video"


def _format_duration(seconds: float) -> str:
    """Render 30.0 -> ``30`` and 12.5 -> ``12.5``."""

    if float(seconds).is_integer():
        return str(int(seconds))
    return f"{seconds:g}"
