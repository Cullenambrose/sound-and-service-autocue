# PM BOT READ ME

## October 28 follow-up: whole-window evidence and outro-derived intro Cue Loops

The user clarified the no-clean-intro fallback: START on the first playable
BeatGrid downbeat, with a genuine Serato 5 **Cue Loop** on the preceding pad,
using a later outro passage. Pad order is intentionally not time order.

Implemented groundwork:

- Shared complete-coverage envelope validation, a capped adaptive vocal
  threshold, and whole-window activity checks. Even one active coarse vocal frame
  prevents a window from being marked a candidate; no averaging away brief returns.
- Intro assessments prefer configured 8/4/2/1-bar leads. Candidates need no
  detected vocal activity and drum activity in every bar. Missing, truncated,
  negative/nonfinite or invalid stems are unverified, never assumed clean.
- If no supported intro fits, search an outro region for a same-track 8/4/2/1-bar
  loop proposal. Save the proposed first-downbeat START and intended A/B slots.
  Prefix activity counts avoid rescanning the entire track for every window.
- A contaminated/unverified structural Intro is flagged and confidence-capped.
  Evidence is invalidated on map rebuilds or manual position edits.
- The new OUT path no longer calls missing stems a drum outro and no longer
  dilutes a tiny late vocal return into an eight-bar average. Missing drum
  evidence is explicitly `unverified_end`.
- Chorus candidates without a clear 4-bar energy drop are now described as
  unverified, not confirmed.
- Found and fixed another unsnapped-preview gap: the library API had omitted
  `time_s`/`unsnapped`, so the prior browser helper could not use them. API tests
  now assert both reach the browser.
- Added a synthetic-only fixture generator and read-only GEOB snapshot utility;
  exercised actual MP3 generation/capture in a temporary directory, verifying
  unchanged MP3 bytes and audio identity.

**Not applied / blocked:** Serato officially documents Cue Loop behavior, but the
new persisted cue-to-loop linkage is not established by the public legacy tag
format. No reserved-byte guesses or unrelated LOOP entries were written.
`mix_window_evidence.intro_loop_fallback` explicitly records `applied: false`
and `needs_serato_fixture` when a candidate exists. Current START/A positions
are unchanged until the genuine Cue Loop writer is verified. This is not a
completed fallback implementation or real-track musical-accuracy validation.

The review UI exposes the proposal and blocker. Detailed synthetic capture steps
and official sources: [SERATO-CUE-LOOP-QA](./SERATO-CUE-LOOP-QA.md).
Chronological cue sorting must be disabled to preserve A-before-B pad order.

Validation: 283 engine tests, 41 web tests, 15 browser tests passed; JavaScript
syntax and diff checks passed. Synthetic fixture generation/capture kept MP3
bytes unchanged. No real library audio or Track #1 was read/written.

## October 28, 2026: phrasing failure audit and v6.2 arithmetic update

**Release status: NOT performance-ready.** Crate listening reports incorrect
phrasing. Passing synthetic tests is not real-track accuracy certification.
Track #1, Happy, remains read-only. This audit did not read or write any real
MP3 content, reprocess the music library, or change the saved library databases.

### Frozen baseline

- Branch: `v6`.
- Pre-update commit: `85acd90a18e3c148357820b0d586a1697d093eab`.
- Annotated tag: `v6.1.0-phrasing-baseline`, pushed before editing.
- The tag explicitly records the failed crate-listening status.
- New plans identify this arithmetic/label revision with `phrasing_version: "v6.2"`.
  `version: "v7"` remains the existing planner family and
  `cue_spec: "v6.1-pop-vocal"` remains the pad-layout identifier. These are different
  fields, not interchangeable evidence of which code produced a saved plan.
- Update tag: `v6.2.0-phrasing-review.1` (review prerelease, not a production approval).

## 1. The actual current rules

All current timing assumes **4 beats per bar** (4/4).

| Surface | Preference / meaning |
|---|---|
| Intro lead | Try 32, 16, 8, 4 beats = 8, 4, 2, 1 bars before START's structural anchor. This is a lead-in, not necessarily the entire opening. |
| START | Exact unsnapped vocal onset when available, with a separate nearest-bar anchor. Old cached/retained START can bypass onset detection. |
| GO before first chorus exit | Prefer 8 bars / 32 beats; in v6.2 shorten to an allowed 4/2/1-bar window when track/START bounds prevent the longer lead. |
| Mix before first chorus exit | Prefer 4 bars / 16 beats; shorten to allowed 2/1-bar windows if necessary. |
| GO before OUT | Prefer 8 bars / 32 beats; shorten to allowed 4/2/1-bar windows when track bounds require it. |
| Legacy configurable DROP / VOCAL | Still supported. Some old EXIT/DROP offsets intentionally count 16/32 **bars**, not beats. Do not equate these with the newer 32-beat mixing windows. |

Intro and GO are selected **independently**. They are not a paired two-track mixing
planner. The user's clarified policy allows shorter windows, but requires truthful
labels. Matching two songs' clean available windows is still a separate workflow.

## 2. Measured local evidence

Read-only inspection of stored JSON in three Air SQLite databases:

| Database | Plans | Measured intervals |
|---|---:|---|
| `web-data/library.sqlite3` | 85 | Every G named `GO -32` is **32 bars / 128 beats** before H, not 8 bars / 32 beats. |
| Local `review-web/library.sqlite3` | 24 populated of 40 tracks | G `GO-32`: 23 plans at **8 bars / 32 beats** before H. One G `GO-16`: **4 bars / 16 beats**. |
| Local `rasputin-comparison-web/library.sqlite3` | 1 | G `GO-32`: **8 bars / 32 beats** before H. |

The 85 old plans' Intro-to-START intervals:

| Label | Count | Actual bars | Actual beats |
|---|---:|---:|---:|
| `INTRO -8BS` | 50 | 8 | 32 |
| `INTRO -4BS` | 14 | 4 | 16 |
| `INTRO -16BS` | 14 | 16 | 64 |
| `INTRO -12BS` | 7 | 12 | 48 |

Those old Intro labels agree with **bars**, while GO uses a 32-bar offset. Their
stored snapped timestamps match their own grid to within 0.5 ms. That means this
particular discrepancy is not explained by timestamp rounding: the underlying
stored bar offsets and historical meanings differ.

No human review flags were set in these databases. No populated local plan had
`cue_spec: "v6.1-pop-vocal"` at audit time. These records are **not proven to be
the user's latest Pro crate run**, nor is a populated plan proof that real audio
was successfully analyzed. The exact recent test set/build remains unidentified.

## 3. Confirmed defects and the first update

| Priority | Finding | v6.2 action |
|---|---|---|
| Blocker | Nearly identical numeric labels have incompatible historical units: old `GO -32` = 32 bars, newer `GO-32` = 32 beats. Updating code does not rebuild saved cues or tags already written in Serato. | Added offline stored-plan audit. Unmapped legacy `GO -N` plans are rejected by export preflight; reanalysis and a new review are required. No silent cue relocation. |
| High | v6.1 Intro label put a bar count into `BTS`, which reads like beats: `Intro - 8BTS` described 8 bars, not 8 beats. | New Pop/Vocal labels state both units: `Intro 8 bars (32 beats)`. Exact known v6.1 templates migrate during settings normalization, preserving custom names/colors. |
| High | Hard-coded `GO-8Exit`, `Mix-4exit`, `GO-8OUT` did not express units and were not general labels for shorter/custom windows. The new 8/4-bar countdowns lacked 4/2/1 fallback handling. | Dynamic explicit-unit labels and allowed descending preferences; countdown bounds include START for the new chorus leads. Empty when no allowed window fits. |
| High | Manual review moved positions without refreshing countdown labels or offsets; automatic guards did not run on review saves. | Review saves relabel mapped countdowns from actual visible target positions, update resolved offsets, and reject invalid countdowns. Do not silently move other manually reviewed cue positions. Explicit custom renames remain custom. |
| High | Automatic label checking and the label scorer compared a cue name to its copied resolved name, so two equally wrong strings could pass. | Independently render dynamic/countdown labels from actual target distance. Recheck mapped arithmetic on automatic construction, review save, label scoring, web export preflight and single-track pad generation. |
| High | Waveform markers, hit-testing, preview and cue centering used START's bar even when Serato export used its exact unsnapped vocal timestamp. | Shared browser cue-time helper retains the unsnapped timestamp while its bar is unchanged; moved cues snap according to the existing editor behavior. |
| High | Human scoring answers discarded unsnapped timestamps. A correct pickup could be scored against its structural downbeat instead. Corrections also logged the downbeat rather than actual START time. | Review answers, partial-answer merges, summaries and correction times preserve the unsnapped timestamp. |

The export guard does not interpret arbitrary user-written prose as a numeric
contract. It also cannot prove that a grid or event is musically correct.

## 4. Remaining musical/algorithm gaps: do not call these fixed

### A. Grid correctness is upstream of every bar count

Sources: [serato_grid.py](../engine/serato_grid.py),
[timing.py](../engine/timing.py), [analyze.py](../engine/analyze.py).

- Valid constant-tempo Serato grids are authoritative, even if detected tempo
  disagrees. This preserves Serato counts but does not certify the imported grid.
  Half/double-time Serato BPM will produce half/double musical bar lengths.
- Missing/unsupported/malformed tags fall back to detected grids with diagnostics.
  Import supports MP3/M4A, not every format supported by the writer.
- Variable-tempo grids are not represented piecewise. Multi-marker grids within
  the 0.5% tempo-spread tolerance are flattened; larger changes use detection.
  Even a small allowed difference can accumulate meaningful late-track error.
- Detected grids are a single robust line with median residual checking. This
  does not measure local late-track drift, half/double tempo or actual meter.
- BPM outside 60–220 is rejected by imported-grid adoption. There is no 3/4,
  changing-meter or reliable phrase-phase model.
- Beat detection and fitting run before grid reconciliation. A valid imported
  grid does not currently rescue a failure earlier in that pipeline.

### B. A vocal timestamp is not necessarily the song's lead-vocal START

Sources: [pop_cues.py](../engine/pop_cues.py),
[rules_v6.py](../engine/rules_v6.py).

- `first_sung_word` checks stem activity, not singing-versus-speech or lead
  versus backing vocals. DJ tags, spoken intros and samples can become START.
- Retained in-bounds START skips the new onset path. Inspect retained/cached
  source provenance before blaming the fine-resolution onset detector.
- Anchor = nearest bar to the first accepted word. This is not a learned
  structural phrase boundary.
- A vocal pickup is intentionally unsnapped. For example, Happy's 23.094 s
  onset precedes the Bar 8 downbeat. An 8-bar label counts to the **structural
  anchor**, not 8 fully vocal-free bars ending at that exact onset.
- Intro selection currently checks available bars, not a complete clean-window
  scan. It cannot certify "no speech/vocals anywhere in these 8 bars."
- The old claim "Intro at Bar 0" was too broad: START at Bar 32 yields Intro
  at Bar 24; short/nonstandard entries yield other positions.

### C. Chorus EXIT may be on the wrong phrase, even with exact countdown math

Sources: [synced_lyrics.py](../engine/synced_lyrics.py),
[rules_v6.py](../engine/rules_v6.py), [pop_cues.py](../engine/pop_cues.py).

- Repeated lyric clusters are heuristic chorus candidates; repeating verses,
  hooks, ad-libs and edit rearrangements can be mistaken for choruses.
- The last word's end is not necessarily the musical chorus end.
- Lyric boundaries are rounded to bars, then snapped to 4-bar phases relative
  to START's inferred anchor. A wrong START phase biases later sections.
- Energy refinement only compares the candidate and +/-4 bars. It does not
  search the full song for the correct chorus ending.
- If energy exists but no meaningful drop is found, the candidate can still
  be described as "confirmed." That evidence wording overstates certainty.
- Audio-section fallback boundaries are rounded to bars, not consistently
  phrase-snapped. Cached section `j0/j1` values take precedence over timestamps
  and may belong to a different historical grid.
- Legacy fallback-to-v5 EXIT can populate old layouts without independent
  current chorus evidence; custom maps and pad meanings must remain visible.

### D. OUT is not proof of an 8-bar clean drum outro

- Full-volume endings can be called `drum_outro` without a vocal stem.
  `_valid_envelope` only checks nonempty values/hop, not whole-tail coverage.
- The new OUT path averages the final vocal window; sparse vocal returns may
  be diluted. It does not reuse all the stronger whole-track legacy
  `outro_vocal_evidence` protections and has no explicit drum-content test.
- `full_bars = floor(time_to_bar(duration))` treats decoded file duration as
  a musical endpoint. Trailing silence, reverb, encoder padding and partial
  bars can change that interpretation.
- Fade classification uses the track median and a sustained low, near-monotonic
  tail. It may confuse a quiet arrangement change with a fade, or miss a
  fluctuating real fade. The selected boundary is relative to START's phase,
  and the last phrase is not independently proven to remain full-volume.
- A fixed arithmetic 8-bar subtraction therefore does not guarantee eight bars
  of full-volume drums without lyrics.

### E. Versions, caches and evidence are not release gates yet

- Existing saved SQLite plans and existing Serato tags are not silently migrated.
  Refreshing the browser or pulling code does not regenerate them.
- Historical documents and planners describe several different GO/EXIT semantics.
  Read the saved cue map, not only the branch name or label.
- Feature/model caches are keyed to file content, not a complete rules/model
  revision fingerprint. Writing ID3 tags changes the file hash without changing
  the audio, potentially creating another cache identity.
- Existing scoring accepts up to one beat, and label-only checks historically
  compared names to copied metadata. That is not a phrasing release gate.
- Serato dual-tag round trips and unchanged audio hashes prove encoding/integrity,
  not correct bar/phrase placement or Serato DJ Pro's live display behavior.
- Happy's requested reference times themselves differ from its supplied constant
  grid by up to 22 ms; do not "fix" that by shifting the ruler.

## 5. Portable read-only crate audit

On the machine holding the actual test library:

```bash
git pull origin v6
.venv/bin/python scripts/audit_phrasing.py --library web-data/library.sqlite3
```

Use the actual library path if `AUTOCUE_WEB_DATA` points elsewhere. The command
opens SQLite in read-only mode, reads only saved plans, prints JSON to stdout,
does not touch music files, and runs no models/network calls.

Each mapped countdown reports structural bars and beats to its target. Unmapped
legacy plans report inferred A-to-B and G-to-H intervals explicitly as inferred.
It reports invalid/unversioned/not-analyzed records separately. Exit 0 means
all stored plans passed this arithmetic audit, **not musical accuracy**; exit 1
means invalid/unversioned/missing plans, and exit 2 means the audit could not run.

This is the next cheap step before another hours-long crate processing job.

## 6. Next development gates, in order

1. Obtain the actual Pro library audit and identify its producer/build, map and
   original grid. Preserve original cues and reviews before any reanalysis.
2. Select a small failure set spanning: spoken intro, pickup, late/odd intro,
   short outro, vocal return, fade and drifting/half-time grid. Human-mark the
   desired structural targets and clean windows independently of old cues.
3. Use one shared whole-window vocal/drum coverage check for intro and OUT.
   Prefer 8/4/2/1 bars only when the entire window qualifies; otherwise mark it
   unverified or unavailable. Never label uncertain evidence as confirmed.
4. Add grid-fit diagnostics over the entire song and explicit unsupported
   variable-tempo/meter gates before trusting countdown lengths.
5. Improve phrase/chorus boundary evidence without anchoring every phrase to
   the first word. Compare against hand-marked targets, not synthetic truth.
6. Require exact structural offset/label checks plus measured clean-window and
   phrase accuracy on the small real failure set before expanding the crate.

## 7. Verification of this update

Final validation:

- Engine: 267 tests.
- Web: 41 tests.
- Browser timing/review/waveform: 14 tests.
- JavaScript syntax and diff whitespace checks passed.

New regressions cover 8/4/2/1-bar fallbacks, explicit label units, independent
target-distance checking, post-edit countdown names, both export paths, exact
unsnapped START preview/scoring/corrections, and a byte-unchanged read-only
SQLite audit. The read-only CLI also reported all 85 old local plans as invalid
legacy GO plans, measured G-to-H as 32 bars / 128 beats, and left the database's
SHA-256 unchanged. A missing database returns exit 2 without creating a file.
