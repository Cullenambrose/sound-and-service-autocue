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


## v5 (switchable improvements)

`engine/rules_v5.py` post-processes v4 (`v4_go32`); each change can be turned off with `--off a,b` or tested alone with `--only a`. Details and ablation: `docs/SCORE-v5-2026-10-15.md`.

| # | Change | What it does |
|---|--------|----------------|
| 1 | `loops_phrase` | Intro/outro loops only on phrase-aligned bars (4k from START/EXIT) + clean-beat stem check |
| 2 | `exit_phrase` | EXIT snaps to 8- then 4-bar grid from START; G = EXIT − 8 bars; flag if no nearby boundary |
| 3 | `chorus_verify` | Keep C/D only if section recurs (chroma/MFCC or lyric line); else low-conf suggestion |
| 4 | `drop_robust` | EXIT drop needs drums+bass down for a whole bar (not a one-frame dip) |
| 5 | `tempo_check` | Flag tempo drift / multi-BPM grids; rebuild bar times from detected beats when unstable |
| 6 | `cold_start` | Vocals+drums in bar 1 → deliberate cold start (A empty + flag); vocals-only → a cappella intro |
| 7 | `consensus` | Bump or cut START/EXIT confidence when allin1 / SongFormer / stems / energy agree or disagree |
| 8 | `genre_params` | Bucket (pop / hiphop_rnb / house_edm / latin / other); params in `config/genre_params_v5.json`; tune with `learner_marks.py --buckets` |

**Score vs Cullen’s marks (18 cues / 4 songs):** v4 and v5 are tied at **9/18**. v5 auto-sets fewer cues and adds review flags. **v4 stays the default** until there are ~50+ stress-crate marks, then re-score and decide. Roadmap: `docs/ROADMAP.md`.

## Status

See `docs/STATUS.md` for current scores, the v5 ablation, and the 5-crate stress run.
