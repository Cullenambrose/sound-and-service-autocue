# Auto-cue engine vs Cullen's hand-set cues (Oct 15, 2026)
Truth: Serato Markers2 that Cullen set Oct 14 (12:19–5:07 PM PT) on the 20 ORIGINALS in the Master Music library (Pro). Read-only copies in truth/ (chmod a-w), parsed to truth_tags.json. Scorer: tools/score_vs_truth.py -> score_rows.json.
His slot map differs from SPEC A–H; slots vary per track. Scored by meaning: 16BS(pre-start, 16 beats before START), START, 1st chorus, last chorus, GO, EXIT.
allin1 (all 20): START 47% beat / 52% bar; EXIT 35%/45%; 1st chorus 18%/18%; last chorus 18%/18%; A-vs-16BS 0%; H-vs-GO 0%. Overall 20% beat / 23% bar.
SongFormer (7 tracks): START 71/85%, EXIT 42%, last chorus 50%, 1st chorus 16%. Head-to-head on same 7 tracks: tie, 11/36 beat, 12/36 bar.
Rules learned: 16BS = START − 16 beats (7/7 when START right). GO = EXIT − 16 beats (6/6 when EXIT right). Engine's 16-bar H and intro-start A are the wrong rules.
Confidence (allin1): ≥0.8 → ~50% within a beat; <0.7 → <10%.

---

**Erratum (2026-10-15 evening PT):** The Serato Markers2 used as “truth” in this file align with Mixed In Key energy cues, not Cullen’s hand-set cues. Do not use this score as the product metric. Prefer review-page marks and `STATUS.md`.
