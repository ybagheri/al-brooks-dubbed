# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-10-09

Glossary decision: the Persian track keeps Al Brooks terms in English.

### Changed

- `terms[].persian` for the ten core terms now holds the English term, and a
  new `terms[].speak_original: true` flag records that this is deliberate.
  Persian traders say "wedge bull flag" rather than a literal Persian
  rendering. To translate a term instead, replace `persian` and drop the flag.
- `TerminologyEntry` gains `spoken_as`, `is_untranslated` and
  `speak_original`; `summary()` reports `kept_in_english`.
- Tests enforce that any spoken-as value equal to the English original carries
  the flag, so the next maintainer does not "fix" it as a typo.

### Added

- `enable_utf8_console()` reconfigures stdout/stderr to UTF-8 at the start of
  `main()`. Printing a Persian line previously raised `UnicodeEncodeError`
  because a Windows console defaults to cp1252 - this would have killed every
  Phase 3 run the moment it printed a translated line. Verified end to end
  through a subprocess: identical codepoints, exit code 0.

### Fixed

- The `duplicate_confirmed` verdict no longer claims the speaker said it twice.
  It now states plainly that reproducing a duplicate with the *same* model is
  not proof, because a deterministic model repeats its own mistake. This was
  confirmed by listening: the trader reports the opening sentence was said
  once, so the earlier inference was wrong.

## [0.3.0] - 2026-10-09

Confusable-term detection, added after analysing a 10-minute sample of the real
lecture. Phase 1 and Phase 2.0 behaviour is unchanged.

### Added

**Confusable term detection (`src/terminology.py`, `src/qa.py`)**

- Detects readable-but-wrong technical terms, the errors most damaging for
  translation. On the 10-minute sample of the real lecture the transcription
  contained `training range` 6 times (should be `trading range`) and
  `bare bar` / `bare body` 3 times (should be `bear bar` / `bear body`).
  Translating those literally would produce a term the trader never used.
- Reported as `confusable_term` at `medium` severity, attached to the exact
  segments and time ranges, and surfaced in the prepared JSON under
  `terminology_summary.known_confusions_detected` for the translation stage.
  **Advisory only - the text is never changed.**
- Two complementary mechanisms, because neither alone is precise:
  1. `known_confusions` in `terminology.json` - a curated, human-approved list
     the trader can extend without touching code;
  2. anchored single-edit detection, where the neighbouring word must match a
     known domain word exactly (`confusable_extra_anchors`).
- `damerau_levenshtein()` in `src/text_cleaning.py`, with an exact length
  prefilter and no unsafe early exit.

**Calibration on real data**

- `segment_gap` severity lowered from `medium` to `low`. The source is live
  trading commentary, so multi-second pauses while charts are read are normal
  and must not alarm. The explanation now says so.

### Fixed

- The first implementation of the anchored scan produced 196 false positives on
  the 10-minute sample, because a broken early exit in the edit-distance
  function returned distances far beyond the requested limit instead of
  rejecting them. The distance function is now exact and tested against known
  values.
- Single-word edit distance was tried and abandoned: it cannot distinguish the
  correct English "bulls", "reverse down" and "near the top" from real errors.
  Anchoring on an exact neighbouring word is what makes the check usable.
- A generic edit distance of two was also rejected: at two, "clear breakout" is
  indistinguishable from the real "bare bar" mistake. The distance-two cases
  are now curated data instead of a heuristic.

### Tests

55 new cases in `tests/test_confusables.py`, including a precision suite that
asserts 14 ordinary English phrases are **not** flagged. Total: **418 passing**,
no network access, no API charges. Ruff and mypy clean.

### Performance

Confusable detection is indexed on the anchor's second word, so the sweep is a
single dictionary hit per position. A full 221,000-word transcript analyses in
about 6 seconds.

## [0.2.0] - 2026-10-09

Phase 2: transcript quality assurance and preparation for translation.
Phase 1 behaviour and artifacts are unchanged.

### Added

**Quality assurance (`src/qa.py`)**

- 18 structured checks, each reported with severity, affected segment ids, time
  range, an explanation and a suggested action:
  - `exact_duplicate_segment`, `near_duplicate_segment`,
    `duplicate_segment_duration_mismatch`
  - `repeated_phrase` (conservative, never auto-removed)
  - `empty_segment`, `no_segments`
  - `invalid_timestamp`, `segment_end_before_start`, `segment_out_of_order`,
    `segment_timestamp_out_of_bounds`
  - `segment_gap`, `segment_overlap`
  - `transcript_segment_mismatch`
  - `segment_suspiciously_short`, `segment_suspiciously_long`
  - `incomplete_speech_at_start`, `incomplete_speech_at_end`
  - `terminology_detected` (annotation only)
- Five-level severity scale; any `critical`/`high`/`medium` finding sets the run
  status to `NEEDS_REVIEW`.
- Configurable `QaThresholds` so limits can be tuned without code changes.

**Deterministic cleaning (`src/text_cleaning.py`)**

- Formatting-only cleanup: Unicode NFKC, whitespace collapse, spacing before
  punctuation, bracket binding, space after clause punctuation, ellipsis
  normalisation.
- Never rewrites words, fixes grammar, translates or removes content. A test
  asserts the cleaned text introduces no new word tokens.
- A separate punctuation-free comparison key used only for equality and
  similarity checks, never written to an output file.

**Prepared transcript schema (`src/transcript.py`)**

- Versioned `schema_version: 2` in `*.en.clean.json`, documented field by field
  in the README.
- Each segment carries `segment_id`, `source_segment_ids`, audio time range,
  `raw_text`, `cleaned_text`, `quality_flags`, `correction_status`,
  `timestamp_confidence` and detected `terminology`.
- Unreliable boundaries are represented explicitly via
  `timestamp_confidence` (`reliable` / `suspect` / `missing`) rather than
  invented. No word-level timestamps or confidence scores are fabricated.
- Tolerates malformed input: non-object segments, missing/NaN/negative/string
  timestamps, out-of-order segments, transcripts without segments.

**Cautious recovery strategy (`src/verification.py`)**

- Optional, explicitly requested, bounded re-transcription of the *same audio*
  (`--verify-transcript`, `--verify-attempts`).
- Verdicts: `duplicate_contradicted` (the only case that may merge segments),
  `duplicate_confirmed`, `inconclusive`, `failed`, `not_performed`.
- Never claims a transcript was verified unless a real request completed.
- Permanent errors (invalid key, forbidden, oversized) are **not** retried,
  even at the verification level.
- Audio is located from `--audio`, an export beside the artifact, or
  re-extracted from the source video with FFmpeg; when none is possible the
  command lists exactly what is missing.

**Al Brooks terminology (`src/terminology.py`, `src/resources/terminology.json`)**

- 35 domain terms with categories, aliases, notes and empty `persian` fields
  ready for the translation phase.
- Case-insensitive, whole-word matching that prefers the longest match
  (`wedge bull flag` wins over `wedge` and `bull flag`).
- Terms that are also ordinary English words are marked `ambiguous`.
- Used for annotation only; a test asserts it never changes source text.

**CLI (`--prepare-transcript`)**

- `python -m src.main --prepare-transcript output/lecture_test_30s.en.json`
  — QA and cleaning only; makes **no** API call and needs no API key.
- `--verify-transcript`, `--audio`, `--verify-attempts` for optional
  cross-checking. Off by default so routine preparation costs nothing.
- All Phase 1 flags and commands are unchanged and still covered by tests.
- New exit code `7` for transcript problems.

**Output protection**

- New artifacts `<name>.en.clean.txt`, `<name>.en.clean.json`, `<name>.qa.json`
  are protected from accidental overwrite; `--overwrite` is required.
- Raw `*.en.json` and `*.en.txt` are opened read-only and are never rewritten.

### Changed

- `src/outputs.py` gained `PreparedPaths` and a public atomic-write helper.
- `src/transcription.py` gained `is_retryable_error()` so higher-level loops can
  avoid retrying permanent failures.
- `src/errors.py` gained the Phase 2 exception hierarchy.
- `src/enums.py` added for a Python 3.10-compatible string enum (the project
  targets 3.10, where `enum.StrEnum` does not exist).

### Fixed

- `text_cleaning._open_brackets` dropped the opening bracket instead of the
  space after it, so `( spaced )` became `spaced)`.
- `OutputPaths` is unaffected; the QA coverage early-return no longer skips
  bounds and completeness checks for single-segment transcripts.
- Out-of-order segments are now detected (`segment_out_of_order`), which was
  specified but previously missing.

### Performance

- Repeated-phrase detection rewritten from an n-gram sweep to a seed-and-extend
  index; terminology matching replaced an O(n²) overlap check with a sorted
  watermark; similarity on very long text is bounded. A full 221,000-word
  transcript (a whole 2 h 49 m lecture) now analyses in about **3.4 s** instead
  of 148 s. A performance regression test guards this.

### Tests — 363 total, all passing

201 new Phase 2 cases covering exact and near duplicates, legitimate repeated
terminology, duplicate transcript vs segment text, invalid/negative/NaN/
out-of-order/overlapping timestamps, gaps and overlaps, empty segments,
transcript/segment inconsistency, conservative cleaning and the guarantee that
raw files are untouched, overwrite protection, uncertain corrections, missing
audio during verification, API failures and auth failures during verification,
bounded retries, schema serialisation and validity, terminology detection
rules, and long-transcript performance. All API access is mocked; no test needs
a real key or spends credits, and none performs a network request.

### Known issues

- Unchanged from 0.1.0: pytest's default temporary directory is not writable on
  this machine, so `PYTEST_DEBUG_TEMPROOT` must point at a project-local folder
  (documented in the README).

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

[0.3.1]: https://example.invalid/al-brooks-dubbed/releases/0.3.1
[0.3.0]: https://example.invalid/al-brooks-dubbed/releases/0.3.0
[0.2.0]: https://example.invalid/al-brooks-dubbed/releases/0.2.0
[0.1.0]: https://example.invalid/al-brooks-dubbed/releases/0.1.0