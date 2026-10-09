# Roadmap

Phase 1 and Phase 2 are complete and verified against a real lecture video.
Everything below is **future work** and is deliberately *not* implemented yet.

## Status summary

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Video extraction + English transcription | ✅ Complete |
| **Phase 2** | Transcript QA + preparation for translation | ✅ Complete |
| Phase 3 | English → Persian translation with terminology control | ⬜ Not started |
| Phase 4 | Persian text-to-speech (natural, correct pronunciation) | ⬜ Not started |
| Phase 5 | Voice similarity / speaker matching | ⬜ Not started |
| Phase 6 | Timing alignment and segment fitting | ⬜ Not started |
| Phase 7 | Optional lip synchronisation | ⬜ Not started |
| Phase 8 | Full-length dubbing and muxing | ⬜ Not started |

---

## Phase 2 — Transcript quality assurance and preparation (complete)

Delivered in v0.2.0. What exists for the next phase to build on:

- **Raw artifacts stay authoritative.** Phase 1 output is opened read-only and
  never rewritten; cleaned output lives in separate `*.en.clean.*` files.
- **18 QA checks** covering duplicates, timestamp validity, ordering, bounds,
  gaps, overlaps, coverage, transcript/segment consistency, segment length and
  terminology, each reported with severity, affected segments, time range and
  an explanation.
- **A versioned cleaned schema** (`schema_version: 2`) in which every segment
  keeps `source_segment_ids`, its audio time range, raw text, cleaned text,
  quality flags, correction status and an explicit `timestamp_confidence`.
  Unreliable boundaries are represented, never invented.
- **Cautious recovery.** A duplicated segment is only merged when a bounded
  re-transcription of the *same audio* contradicts it. Otherwise the wording is
  preserved verbatim and flagged for review.
- **A 35-term Al Brooks glossary** with empty `persian` fields ready for the
  translation phase.

Deliberate non-goals: no transcript rewriting, no summarising, no grammar
correction, no translation, no invented timestamps or confidence scores.

---

## Phase 3 — Persian translation

**Goal:** produce a faithful Persian transcript suitable for dubbing.

- English → Persian translation driven by the Phase 2 prepared segments, so
  each translated unit maps cleanly onto an audio range.
- Fill the `persian` field of `src/resources/terminology.json` with approved
  equivalents, then apply them consistently so trading vocabulary is
  translated the same way across the whole lecture.
- Keep the original English alongside the Persian for review.
- Carry `source_segment_ids` through so any translation can be traced back to
  the audio.
- Human review checkpoint: the trader should be able to correct terminology
  before any audio is generated.

**Inputs already in place:** `*.en.clean.json` (segments, timing, flags) and the
annotated terminology resource.

**Open questions**

- Which translation model (local vs. API) - a local model keeps cost at zero
  but needs the 12 GB of RAM to be used carefully.
- How to handle segments already flagged `uncertain_review_required`: skip
  them, or translate and mark them?

---

## Phase 4 — Natural Persian speech synthesis

**Goal:** turn the Persian text into audio that sounds like a person, not a
robot.

- Evaluate Persian TTS quality (voice naturalness, correct pronunciation of
  English trading terms kept in Persian text).
- Produce one audio file per segment so timing can be adjusted per segment.
- Normalise sample rate / loudness to match the original track.
- Must run on CPU within the available RAM budget, or accept a cloud TTS
  provider if quality demands it.

**Risk:** mixed Persian/English technical vocabulary is the hardest part for
Persian TTS engines and is likely to dictate the model choice.

---

## Phase 5 — Voice similarity

**Goal:** make the dubbed track sound like the original speaker.

- Extract speaker characteristics (timbre, pitch range, pace) from the source.
- Evaluate voice-conversion models that preserve the *content* of the
  synthesised Persian speech while adopting the speaker's voice.
- Quantify similarity rather than judging by ear alone.

**Open question:** whether voice conversion is legally and ethically
appropriate for the intended use; the original speaker's consent should be
confirmed before shipping anything derived from their voice.

---

## Phase 6 — Timing alignment

**Goal:** fit the Persian audio into the original video timeline.

- Use the Phase 2 segment times as the alignment skeleton.
- Adjust speaking rate per segment to match each segment's original slot.
- Handle segments that are too short for their Persian audio (speed up) and
  segments with slack (insert a natural pause).
- Detect and report segments where no acceptable fit exists, rather than
  silently drifting out of sync.
- Treat `timestamp_confidence != "reliable"` segments conservatively, since
  Phase 2 has already marked their boundaries as suspect.

---

## Phase 7 — Optional lip synchronisation

**Goal:** keep mouth movements consistent with the new audio.

- Only worth doing if the final output actually shows the speaker's face
  clearly; otherwise it is wasted effort.
- Likely a Wav2Lip-class or similar post-processing pass over the video track.

---

## Phase 8 — Full-length dubbing

**Goal:** process the entire lecture, not just 30 seconds.

- Chunk the full video into segments sized to the API upload limit and free-tier
  budget, with resumable checkpointing.
- Run Phase 2 QA per chunk, so a bad chunk is re-transcribed rather than
  silently accepted.
- Produce a final MP4 with the Persian audio track muxed in, keeping the
  original video stream.
- A full 2h50m lecture is roughly 170 audio minutes - this is far beyond a free
  tier's daily allowance and must be budgeted for or split across days.

---

## Cross-cutting concerns for future phases

- **Cost control:** every API stage multiplies the bill. Batch, cache, and make
  re-runs cheap. Phase 2 shows the pattern: the expensive verification step is
  opt-in.
- **Reviewability:** each phase keeps the previous phase's artifacts so a bad
  translation can be fixed without re-transcribing.
- **Hardware:** the target machine has 12 GB RAM and a 2 GB GPU. Anything that
  needs more must run elsewhere.
- **Reproducibility:** pin models and record the exact version in metadata, the
  way Phases 1 and 2 already record the transcription model, timestamps and the
  exact verification outcome.
- **Scale:** Phase 2's QA stage analyses a full-length 221,000-word transcript
  in about 3 seconds, so quality assurance is not a bottleneck for Phase 8.