"""Command line interface for Phase 1.

Usage examples::

    python -m src.main
    python -m src.main --input lecture.mp4
    python -m src.main --input lecture.mp4 --duration 30
    python -m src.main --help
    python -m src.main --check-env
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from . import __version__
from .config import (
    API_KEY_ENV_VAR,
    DEFAULT_MODEL,
    FFMPEG_ENV_VAR,
    FFPROBE_ENV_VAR,
    KNOWN_TRANSCRIPTION_MODELS,
    MODEL_ENV_VAR,
    PROJECT_ROOT,
    build_config,
    read_environment,
    resolve_tool_paths,
)
from .discovery import InputResolver
from .errors import AlBrooksError, ConfigurationError
from .logging_utils import register_secret, setup_logging
from .media import verify_executables
from .pipeline import Phase1Pipeline, render_final_report

logger = logging.getLogger(__name__)

PROGRAM_NAME: Final[str] = "python -m src.main"

_DESCRIPTION: Final[str] = (
    "Phase 1: extract the first N seconds of a video and transcribe the English "
    "speech with the Groq speech-to-text API."
)

_EPILOG: Final[str] = f"""
examples:
  {PROGRAM_NAME}                              process the only video in .\\data
  {PROGRAM_NAME} --input lecture.mp4           process a specific file
  {PROGRAM_NAME} --input lecture.mp4 --duration 30
  {PROGRAM_NAME} --overwrite                   replace existing artifacts
  {PROGRAM_NAME} --check-env                   verify ffmpeg, ffprobe and {API_KEY_ENV_VAR}
  {PROGRAM_NAME} --list-inputs                 list candidate videos in .\\data

environment variables:
  {API_KEY_ENV_VAR}              (required) Groq API key
  {MODEL_ENV_VAR}   (optional) transcription model, default '{DEFAULT_MODEL}'
  FFMPEG_PATH / FFPROBE_PATH      (optional) explicit executable paths

outputs are written next to this package in .\\output\\ as
  <name>_test_<duration>s.mp4 / .en.txt / .en.json / .metadata.json
"""


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser (also used to render ``--help`` in tests)."""

    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        "-i",
        metavar="FILENAME",
        help=(
            "Input video file name (relative to the data directory, or an "
            "absolute path). If omitted, the single video found in the data "
            "directory is used; several candidates require an explicit choice."
        ),
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=float,
        metavar="SECONDS",
        help="Length of the test segment to extract (default: 30 seconds).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="DIR",
        help=f"Input directory (default: {PROJECT_ROOT / 'data'}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        metavar="DIR",
        help=f"Directory for generated artifacts (default: {PROJECT_ROOT / 'output'}).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        metavar="DIR",
        help=f"Directory for run logs (default: {PROJECT_ROOT / 'logs'}).",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help=(
            "Groq transcription model "
            f"(default: {DEFAULT_MODEL} or ${MODEL_ENV_VAR}). "
            f"Known models: {', '.join(KNOWN_TRANSCRIPTION_MODELS)}."
        ),
    )
    parser.add_argument(
        "--language",
        metavar="CODE",
        help="Language hint sent to the API (default: en).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output artifacts instead of failing.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary audio file instead of deleting it after transcription.",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="Verify ffmpeg/ffprobe availability and that the API key is set, then exit.",
    )
    parser.add_argument(
        "--list-inputs",
        action="store_true",
        help="List the candidate videos in the data directory and exit.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug level logging.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"al-brooks-dubbed {__version__} (Phase 1)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code instead of raising."""

    parser = build_parser()
    args = parser.parse_args(argv)

    log_file = setup_logging(log_dir=args.log_dir, verbose=args.verbose)

    # Read the key early so that it can be redacted from every later message.
    import os

    register_secret(os.environ.get(API_KEY_ENV_VAR))

    try:
        env = read_environment(
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            log_dir=args.log_dir,
            duration_seconds=args.duration,
            model=args.model,
            language=args.language,
            overwrite=args.overwrite,
            keep_temp=args.keep_temp,
        )

        if args.list_inputs:
            return _run_list_inputs(env.data_dir, parser)

        if args.check_env:
            return _run_check_env()

        config = build_config(env)
        pipeline = Phase1Pipeline(config)
        result = pipeline.run(input_name=args.input)

        print(render_final_report(result))
        if log_file is not None:
            print(f"  Log file              : {log_file}")
        return 0

    except AlBrooksError as exc:
        logger.error("")
        logger.error("FAILED: %s", exc)
        logger.error("Exit code %d. See the log file for full details.", exc.exit_code)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        logger.error("Interrupted by the user.")
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort safety net
        logger.exception("Unexpected error: %s", exc)
        return 1


def _run_list_inputs(data_dir: Path, parser: argparse.ArgumentParser) -> int:
    """Implementation of ``--list-inputs``."""

    resolver = InputResolver(data_dir)
    try:
        candidates = resolver.list_candidates()
    except AlBrooksError as exc:
        logger.error("%s", exc)
        return exc.exit_code

    if not candidates:
        logger.warning("No supported video found in %s", data_dir)
        return 0

    logger.info("Candidate videos in %s:", data_dir)
    for candidate in candidates:
        logger.info("  - %s (%.1f MB)", candidate.name, candidate.size_bytes / 1e6)
    if len(candidates) == 1:
        logger.info("Exactly one video: %s will be processed automatically.", candidates[0].name)
    else:
        first = candidates[0].name
        logger.warning(
            '%d videos found - select one explicitly, e.g. %s --input "%s"',
            len(candidates),
            parser.prog,
            first,
        )
    return 0


def _run_check_env() -> int:
    """Implementation of ``--check-env``: never prints the key value."""

    import os

    problems: list[str] = []

    try:
        ffmpeg_path, ffprobe_path = resolve_tool_paths()
    except ConfigurationError as exc:
        logger.error("%s", exc)
        return 2

    if ffmpeg_path is None:
        problems.append(
            "ffmpeg: not found on PATH. Install FFmpeg from "
            "https://ffmpeg.org/download.html and add its bin folder to PATH, "
            f"or set {FFMPEG_ENV_VAR}."
        )
    if ffprobe_path is None:
        problems.append(
            "ffprobe: not found on PATH. FFprobe ships with FFmpeg - install it "
            f"and add its bin folder to PATH, or set {FFPROBE_ENV_VAR}."
        )

    if ffmpeg_path and ffprobe_path:
        try:
            for label, banner in verify_executables(ffmpeg_path, ffprobe_path).items():
                logger.info("%-8s %s", label, banner)
        except AlBrooksError as exc:
            problems.append(str(exc))

    raw_key = os.environ.get(API_KEY_ENV_VAR, "")
    if raw_key.strip():
        logger.info(
            "%s is set (value hidden, %d characters).", API_KEY_ENV_VAR, len(raw_key.strip())
        )
    else:
        problems.append(
            f"{API_KEY_ENV_VAR} is not set. Check without printing it with: "
            'if ($env:GROQ_API_KEY) { "set" } else { "missing" }'
        )

    if problems:
        logger.error("Environment check FAILED:")
        for problem in problems:
            logger.error("  - %s", problem)
        return 2

    logger.info("Environment check PASSED - ready to run %s", PROGRAM_NAME)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
