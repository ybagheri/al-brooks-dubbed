"""Discovery and validation of the input video file."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .config import SUPPORTED_VIDEO_EXTENSIONS
from .errors import (
    AmbiguousInputError,
    InputDirectoryMissingError,
    InvalidInputError,
    NoInputVideoError,
)

logger = logging.getLogger(__name__)

#: Smallest file we are willing to treat as a video (guards against stray
#: zero-byte or placeholder files).
MIN_VIDEO_BYTES: int = 1024


@dataclass(frozen=True)
class VideoCandidate:
    """A single discovered video file."""

    path: Path
    size_bytes: int

    @property
    def name(self) -> str:
        return self.path.name


class InputResolver:
    """Finds and validates the source video.

    Selection rules:

    * An explicit ``--input`` name is resolved against the data directory
      (or used as-is when it is an absolute/existing path).
    * Without ``--input`` exactly one candidate is selected automatically.
    * Several candidates raise :class:`AmbiguousInputError` and the caller must
      ask the user to pick one - we never guess.
    """

    def __init__(
        self,
        data_dir: Path,
        extensions: tuple[str, ...] = SUPPORTED_VIDEO_EXTENSIONS,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.extensions = tuple(ext.lower() for ext in extensions)

    def list_candidates(self) -> list[VideoCandidate]:
        """Return all supported video files in the data directory, sorted."""

        if not self.data_dir.is_dir():
            raise InputDirectoryMissingError(
                f"Input directory not found: {self.data_dir}\n"
                f"Create it and place the source video inside, e.g. "
                f"{self.data_dir / 'lecture.mp4'}."
            )

        candidates: list[VideoCandidate] = []
        for entry in self.data_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in self.extensions:
                continue
            try:
                size = entry.stat().st_size
            except OSError as exc:  # pragma: no cover - unusual filesystem error
                logger.warning("Skipping %s: cannot stat file (%s).", entry, exc)
                continue
            candidates.append(VideoCandidate(path=entry.resolve(), size_bytes=size))

        return sorted(candidates, key=lambda c: c.path.name.lower())

    def resolve(self, input_name: str | None = None) -> VideoCandidate:
        """Resolve and validate the input video, or raise a specific error."""

        candidate = self._resolve_explicit(input_name) if input_name else self._resolve_automatic()
        self.validate(candidate)
        return candidate

    def _resolve_automatic(self) -> VideoCandidate:
        candidates = self.list_candidates()
        if not candidates:
            raise NoInputVideoError(
                f"No supported video found in {self.data_dir}.\n"
                f"Supported extensions: {', '.join(self.extensions)}.\n"
                f"Place the source video in that folder, for example: "
                f"{self.data_dir / 'lecture.mp4'}"
            )
        if len(candidates) > 1:
            listing = "\n".join(f"  - {c.name} ({c.size_bytes / 1e6:.1f} MB)" for c in candidates)
            raise AmbiguousInputError(
                f"Found {len(candidates)} videos in {self.data_dir}:\n{listing}\n"
                "Specify one explicitly, e.g.:\n"
                f'  python -m src.main --input "{candidates[0].name}"'
            )
        logger.info("Auto-selected the only available video: %s", candidates[0].name)
        return candidates[0]

    def _resolve_explicit(self, input_name: str) -> VideoCandidate:
        raw = Path(input_name).expanduser()
        path = raw if raw.is_absolute() else self.data_dir / raw

        if not path.is_file():
            available = self._safe_list_names()
            suffix_hint = (
                ""
                if path.suffix.lower() in self.extensions
                else f"\n'{path.suffix or input_name}' is not a supported video "
                f"extension ({', '.join(self.extensions)})."
            )
            raise InvalidInputError(
                f"Input video not found: {path}{suffix_hint}"
                + (f"\nAvailable files in {self.data_dir}: {available}" if available else "")
            )
        return VideoCandidate(path=path.resolve(), size_bytes=path.stat().st_size)

    def _safe_list_names(self) -> str:
        try:
            names = [c.name for c in self.list_candidates()]
        except InputDirectoryMissingError:
            return ""
        return ", ".join(names) if names else ""

    def validate(self, candidate: VideoCandidate) -> None:
        """Ensure the resolved candidate is an existing, non-empty file."""

        if not candidate.path.is_file():
            raise InvalidInputError(f"Input video does not exist: {candidate.path}")
        if candidate.size_bytes < MIN_VIDEO_BYTES:
            raise InvalidInputError(
                f"Input video is empty or truncated ({candidate.size_bytes} bytes): "
                f"{candidate.path}"
            )
        if not os.access(candidate.path, os.R_OK):  # pragma: no cover - Windows/ACL edge
            raise InvalidInputError(f"Input video is not readable: {candidate.path}")
