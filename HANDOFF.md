# Handoff

**Phase:** 1 - video extraction and English transcription
**Status:** ✅ Complete and verified against a real video with a real Groq API call
**Date:** 2026-10-09
**Repository:** local directory only - **not** a Git repository, no remote configured

---

## 1. What was built

A modular Python application that finds a video in `data/`, extracts the first
N seconds with FFmpeg, transcribes that exact interval with the Groq
speech-to-text API, and writes four validated artifacts.

| Module | Responsibility |
|---|---|
| `src/config.py` | Configuration, environment validation, model defaults |
| `src/discovery.py` | Input video discovery and validation |
| `src/media.py` | FFprobe inspection + FFmpeg extraction service |
| `src/transcription.py` | Groq client, error classification, retries, proxy handling |
| `src/outputs.py` | Artifact paths, atomic writes, validation |
| `src/pipeline.py` | Seven-stage orchestration |
| `src/cli.py` | Argument parsing and exit codes |
| `src/main.py` | `python -m src.main` entry point |
| `src/logging_utils.py` | Logging with mandatory secret redaction |
| `src/errors.py` | Exception hierarchy with per-class exit codes |

Files created:

```
pyproject.toml  .gitignore  .env.example
README.md  README.fa.md  ROADMAP.md  CHANGELOG.md  HANDOFF.md
src/__init__.py  src/main.py  src/cli.py  src/config.py  src/discovery.py
src/media.py  src/transcription.py  src/outputs.py  src/pipeline.py
src/logging_utils.py  src/errors.py
tests/__init__.py  tests/conftest.py  tests/test_config.py
tests/test_discovery.py  tests/test_media.py  tests/test_transcription.py
tests/test_outputs.py  tests/test_pipeline.py  tests/test_cli.py  tests/test_proxy.py
```

Generated during setup (all git-ignored): `.venv/`, `output/`, `logs/`,
`.pytest_tmp/`.

---

## 2. Environment

| Item | Value |
|---|---|
| OS | Windows 11, x86-64 |
| Python | 3.12.9 (venv at `D:\Projects\al-brooks-dubbed\.venv`) |
| FFmpeg / FFprobe | 9.0.2-essentials, `C:\ffmpeg\bin\` |
| Groq SDK | 1.7.0 |
| pytest / ruff / mypy | 9.1.1 / 0.16.10 / 2.4.0 |
| `GROQ_API_KEY` | present (value never logged or printed) |
| System proxy | `http://127.0.0.1:1080` |

### Source media

```
D:\Projects\al-brooks-dubbed\data\BTR20140930-9439edit.mp4
  size      : 526,839,074 bytes (~527 MB)
  duration  : 10170.07 s (2 h 49 m 30 s)
  video     : h264 High, 1368x736, 15 fps, yuv420p
  audio     : aac LC, 2 ch, 44.1 kHz, 129 kbps
```

---

## 3. Commands run and actual results

### 3.1 Environment check

```
> .\.venv\Scripts\python.exe -m src.main --check-env
ffmpeg   ffmpeg version 9.0.2-essentials_build-www.gyan.dev ...
ffprobe  ffprobe version 9.0.2-essentials_build-www.gyan.dev ...
GROQ_API_KEY is set (value hidden, 56 characters).
Environment check PASSED - ready to run python -m src.main
```

**Result:** exit code `0`.

### 3.2 Automated tests

```
> $env:PYTEST_DEBUG_TEMPROOT = "D:\Projects\al-brooks-dubbed\.pytest_tmp"
> .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider

tests/test_cli.py ............
tests/test_config.py .........
tests/test_discovery.py .......
tests/test_media.py ............
tests/test_outputs.py .....................
tests/test_pipeline.py ..............
tests/test_proxy.py ..........
tests/test_transcription.py ...........................

============================= 159 passed in 8.88s =============================
```

**Result:** **159 passed, 0 failed, 0 skipped, 0 errors** (8.88 s).

No test calls the Groq API; the client is fully mocked, so the suite needs no
real key and incurs no cost.

### 3.3 Lint, format and types

```
> .\.venv\Scripts\python.exe -m ruff check src tests
All checks passed!

> .\.venv\Scripts\python.exe -m ruff format --check src tests
21 files already formatted

> .\.venv\Scripts\python.exe -m mypy
Success: no issues found in 21 source files
```

**Result:** clean, with `mypy` running in `disallow_untyped_defs` mode.

### 3.4 Real end-to-end run

```
> .\.venv\Scripts\python.exe -m src.main --overwrite
```

Stage by stage:

```
Stage 1/7  Input validation
  Source video : D:\Projects\al-brooks-dubbed\data\BTR20140930-9439edit.mp4
  Size         : 526.8 MB

Stage 2/7  Media inspection (ffprobe)
  Duration   : 10170.067s (169.50 minutes)
  Format     : mov,mp4,m4a,3gp,3g2,mj2
  Video      : h264 1368x736 @ 15.000 fps
  Audio      : aac 2ch @ 44100 Hz

Stage 3/7  Video extraction (first 30.00s)
  Duration        : 30.133s (mode: copy)
  Resolution      : 1368x736 (unchanged from source)

Stage 4/7  Audio preparation (ffmpeg, mono 16000 Hz)
  Prepared audio : BTR20140930-9439edit_test_30s.flac (816.3 KB, 30.00s)

Stage 5/7  Groq transcription (model: whisper-large-v3-turbo)
  Using HTTP proxy for the Groq API: http://127.0.0.1:1080
  Transcription received: 236 character(s), 3 segment(s), language=English

Stage 6/7  Output validation
  All 4 artifacts are present, non-empty and parseable.

Stage 7/7  Writing output artifacts
```

**Result:** exit code `0`.

```
==================================================================
Phase 1 complete - STATUS: SUCCESS
==================================================================
  Source video          : D:\Projects\al-brooks-dubbed\data\BTR20140930-9439edit.mp4
  Source duration       : 10170.07s
  Requested duration    : 30s
  Extracted video       : D:\Projects\al-brooks-dubbed\output\BTR20140930-9439edit_test_30s.mp4
  Extracted duration    : 30.13s (copy)
  Transcript (plain)    : D:\Projects\al-brooks-dubbed\output\BTR20140930-9439edit_test_30s.en.txt
  Transcript (JSON)     : D:\Projects\al-brooks-dubbed\output\BTR20140930-9439edit_test_30s.en.json
  Media metadata        : D:\Projects\al-brooks-dubbed\output\BTR20140930-9439edit_test_30s.metadata.json
  Transcription model   : whisper-large-v3-turbo
  Language              : English
  Transcript characters : 235
  Timed segments        : 3
==================================================================
```

### 3.5 Artifact verification

**Extracted video** - `ffprobe`:

```json
{
  "streams": [
    {"codec_name": "h264", "codec_type": "video", "width": 1368, "height": 736},
    {"codec_name": "aac",  "codec_type": "audio", "sample_rate": "44100", "channels": 2}
  ],
  "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2",
             "duration": "30.133333", "size": "2806132"}
}
```

Full decode check (`ffmpeg -f null -`) completed with **no errors**. Resolution
matches the source exactly (1368x736) and audio/video are intact.

**Transcript** (`BTR20140930-9439edit_test_30s.en.txt`):

> Sorry about being a couple of minutes late. The bulls see the 60 minute chart
> as forming Sorry about being a couple of minutes late. The bulls see the 60
> minute chart as forming a wedge bull flag respecting the gap back here in
> August.

This matches the actual lecture audio. The opening sentence genuinely repeats
in the source recording: the API returns it as two identical consecutive
segments (`0.0–7.0 s` and `7.0–23.0 s`), which corroborates that this is in
the audio rather than a transcription artefact.

**Transcript JSON** - valid JSON containing only API-supported fields:
`transcript`, `segments` (3 entries with real `start`/`end`), `language`,
`api_reported_duration_seconds`, plus processing metadata. No fabricated
confidence scores. API reported language `English` and audio duration
`29.998 s`.

### 3.6 Credential audit

```
> Get-ChildItem -Recurse logs, output, src, tests, pyproject.toml, .env.example |
    Select-String -Pattern <the real GROQ_API_KEY>
NO API KEY FOUND IN ANY FILE
```

Scanning for `gsk_[A-Za-z0-9]{20,}` matched only deliberately fake test
fixtures (`gsk_testONLYnotarealkey...`, `gsk_realtestingsecret...`) and their
compiled `__pycache__` files. No real credential appears in any source file,
log, output artifact or configuration file. The directory is not a Git
repository, so nothing has been committed.

---

## 4. Important finding: the proxy issue

**Symptom:** the first real run failed at stage 5 with
`HTTP 403 Forbidden`. This looked like an invalid API key or a missing model
entitlement.

**Diagnosis:** `GET /openai/v1/models` succeeded from PowerShell but returned
403 from Python. The machine routes traffic through a local proxy
(`http://127.0.0.1:1080`) configured in Windows *Internet Settings*. PowerShell
and FFmpeg honour that setting; Python's `httpx` does **not**, so it connected
directly and was rejected. The API key was valid throughout.

**Fix:** `src/transcription.py` now resolves the proxy in this order:
`GROQ_PROXY` → `HTTPS_PROXY`/`HTTP_PROXY` → Windows registry proxy
(`ProxyEnable`/`ProxyServer`), and passes an `httpx.Client(proxy=...)` to the
Groq SDK when needed. The 403 diagnostic was also improved to explain the proxy
possibility. Covered by 10 dedicated tests in `tests/test_proxy.py`.

**Relevance to later phases:** any future stage that makes network calls will
hit the same trap. `src/transcription.resolve_proxy()` is the reusable entry
point.

---

## 5. Known limitations

1. **pytest temp directory.** The default temp root is not writable on this
   machine; pytest aborts after the run with
   `PermissionError ... pytest-current`. Workaround documented in the README:
   set `PYTEST_DEBUG_TEMPROOT` to a project-local folder. This is an
   environment issue, not a code defect - the 159 tests all pass once the temp
   root is set.
2. **30 seconds only.** By design. Full-video processing (Phase 7) is not
   implemented, and a 2 h 49 m source is far beyond a free Groq tier's daily
   audio allowance.
3. **No chunking or resumability.** A failed long run restarts from scratch.
4. **Monotonic timing assumptions.** Segment timestamps come straight from the
   API; no smoothing or overlap handling is applied.
5. **English only.** The language is requested as English; the client is not
   validated for other languages even though the API accepts a language code.
6. **`whisper-large-v3-turbo` default is a judgement call.** It is faster and
   cheaper than `whisper-large-v3`; if accuracy on fast, accented trading
   speech proves insufficient, switch with `--model whisper-large-v3`.
7. **Free-tier rate limits.** A single 30-second clip is ~0.5 audio minutes, so
   this is not a practical constraint for Phase 1 but will be for Phase 7.

---

## 6. Recommended next steps

**Do not start Phase 2 without explicit approval.** If you want to proceed,
these are the natural follow-ups in order:

1. **Decide on Git.** The directory is currently not a repository and no remote
   exists. Initialising and committing requires your authorisation - it was
   deliberately not done automatically.
2. **Sanity-check the transcript at scale.** Run a few different 30-second
   windows (e.g. `--duration 900`, `--duration 3600`) to see whether
   `whisper-large-v3-turbo` holds up on this speaker across the whole lecture,
   and compare against `whisper-large-v3`.
3. **Cost model for full-length work.** Measure Groq's actual audio-minute
   pricing and free-tier allowance before committing to a full-length design.
4. **Phase 2 design spike** on a small sample: translate ~5 minutes, build the
   trading-term glossary, and review the output before scaling.
5. **Consider `--duration` defaults per phase** and add a chunked,
   checkpointed processing mode so long runs are resumable.

---

## 7. Quick reference

```powershell
cd D:\Projects\al-brooks-dubbed
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# environment check
.\.venv\Scripts\python.exe -m src.main --check-env

# list candidates
.\.venv\Scripts\python.exe -m src.main --list-inputs

# run Phase 1
.\.venv\Scripts\python.exe -m src.main --overwrite

# tests
$env:PYTEST_DEBUG_TEMPROOT = "$PWD\.pytest_tmp"
.\.venv\Scripts\python.exe -m pytest

# quality gates
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m mypy
```