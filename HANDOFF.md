# Handoff

**Phase:** 2 (complete) + confusable-term detection + glossary decision
**Status:** ✅ Complete and verified against the real lecture, 30-second and 10-minute samples
**Date:** 2026-10-09
**Version:** 0.3.1
**Repository:** local Git repo on `main`. A GitHub remote (`origin`) already existed and was **not** modified or pushed to by me.

---

## 0a. Glossary decision (0.3.1)

**The Persian track keeps Al Brooks terms in English.** Persian traders say
"wedge bull flag", not a literal Persian rendering. All ten core terms are
recorded with the English term as their spoken form plus
`speak_original: true`, so the decision is explicit rather than looking like a
typo in a field named `persian`.

To translate a term instead: replace `persian`, drop the flag. A test enforces
that any spoken-as value equal to the English original carries the flag.

The 25 remaining entries stay unset on purpose - Phase 2 only annotates with
them.

---

## 0b. What changed since the Phase 2 handoff

A 10-minute sample (`--duration 600`) was transcribed and analysed. It exposed
that the most damaging transcription errors were going undetected, so
confusable-term detection was added.

| Item | Result |
|---|---|
| Real errors now caught | `training range` ×6 → `trading range`; `bare bar`/`bare body` ×3 → `bear bar`/`bear body` |
| False positives on that sample | **0** |
| Findings on the 10-minute sample | 21 (2 high, 7 medium, 11 low, 1 info) |
| `segment_gap` severity | lowered `medium` → `low` (live trader pauses are normal) |
| Tests | 418 passing (55 new), no network, no charges |

### An earlier claim I withdrew

I first reported "≈42 seconds of missing content" from the 8 transcript gaps.
**That was wrong.** The speaker is trading live and pauses for several seconds
to read charts; a 1.93 words/second rate is normal for intermittent speech. Gaps
are now `low` severity and explicitly described as likely pauses. The one case
still worth a listen is the ungrammatical *"there were problems with the …
it."* at ≈84 s.

---

## 1. What was built

Phase 1 is untouched. Phase 2 adds a QA and preparation stage that runs on an
existing Phase 1 transcript, makes **no API call by default**, and never alters
the raw artifacts.

| Module | Responsibility |
|---|---|
| `src/text_cleaning.py` | Deterministic, formatting-only cleanup + similarity helpers |
| `src/qa.py` | 18 quality checks, severities, findings, duplicate groups |
| `src/transcript.py` | Domain model: raw transcript in, prepared transcript out (schema v2) |
| `src/terminology.py` | Al Brooks glossary loading and annotation |
| `src/resources/terminology.json` | 35 editable domain terms with empty `persian` fields |
| `src/verification.py` | Optional bounded re-transcription and verdict logic |
| `src/preparation.py` | Three-stage orchestration and artifact writing |
| `src/enums.py` | Python 3.10-compatible string enum |
| `src/terminology.py` | **extended** with confusable detection and curated confusions |
| `src/outputs.py` | **extended** with `PreparedPaths` and a public atomic writer |
| `src/transcription.py` | **extended** with `is_retryable_error()` |
| `src/errors.py` | **extended** with the Phase 2 exception hierarchy (exit code 7) |
| `src/cli.py` | **extended** with `--prepare-transcript` and friends |

### Files created

```
src/qa.py
src/text_cleaning.py
src/transcript.py
src/terminology.py
src/verification.py
src/preparation.py
src/enums.py
src/resources/terminology.json
tests/test_qa.py
tests/test_text_cleaning.py
tests/test_terminology.py
tests/test_verification.py
tests/test_preparation.py
tests/test_prepare_cli.py
tests/test_confusables.py
```

### Files modified

```
src/cli.py            src/outputs.py         src/transcription.py
src/errors.py         tests/conftest.py
README.md             README.fa.md           ROADMAP.md
CHANGELOG.md          HANDOFF.md
```

No Phase 1 behaviour, flag, output name or exit code was changed.

---

## 2. The central design rule

**A suspect transcript is never silently changed.**

The raw `*.en.json` and `*.en.txt` are opened read-only. Cleaning writes
`*.en.clean.*`. A duplicated segment is merged **only** when a fresh
transcription of the *same audio* positively contradicts the duplication.
Otherwise the wording is preserved verbatim and the segments are marked
`uncertain_review_required`.

---

## 3. Commands run and actual results

### 3.1 Automated tests

```
> $env:PYTEST_DEBUG_TEMPROOT = "D:\Projects\al-brooks-dubbed\.pytest_tmp"
> .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider

tests\test_cli.py ...............
tests\test_config.py ...............
tests\test_discovery.py ...............
tests\test_media.py ...............
tests\test_outputs.py ..........................
tests\test_pipeline.py ...............
tests\test_preparation.py ..................................
tests\test_prepare_cli.py ..................
tests\test_proxy.py ..........
tests\test_qa.py .........................................................
tests\test_terminology.py ...............................
tests\test_text_cleaning.py .....................................
tests\test_transcription.py ...........................
tests\test_verification.py .......................

============================ 418 passed in 26.47s =============================
```

**Result:** **418 passed, 0 failed, 0 skipped, 0 errors** (26.47 s).
159 are the Phase 1 suite, 204 the original Phase 2 cases, and **55 cover
confusable detection**.

No test calls the Groq API. The transcription client is mocked, and the two
verification integration tests inject an offline client double that raises
locally, so the suite performs **no network requests** and cannot incur charges.

### 3.2 Lint, format and types

```
> .\.venv\Scripts\python.exe -m ruff check src tests
All checks passed!

> .\.venv\Scripts\python.exe -m ruff format --check src tests
34 files already formatted

> .\.venv\Scripts\python.exe -m mypy
Success: no issues found in 34 source files
```

### 3.3 Real run on the genuine sample (no API calls)

```
> .\.venv\Scripts\python.exe -m src.main --prepare-transcript `
      output\BTR20140930-9439edit_test_30s.en.json --overwrite
```

```
QA findings  : 4 (max severity: high)
  [high    ] duplicate_segment_duration_mismatch  Duplicated text spans very different durations (7.00s vs 16.00s)
  [high    ] exact_duplicate_segment             Identical text in segments 0, 1
  [medium  ] segment_suspiciously_long           Segment 1 lasts 16.00s, far longer than the 7.00s median
  [info    ] terminology_detected                3 Al Brooks term(s) detected

Phase 2 preparation complete - STATUS: NEEDS_REVIEW
  Segments prepared  : 3
  Verification       : not_performed
```

**Result:** exit code `0`. The known duplicate from the brief is flagged
correctly, and the wording is preserved.

### 3.4 Real verification against the Groq API (opt-in, one call)

```
> .\.venv\Scripts\python.exe -m src.main --prepare-transcript `
      output\BTR20140930-9439edit_test_30s.en.json --verify-transcript --overwrite
```

```
Verifying 1 duplicate group(s) against verify.flac (max 1 attempt(s))
Using HTTP proxy for the Groq API: http://127.0.0.1:1080
Transcription received: 236 character(s), 3 segment(s), language=English
```

**Result:** `duplicate_confirmed`.

The audio was located automatically (an audio export beside the artifact was
not present, so the 30 s interval was re-extracted from the extracted MP4 with
FFmpeg). The independent transcription returned **the same duplicated wording**
(2 occurrences in both the original and the alternative), so the repetition is
present in the audio. **Nothing was removed** and the status remained
`NEEDS_REVIEW`.

Verification evidence recorded in the artifacts:

```json
{
  "performed": true,
  "status": "duplicate_confirmed",
  "method": "grok_retranscription",
  "attempts": 1,
  "supports_correction": false,
  "evidence": {
    "groups": [{
      "segment_ids": ["0", "1"],
      "occurrences_in_original": 2,
      "occurrences_in_alternative": 2,
      "alternative_has_duplicate_segments": true,
      "verdict": "present_in_alternative"
    }],
    "original_characters": 232,
    "alternative_characters": 232
  }
}
```

### 3.5 Raw artifact integrity

```
> (Get-FileHash output\BTR20140930-9439edit_test_30s.en.json).Hash
8225ECE1EE86F3EBDC8E684B81390919099BF353E405E1C2C8598958E93A6312
```

Identical to the value recorded at the end of Phase 1, before any Phase 2 code
existed. A test (`test_raw_files_are_never_modified`) asserts this by hash, and
`test_duplicate_is_preserved_without_verification` asserts the duplicated
sentence still appears twice in the cleaned text.

### 3.6 Performance

| Input | Analysis time |
|---|---|
| 30-second sample (45 words, 3 segments) | < 0.05 s |
| 10-minute sample (1,156 words, 88 segments) | < 0.1 s |
| Full lecture simulation (221,000 words, 400 segments) | **6.2 s** |

The full-lecture figure was 148 s before optimisation. A regression test
(`test_full_length_transcript_is_analysed_quickly`) fails if it exceeds 30 s.
Confusable detection is indexed on the anchor's second word, so the sweep costs
one dictionary lookup per token.

### 3.7 Credential audit

No new secret handling was introduced. The API key is still read only from
`GROQ_API_KEY`, still redacted by the logging filter, and `--prepare-transcript`
works with no key at all (tested). A scan of `src`, `tests`, `output`, `logs`
and the documentation files finds no real credential.

---

## 4. New outputs

For `lecture_test_30s.en.json`:

| File | Purpose |
|---|---|
| `output/lecture_test_30s.en.clean.txt` | Cleaned, translation-ready English |
| `output/lecture_test_30s.en.clean.json` | Prepared segments, `schema_version: 2` |
| `output/lecture_test_30s.qa.json` | Structured findings, `schema_version: 1` |

The `cleaning` block in the prepared JSON records proof of conservatism:

```json
{
  "rewrote_words": false,
  "grammar_corrected": false,
  "translated": false,
  "removed_content": false,
  "raw_word_count": 45,
  "cleaned_word_count": 45,
  "unexpected_new_words": []
}
```

---

## 5. Bugs found and fixed during Phase 2

| Bug | Impact | Fix |
|---|---|---|
| `_open_brackets` dropped the bracket instead of the following space | `( spaced )` became `spaced)` | Rewrote the loop to keep the bracket |
| `_check_coverage` returned early for single-segment transcripts | Bounds and completeness checks silently skipped | Split into `_check_adjacent_pairs`, `_check_bounds`, `_check_completeness` |
| Out-of-order timestamps were never detected | Specified check missing | Added `segment_out_of_order` |
| `Terminology.find` used an O(n²) overlap check | 148 s on a full lecture | Sorted sweep with a watermark |
| Repeated-phrase detection scanned all n-gram sizes | Quadratic in transcript length | Seed-and-extend index |
| `similarity()` was unbounded on long text | Potential multi-minute stall | Length prefilter + bounded prefix |
| Verification retried permanent errors | An invalid key was retried up to `max_attempts` | Added `is_retryable_error()` and bail out |
| Long segment heuristic too lax (2.5× median) | The known 16 s segment was not flagged | Multiplier 2.0, floor 10 s |
| Repeated phrases reported 6 times for one repetition | Noise | Span-overlap clustering; the known example now yields 0 extra findings because the repetition is contiguous and already reported as a high-severity duplicate |
| `damerau_levenshtein` early exit returned distances beyond the limit | 196 false positives on the 10-minute sample | Rewrote the function to return the exact distance and let the caller threshold it; added tests against known values |
| `bar` was in the confusable stoplist | The "bear bar" anchor was silently dropped | Stoplist now covers only ordinary English; domain words are excluded by a vocabulary check instead |
| Confusion `occurrences` counted de-duplicated entries | Reported 1 instead of 6 | Count occurrences directly from the token list |

---

## 5b. Design notes worth keeping

**Why confusable detection is anchored.** A single-word edit-distance scan
cannot work here: the lecture is full of correct English that looks close to
domain vocabulary ("the bulls", "tried to reverse down", "near the top",
"more"). Requiring the *neighbouring* word to match a domain term exactly
removes all of that noise.

**Why the generic distance is one edit, not two.** At distance two, `clear
breakout` is structurally identical to the real `bare bar` mistake - a
four-letter non-domain word two edits from `bear`. No heuristic separates them.
Distance-two cases are therefore curated data (`known_confusions`), decided by
a human, rather than guessed.

**Why the duplicate is still not auto-resolved.** Three independent
transcriptions (30 s, 30 s verification, 600 s) all repeat the opening
sentence. Two-way agreement is the strongest evidence available without a
human, and it is still inference. The tool records the agreement and leaves the
decision to you.

---

## 6. Known limitations

1. **pytest temp directory** — unchanged from Phase 1; set
   `PYTEST_DEBUG_TEMPROOT` to a project-local folder.
2. **Verification needs audio** — without `--audio`, a sibling audio export or
   the source video, verification cannot run. The command says exactly what is
   missing. This is correct: it will not guess.
3. **Similarity is approximate on very long text** — inputs beyond 20,000
   characters are compared on a truncated prefix after a cheap length check.
   This keeps the QA stage usable at full-lecture scale; it slightly weakens
   detection of a mismatch that only appears late in a very long transcript.
4. **Terminology matching is literal** — no stemming or lemmatisation, so
   `bull` matches `bulls` via the alias list but an unlisted inflection is
   missed.
5. **No Persian equivalents yet** — all 35 `persian` fields are `null` by
   design; filling them is Phase 3 work.
6. **Timestamp heuristics** — the "unusually long" and "incomplete coverage"
   thresholds are tuned for this lecture style (15 fps lecture video). A
   different speaker may need different `QaThresholds`.
7. **The duplicate is still unresolved by machine** — two transcriptions agree,
   but a human should still listen and decide. That is the intended workflow,
   not a defect.
8. **Confusable detection is calibration-limited.** It catches what is curated
   plus single-edit slips. A two-edit error absent from `known_confusions` will
   be missed, because the generic check cannot be made precise enough to catch
   it. Adding entries to that list is the intended maintenance path.
9. **Numbers are not validated.** The 10-minute sample contains `19.20`
   (probably 1920) and `double bottom 69`. Price levels matter in a trading
   lecture and nothing currently flags them. Worth a small Phase 2.1 addition.

---

## 7. Recommended next steps

**Do not start Phase 3 without explicit approval.**

1. **Listen to 0–23 s of the extracted video** and confirm whether Al Brooks
   really says the opening sentence twice. Three independent transcriptions
   agree that he does; the tooling deliberately still stops short of deciding.
2. **Listen to 279 s and 318 s of the 10-minute sample**, where `training range`
   and `bare bar` / `bare body` were flagged. Confirming these turns the
   curated entries into verified knowledge, and tells you whether more entries
   are needed for this speaker.
3. **Check the one ungrammatical spot** at ≈84 s ("there were problems with the
   … it.") — the only gap that still looks like lost speech rather than a pause.
4. **Fill in the remaining 25 glossary entries** - optional, and they will be
   English too unless you say otherwise.
5. **Decide the translation approach** (local model vs. API) and estimate cost
   for ~170 audio minutes before building anything.
6. **Decide how to handle `uncertain_review_required` segments in Phase 3** —
   skip them, or translate and mark them?
7. **Consider a small number-validation check** before Phase 3, since price
   levels are load-bearing in this material.

---

## 8. Quick reference

```powershell
cd D:\Projects\al-brooks-dubbed
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# phase 1
.\.venv\Scripts\python.exe -m src.main --check-env
.\.venv\Scripts\python.exe -m src.main --overwrite

# phase 2 (no API calls)
.\.venv\Scripts\python.exe -m src.main --prepare-transcript output\BTR20140930-9439edit_test_30s.en.json

# phase 2 with API verification
.\.venv\Scripts\python.exe -m src.main --prepare-transcript output\BTR20140930-9439edit_test_30s.en.json --verify-transcript --overwrite

# quality gates
$env:PYTEST_DEBUG_TEMPROOT = "$PWD\.pytest_tmp"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m mypy
```
