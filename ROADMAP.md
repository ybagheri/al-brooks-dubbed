# Roadmap

Phase 1 is complete and verified against a real lecture video. Everything below
is **future work** and is deliberately *not* implemented yet.

## Status summary

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Video extraction + English transcription | ✅ Complete |
| Phase 2 | English → Persian translation with terminology control | ⬜ Not started |
| Phase 3 | Persian text-to-speech (natural, correct pronunciation) | ⬜ Not started |
| Phase 4 | Voice similarity / speaker matching | ⬜ Not started |
| Phase 5 | Timing alignment and segment fitting | ⬜ Not started |
| Phase 6 | Optional lip synchronisation | ⬜ Not started |
| Phase 7 | Full-length dubbing and muxing | ⬜ Not started |

---

## Phase 2 — Persian translation

**Goal:** produce a faithful Persian transcript suitable for dubbing.

- English → Persian translation with a terminology glossary for trading
  vocabulary (e.g. `bar`, `candle`, `wedge`, `gap`, `trend line`) so that
  wording stays consistent across the whole lecture.
- Preserve sentence boundaries from the Phase 1 segment timestamps so
  translation units map cleanly onto audio segments.
- Keep the original English alongside the Persian for review.
- Human review checkpoint: the trader should be able to correct the
  terminology before any audio is generated.

**Open questions**

- Which translation model (local vs. API) - a local model keeps cost at zero
  but needs the 12 GB of RAM to be used carefully.
- Glossary source of truth and how it is version-controlled.

---

## Phase 3 — Natural Persian speech synthesis

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

## Phase 4 — Voice similarity

**Goal:** make the dubbed track sound like the original speaker.

- Extract speaker characteristics (timbre, pitch range, pace) from the source.
- Evaluate voice-conversion models that preserve the *content* of the
  synthesised Persian speech while adopting the speaker's voice.
- Quantify similarity rather than judging by ear alone.

**Open question:** whether voice conversion is legally and ethically
appropriate for the intended use; the original speaker's consent should be
confirmed before shipping anything derived from their voice.

---

## Phase 5 — Timing alignment

**Goal:** fit the Persian audio into the original video timeline.

- Use Phase 1 segment timestamps as the alignment skeleton.
- Adjust speaking rate per segment to match each segment's original slot.
- Handle segments that are too short for their Persian audio (speed up) and
  segments with slack (insert a natural pause).
- Detect and report segments where no acceptable fit exists, rather than
  silently drifting out of sync.

---

## Phase 6 — Optional lip synchronisation

**Goal:** keep mouth movements consistent with the new audio.

- Only worth doing if the final output actually shows the speaker's face
  clearly; otherwise it is wasted effort.
- Likely a Wav2Lip-class or similar post-processing pass over the video track.

---

## Phase 7 — Full-length dubbing

**Goal:** process the entire lecture, not just 30 seconds.

- Chunk the full video into segments sized to the API upload limit and free-tier
  budget, with resumable checkpointing.
- Produce a final MP4 with the Persian audio track muxed in, keeping the
  original video stream.
- A full 2h50m lecture is roughly 170 audio minutes - this is far beyond a free
  tier's daily allowance and must be budgeted for or split across days.

---

## Cross-cutting concerns for future phases

- **Cost control:** every API stage multiplies the bill. Batch, cache, and make
  re-runs cheap.
- **Reviewability:** each phase should keep the previous phase's artifacts so a
  bad translation can be fixed without re-transcribing.
- **Hardware:** the target machine has 12 GB RAM and a 2 GB GPU. Anything that
  needs more must run elsewhere.
- **Reproducibility:** pin models and record the exact version in metadata,
  the way Phase 1 already records the transcription model and timestamp.