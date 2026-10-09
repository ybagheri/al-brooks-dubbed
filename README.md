# Al Brooks Dubbed

Local, CPU-only pipeline that turns an English trading lecture video into a
verified 30-second test segment plus an accurate English transcript with
timestamps. It is the foundation for a later Persian dubbing workflow.

**Persian version: [README.fa.md](README.fa.md)**

---

## Phase 1 scope

Phase 1 does exactly two things:

1. **Video extraction** - cut the first *N* seconds (default 30) out of the
   source video into a standalone, playable MP4 using FFmpeg.
2. **English transcription** - send the audio of that *same* interval to the
   Groq speech-to-text API and save the transcript as UTF-8 text plus a
   structured JSON file.

### Explicitly NOT in Phase 1

None of the following is implemented yet (see [ROADMAP.md](ROADMAP.md)):

- Persian translation
- Text-to-speech / speech synthesis
- Voice cloning or voice conversion
- Lip synchronisation
- Full-video (multi-hour) dubbing

Phase 1 exists to prove out the media and transcription plumbing on a real
lecture before any of the harder, more expensive stages are attempted.

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

Every run reports each stage explicitly:

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
| `6` | Output problem (not writable, validation failed). |

---

## Output files

For an input named `lecture.mp4`, `output/` receives:

| File | Contents |
|---|---|
| `lecture_test_30s.mp4` | The extracted video segment. |
| `lecture_test_30s.en.txt` | Plain UTF-8 transcript, ready for translation. |
| `lecture_test_30s.en.json` | Structured transcript + processing metadata. |
| `lecture_test_30s.metadata.json` | Media inspection and extraction details. |

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
│   ├── outputs.py            # artifact paths, writing, validation
│   ├── pipeline.py           # stage orchestration
│   ├── logging_utils.py      # logging + secret redaction
│   └── errors.py             # exception hierarchy
└── tests/                    # pytest suite (no API calls, no media fixtures)
```

## Further reading

- [ROADMAP.md](ROADMAP.md) - what comes after Phase 1
- [CHANGELOG.md](CHANGELOG.md) - release history
- [HANDOFF.md](HANDOFF.md) - current status, verification results, next steps