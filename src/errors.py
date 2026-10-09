"""Exception hierarchy for the Phase 1 pipeline.

Every error raised on purpose by this package derives from
:class:`AlBrooksError` so that the CLI can turn any expected failure into a
clean message plus a meaningful exit code instead of a traceback.
"""

from __future__ import annotations


class AlBrooksError(Exception):
    """Base class for all expected failures of the pipeline."""

    #: Process exit code associated with this error class.
    exit_code: int = 1


class ConfigurationError(AlBrooksError):
    """Environment or configuration is missing or invalid."""

    exit_code = 2


class ToolNotFoundError(ConfigurationError):
    """A required external executable (ffmpeg/ffprobe) is unavailable."""


class MissingApiKeyError(ConfigurationError):
    """``GROQ_API_KEY`` is not present in the environment."""


class InputDirectoryMissingError(AlBrooksError):
    """The configured input directory does not exist."""

    exit_code = 3


class NoInputVideoError(AlBrooksError):
    """No supported video file was found in the input directory."""

    exit_code = 3


class AmbiguousInputError(AlBrooksError):
    """Several candidate videos exist and no explicit selection was made."""

    exit_code = 3


class InvalidInputError(AlBrooksError):
    """The selected input file is not a usable, non-empty video file."""

    exit_code = 3


class MediaProbeError(AlBrooksError):
    """FFprobe could not inspect the given media file."""

    exit_code = 4


class ExtractionError(AlBrooksError):
    """FFmpeg failed to extract video or audio."""

    exit_code = 4


class TranscriptionError(AlBrooksError):
    """The speech-to-text request failed."""

    exit_code = 5


class TranscriptionAuthError(TranscriptionError):
    """The Groq API rejected the credentials (permanent failure)."""

    exit_code = 5


class TranscriptionSizeError(TranscriptionError):
    """The prepared audio exceeds the upload size limit of the endpoint."""


class EmptyTranscriptError(TranscriptionError):
    """The API returned a successful response without any transcript text."""


class OutputError(AlBrooksError):
    """An output artifact could not be written or failed validation."""

    exit_code = 6


class OutputExistsError(OutputError):
    """An output artifact already exists and overwriting was not requested."""
