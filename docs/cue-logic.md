# Cue logic

## Outro-derived intro Cue Loop proposal

If no clean intro fits, the desired fallback is a later outro-derived Serato 5
Cue Loop on A, with START on B at the first playable downbeat. Whole-window stem
checks and a review proposal are implemented, but no fallback positions or
loop tags are applied until the new Serato linkage format is verified. See
[the synthetic-only verification guide](./SERATO-CUE-LOOP-QA.md).
One active vocal envelope frame disqualifies a candidate; drum activity is
required in each bar, and incomplete evidence is unverified. This does not
distinguish real speech from singing or certify loop seams.

## v6.2 phrasing safety update (October 28, 2026)

The current Pop/Vocal defaults use explicit-unit labels such as
`Intro 8 bars (32 beats)` and `GO 4 bars (16 beats) > EXIT`.
Prefer 8/4/2/1-bar windows; the Mix lead caps at 4 bars. Intro and GO are independent,
not guaranteed matching two-track windows. Intro counts to START's structural
anchor; an exact unsnapped pickup can start earlier. Available track space does not
prove that a window is free of vocals or speech.

Mapped review saves preserve manually placed positions but refresh countdown names
and resolved offsets to their actual targets. Intentional custom renames remain
custom. Invalid mapped countdowns and ambiguous unmapped legacy `GO -N` plans
are blocked before export. Preview and scoring use the exact unsnapped START time
while its bar is unchanged. The full automatic event-order/clean-outro guard is
still not a manual-review constraint.

See [PM BOT READ ME](./PM%20BOT%20READ%20ME.md) for the full audit, known musical
accuracy gaps and portable read-only audit command. The sections below document
the earlier configurable v7 layout, not the current Pop/Vocal pad meanings.

## v7 mix windows (confirmed Oct 21, 2026)

`INTRO-N` counts **beats**, not bars, and is a lead-in cue before START, not a measurement of the entire opening section.
Choose **8 bars (32 beats)** before START whenever it fits inside the track; otherwise try
**4 bars (16 beats)**, **2 bars (8 beats)**, then **1 bar (4 beats)**.
Leave the cue empty and flag it if no full bar fits. The default label reports beats.

For example, START at bar 32 means `INTRO-32` at bar 24. START at bar 8 means
`INTRO-32` at bar 0; START at bar 3 means `INTRO-8` at bar 1.
The detected first playable bar is the lower bound, which may be negative when the grid starts late.
Section labels must not move this lead-in, and START is not moved just to obtain an 8-bar lead-in.
Both DROP and VOCAL maps use this preference. Gemini changes to START recalculate the lead-in;
manual review edits remain under the user's control. Existing saved plans require re-analysis to
adopt the new placement; they are not silently migrated.

### START evidence

Retain an existing in-bounds START or the end of an opening intro section, including short
and odd-length intros. A section called "intro" later in the track does not define the opening.
If neither exists, dance tracks may use an observed early energy/vocal entry at its actual bar,
without rounding to 16 or 32 bars. A generic energy increase is not by itself a detected drop.
Without entry evidence, use the first playable bar at low confidence and flag START for review;
do not invent a 32-bar intro.

### Optional Gemini edits

Gemini's picks refer to the cue map sent in its request. If a confident verdict switches maps,
discard those picks rather than reinterpret pad letters under new meanings. Preserve the original
rules-only plan in `gemini.baseline` for later comparison. Concerns about the old map are identified
as such.

For an unchanged map, only low-confidence anchor candidates can move. Changes are checked together
for contradictory event ordering before being applied, then all dependent cues are rebuilt with
the same resolver, custom settings and mix-length preferences as initial analysis. This can shorten
or clear a lead-in instead of leaving it on/before the first drop or before START.
Malformed responses are reported in the plan's Gemini status without changing the local cues.
This is not a live-service or musical-accuracy guarantee; budget and real-library validation remain
release gates in the [development board](./v7-completion-plan.md).

### Grid timing

Every current planner, editor and exporter uses
`audio_seconds = t1 + bar * bar_s - offset_ms / 1000`.
The inverse conversion includes the offset too. Positive offsets move cues earlier; missing
offsets mean zero. Fractional bars (especially EXIT-END) are preserved when saving or adopting
a variant; the editor does not round those to whole beats unless explicitly snapping an edit.
Cue times, correction timestamps and Serato milliseconds agree to within 1 ms. Serato timestamps
cannot be negative and are clamped to zero when a cue falls before the audio begins.

Review saves refresh the cue's stored timestamp and record the offset alongside corrections.
Scoring uses reviewed audio timestamps and the reviewed tempo where available, not equal bar
numbers on potentially different grids. Older beat-only marks still work; when no original
timing is available they use the current grid as the reference. Previously saved incorrect
timestamps are not silently repaired: listen and save a new review.

### Review editor safety

Unsaved bar **and label** changes are tracked independently from waveform undo history.
Prev/Next, closing the review (including Escape) and changing its map offer **Save and continue**,
**Discard changes**, or **Keep editing**. Reloading or leaving the page uses the browser's native
unsaved-change warning. A map change still requires its separate confirmation because it replaces
the saved cue plan.

During a save, editing and navigation are blocked. A failed save keeps the review open with edits
intact for retry; Save & next advances only after a successful save. Waveform fetches are aborted
when leaving a track, and each decode result is checked against the active load before shared
waveform state changes. Late successes or failures cannot replace the current track's waveform.
These protections do not provide crash recovery or an automatic draft store.

### GO and EXIT

`GO-32` is 32 beats (8 bars) before the beginning of the outro. `EXIT-32` is at the
beginning of the outro, offering a separate 32-beat mix window afterward. EXIT is not moved
to eight bars before the file ends: a longer outro still offers a 32-beat window.
Both windows fall back to 16, 8, then 4 beats if necessary, and labels show the selected window.
GO must stay after the final drop/chorus anchor. Partial final bars do not count as full mix bars.

An outro must have **no later detected vocals**, including short vocal returns. Scan the entire
remaining vocal stem, using an adaptive activity threshold: 15% of the greater of its
95th-percentile level or 10% of its peak, bounded below by 0.00001 and above by the previous
0.04 absolute threshold. This catches quieter returns without raising the old threshold.
Word endings extend the boundary only with nearby vocal-stem activity in their final 250 ms;
invalid/out-of-track or stem-unsupported word timings are counted and ignored. This reduces
silent-tail transcription hallucinations, but is not a trained vocal/nonvocal classifier.
Move an earlier section/fade boundary forward to the first whole bar after the last vocal.
If vocals continue through the end, the default end-of-song fallback places GO 32 beats before
the file ends (or a shorter configured preference) and `EXIT-END` at the end itself.
This is explicitly **not an instrumental outro**: GO may overlap vocals, and EXIT-END has
zero mix time afterward. If the ending falls between downbeats these end-relative cues
are not necessarily whole-bar grid positions. Disable this fallback in Cue maps to leave
GO and EXIT empty instead. New stem envelopes include the final partial frame, so even a
very short return at the file end is considered. A missing, invalid or incomplete vocal stem
leaves the outro explicitly unverified at reduced confidence; automated detection cannot guarantee
perfect silence, so listen before writing cues. Older cached envelopes that omit the final
partial frame remain unverified; they are not silently treated as complete. The review panel
shows the evidence status and last detected vocal time, warns about leakage/reverb and missed
vocals, and distinguishes EXIT-END from an instrumental outro. Empty pads show why their
configured mix window is unavailable; cue-title tooltips include reasons and warnings.

### Automatic-plan integrity

Initial planning, map rebuilding, experiments and mapped Gemini edits share the cue resolver.
It snapshots event evidence before assigning pads, so disabling or remapping a pad cannot erase
the underlying START/outro event. A final guard rejects contradictory first/last event order,
invalid cue bounds, timestamp/grid disagreements, disabled-pad positions, inconsistent derived
offsets/labels, vocal-boundary violations and insufficient EXIT windows. Gemini changes are
transactional: rejected plans retain the local cues with an explicit warning.
Pad letters are **not** required to be chronological. A single detected drop still intentionally
supplies both first/last pads, with a shared-event warning; custom repeated meanings are preserved.
Manual review edits do not run the automatic guard and are not silently rewritten.

### Transcription reuse

The built-in worker and transcript CLI reuse one lazy small CPU/int8 WhisperX model and retain
at most two recently used language aligners per process. Shared model access is serialized.
Language detection still runs per track on its strongest vocal window; transcription and alignment
still use the full track, so the language window does not change word offsets or omit the ending.
Successful per-file results remain cached. Model/aligner failures and saved alignment-error records
can be retried on the next request. Real-model runtime and timing-quality benchmarks remain pending.

### Remix-aware synced lyrics

Synced lyrics are matched to actual local word timings in ordered local runs. Each run needs
at least two distinct lyric lines; each line needs at least three matching tokens and at least
65% token coverage, with bounded word gaps and phrase duration. Local tempo ratios must stay
between 0.9 and 1.1; every matched line must satisfy the residual limit (default 2.5 seconds),
not merely the median line. A run can skip up to two source lines. Nonoverlapping runs may
represent inserted instrumental breaks, deleted lines, repeated sections or reordered remix blocks.
Short generic lines, isolated matches, unsupported timestamps and excessive ambiguity are rejected.

The result retains local source/audio boundaries, tempo/offset fits and matched-line provenance.
Global offset/tempo fields are populated only when one fit supports all accepted runs. Chorus
candidates require at least two distinct locally matched repeated lines; no unheard lyric sections
are projected into the audio. Repeated lyrics are still only chorus evidence, not proof of a chorus.
Audio-section/energy fallbacks remain available when lyric evidence is absent or rejected.
Existing fuzzy matching remains an experimental variant; the baseline uses exact normalized tokens.

### Current-library scoring

Save reviews to create explicit human answer keys. Automatic rules-only and optional Gemini
predictions are snapshotted before manual edits; subsequent reviews do not overwrite them.
Partial saves grade only submitted slots and retain earlier targets in physical audio time
through grid changes. Custom names, meanings, map identity and explicit empty decisions survive.
Reanalysis preserves the key separately from the new predictions.

The Experiments report separates timing accuracy on positioned targets from correctness on
explicit empty targets. Missing target slots are unknown; missing prediction slots are failures.
Incompatible cue meanings/maps are excluded from paired timing wins/losses and reported explicitly;
known map choices are compared separately. Timing errors use the reviewed grid's beat duration.
Exports contain reusable answer keys and per-cue failure/exclusion reasons, plus recorded new
Gemini costs for the saved runs. They do not infer provider/lifetime spending or certify generalization.
`engine/score_v6.py --library-export ... --report ...` evaluates that JSON without legacy directories.
No human targets means no accuracy estimate. Old reviews cannot recover predictions that were
never saved, and old assisted baselines without independent rules snapshots are marked unavailable.

### Customization

In **Cue maps**, edit the intro, GO and EXIT preference lists independently in beats (descending,
unique multiples of 4, from 4 to 256). These preferences apply to both maps. Per-pad meanings,
labels, colours and enabled states remain editable. Labels support `{beats}` and `{bars}`.
The end-of-song fallback is independently switchable and defaults on. `{beats}` and `{bars}`
render `END` for the end-of-song EXIT. The default `before_outro_8` meaning uses the GO preference list; older fixed 16/32-bar outro
meanings remain available as explicit choices.

Settings schema v2 upgrades unchanged legacy A/G/H defaults to beat-count labels and the new
mix-window meanings, preserving custom labels, colours and disabled pads. Changes apply on the
next analysis; existing saved plans and manual edits are not rewritten.

## Historical v2 layout (approved Oct 15, 2026)

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
