# Cue logic v2 (Cullen's layout, approved Oct 15, 2026)

| Slot | Name | Rule | Auto-set? |
|---|---|---|---|
| A | `8BS` / `4BS` / `2BS` / `1BS` ("bars before start") | 8 bars (32 beats) before START; if that is before the track or its first downbeat, 4 bars, then 2, then 1; empty + flag only if not even 1 bar fits | yes, conf ≥ 0.85 |
| B | START | first downbeat of the vocal song proper | yes, conf ≥ 0.85 |
| C | 1st Chorus | first chorus section starting ≥4 bars AFTER START | never (suggestion) |
| D | Last Chorus | last chorus section starting before EXIT | never (suggestion) |
| E, F | — | always empty (no hot-cue loops / scratch cues) | — |
| G | GO | `exit_lead_beats` before EXIT (default **32**, 16 allowed) | yes, conf ≥ 0.85 |
| H | EXIT | bar where the last chorus actually ends (see below) | yes, conf ≥ 0.85 |

Saved loops (suggestions only, never auto-set, always bar-snapped): **intro loop** 4 bars (16 beats) from A, or the first clean beats-only 4 bars before the vocals; **outro loop** 4 bars from GO if beats-only there, else the first clean beats-only 4 bars after EXIT. "Clean" = drums present (>40% of frames) and vocals near-silent (<15%) on the Demucs stems.

Scoring note: Cullen's 20 hand-set truth cues use 16 beats for A and GO. A is scored only where the engine picked 4BS; G is scored with a 16-beat run; the 32-beat GO run is only checked for landing on a downbeat inside the song.

## START
- Engine's first non-intro section boundary, snapped to the Serato bar grid.
- No-intro fix: if the engine puts START within 4 bars of the top (it labeled the intro "chorus"/"verse" or found no intro), use bar 8 (DJ-edit intro) and cap confidence at 0.7 (review).

## 1st chorus fix
v1 put the first chorus on the START beat when the first section was labeled chorus. v2 requires the chorus to start ≥ 4 bars after START; if the engine merged all choruses into one block, C/D are left empty.

## EXIT
Start from the bar after the last chorus block (engine boundary). Using bar loudness (features rms):
- energy drops ≥ 2.5 dB on that bar → keep it;
- else it drops ≥ 2.5 dB on the NEXT bar → EXIT = boundary + 1 bar (the chorus tail rings one more bar; this was the "one bar early" pattern);
- else keep the boundary, confidence −0.1.

## Lyrics layer (box-only)
WhisperX `small` (int8, CPU) on the Demucs vocal stem gives word times; LRCLIB plain lyrics (text lookup by artist/title, no audio sent) mark repeated hook lines.
- START: the first sung word (start of a run of ≥4 words) **confirms** the engine's START when it falls in that bar or the pickup bar just before (+0.1 conf). The pickup rule ("word in 2nd half of a bar → next downbeat") is kept as option `pickup_rule_moves_start` but is OFF: on 07, 13 and 16 Cullen puts START on the bar that contains the pickup word, so moving it made START one bar late.
- Chorus: the most-repeated 4-word phrase (boosted when it is a repeated LRCLIB line) gives hook times; used for C/D only when the section labels give no chorus after START.
- EXIT: a vocal-stem drop on the same bar confirms EXIT (+0.1); it never moves EXIT (moving it hurt song 14).

## Gemini step (optional, Cullen OK'd Oct 15)
Tiebreaker for B/H below 0.85 and for C/D: one ≤20 s mono clip around the candidates + timed lyrics; model picks a candidate bar or none. Gemini alone never raises a cue to auto-set (capped at 0.84). QC: the cue plan as text, model flags anything illogical. Full songs are never sent. Results in `proof/real/gemini/`.

Profile: `{"a_rule": "8BS > 4BS > 2BS > 1BS", "exit_lead_beats": 32, "auto_set_min_conf": 0.85, "exit_drop_db": 2.5, "min_start_bar": 8, "pickup_rule_moves_start": false}`. Code: `proof/real/tools/rules_v2.py`; tests `test_rules_v2.py`; scorer `score_v2.py`.
