# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-09

Phase 1: video extraction and English transcription. First working release,
verified end to end against a real lecture video.

### Added

**Application**

- `python -m src.main` command line entry point with `--input`, `--duration`,
  `--data-dir`, `--output-dir`, `--log-dir`, `--model`, `--language`,
  `--overwrite`, `--keep-temp`, `--check-env`, `--list-inputs`, `--verbose`
  and `--version`.
- Seven-stage pipeline with explicit progress reporting: input validation,
  media inspection, video extraction, audio preparation, transcription, output
  validation, final result locations.
- Meaningful process exit codes (0 success, 2 configuration, 3 input,
  4 media, 5 transcription, 6 output).

**Input handling**

- Discovery of MP4, MKV, MOV, AVI, WebM and other FFmpeg-supported formats.
- Automatic selection when exactly one video is present.
- Explicit error listing all candidates when several exist - never a guess.
- Absolute and data-relative `--input` paths; spaces and non-ASCII filenames
  work correctly on Windows.
- Rejection of empty, truncated or unreadable files.

**Media processing**

- `MediaService` wrapping FFprobe/FFmpeg, invoked with argument lists and never
  through a shell.
- Lossless stream copy (`-c copy`) when codecs fit the MP4 container; selective
  re-encoding (`libx264` CRF 20 / AAC 128 kbps) for incompatible streams only.
- Resolution is never reduced; streams are read from disk rather than buffered
  in memory.
- Structured `MediaInfo` reporting duration, codecs, resolution, frame rate,
  sample rate and channel count.
- Duration clamping: a source shorter than `--duration` is processed fully and
  the actual duration is reported.
- Output validation: existence, non-zero size, presence of a video stream and
  a plausible duration.

**Transcription**

- Official Groq SDK against the dedicated audio transcription endpoint.
- Default model `whisper-large-v3-turbo`, verified as available for this
  account alongside `whisper-large-v3`; selectable by flag or
  `GROQ_TRANSCRIPTION_MODEL`.
- Mono 16 kHz FLAC audio prepared in a temporary directory that is always
  cleaned up, including after errors.
- Explicit upload-size guard that fails loudly instead of truncating audio.
- Explicit `language="en"` request; verbatim wording with no translation,
  summarisation or correction.
- Segment timestamps requested and saved when returned; never fabricated.
- Bounded exponential backoff for transient failures only (408/409/425/429/5xx
  and connection errors). Authentication, permission, bad-request and
  payload-too-large errors fail immediately and are never retried.
- Explicit empty-response handling.

**Networking**

- Automatic detection of the Windows *Internet Settings* proxy. Without it,
  Python's HTTP stack bypasses the system proxy and every Groq call fails with
  a misleading `403 Forbidden`; PowerShell and FFmpeg are unaffected. Overridable
  with `GROQ_PROXY`, `HTTPS_PROXY` or `HTTP_PROXY`.

**Security**

- API key read only from `GROQ_API_KEY`; never accepted on the command line and
  never written to a file.
- Central redaction filter applied to every log record and error message, with
  a regex fallback for key-shaped tokens.
- `--check-env` reports only the key's presence and length.

**Output**

- Predictable names: `<name>_test_<duration>s.mp4`, `.en.txt`, `.en.json`,
  `.metadata.json`.
- Atomic writes (temporary file plus replace) so artifacts are never left
  half-written.
- UTF-8 plain-text transcript with normalised newlines, ready for translation.
- Structured JSON containing only fields the API actually returned.
- Existing artifacts are protected; `--overwrite` is required to replace them.

**Project and tooling**

- `pyproject.toml` with pinned dependency ranges and `ruff`, `mypy` and
  `pytest` configuration.
- `.gitignore` covering secrets, `data/`, `output/`, `logs/`, media files and
  Python/tooling caches.
- `.env.example` with placeholders only.
- `README.md` (English) and `README.fa.md` (Persian).

**Tests - 159 cases, all passing**

- Missing input directory, no video found, multiple videos found, explicit
  input selection, empty video, unsupported extension.
- Missing and empty `GROQ_API_KEY`, invalid duration, invalid env var values.
- Missing or unusable FFmpeg/FFprobe, FFprobe failure, invalid JSON output,
  missing duration.
- Shorter-than-30-second inputs, audio-only and video-only sources, sources
  without an audio stream.
- FFmpeg extraction failure with captured diagnostics.
- Empty, whitespace-only, missing-field and `None` transcription responses.
- Groq authentication and permission failures (asserted *not* retried),
  retryable failures (asserted retried), bounded retries, exponential backoff
  with a cap.
- Transcript and JSON serialization, UTF-8 and non-ASCII round trips, output
  validation failures.
- Existing-output protection and overwrite behaviour.
- Proxy resolution and Windows registry proxy parsing.
- Windows-style path handling and filenames with spaces / non-ASCII characters.
- Secret hygiene: keys must never appear in logs, captured output or errors.
- Integration tests generating a synthetic H.264/AAC video with FFmpeg inside
  pytest's temporary directory - no media fixtures are committed.

**Documentation**

- `README.md`, `README.fa.md`, `ROADMAP.md`, `HANDOFF.md`, `CHANGELOG.md`.

### Known issues

- pytest's default temporary directory can fail on this machine with
  `PermissionError ... pytest-current`; set `PYTEST_DEBUG_TEMPROOT` to a
  project-local folder (documented in the README).
- The Groq free tier limits audio minutes per day, which matters for later
  full-length phases but not for a 30-second test.

[0.1.0]: https://example.invalid/al-brooks-dubbed/releases/0.1.0