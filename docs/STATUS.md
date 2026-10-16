# AutoCue status (as of 2026-10-15 ~9:45 PM PT)

## What this is

Proof-stage library-prep engine for Sound & Service. Lives in this private repo. Not yet a Mac app; not yet writing cues into Serato production files.

## Ground truth (important)

Early scores compared engine/Gemini picks to Serato Markers2 on 20 tracks under Master Music. Those tags **match Mixed In Key’s energy cues**, not Cullen’s hand-set truth. That made Gemini look slightly worse than rules-only (47/107 vs 49) — that comparison is **retired**.

**Current ground truth:** Cullen’s marks on the local review page (Correct / nudge / snap). Learner: `engine/learner_marks.py` → `config/learned_profile_marks.json`. Scorer vs marks: `engine/score_vs_marks.py` / `engine/score_v5.py`.

## v5 (built 2026-10-15 evening PT)

Eight switchable post-v4 improvements in `engine/rules_v5.py` (`--off` / `--only`). Full write-up: `docs/SCORE-v5-2026-10-15.md`. Raw ablation: `config/score_v5.json`.

| # | Flag | Role |
|---|------|------|
| 1 | `loops_phrase` | Phrase-aligned clean loops |
| 2 | `exit_phrase` | EXIT/GO on 8- then 4-bar grid from START |
| 3 | `chorus_verify` | Recurrence check for C/D |
| 4 | `drop_robust` | Whole-bar drums+bass drop for EXIT |
| 5 | `tempo_check` | Drift / multi-BPM flag + bar rebuild |
| 6 | `cold_start` | Cold start vs a cappella intro |
| 7 | `consensus` | Multi-analyzer confidence adjust |
| 8 | `genre_params` | Per-bucket params; `learner_marks.py --buckets` |

**Vs Cullen’s marks (18 cues on songs 02, 14, 15, 20):** v4 **9/18**, v5_all **9/18** (tied). Same positions on every marked cue; v5 auto-sets fewer (27→19 on the 20-song set) and adds review flags. Ablations do not move mark accuracy yet — too little data.

**Default:** keep **v4** in production paths until ~**50+ stress-crate marks**, then re-score v4 vs v5 and pick. SongFormer on Mac still blocked on disk (~98% full); see `docs/SONGFORMER-ON-MAC.md`.

## Scores (20-song set, vs old MIK-aligned tags — historical only)

From `SCORE-v4-2026-10-15.md` (engine-vs-engine on MIK tags):

| variant | A | B START | C | D | G GO | H EXIT | total | Gemini $ |
|---------|---|---------|---|---|------|--------|-------|----------|
| v3 | 2/4 | 12/19 | 6/16 | 4/11 | 7/19 | 9/20 | 40/89 | 0 |
| v4 | 2/3 | 13/19 | 6/16 | 4/11 | 8/19 | 10/20 | **43/88** | 0 |
| v4+gemini_v3 | 2/5 | 11/19 | 6/16 | 5/11 | 6/19 | 7/20 | 37/90 | ~1.52 |

Gemini on the stress run is **suggestions only** when A/B/G/H confidence &lt; 0.85.

Older tables: `SCORE-v2-*.md`, `SCORE-v3-*.md`, `SCORE-gemini-v2-*.md`, `SCORE-vs-Cullen-*.md` (MIK-aligned).

## In flight

- **5-crate stress run** (~326 tracks): Phrasing Drills, Latin Fiesta, Disco Dance Party, 90s/2000s Hip Hop & R&B, Deep House. Review on Pro at localhost:8766. Need Cullen’s marks there before a real v5 decision.
- Next steps: `docs/ROADMAP.md`.

## Review UI

- 20-song set: Pro localhost:8765 (`review-ui/`, launchd on MacBook Pro).
- Stress crates: localhost:8766.
- Zoom-to-cue, beat/bar grid, snap-to-downbeat, corrections saved locally (not in git).

## Shared analysis for Booth Buddy

Draft schema: `docs/ANALYSIS-FORMAT.md`. AutoCue writes; Booth Buddy reads later. No shared runtime yet.

## Repo hygiene

No audio, stems, `out/`, venvs, `proof/src`, `paths.json`, `corrections.json`, or API keys in git. `GEMINI_API_KEY` from the environment only.
