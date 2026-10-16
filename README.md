# Sound & Service AutoCue

Library-prep engine that analyzes DJ tracks and suggests Serato cue points (A–H layout) plus two clean 4-bar loop ideas. It is a **prep app**, not a booth player: run it on the library before a gig, review the picks, then (later) write approved cues into Serato.

A native Mac app (Xcode) is planned once the logic is proven. Per-song analysis JSON (see `docs/ANALYSIS-FORMAT.md`) is designed so **Booth Buddy** can read the same results later without coupling the two codebases.

Private repo: `Cullenambrose/sound-and-service-autocue`. Separate from the booking site (`dj-av-booking`) and the iOS app.

## Cue layout (approved)

| Slot | Name | Rule | Auto-set? |
|------|------|------|-----------|
| A | 8BS / 4BS / 2BS / 1BS | 8 bars (32 beats) before START; step down to 4 → 2 → 1; empty only if 1 bar will not fit | yes, conf ≥ 0.85 |
| B | START | first downbeat of the vocal song proper | yes, conf ≥ 0.85 |
| C | 1st Chorus | first chorus ≥4 bars after START | suggestion only |
| D | Last Chorus | last chorus starting before EXIT | suggestion only |
| E, F | — | always empty | — |
| G | GO | 32 beats before EXIT (16 allowed) | yes, conf ≥ 0.85 |
| H | EXIT | bar where the last chorus actually ends | yes, conf ≥ 0.85 |

Saved loops (suggestions only): intro loop 4 bars from A (or first clean beats-only 4 bars before vocals); outro loop 4 bars from GO (or first clean beats-only 4 bars after EXIT). Phrase-aligned (bar 1 of phrase).

Full rules: `docs/cue-logic.md`, `docs/SPEC.md`.

## Layout

```
engine/           Python/shell tools (rules, scorers, Gemini step, learners, stress5/)
review-ui/        Local review page (index.html + serve.py)
docs/             Specs, scores, install notes, Google Serato handover
config/           Learned profiles + genre bucket params (no keys)
```

## How to run (high level)

1. Install analyzers per `docs/INSTALL.md` and `docs/SONGFORMER-ON-MAC.md` (SongFormer needs disk space on the Mac).
2. Set `GEMINI_API_KEY` in the environment when you want AI suggestions on low-confidence cues. Never commit the key.
3. Source `engine/env.sh` (adjust `PROOF` to your checkout) and run the pipeline scripts under `engine/` (e.g. feature extract → `rules_v5.py` / build → review).
4. Open the review UI:

```bash
cd review-ui
HOST=127.0.0.1 python3 serve.py   # default port 8765
```

Corrections stay local (`corrections.json` is gitignored). Feed them back through the learner scripts to tune the profile in `config/`.

## Ground truth

**Do not** treat the old Serato Markers2 on the 20-song test set as Cullen’s hand cues — those lined up with Mixed In Key energy cues. Scoring and learning use **Cullen’s review-page marks** (`corrections.json` → `learner_marks.py` → `config/learned_profile_marks.json`). See `docs/STATUS.md`.

## Status

See `docs/STATUS.md` for current scores and what is in flight (v5 Gemini-review fixes, 5-crate stress run).
