# v7 completion and validation plan

## Goal and boundaries

Make v7 reliable for reviewed library preparation, then measure cue accuracy before bulk use.
Passing unit tests is not evidence that the musical choices are correct.

## Development board

This is the persistent backlog. Status is updated only after implementation and verification.
Items within each milestone are ordered by priority, not promises of a delivery date.
The detailed acceptance criteria for v7 fixes follow this board.

Definition of done: implementation persists in the repository, focused regression tests pass,
related documentation is updated, and limitations are recorded. "Done" on an individual story
does not mean the milestone or v7 release is complete. Model promotion additionally requires a
held-out listening benchmark, not merely passing software tests.

### Milestone A: safe v7 review candidate

| ID | Story / deliverable | Depends on | Status |
|---|---|---|---|
| A01 | Preserve evidence-backed START; remove forced dance intro lengths and assumed drops | None | Done: regression verified |
| A02 | Re-resolve mapped cue dependencies after Gemini changes; reject contradictory anchor moves | None | Done: regression verified |
| A03 | Reject old-map picks after a Gemini map switch; preserve original baseline | None | Done: regression verified |
| A04 | Validate malformed Gemini responses without losing the local plan | None | Done: regression verified |
| A05 | Recover credential configuration; show safe credential/model status in Settings and CLI | None | Status/configuration implemented; historical credential recovery blocked |
| A06 | Add request eligibility, richer evidence, versioned caches, bounded spending and usage reconciliation | A03, A04, A05 | Implemented + offline tests passed; live validation blocked by A05 |
| A07 | Unify timing/offsets across browser, export, corrections and scoring | None | Done: automated + browser verified |
| A08 | Strengthen outro evidence, insufficient-window handling and final automatic-plan validation | A01, A02, A07 | Implemented + regression verified; real-audio calibration/listening pending |
| A09 | Protect unsaved reviews; make save/navigation safe; fix stale waveform decode races | None | Done: automated + browser verified |
| A10 | Add true multiresolution waveform caching, matching overview colours and browser regression tests | A09 | Done: automated + generated-audio browser verified |
| A11 | Harden Serato grids, retain provenance, protect imported grids in experiments | None | Implemented + fixture tests passed; real Serato sample validation pending |
| A12 | Implement section-wise remix lyric alignment with rejection tests | None | Done: local-match and planner integration regressions verified |
| A13 | Benchmark and optimize WhisperX without weakening vocal/outro verification | A08 | Model reuse + timing/failure fixtures verified; real-model runtime/quality benchmark pending |
| A14 | Migrate staged audio to centrally resolved relative paths | None | Done: migration, moved-library playback and processing/removal regressions verified |
| A15 | Adapt library scoring, baseline/AI comparison and answer-key export | A03, A07 | Done: regression, export CLI and synthetic-metadata browser checks verified |
| A16 | Update docs/version labels; fresh-audio, browser, backup/restore and Serato end-marker checks | A01–A15 | Backlog |

### Milestone B: library intelligence and imported anchors

| ID | Story / acceptance criterion | Depends on | Status |
|---|---|---|---|
| B01 | Read-only, resumable metadata inventory: explicit roots, progress, errors, counts; no audio/model jobs during scan | None | Done: CLI + generated metadata/header fixtures verified; actual library scan not started |
| B02 | Import existing cues and grids with format/version tests and known/unknown provenance; never mutate tags | B01, A11 | Implemented: read-only container/version fixtures; CuePoints schema and real Serato/MIK samples still need validation |
| B03 | Deduplicate recordings and group alternate mixes; freeze reproducible track-level dataset splits | B01 | Implemented + generated-audio tests passed; metadata grouping remains a review candidate |
| B04 | Cache beat/bar-aligned features with extractor versions and quality flags; checkpoint interrupted jobs | B02 | Implemented + synthetic/FFmpeg tests passed; real-library extraction pending approved budget and grid review |
| B05 | Switchable imported-anchor-assisted detector; compare candidate recall and review corrections to audio-only | B02, A08, A15 | Implemented + synthetic paired-review tests passed; real-library evaluation pending |
| B06 | Build reviewed event-label UI (drop/build/breakdown/chorus/other), annotation provenance and uncertain/skip states | B02, A09 | Implemented + generated API/UI regression tests passed; real reviewed labels not yet collected |
| B07 | Establish held-out human-reviewed benchmark; group related mixes; report coverage, timing and false positives | B03, B06, A15 | Offline benchmark workflow + fixtures implemented; real benchmark blocked on completed human-reviewed test groups |

### Milestone C: our local model

| ID | Story / acceptance criterion | Depends on | Status |
|---|---|---|---|
| C01 | Dataset builder combines weak transition labels and reviewed event labels; never treats all unmarked bars as negatives | B03, B04, B06 | Backlog |
| C02 | Train reproducible lightweight candidate ranker; save seeds, feature schema, parameters, metrics and model artifact | C01, B07 | Backlog |
| C03 | Benchmark a suitably licensed pretrained audio embedding/temporal model against C02; do not train a giant model from scratch | C02 | Backlog |
| C04 | Integrate chosen local event model behind a switch, with confidence, deterministic cue rules, fallback and rollback | C02, A08 | Backlog |
| C05 | Active-learning queue prioritizes disagreement, uncertainty and genre gaps; reviewed examples feed versioned retraining | C04, B06 | Backlog |
| C06 | Evaluate audio-only rules, imported anchors, model without imported markers, and model with markers on untouched held-out songs | C04, B07 | Backlog |

### Milestone D: Gemini-assisted learning operations

| ID | Story / acceptance criterion | Depends on | Status |
|---|---|---|---|
| D01 | Durable local job/dataset/experiment registry: bounded workers, resume, cancellation, audit trail and cost limits | B01 | Backlog |
| D02 | Gemini tools may query dataset summaries and propose bounded experiments; deterministic code executes only approved actions | D01, A06, C02 | Backlog |
| D03 | Opt-in structured-data annotation assistant: provisional labels, provenance and human confirmation; evaluate hallucinations | B06, A06 | Backlog |
| D04 | Optional authorized batch evaluation/annotation with reconciled costs; no automatic library upload or audio teacher | D03 | Needs data policy and budget approval |
| D05 | Model promotion gate, rollback, dataset/model documentation and ongoing genre-specific regression monitoring | C06, D01 | Backlog |

### First implementation pass: verification

- A01: supplied short/odd STARTs and opening intro boundaries are preserved. Unsupported late
  intro labels and flat/missing energy no longer create a bar-32 START. Real early energy entries
  are not rounded to a preferred phrase length or promoted into assumed drops.
- A02: mapped Gemini edits are transactional and use the shared resolver. Reproduced first-drop
  24 / last-drop 57 moved to 56: the last-drop lead now shortens to 16 bars (bar 40), rather than
  colliding at bar 24. Contradictory event picks are rejected with a review flag. Custom/disabled
  pads survive; chorus lead-ins cannot precede START.
- A03: old-map picks are discarded on a switch, concerns retain their original-map context, and
  `gemini.baseline` saves an independent rules-only plan for future scoring.
- A04: malformed structured replies and API envelopes report an error without changing local cues.
  Confidence/types and response usage/text shapes are checked. Thinking text is not parsed as the
  answer. Spending/accounting guarantees remain explicitly unfinished in A06.
- Verification: 75 engine unittest cases and the separate 13-case legacy test harness passed;
  20 web/server tests passed; JavaScript syntax and `git diff --check` passed.
- No live Gemini requests, new audio analyses, library scans, training jobs, or tag writes were run.
  Fresh-audio musical validation and real Serato compatibility remain release gates.

### Second implementation pass: timing and editor safety

- A07: shared Python conversions and matching browser conversions now include grid offsets in
  both directions. Section/lyric boundaries, feature windows, cue timestamps, waveform markers,
  variant adoption, correction events and Serato exports use the same audio-time convention.
  Review saves refresh stored cue times; fractional/end-of-song bars are not truncated.
  Scoring compares audio times using the reviewed grid/tempo when known, including comparisons
  between plans with different grids. Old beat-only marks retain their compatibility fallback.
- A09: navigation, close/Escape and map changes offer save/discard/cancel for unsaved bars and
  names; reload/navigation uses the browser warning. Saves block concurrent editing/navigation
  and duplicate submissions; a failure leaves edits available for retry. Storage errors return
  an explicit API error and roll back the track update. Custom map labels survive review saves.
  Audio fetches are cancellable and decode results commit only for the current load.
- Scripted browser checks on an isolated three-track synthetic library covered offset preview
  and exact-end adoption; name-only cancel/discard; failed-save retry; save-and-continue; close
  and Escape; map-change cancel; undo returning to a clean review; unload prevention; serialized
  slow saves; and a deliberately delayed old decode resolving after the next track was ready.
  Zoom in/out, beat nudging/undo and actual audio seeking at the offset-adjusted cue also passed.
  Cancelling a pending play by pausing/navigating no longer leaves an unhandled playback promise;
  other playback failures are surfaced as notifications.
- Verification: 85 engine unittest cases plus 13 legacy checks, 24 server tests and two Node
  timing tests passed. Python/browser timing tests share positive/negative-offset, missing-offset,
  preroll, fractional-bar and exact-end fixtures. JavaScript syntax and diff checks passed.
- This does not complete A10's cached multiresolution waveform work or A15's full scoring workflow.
  Existing incorrectly saved timestamps are not silently migrated; fresh review is required.
  Live Gemini, real-audio accuracy, and real Serato playback remain unverified release gates.

### Third implementation pass: current checkpoint

- A05: environment, `.env.local`, `.env` and explicit `AUTOCUE_ENV_FILE` configuration are
  parsed without executing shell files. Settings and CLI report source/model/configured status
  without exposing credentials. Current and known previous project locations did not yield the
  historical credential. Configured status never claims verified live access.
- A06: uncertainty eligibility, explicit cue meanings/units, BPM, compact energy neighborhoods,
  structured response schema, low thinking and versioned request caching are implemented.
  Process-serialized persistent reservations, usage reconciliation, atomic cache writes and
  fail-closed accounting protect paid work. Timeout/unknown usage requires operator reconciliation;
  cost overruns block further spending. Model-specific pricing is verified through December 1,
  2026, after which paid requests are disabled pending a pricing update. No live call was made.
- A10: versioned disk waveform pyramids support bounded overview/detail requests. The editor
  no longer decodes full audio for its waveform. Matching frequency colours use one rendering
  helper. Zoom requests are debounced, cancellable and guarded against stale results.
  On one generated four-minute track: first cache build/open 2,759 ms; cached open after a page
  reload 84 ms with zero browser decodes; old browser decode/filter benchmark 767 ms.
  These are individual local observations, not a general latency guarantee. Zoom reached level 0
  (about 5.8 ms buckets); overview pan, error notification, nudging/undo and track navigation
  were exercised on the isolated browser fixture at port 8783.
- A11: malformed/nonfinite/out-of-order grids are rejected with diagnostics; plan grids retain
  BPM/source/provenance without assuming Serato data was manually checked. Kick-grid experiments
  cannot replace an imported Serato grid. MP3 ID3 fixture readback passes without file mutation;
  user-provided sample interoperability remains pending.
- Latest full-suite run: 110 engine tests plus 13 legacy checks, 26 web tests and six Node tests
  passed. Later focused Node/syntax/diff checks also passed.
- No real library scan, model training, music upload, paid request or user tag write occurred.

### Fourth implementation pass: portable library

- A14: new imports store library-relative staged audio paths. A central resolver is used by
  built-in/external processing, detailed waveforms, playback and removal. Startup migrates legacy
  absolute paths to the current library's staged copy, preferring it even when an old copy exists.
  Original music links remain separate and are never used as a staged-audio fallback.
- Missing/corrupt paths are reported without erasing unrelated analysis errors. Migration is
  idempotent; path-traversal and staged symlink tests protect original files. Failed file deletions
  retain their database entries and report explicit partial-batch errors.
- Verification: 33 server tests passed, including moving a complete SQLite/audio library,
  playback bytes, built-in and external processor paths, existing review/export regressions,
  symlink/traversal rejection, missing-file recovery and deletion failure. Six waveform and
  11 Serato-grid tests also passed after the latest robustness changes.

### Fifth implementation pass: cue integrity, outro evidence and transcription reuse

- A08: the automatic resolver snapshots event evidence independently of pad assignments.
  Disabled/remapped pads no longer erase START/outro anchors. Automatic output is checked for
  first/last event order, track bounds, derived offsets, labels, disabled pads, timing consistency,
  vocal boundaries and configured EXIT windows. Gemini validation failures preserve the local
  plan. Deliberate single-drop repeats retain explicit shared-event warnings.
- Quiet vocal returns use an adaptive threshold capped at the previous absolute threshold.
  Invalid/unsupported word endings cannot extend a boundary through a silent tail. The detected
  boundary remains conservative about stem leakage/reverb; it is not a trained vocal classifier.
  New envelopes retain final partial frames. Incomplete/invalid old envelopes remain unverified.
  Real-audio calibration, leakage/missed-vocal evaluation and listening sign-off are still pending.
- Review now exposes vocal evidence, rejected-word counts and the distinction between an
  instrumental outro and an end-of-song fallback. Empty pads explain unavailable mix windows.
  Browser checks covered all three evidence states on generated tracks; zoom reached a 1.875 s
  viewport with roughly 5.8 ms buckets; beat nudge/undo, unsaved-name protection and local save/reopen
  passed with relative staged paths. The cancel handler closed a clean review when dispatched.
  Native Escape generated a key event but no native cancel event in this automated browser
  session, so native Escape behavior was not verified by this pass.
- A13: web analysis and manifest/one-file transcription share one lazily loaded small CPU/int8
  WhisperX model, with serialized access and at most two recently used language aligners.
  Each track still detects its own language from the vocal window and transcribes/aligns the
  full audio. Failed model/aligner loads are retryable; failed per-file alignment records no longer
  suppress a subsequent retry. Successful per-file caches still skip work.
- Model-reuse fixtures verify one model load across five tracks, language changes, bounded
  aligner retention, unchanged full-track word offsets, concurrency serialization and retries.
  These are orchestration tests, not a measured real-model speedup or an audio-quality benchmark.
- Verification: 127 engine tests plus 13 legacy checks, 34 server tests and nine Node tests
  passed. JavaScript syntax and diff checks passed. The generated-audio helper was stopped and
  its temporary library/cache removed; the unrelated server at port 8772 was left untouched.
- No full-library processing, model training, paid Gemini call, music upload or original tag write
  was performed.

### Sixth implementation pass: remix alignment and honest evaluation

- A12: lyrics align through ordered local phrase matches instead of one forced global offset.
  Fixtures cover inserted breaks, deleted lines, repeated/reordered remix blocks, malformed word
  timings and rejected unsupported sections. Repeated-lyric chorus candidates use only matched
  audio spans, never projected lyrics in unheard sections. Planner integration is tested.
  These are synthetic timing tests; real remix listening accuracy is not yet established.
- A15: current-library reports and schema-2 exports carry independent rules/assisted predictions
  and reusable explicit human answer keys. Partial reviews merge across timing-grid changes.
  Reanalysis preserves targets. Historical reviewed plans without saved automatic predictions
  are unavailable, not retroactively relabelled as rules-only or assisted predictions.
- Positioned targets and explicit empty targets have separate denominators, including positioned
  breakdowns by map. Missing predictions fail; omitted human targets remain unknown. Incompatible
  cue meanings/maps are excluded from timing comparisons, with separate map classification and
  exported per-cue reasons. Cost covers saved evaluated runs, not lifetime/provider billing.
- The scoring CLI accepts a current-library JSON export and writes a detailed Markdown report;
  legacy external marks remain supported. README and cue-logic documentation describe the workflow.
- Verification: 149 engine tests plus 13 legacy checks, 37 server tests and 11 Node tests passed.
  Syntax and diff checks passed. Browser verification used isolated synthetic metadata only:
  independent rules/assisted errors, explicit empty targets, saved-run costs and schema-2 export.
  No measured musical accuracy, live Gemini request or real Serato sign-off is claimed.

### Seventh implementation pass: read-only metadata inventory

- B01: a standalone CLI accepts explicit, disjoint source directories and a separate private
  SQLite inventory outside those roots. It reads tags/audio headers only, checkpoints each
  completed entry and supports bounded audio-attempt batches, pause/resume, scan-ID recovery,
  counts, discovered bytes and explicit bounded error details. No web import or model job starts.
- Indexed durable queues avoid sorting the entire pending library on every file. A process lock
  rejects concurrent writers. File identity/timestamps are checked before/after reads; queued paths
  traverse directory descriptors without following symlinks. Missing/replaced roots fail visibly.
  Entry errors persist and require a new scan to revisit; resume handles unfinished work only.
- Verification: 19 inventory cases use generated WAV headers/ID3 tags and temporary directories,
  covering byte-preserving reads, nested roots, interruptions, failures, changed files, symlinks,
  special files, concurrent access, unrelated-database protection, bounded errors and CLI exit codes.
  The latest full engine run passes 168 tests plus 13 legacy checks. Last server/Node runs passed
  37 and 11 tests respectively. No actual music-library root was supplied or scanned.
- Existing marker import, provenance classification, grouping and training remain separate stories.

### Eighth implementation pass: imported annotation evidence (paused for restart)

- B02 implementation is saved in `engine/imported_anchors.py`, with shared Serato envelopes
  in `engine/serato_tags.py` reused by the existing web writer. Inventory metadata extractor v2
  preserves native Serato Markers2 cue/loop records, BeatGrid markers and raw source payloads
  with hashes, container/field provenance and unknown authorship. Nothing is promoted to a
  human answer key or mapped to AutoCue's pad meanings.
- Generated MP3/AIFF/WAV/FLAC/M4A fixtures verify read-only extraction. CuePoints JSON is retained
  without guessing its schema/units or asserting Mixed In Key authorship. Legacy tags and
  unknown entries are preserved; malformed/conflicting tags remain visible. Oversized sources
  retain size/hash only and report errors. Inventory progress reports separate annotation errors
  and unsupported counts plus bounded diagnostics, without raw payloads.
- Verification before the pause: the first 10 importer tests, all 19 inventory tests and all
  37 server tests passed. Two additional importer regressions (ID3 v2.3/v2.4 and bounded
  unsupported diagnostics) and stricter wrapper validation were subsequently added.
- The outstanding checks completed before restart: all 180 engine tests plus 13 legacy checks
  passed, including the final importer edits. `git diff --check` reported no whitespace errors,
  and `.venv/bin/python engine/library_inventory.py --help` completed successfully. The latest
  server run passed all 37 tests. Real-library validation remains separate from these checks.
- No real library root was scanned, no Gemini request/upload/training was initiated, and no
  original music tags were written. All work remains uncommitted on disk; preserve the existing
  dirty worktree. Real Serato/MIK samples and CuePoints schema validation still gate B02 sign-off.
- Next: finish validation, then continue the library-intelligence backlog. Do not silently
  substitute fixture success for real-library compatibility or musical-accuracy evidence.

### Ninth implementation pass: deduplication and frozen dataset splits

- B03: `engine/dataset_splits.py` reads only completed inventory scans, hashes file bytes locally
  to collapse exact copies, and retains every source path for each unique byte sequence.
- Tracks with normalized artist/title metadata share a conservative grouping key after recognized
  trailing mix/version labels are removed. Groups are explicitly marked as requiring review; they
  are not asserted song-identity truth. Missing artist/title falls back to the exact audio hash.
- A supplied seed and train/validation/test ratios deterministically assign each group to one split.
  The versioned JSON snapshot records source roots, paths, hashes, grouping methods, exclusions,
  settings and split assignments. Existing output files are never overwritten; source files and
  inventory rows are not changed.
- Verification: all 37 focused dataset-split, inventory and imported-anchor tests passed; syntax
  and diff checks passed. Generated fixtures cover exact duplicate collapse, alternate mix
  grouping, stable and seed-sensitive split assignments, incomplete scans, changed source
  detection, CLI output and immutable manifest behavior. No real library was scanned; metadata-
  group accuracy and final group review remain prerequisites before treating a split as a
  held-out benchmark.

### Tenth implementation pass: resumable beat/bar feature cache

- B04: `engine/feature_cache.py` consumes a frozen B03 manifest and uses either an explicit
  SHA-keyed grid file or one supported constant-tempo imported Serato grid. Variable-tempo,
  malformed, conflicting and missing grids are skipped with explicit quality flags; imported
  grids remain unverified.
- The extractor reuses the existing 50 fps peak/RMS/low-mid-high analysis and aggregates frames
  into offset-aware bars. Compressed per-track artifacts record bar indices/times, coverage,
  energy, grid provenance, source audio hash, bar extractor/base feature/schema versions and
  FFmpeg version.
- A locked SQLite checkpoint records queued/running/done/skipped/error jobs. A bounded run can be
  resumed with the same manifest, grids, extractor settings and decoder version; failures only
  retry with `--retry-errors`. Source paths are opened without following symlinks, checked against
  inventory fingerprints, and rehashed against the frozen dataset after decode.
- Verification: 70 focused feature-cache, dataset, inventory, imported-anchor, grid, timing
  and analysis tests ran; 68 passed and 2 optional tests were skipped. This includes synthetic
  feature fixtures and an actual FFmpeg decode of generated audio; compilation and CLI help
  checks passed. No real library feature run occurred. Full extraction remains gated on an
  approved compute/storage budget, usable reviewed grids, and quality review.

### Eleventh implementation pass: imported-anchor candidate comparison

- B05: `engine/anchor_detector.py` reads only the frozen dataset and completed B04 cache. Its
  switch selects audio-only candidates, the union of audio candidates and imported Serato
  Markers2 cue positions, or both pools side by side. The deterministic audio-only baseline scores
  bar boundaries using local RMS/low-band energy ratios; imported loop records and unsupported
  marker formats are not treated as event candidates.
- Imported markers remain untyped. Reports preserve exact marker times, names, source field/hash,
  and distance to the nearest grid bar; marker names and pad indices never determine event class.
  Local audio evidence is attached where available, but does not promote an imported marker to a
  verified event.
- Optional evaluation consumes only schema-2 exports with explicit `human_review` answer keys.
  It reports positioned-target and baseline-correction coverage per slot at a stated tolerance.
  A correction is identified from the saved baseline when its meaning differs, its position
  differs by over one beat, or it has no saved position. This measures candidate coverage, not
  reduced human effort; missing baselines remain unknown, and independence of review targets
  from imported tags is unknown.
- Verification: synthetic feature arrays cover audio scoring, and a generated WAV exercises the
  B03/B04-to-B05 path, hash association, untyped marker provenance, switchable candidate pools,
  paired review metrics and report output. No real library was scanned or processed. Real
  sample compatibility and musical benefit remain unverified.

### Twelfth implementation pass: human-reviewed event labels

- B06: the local waveform review now records drop/build/breakdown/chorus/other annotations at the
  playhead, with exact seconds, optional offset-aware bar, certainty, notes, human-review source,
  grid snapshot and UTC timestamp. These records are independent of cue edits and imported Serato
  markers. Labels can be retracted from active review while their provenance remains in history.
- Track review state distinguishes pending, in-progress, complete, uncertain and skipped. A
  complete review with no labels is an explicit no-target-events decision; uncertain and skipped
  states are preserved rather than inferred as negative examples. Labels remain local in SQLite.
- The versioned `autocue-event-labels.json` export provides track hashes, taxonomy, review state,
  active and retracted annotations, and provenance for later B07 dataset work.
- Verification: generated server fixtures cover offset/grid snapshots, gridless second-based
  labels, uncertainty/skip state, export, retraction, invalid inputs and local-token protection.
  UI checks cover taxonomy, certainty, state controls, rendering and note escaping. Forty web
server tests and seven Node review/UI tests passed; app JavaScript syntax and a static HTML
accessibility snapshot passed. No real review labels or music library were processed.

### Thirteenth implementation pass: held-out event benchmark workflow

- B07: `engine/event_benchmark.py` joins the frozen B03 manifest, B06 event-label export, and B05
candidate report by exact audio SHA-256. It restricts scoring to the frozen test split and
completed human-review tracks, verifies each track's split against its related-mix group, and
reports test-group coverage plus any metadata grouping review requirement.
- Matching is ordered and one-to-one within a stated tolerance (four beats from the saved review
grid by default, with a two-second fallback for gridless reviews). Reports include target recall,
candidate precision, unmatched candidate counts, timing errors and per-event-type target recall.
The detector is generic: event-type classification is not evaluated. Uncertain event positions
are not positive or negative examples; candidates near them are excluded from false-positive
counts. Incomplete/uncertain/skipped tracks are excluded, while a complete zero-label review is
explicit and candidate positions on it count as false positives.
- The benchmark refuses malformed hashes, duplicate tracks/reviews, missing split groups,
nonfinite positions, and group/split mismatches. It makes no model-promotion claim and cannot
verify whether detector settings were tuned on the test results.
- Verification: synthetic fixtures exercise held-out-only joins, related-mix group reporting,
complete/uncertain/skipped review semantics, grid and fallback tolerances, one-to-one matching,
recall/precision/timing/false-positive output, malformed inputs and non-overwriting report
writes. No actual completed test-group reviews are available yet; therefore a real benchmark
and musical-accuracy estimate remain blocked.

### External gates

- Confirm actual library roots before any 20k-song scan; begin with a representative read-only pilot.
- Find earlier credential configuration or safely reconfigure; verify live access only after cost protections.
- Supply real Mixed In Key/Serato-tagged sample files for interoperability checks.
- Review benchmark examples by ear. Existing automatic markers alone cannot certify accuracy.
- Approve a measured compute/storage budget before full feature extraction or training.
- Any transmission to Google needs a separate data-permission and budget decision.
- Verify real Serato playback/write behavior on copies. Commit/tag only when requested.

Preserve reviewed cues, source audio, existing Serato data, custom maps, and verified backups.
Do not bulk re-analyze or write tags while fixing the implementation. Use isolated libraries and
copies for end-to-end tests. No new paid requests until credential discovery and spending controls
are verified. Do not record credentials in logs, documentation, source control, or test fixtures.

## 1. Restore predictable Google credential discovery

- Locate the previously configured credential using configuration locations and presence checks,
  not credential searches in conversation text or output of secret values.
- Check the prior project location and intended local launch configuration. If the original
  environment is unavailable, report that limitation rather than saying no key was ever supplied.
- Unify credential and model configuration across the UI, built-in pipeline, and supported CLI.
  Distinguish shell-environment configuration from configuration available when launched in VS Code.
- Show configured/missing status, configuration source (never the value), selected model, and
  last request error in Settings. A configured key is not necessarily a valid key.

Acceptance: environment and supported local configuration work under the normal launcher;
missing/invalid credentials are actionable and never exposed. A separately authorized live smoke
test checks model access after the protections in step 4.

## 2. Fix START and introduce final cue validation

- Remove the unsupported minimum 16-bar/32-bar fallback that overrides short dance intros.
  Retain evidence-backed section, vocal, energy, and grid decisions; flag uncertain starts.
- Keep START detection separate from intro mix-length preferences.
- Reuse one dependency-resolution and validation path after initial planning, map rebuilding,
  Gemini changes, and experiment planning. Do not silently rewrite manual review edits.
- Validate bounds, first/last event ordering, derived offsets, vocal-free boundaries, labels,
  disabled/custom pads, and permitted end-of-song exceptions. Do not require all pad letters
  to be chronologically ordered: lead-ins can legitimately precede other pads.
- Make single-drop duplicate behavior explicit; avoid redundant default pads where practical,
  while preserving intentionally customized duplicate meanings.

Acceptance: short-intro fixtures keep evidence-backed START; intro lengths remain 32, 16, 8,
then 4 beats by default. Anchor changes cannot recreate invalid lead-ins or contradictory labels.

## 3. Unify timing, outro evidence, and Serato output

- Use the same bar/time conversion, including offset_ms and fractional end positions, in
  planning, browser preview, corrections, experiment scoring, and tag export.
- Validate whole-track vocal evidence; account for missing coverage, stem leakage, short returns,
  reverb, and transcription hallucinations. Avoid treating a single absolute threshold as proof
  of a vocal-free ending. Show evidence and review warnings.
- Preserve the distinction between EXIT at an instrumental outro and EXIT-END at song end.
  Handle insufficient space for the user's selected minimum mix length explicitly.
- Verify EXIT-END round-trips and behaves usefully in Serato; do not silently shift it earlier
  if Serato cannot use an exact end marker.
- Preserve beatgrid source/BPM/provenance in plans. Validate malformed/nonfinite grids and
  report unsupported formats and variable-tempo cases.
- Prevent optional kick-grid experiments from silently replacing an imported trusted grid.

Acceptance: preview, saved corrections, and exported cues agree within 1 ms for supported fixtures;
late vocals invalidate earlier outros; missing evidence is unverified; exact-end behavior is tested
on a disposable Serato-compatible file and checked in Serato before release.

## 4. Make Gemini reliable and economical

- Prevent old-map slot picks from being applied after DROP/VOCAL switching. Include both map
  alternatives in a structured request, or discard incompatible picks; avoid unconditional extra calls.
- Run assistance only for eligible uncertainty/review cases, not every confidently analyzed track.
- Supply BPM, timing units, cue meanings, configured lengths, vocal evidence, and compact bar-energy
  context. Preserve an unassisted baseline for meaningful rules-only versus AI comparisons.
- Validate response types, finite confidence ranges, candidate membership, and map applicability.
  Malformed responses and transport failures must preserve the baseline and surface a clear error.
- Route accepted changes through step 2's final validator.
- Version cache keys by prompt/schema/model/settings. Separate cache reuse from newly incurred cost.
- Replace the approximate cap claim with bounded request settings, model-aware pricing,
  preflight budget reservation, safe accounting for concurrent requests, and actual usage reconciliation
  including reasoning tokens. Report accounting failures and overruns; block further spending.
  An application budget is not a provider-enforced billing guarantee.
- Test timeouts, invalid keys, missing models, truncated JSON, cache invalidation, spending limits,
  and malformed replies before a minimal approved live request.

Acceptance: no semantic slot reuse across maps, no invalid cue sets, no repeated paid request for
identical eligible inputs, and visible model/cost/error status. Keep default off until a reviewed
rules-only versus Gemini comparison demonstrates benefit.

## 5. Harden the review editor and waveform

- Warn before discarding unsaved edits on navigation/close/map changes; offer save, discard, cancel.
- Make save/navigation atomic from the user's perspective, including error/retry handling.
- Prevent stale fetch/decode results from overwriting the active track; guard all shared waveform state.
- Use cached multiresolution waveform data for real interactive opening, rather than only a placeholder.
  Keep frequency-band colours consistent between detail and overview; avoid unnecessary main-thread work.
- Verify snap units, labels, undo/redo, seek, audition-stop behavior, follow during actual playback,
  save-and-next, rapid navigation, and failure states with browser tests.

Acceptance: edits cannot be lost without confirmation; rapid navigation never shows another track's
waveform; reopening cached data avoids full audio decoding for initial editing. Record opening latency
on a fixed test track before and after, and preserve existing playback/editor behavior.

## 6. Complete the unfinished analysis work

- Section-wise lyric alignment: support remix edits/repeats with ordered local matches and reject
  unsupported spans; do not force a global match or silently accept a weak alignment.
- WhisperX speed: reuse models/aligners safely, eliminate unnecessary language-detection work, and
  evaluate timestamp-preserving vocal-window transcription. Do not skip vocal analysis merely because
  the track is DROP: outro verification still needs it. Keep quality and full-analysis fallbacks.
- Relative audio paths: migrate staged-library paths to a documented DATA-relative representation
  and resolve centrally across processing, playback, export, and removal. Keep originals separate.
- Scoring: adapt the current library/answer-key workflow rather than relying on hardcoded legacy paths;
  score in time with grid offsets, include per-cue failures, and compare saved rules-only/AI variants.

Acceptance: moved-library fixture works end to end; representative remix alignment tests pass;
measure runtime and timestamp quality before/after optimization; scoring runs with fixtures now and
with user-reviewed marks when available. Report absent ground truth rather than invent an accuracy score.

## 7. Integrate existing Mixed In Key/Serato anchors

- Verify current official export behavior and supported versions before promising interoperability.
- Read existing cues as non-destructive input; retain provenance and never assume every Serato cue is
  Mixed In Key-generated or a drop.
- Combine candidate anchors with imported grids and audio evidence; do not map incoming pad numbers
  directly to our meanings. Preserve source markers for comparison and backup.
- Add a switchable UI mode and import summary. Test parsers with fixtures first.

Acceptance: actual user-prepared sample files validate marker/grid import; compare resulting cues
against reviewed targets. Real compatibility/quality sign-off remains blocked until samples exist.

### Learning from existing energy cues

Use Mixed In Key cues as weak labels for candidate musical changes, not ground-truth drop labels.
Record incoming cue time/name, grid, provenance, and audio features around each marker. Preserve
uncertainty about the original generating tool when tags do not establish it. A track-level energy
rating is not a per-section energy curve.

First measure whether imported markers improve candidate recall and reduce manual corrections.
Have the user classify a small sample as drop, build, breakdown, chorus, or other before tuning
rules. Begin with threshold/ranking calibration rather than training a new model on a small dataset.
Unmarked positions are not automatically negative examples.

If enough reviewed data accumulates, train a lightweight candidate ranker using permitted local
data. Split evaluation by track and keep alternate mixes of the same song together to prevent
leakage. Compare audio-only, imported-anchor-assisted, and reviewed-label-calibrated systems on
held-out tracks. Never score imported cues as both the training source and independent proof of
accuracy; user-reviewed targets remain the final evaluation reference.

## 8. Release checks and listening test

- Update stale version labels, setup instructions, configuration docs, and limitations. Keep legacy
  module filenames where renaming would add compatibility risk.
- Correct checklist statuses to distinguish implemented, partially implemented, verified, and blocked.
- Run engine/web suites, JavaScript checks, focused browser tests, and a fresh-audio end-to-end case.
- Replan a fixed cached track set without altering library results, then run an approved batch on copies.
  Include short intros, chorus-led house, multiple/single drops, vocal returns, vocals to the end,
  instrumentals, remixes, and imported Serato/Mixed In Key data.
- Review at least 10 representative tracks by ear and save expected cues. Report per-cue timing errors,
  mix-window compliance, false vocal-free outros, collisions, runtime, and AI cost/benefit.
- Verify backup/write/restore on copies and real Serato compatibility. Stop test services and remove
  temporary test libraries. Commit/tag only when requested.

Release gate: no known cue-ordering, timing/export, edit-loss, or budget-accounting blockers;
latest rules tested on real audio; limitations documented; reviewed accuracy measured.
Until then describe the build as a v7 review candidate, not a completed release.

## Dependencies and external inputs

Steps 2–4 are prerequisites for trusting AI-generated cue plans. Step 3 is a prerequisite for
tag-write sign-off. Step 5 is a prerequisite for safe batch review. Step 6's scoring implementation
can proceed before marks exist, but accuracy claims require reviewed marks.

External inputs: access to the previous credential configuration (or safe reconfiguration if lost),
approval for a minimal paid live Gemini test, Mixed In Key/Serato sample files, user listening decisions,
and a real Serato end-marker/write check. Everything else can be implemented and fixture-tested locally.
