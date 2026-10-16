# AutoCue status (as of 2026-10-15 PT)

## What this is

Proof-stage library-prep engine for Sound & Service. Lives in this private repo. Not yet a Mac app; not yet writing cues into Serato production files.

## Ground truth (important)

Early scores compared engine/Gemini picks to Serato Markers2 on 20 tracks under Master Music. Those tags **match Mixed In Key’s energy cues**, not Cullen’s hand-set truth. That made Gemini look slightly worse than rules-only (47/107 vs 49) — that comparison is **retired**.

**Current ground truth:** Cullen’s marks on the local review page (Correct / nudge / snap). Learner: `engine/learner_marks.py` → `config/learned_profile_marks.json`. Scorer vs marks: `engine/score_vs_marks.py`.

## Scores (20-song set, vs old MIK-aligned tags — historical only)

From `SCORE-v4-2026-10-15.md` (still useful as a relative engine-vs-engine table):

| variant | A | B START | C | D | G GO | H EXIT | total | Gemini $ |
|---------|---|---------|---|---|------|--------|-------|----------|
| v3 | 2/4 | 12/19 | 6/16 | 4/11 | 7/19 | 9/20 | 40/89 | 0 |
| v4 | 2/3 | 13/19 | 6/16 | 4/11 | 8/19 | 10/20 | **43/88** | 0 |
| v4+gemini_v3 | 2/5 | 11/19 | 6/16 | 5/11 | 6/19 | 7/20 | 37/90 | ~1.52 |

v4 improved START/EXIT/GO vs v3 without Gemini. Gemini-as-auto-set hurt when scored against MIK tags; on the stress run Gemini is **suggestions only** when A/B/G/H confidence &lt; 0.85.

Older tables: `SCORE-v2-*.md`, `SCORE-v3-*.md`, `SCORE-gemini-v2-*.md`, `SCORE-vs-Cullen-*.md` (filename is historical; content describes the MIK-aligned tags).

## In flight (2026-10-15 evening PT)

- **5-crate stress run** (~326 tracks): Phrasing Drills, Latin Fiesta, Disco Dance Party, 90s/2000s Hip Hop & R&B, Deep House. Review page on Pro at localhost:8766. Gemini only on unsure songs; spend capped.
- **v5** (Gemini review of the algo): phrase-aligned loops; GO/EXIT on phrase grid; chorus recurrence check; softer drop hold; tempo-drift flag; cold-start flag; multi-model consensus hooks; genre-bucket params in `config/genre_params_v5.json`. SongFormer install on Mac deferred (disk ~98% full).

## Review UI

- 20-song set: Pro localhost:8765 (`review-ui/`, launchd on MacBook Pro).
- Stress crates: localhost:8766.
- Zoom-to-cue, beat/bar grid, snap-to-downbeat, corrections saved locally (not in git).

## Shared analysis for Booth Buddy

Draft schema: `docs/ANALYSIS-FORMAT.md`. AutoCue writes; Booth Buddy reads later. No shared runtime yet.

## Repo hygiene

No audio, stems, `out/`, venvs, `proof/src`, `paths.json`, `corrections.json`, or API keys in git. `GEMINI_API_KEY` from the environment only.
