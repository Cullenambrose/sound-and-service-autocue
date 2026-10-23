# AutoCue roadmap

Ordered for proof → prep app → Booth Buddy handoff. Dates are targets, not promises.

## v6 implementation status

- Implemented a separate v6 plan builder, synced-lyrics cache/alignment, resumable manifest-based WhisperX runner, drop/genre map selection, review export, and review-mark scorer.
- v6 remains opt-in; Gemini is disabled unless explicitly enabled and will only consider low-confidence candidate bars.
- Validation against the stress set and a v5-vs-v6 score still requires the machine-local stress manifest, cached analysis/features/stems, WhisperX input stems, and review-page `corrections.json`. None of those data files are in this repository checkout.

## 1. v5 evaluation (next)

- Collect **~50+** Cullen marks on the stress-crate review page (localhost:8766), not only the 18 cues on 4 songs.
- Re-run `engine/score_v5.py` (v4 vs v5_all + ablations). Promote v5 only if it wins on marks or clearly better review flags / auto-set precision.
- Run `learner_marks.py --buckets` once buckets have enough marks (`config/genre_params_v5.json`).
- Keep **v4 as default** until that re-score.

## 2. SongFormer on the Mac

- Blocked while the Pro is ~98% full (~20 GB free; install needs ~4.5 GB). Plan: `docs/SONGFORMER-ON-MAC.md`.
- After install: feed consensus (`rules_v5` change 7) with real SongFormer boundaries on more than the 7 box-run tracks.
- Nothing installs into system Python or touches `~/Music` / Serato without a separate OK.

## 3. Learning loop

- Review UI → local `corrections.json` (gitignored) → `learner_marks.py` / `--buckets` → `config/learned_profile_marks.json` + genre params.
- Prefer leave-one-out / held-out crate checks before trusting a new profile.
- Gemini stays **suggestion-only** on low-confidence A/B/G/H; re-score Gemini against Cullen’s marks (not Mixed In Key) after more reviews.

## 4. Serato test write (one file, with backup)

- First real write is a **single** approved track copy, never the live Master Music original.
- Steps: back up Serato library / crate data → write Markers2 on the copy → Cullen opens in Serato and confirms → only then discuss batch writes.
- Read-only hashing (`hashes_before.sha256` style) before and after.

## 5. Native Mac app (Xcode)

- Same cue logic as this repo; UI aimed at prep, not booth play.
- **First-launch orientation / calibration:** short walkthrough of A–H + loops; optional “mark a few songs your way” to seed the learner.
- **Waveform zoom** like the review page (bars, 8-bar lines, snap to downbeat, ±beat / ±bar nudges).
- Local analysis cache; optional Gemini on unsure songs with a spend cap.
- Ship after logic is proven on stress-crate marks + at least one successful Serato test write.

## 6. Booth Buddy analysis export

- Emit per-song JSON per `docs/ANALYSIS-FORMAT.md` (BPM, grid, sections, cues, energy, vocals, genre bucket, confidence).
- AutoCue / Mac prep app **writes**; Booth Buddy **reads** (smarter picks, no shared runtime).
- No stem blobs or full transcripts in the export; no Serato writes from Booth Buddy.
