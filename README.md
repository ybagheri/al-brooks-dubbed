# Al Brooks Dubbed

Local, CPU-only pipeline that turns an English trading lecture video into a
verified 30-second test segment plus an accurate English transcript with
timestamps. It is the foundation for a later Persian dubbing workflow.

**Persian version: [README.fa.md](README.fa.md)**

---

## Phases and scope

### Phase 1 — video extraction and English transcription ✅

1. **Video extraction** - cut the first *N* seconds (default 30) out of the
   source video into a standalone, playable MP4 using FFmpeg.
2. **English transcription** - send the audio of that *same* interval to the
   Groq speech-to-text API and save the transcript as UTF-8 text plus a
   structured JSON file.

### Phase 2 — transcript quality assurance and preparation ✅

3. **Quality assurance** - detect duplicated segments, broken timestamps,
   gaps, overlaps, transcript/segment mismatches and more, reported in a
   structured JSON report.
4. **Preparation** - deterministic formatting cleanup plus a cleaned,
   translation-ready JSON schema that keeps every link back to the original
   audio timing.
5. **Optional verification** - an explicitly requested, bounded re-transcription
   of the *same audio* that can turn real evidence into a correction.

### Explicitly NOT implemented yet

None of the following is implemented (see [ROADMAP.md](ROADMAP.md)):

- Persian translation
- Text-to-speech / speech synthesis
- Voice cloning or voice conversion
- Lip synchronisation
- Full-video (multi-hour) dubbing

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11, 64-bit | Paths are handled with `pathlib`; the code is cross-platform. |
| Python 3.10+ | 3.12 recommended. |
| FFmpeg **and** FFprobe | Both binaries are required. |
| A Groq API key | Free tier is enough for Phase 1. |
| Network access to `api.groq.com` | Directly, or through a working proxy. |

No GPU is used or required. Everything runs on CPU.

### Installing FFmpeg on Windows

Download a build from <https://ffmpeg.org/download.html> (the
`essentials` build is fine), extract it, and add its `bin` folder to `PATH` -
for example `C:\ffmpeg\bin`.

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

If you prefer not to modify `PATH`, set `FFMPEG_PATH` and `FFPROBE_PATH`
instead (see [Configuration](#configuration)).

---

## Windows setup

### 1. Create the virtual environment

Run from the project root (`D:\Projects\al-brooks-dubbed`):

```powershell
cd D:\Projects\al-brooks-dubbed
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, either allow it once with
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` or simply call the
interpreter directly as `.\.venv\Scripts\python.exe` in every command below.

> Dependencies are **never** installed globally - always activate the venv, or
> use the explicit interpreter path.

### 2. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The `-e` flag installs the project in editable mode, which also creates the
`al-brooks-dubbed` console command. Dependencies are pinned to compatible
ranges in `pyproject.toml`:

- `groq>=0.13,<2` - official Groq SDK (audio transcription endpoint)
- `python-dotenv>=1.0,<2` - optional `.env` support
- dev extras: `pytest`, `pytest-cov`, `ruff`, `mypy`

### 3. Configure `GROQ_API_KEY`

The key is read **only** from the environment. Never put it in source code.

Create a key at <https://console.groq.com/keys>, then set it for the current
session:

```powershell
$env:GROQ_API_KEY = "gsk_..."
```

Or persist it for your user account (takes effect in *new* terminals):

```powershell
[Environment]::SetEnvironmentVariable("GROQ_API_KEY", "gsk_...", "User")
```

From `cmd.exe`:

```cmd
setx GROQ_API_KEY "gsk_..."
```

**Check that the variable exists without printing its value:**

```powershell
if ($env:GROQ_API_KEY) { "GROQ_API_KEY is set" } else { "GROQ_API_KEY is missing" }
```

Or let the application check everything for you:

```powershell
python -m src.main --check-env
```

This prints the ffmpeg/ffprobe versions and confirms the key is present while
reporting only its length - never its value.

> A `.env` file is **optional**. If you prefer one, copy `.env.example` to
> `.env` and fill it in; the Windows environment variable always wins and is
> sufficient on its own. `.env` is git-ignored.

---

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | yes | - | Groq API key. |
| `GROQ_TRANSCRIPTION_MODEL` | no | `whisper-large-v3-turbo` | Speech-to-text model. |
| `GROQ_PROXY` | no | auto | Proxy URL for the Groq API. |
| `HTTPS_PROXY` / `HTTP_PROXY` | no | auto | Standard proxy variables. |
| `FFMPEG_PATH` / `FFPROBE_PATH` | no | auto | Explicit executable paths. |
| `AL_BROOKS_DURATION_SECONDS` | no | `30` | Default test duration. |
| `AL_BROOKS_LANGUAGE` | no | `en` | Language hint sent to the API. |

Command line arguments override environment variables, which override
defaults.

### Model selection

The model was **verified against your own Groq account** (the model listing
returned `whisper-large-v3` and `whisper-large-v3-turbo`). The default is
`whisper-large-v3-turbo`: faster and cheaper than the full model while keeping
strong English accuracy, and it supports segment timestamps.

To switch:

```powershell
python -m src.main --model whisper-large-v3
# or
$env:GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3"
```

Only models served by the **audio transcription** endpoint work here. A chat
completion model cannot transcribe audio and the application will not attempt it.

### Proxy configuration

Some networks (including many in Iran) route traffic through a local proxy.
PowerShell and FFmpeg honour the Windows *Internet Settings* proxy, but
Python's HTTP stack does not - which makes every Groq call fail with a
misleading `403 Forbidden` even though the key is valid.

The application therefore detects the Windows proxy automatically. To override:

```powershell
$env:GROQ_PROXY = "http://127.0.0.1:1080"
```

---

## Usage

All commands run from the project root.

### Phase 1 — extract and transcribe

```powershell
# Process the single video in .\data automatically
python -m src.main

# Choose the input explicitly
python -m src.main --input lecture.mp4

# Choose the length of the test segment
python -m src.main --input lecture.mp4 --duration 30

# Replace previously generated artifacts
python -m src.main --overwrite

# See what would be processed, or what is wrong with the setup
python -m src.main --list-inputs
python -m src.main --check-env
```

### Phase 2 — quality assurance and preparation

Runs against an artifact Phase 1 already produced. **It makes no API call by
default**, so it is free and safe to re-run.

```powershell
# Clean and analyse an existing transcript
python -m src.main --prepare-transcript output\lecture_test_30s.en.json

# Replace previous prepared output
python -m src.main --prepare-transcript output\lecture_test_30s.en.json --overwrite

# Additionally re-transcribe the same audio to check a suspected duplicate
# (this DOES spend API usage)
python -m src.main --prepare-transcript output\lecture_test_30s.en.json --verify-transcript

# Point at a specific audio file, allow more than one verification pass
python -m src.main --prepare-transcript output\lecture_test_30s.en.json `
    --verify-transcript --audio output\lecture_test_30s.flac --verify-attempts 2

# Full reference
python -m src.main --help
```

### Input selection rules

- With **no** `--input`: exactly one supported video in `data` is used.
- With **several** videos: the run stops and lists them; you must pass
  `--input`. Nothing is ever guessed.
- With **none**: a clear error tells you where to put the file.
- With `--input`: resolved relative to `data`, or used as-is if absolute.
  Filenames with spaces and non-ASCII characters are fully supported.

Supported extensions: `.mp4 .mkv .mov .avi .webm .m4v .mpg .mpeg .ts .wmv
.flv .ogv`.

### The seven stages

Every Phase 1 run reports each stage explicitly:

```
Stage 1/7  Input validation
Stage 2/7  Media inspection (ffprobe)
Stage 3/7  Video extraction (first 30.00s)
Stage 4/7  Audio preparation (ffmpeg, mono 16000 Hz)
Stage 5/7  Groq transcription (model: whisper-large-v3-turbo)
Stage 6/7  Output validation
Stage 7/7  Writing output artifacts
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Unexpected error. |
| `2` | Configuration problem (missing key, missing ffmpeg, bad duration). |
| `3` | Input problem (missing directory, no video, ambiguous choice). |
| `4` | Media problem (ffprobe/ffmpeg failure). |
| `5` | Transcription problem (auth, network, empty response). |
| `6` | Output problem (not writable, validation failed, existing artifact). |
| `7` | Transcript problem (Phase 2: unreadable artifact, QA failure). |

---

## Phase 2: quality assurance and preparation

### The one rule that matters

**A suspect transcript is never silently changed.** Phase 1's output is the
source of truth and is opened read-only. Phase 2 writes *new* files, and it
only ever removes a repetition when an independent re-transcription of the same
audio positively contradicts it. Otherwise the wording is preserved verbatim
and flagged for a human to decide.

### How to review an uncertain transcript

```powershell
python -m src.main --prepare-transcript output\lecture_test_30s.en.json
```

1. Read the **review notes** in the console output - they name the affected
   segments and time ranges.
2. Open the extracted video at the reported time range and **listen**.
3. Only then decide whether to change anything. The cleaned text is a plain
   UTF-8 file you can edit, but the JSON keeps the original per segment.

If you want a second opinion from the API, add `--verify-transcript`. If the
audio is not available the command says exactly what is missing instead of
guessing.

### What the QA stage checks

| Finding | Severity | Meaning |
|---|---|---|
| `empty_segment` | medium | A segment has no text. |
| `no_segments` | high / critical | No timing data, or the transcript is blank. |
| `exact_duplicate_segment` | high / medium | Two segments carry identical words. |
| `near_duplicate_segment` | high / medium | Segments are almost identical. |
| `duplicate_segment_duration_mismatch` | high | The same words span very different times. |
| `repeated_phrase` | low | A phrase occurs more than once in the combined text. |
| `invalid_timestamp` | high | Missing, non-numeric, NaN or negative. |
| `segment_end_before_start` | critical | The segment has no positive duration. |
| `segment_out_of_order` | high | Segments are not in chronological order. |
| `segment_timestamp_out_of_bounds` | high | A timestamp lies outside the audio. |
| `segment_gap` | low | Audio with no transcript covering it. |
| `segment_overlap` | medium | Two segments claim the same audio. |
| `transcript_segment_mismatch` | high / medium | The transcript and segments disagree. |
| `segment_suspiciously_short` | low | An implausibly brief segment. |
| `segment_suspiciously_long` | medium | Far longer than the median segment. |
| `incomplete_speech_at_start` | medium | Transcription starts after 0s. |
| `incomplete_speech_at_end` | medium | Transcription stops before the audio ends. |
| `confusable_term` | medium | A word is probably a mistranscription of a domain term. |
| `terminology_detected` | info | Al Brooks vocabulary was found and annotated. |

Severity drives the run status: any `critical`, `high` or `medium` finding makes
the CLI report `STATUS: NEEDS_REVIEW`.

> Repetition is **not** automatically an error. Trading lectures deliberately
> restate ideas and repeat terminology, so repeated phrases are reported at
> `low` severity and never removed.

> Speaker pauses are **not** automatically an error. This lecture is live
> trading commentary, so multi-second silences while charts are read are
> normal. Gaps are reported at `low` severity for information only.

### Confusable term detection

The most damaging transcription errors are not garbled words - they are
perfectly readable English with the wrong technical meaning. In the 10-minute
sample of this lecture the API produced:

| Written | Should be | Count |
|---|---|---|
| `training range` | `trading range` | 6 |
| `bare bar` / `bare body` | `bear bar` / `bear body` | 3 |

Left alone, these would be translated literally and a Persian trader would
receive a term that was never used. The QA stage flags them as
`confusable_term` and never changes the text - only the audio can settle it.

Detection uses two complementary mechanisms, because neither alone is precise:

1. **Curated confusions** (`known_confusions` in `terminology.json`). Mistakes
   a human has confirmed occur in this material. Reliable, and **you can add
   entries** as you find more:

   ```json
   {
     "wrong": "training",
     "right": "trading",
     "note": "Heard in the BTR20140930 lecture: 'training range' should be 'trading range'."
   }
   ```

2. **Anchored single-edit detection.** A candidate is only reported when the
   neighbouring word matches a known domain word *exactly*
   (`confusable_extra_anchors`), so a generic word can never be flagged on its
   own.

On the 10-minute sample this reports the two real errors and nothing else.
A wider edit distance was tried and rejected: at two edits, `clear breakout`
becomes indistinguishable from the real `bare bar` mistake, and the false
positives swamp the true findings.

### The cautious recovery strategy

When duplicates are found, the tool will not guess:

1. The duplicate and the unusually long segment are flagged (`high`).
2. With `--verify-transcript`, the same audio is transcribed again, bounded by
   `--verify-attempts` (permanent errors such as an invalid key are *not*
   retried).
3. The two transcriptions are compared occurrence by occurrence.
4. The result is recorded as one of:
   - `duplicate_contradicted` - the fresh transcript does not contain the
     repetition. **Only now** may the duplicate segments be merged, and the
     merge is recorded as `verified_by_retranscription`.
   - `duplicate_confirmed` - the fresh transcript contains it too, so the
     speaker really said it. **Nothing is removed.**
   - `inconclusive` / `failed` - the evidence was not decisive. **Nothing is
     removed** and the segments are flagged `uncertain_review_required`.

A transcript is never described as verified unless a real transcription
request actually completed.

### Al Brooks terminology

`src/resources/terminology.json` holds 35 domain terms (`wedge bull flag`,
`bull flag`, `bear flag`, `trading range`, `measured move`, `price action`,
`gap`, `breakout`, `pullback`, `reversal`, and more).

Phase 2 uses it **only for annotation**. A term match never alters the source
text.

**The Persian track keeps these terms in English.** Persian traders say
"wedge bull flag", not a literal Persian rendering, so the entry records the
English term as its spoken form and flags the decision explicitly:

```json
{
  "id": "bull_flag",
  "canonical": "bull flag",
  "category": "pattern",
  "notes": "Strong bullish continuation flag after a spike.",
  "persian": "bull flag",
  "speak_original": true
}
```

`speak_original: true` matters: an English value sitting in a field called
`persian` otherwise reads as a typo to whoever maintains it next. A test
enforces that every spoken-as value equal to the English original carries the
flag.

To translate a term instead, replace `persian` with the Persian text and drop
the flag:

```json
"persian": "پرچم صعودی"
```

Terms that are also ordinary English words (`gap`, `bull`, `channel`) are
marked `ambiguous` so downstream stages do not over-trust them.

The same file carries two extension points you can edit without touching code:

| Key | Purpose |
|---|---|
| `terms[].persian` | What the term is spoken as in the Persian track. |
| `terms[].speak_original` | `true` when that is deliberately the English original. |
| `known_confusions` | Mistranscriptions confirmed in this material. |
| `confusable_extra_anchors` | Word pairs used to anchor confusable detection. |

### Cleaned JSON schema (version 2)

`output/<name>_test_30s.en.clean.json`:

| Field | Description |
|---|---|
| `schema_version` | Currently `2`. |
| `artifact_type` | `prepared_transcript`. |
| `generated_at` | UTC timestamp of the preparation run. |
| `source_artifact` | The raw `*.en.json` this came from. |
| `source_file` / `source_path` | Original video, if recorded. |
| `transcription_model` | Model that produced the raw transcript. |
| `language` | Language requested/reported. |
| `processed_duration_seconds` | Length of the transcribed interval. |
| `cleaning` | What the deterministic stage changed, and proof it changed nothing else. |
| `verification` | Whether and how the transcript was cross-checked. |
| `qa_summary` | Findings counted by severity and type. |
| `terminology_summary` | Detected domain terms. |
| `cleaned_transcript` | Full cleaned text. |
| `raw_transcript` | The API's text, unmodified. |
| `segments[]` | One entry per prepared segment (below). |

Each `segments[]` entry:

| Field | Description |
|---|---|
| `segment_id` | Stable id, e.g. `0` or `0-1` after a verified merge. |
| `source_segment_ids` | The raw segment(s) it was built from. |
| `start_seconds` / `end_seconds` | Audio timing, or `null` when unknown. |
| `duration_seconds` | End minus start, or `null`. |
| `raw_text` | Original text, unmodified. |
| `cleaned_text` | Formatting-cleaned English. |
| `quality_flags` | Finding types attached to this segment. |
| `correction_status` | `unchanged`, `uncertain_review_required`, `verified_by_retranscription`. |
| `timestamp_confidence` | `reliable`, `suspect` or `missing`. |
| `terminology` | Terms detected in this segment. |

Unreliable boundaries are represented explicitly through
`timestamp_confidence: "missing" | "suspect"` rather than by inventing times.
No word-level timestamps are fabricated.

### Performance

The QA stage is designed for full-length material. A synthetic 221,000-word
transcript (a whole 2 h 49 m lecture) analyses in about **6 seconds**. Repeated
phrases are found with a seed-and-extend index rather than an exhaustive n-gram
sweep, confusable detection is indexed on the anchor word, and long-text
similarity is bounded.

---

## Output files

For an input named `lecture.mp4`, `output/` receives:

**Phase 1 (raw, authoritative, never modified)**

| File | Contents |
|---|---|
| `lecture_test_30s.mp4` | The extracted video segment. |
| `lecture_test_30s.en.txt` | Plain UTF-8 transcript, exactly as returned. |
| `lecture_test_30s.en.json` | Structured transcript + processing metadata. |
| `lecture_test_30s.metadata.json` | Media inspection and extraction details. |

**Phase 2 (derived, regenerate freely)**

| File | Contents |
|---|---|
| `lecture_test_30s.en.clean.txt` | Cleaned, translation-ready English text. |
| `lecture_test_30s.en.clean.json` | Prepared segments, schema version 2. |
| `lecture_test_30s.qa.json` | Structured quality findings. |

Existing files are **never** silently overwritten - the run stops and asks for
`--overwrite`.

Only fields the API actually returned are recorded. If timestamps are not
available, `segments` is `null`; confidence scores are never invented.

Run logs are written to `logs/run-<timestamp>.log`. The API key is redacted
from all log output.

### Extraction strategy

FFmpeg stream copying (`-c copy`) is used whenever the codecs fit the MP4
container, which is instant and lossless. If a stream is incompatible, only
that stream is re-encoded (`libx264` CRF 20 / AAC 128 kbps). The resolution is
never reduced. Extraction streams from disk; the video is never loaded into
memory.

---

## Running the tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

With coverage and lint/type checks:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=src --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m mypy
```

**Unit tests never call the Groq API and never require a real key**, so they
cannot cost you anything. Integration tests generate a small synthetic video
with FFmpeg inside pytest's temporary directory; no media fixtures are
committed.

> **Windows note:** if pytest crashes at the end of the run with
> `PermissionError ... pytest-current`, its default temp directory is not
> writable. Point it at a project-local folder:
>
> ```powershell
> $env:PYTEST_DEBUG_TEMPROOT = "$PWD\.pytest_tmp"
> New-Item -ItemType Directory -Path .pytest_tmp -Force | Out-Null
> .\.venv\Scripts\python.exe -m pytest
> ```

---

## API usage and free-tier limits

- Each run makes **exactly one** transcription request for the selected
  interval. A 30-second clip is a tiny fraction of one audio minute.
- The Groq free tier allows a limited number of audio minutes per day. When the
  limit is hit the API answers `429`; the client retries with exponential
  backoff and, if still failing, tells you to wait.
- Uploads are limited to roughly 25 MB. 30 seconds of mono 16 kHz FLAC is
  about 0.8 MB, so the limit is never reached at the default duration. If audio
  ever did exceed it, the run **stops with an explicit error** rather than
  silently truncating and losing speech.
- `whisper-large-v3-turbo` is the cheaper/faster option; `whisper-large-v3` is
  slightly more accurate and slower.

---

## Troubleshooting

**`GROQ_API_KEY is not set`**
Set the variable in the shell you are running from (see
[Configuring `GROQ_API_KEY`](#3-configure-groq_api_key)). Note that `setx`
only affects *new* terminals.

**`403 Forbidden` from Groq even though the key is valid**
Almost always a proxy issue, not a key issue. Confirm with:

```powershell
python -m src.main --check-env
```

If you use a local proxy, set `$env:GROQ_PROXY = "http://127.0.0.1:1080"`.
The application also auto-detects the Windows Internet Settings proxy.

**`ffmpeg was not found on PATH`**
Add the FFmpeg `bin` folder to `PATH`, or set `FFMPEG_PATH` and
`FFPROBE_PATH` to the full paths.

**`Found N videos in ...`**
The application refuses to guess. Pass `--input "<filename>"`.

**`Output already exists and --overwrite was not given`**
Re-run with `--overwrite`, or choose another `--output-dir`.

**The transcript is empty**
The interval probably contains no speech (silence, music). Try a different
`--duration` or a later part of the video. This is reported as a clear error,
not silently accepted.

**Phase 2 reports `STATUS: NEEDS_REVIEW`**
This is information, not a failure: the run succeeded and the files are valid.
The console review notes tell you which segments and time ranges to listen to.

**Phase 2 says the audio needed for `--verify-transcript` is missing**
Provide it with `--audio <file>`, or place an export next to the transcript
with the same name (`.flac`/`.wav`/`.mp3`/`.m4a`/`.ogg`), or keep the source
video recorded in the artifact so the interval can be re-extracted with FFmpeg.

**Phase 2 shows a duplicated segment**
Expected on speech-to-text output. Read
[How to review an uncertain transcript](#how-to-review-an-uncertain-transcript)
before changing anything. If `--verify-transcript` reports
`duplicate_confirmed`, two independent transcriptions agree and the repetition
is real.

**`The prepared audio ... exceeds the upload limit`**
Shorten the interval: `--duration 30`. Audio is never truncated automatically.

**Groq returns a rate limit (`429`)**
The free tier is exhausted. Wait and retry; retries are automatic and bounded.

---

## Project layout

```
al-brooks-dubbed/
├── data/                     # your source video goes here (git-ignored)
├── output/                   # generated artifacts (git-ignored)
├── logs/                     # run logs (git-ignored)
├── src/
│   ├── main.py               # `python -m src.main`
│   ├── cli.py                # argument parsing and exit codes
│   ├── config.py             # configuration + environment validation
│   ├── discovery.py          # input video discovery and validation
│   ├── media.py              # FFmpeg / FFprobe service
│   ├── transcription.py      # Groq client, retries, proxy handling
│   ├── outputs.py            # artifact paths, atomic writes, validation
│   ├── pipeline.py           # Phase 1 stage orchestration
│   ├── text_cleaning.py      # Phase 2 deterministic formatting cleanup
│   ├── qa.py                 # Phase 2 quality detection and reporting
│   ├── transcript.py         # Phase 2 domain model and schemas
│   ├── terminology.py        # Al Brooks vocabulary annotations
│   ├── verification.py       # Phase 2 optional re-transcription check
│   ├── preparation.py        # Phase 2 stage orchestration
│   ├── enums.py              # Python 3.10-compatible string enums
│   ├── logging_utils.py      # logging + secret redaction
│   ├── errors.py             # exception hierarchy
│   └── resources/
│       └── terminology.json   # editable Al Brooks glossary
└── tests/                    # pytest suite (no API calls, no media fixtures)
```

## Further reading

- [ROADMAP.md](ROADMAP.md) - what comes after Phase 1
- [CHANGELOG.md](CHANGELOG.md) - release history
- [HANDOFF.md](HANDOFF.md) - current status, verification results, next steps