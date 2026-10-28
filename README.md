# Sound & Service AutoCue

Current review candidate: **v7**. Real-library evidence is preliminary; automatic cues
still require listening review. See [the v7 completion plan](docs/v7-completion-plan.md)
for the measured sample, remaining gates, and local review instructions.

**October 28 phrasing audit:** baseline `v6.1.0-phrasing-baseline` is preserved.
The **v6.2 arithmetic/label update is not performance-ready**. Saved legacy plans
can count 32 **bars** where newer labels mean 32 **beats**. See
[PM BOT READ ME](docs/PM%20BOT%20READ%20ME.md) for measured evidence, remaining
algorithm gaps and the read-only crate audit command.

Library-prep engine that analyzes DJ tracks and suggests Serato cue points (A–H layout) plus two clean 4-bar loop ideas. It is a **prep app**, not a booth player: run it on the library before a gig, review the picks, then (later) write approved cues into Serato.

A native Mac app (Xcode) is planned once the logic is proven. Per-song analysis JSON (see `docs/ANALYSIS-FORMAT.md`) is designed so **Booth Buddy** can read the same results later without coupling the two codebases.

Private repo: `Cullenambrose/sound-and-service-autocue`. Separate from the booking site (`dj-av-booking`) and the iOS app.

## Cue layout (approved)

### Pop/Vocal 8-pad spec (v6.2 explicit-unit labels)

| Pad | Slot | Name | Where | Colour |
|-----|------|------|-------|--------|
| 1 | A | `Intro 8 bars (32 beats)` (4/2/1 bars if shorter) | lead-in before START's structural anchor, not necessarily Bar 0; `bar_time(k) = pos0 + k·240/bpm` | `#CC0000` |
| 2 | B | `START` | exact un-snapped lead-vocal onset (Demucs stem + WhisperX first word, refined within −1.2 s/+0.5 s of the downbeat); `start_anchor_bar` keeps the bar | `#CC8800` |
| 3 | C | — | empty | — |
| 4 | D | `GO 8 bars (32 beats) > EXIT` | EXIT − 8 bars, or an allowed shorter lead with the actual distance in its label | `#CCCC00` |
| 5 | E | `Mix 4 bars (16 beats) > EXIT` | EXIT − 4 bars, or an allowed shorter lead with the actual distance in its label | `#CC00CC` |
| 6 | F | `EXIT 1st Chorus` | downbeat where the first chorus ends (synced-lyric repetition + 4-bar vocal/RMS drop-off) | `#00CC00` |
| 7 | G | `GO 8 bars (32 beats) > OUT` | OUT − 8 bars, or an allowed shorter lead with the actual distance in its label | `#00CCCC` |
| 8 | H | `OUT` | drum-outro edits: final full bar; fade-outs: last full-volume 4-bar phrase before RMS drops >6 dB | `#8800CC` |

The Serato BeatGrid is always the bar ruler; AutoCue never shifts its phase to match legacy Mixed In Key cues. Turn the new algorithms off with the `pop_v61` switch; the older VOCAL layout is `cue_maps.legacy_v7_vocal_map()`. Code: `engine/pop_cues.py`, tests: `engine/test_v61_pop_cues.py`.

These are structural bar counts in 4/4, not a certification of speech-free
mixing windows. START may be an unsnapped pickup before its anchor. Intro and GO
preferences are independent. Exact known v6.1 label templates migrate when settings
are normalized; saved cue plans and existing Serato tags require reanalysis/review.
Unmapped legacy `GO -N` countdowns are blocked from export rather than silently moved.

**Single-track QA write (DJ MacBook Pro only, Serato closed):**

```bash
git pull origin v6
.venv/bin/python scripts/apply_single_track_qa.py --dry-run \
  --track "/Users/cullenambrose/Music/Master Music/Van Morrison - Into The Mystic (Re-Drum) - 5B - 84.mp3"
# then the same command without --dry-run to write
```

The script refuses Track #1 (`Happy (N And D Chorus Intro)`), non-MP3 files and a running Serato. It makes a SHA-256-verified backup in `~/Music/AutoCue Backups/`, writes `Serato Markers2` and `Serato Markers_` together, checks that the audio stream hash is unchanged, then reads the tag back and prints all 8 pads.

### Earlier approved layout (reference)
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
web/              Local web library, crate browser, review panel, and backup-gated MP3 writes
docs/             Specs, scores, install notes, Google Serato handover
config/           Learned profiles + genre bucket params (no keys)
```

## How to run (high level)

1. Install analyzers per `docs/INSTALL.md` and `docs/SONGFORMER-ON-MAC.md` (SongFormer needs disk space on the Mac).
2. For optional Gemini assistance, configure `GEMINI_API_KEY` in the environment or a local
   `.env.local` file (copy `.env.example`). Never commit the key. See [Gemini configuration](#gemini-configuration-and-local-spending-controls).
3. Source `engine/env.sh` (adjust `PROOF` to your checkout) and run the pipeline scripts under `engine/` (e.g. feature extract → `rules_v5.py` / build → review).
4. Start the local web library and review interface:

```bash
./run.sh
```

Open `http://127.0.0.1:8770`. The app keeps its SQLite library, staged audio copies, jobs, and corrections in the ignored `web-data/` directory. It binds only to loopback. Music is not uploaded; dragged/picked files are copied to the local app for analysis and their originals are left in place. Paste a local folder path or use the crate picker to link originals for Finder access and Serato writes.

To move the local library, stop the server and copy the **whole** `web-data/` directory,
then set `AUTOCUE_WEB_DATA` to its new location before restarting. Staged audio is stored as
library-relative `audio/<hash>.<extension>` paths; startup migrates legacy absolute paths to
the staged copy in the current library, even if an old copy still exists elsewhere.
Missing or invalid staged files are reported in the track row. Original/source links remain
separate absolute paths: moving the library does not relocate your music or retarget Serato
writes. Playback, analysis and removal never fall back to the original. Symlinked staged files
or staged audio directories are rejected. Removal reports deletion failures and retains
failed entries; a partial batch error states how many entries were removed.

Install the small Serato-tag dependency once to enable backed-up writes:

```bash
python3 -m pip install -r web/requirements.txt
```

The crate browser reads Serato `.crate` files under `~/Music/_Serato_/Subcrates` and mounted `/Volumes/*/_Serato_/Subcrates` folders without editing them. Serato writes are MP3-only, require a locally linked original, require a saved review, and require Serato to be closed. Each batch first zips discovered `_Serato_` folders and exact original MP3 files to `~/Music/AutoCue Backups/`; the app verifies the archive and hashes before writing. Undo restores the exact originals only when files still match the post-write hashes. The Serato `_Serato_` folders are backed up but are not changed by this app.

**Fresh-audio processing is not yet wired to the web job runner.** The v6 planner consumes cached v5 analysis; the repository has no portable one-file pipeline that turns a newly imported arbitrary file into that cache. Until that integration is added, queued jobs report this limitation in the track row rather than inventing cues. The runner can invoke an explicitly configured `AUTOCUE_PROCESSOR` argv command; each command argument may contain `{input}`, `{output}`, or `{hash}` placeholders, and the processor must write v6 JSON with a `cues` object to `{output}`. This is an integration seam, not a built-in engine command.

Corrections stay local in `web-data/corrections.json` (the standalone review UI's `corrections.json` is also gitignored). Feed the marks back through the learner scripts to tune the profile in `config/`.

## Ground truth

**Do not** treat the old Serato Markers2 on the 20-song test set as Cullen’s hand cues — those lined up with Mixed In Key energy cues. Scoring and learning use **Cullen’s review-page marks** (`corrections.json` → `learner_marks.py` → `config/learned_profile_marks.json`). See `docs/STATUS.md`.

### Current v7 evaluation

Open **Experiments** after analyzing and saving human reviews. Automatic predictions are frozen
before editing. The rules-only baseline and optional Gemini-assisted result remain separate,
including after partial reviews or reanalysis. Reviews retain cue meanings, custom names, grid
offsets and explicit empty decisions. Omitted review targets are unknown, not correct empty cues.

The report separates positioned-target accuracy (within one reviewed beat) from empty-target
correctness. Timing comparisons exclude different cue meanings/maps; map classification is
reported separately when known. Exports include answer keys, saved predictions, per-cue errors,
exclusions, skipped tracks and recorded new Gemini cost for those runs—not lifetime billing.
Old assisted plans without an independent rules-only snapshot cannot supply an unassisted baseline.
No reviewed targets means no accuracy estimate. These are development/review metrics, not a
held-out musical-accuracy benchmark.

Use **Export all data** in Experiments to download the JSON, then score it locally:

```bash
.venv/bin/python engine/score_v6.py --library-export "/path/to/autocue-experiments.json" --report "/path/to/evaluation.md"
```

The legacy `--v6-dir` / `--marks` scoring mode remains available. Current web plans still use
some historical `_v6` module filenames.

## Read-only library inventory

Before importing or processing a large library, inventory **explicitly selected** directories:

```bash
.venv/bin/python engine/library_inventory.py --database web-data/inventory.sqlite3 scan --root "/path/to/sample-music" --limit 10
.venv/bin/python engine/library_inventory.py --database web-data/inventory.sqlite3 list
.venv/bin/python engine/library_inventory.py --database web-data/inventory.sqlite3 resume SCAN_ID --limit 100
.venv/bin/python engine/library_inventory.py --database web-data/inventory.sqlite3 status SCAN_ID
```

Repeat `--root` for disjoint directories. The dedicated inventory database must be **outside**
every source root. It is separate from the web library; this command does not import songs.
Mutagen reads tags/audio headers only: no audio copying, decoding, models, network requests,
cue generation or tag writes. The existing `web/requirements.txt` supplies Mutagen.

JSON progress includes the scan ID, per-kind/state counts, discovered audio bytes and up to
20 file/directory errors (with an explicit truncation flag). Imported-annotation errors and
unsupported fields have separate counts and up to 20 concise diagnostics, also with a truncation
flag; raw payloads never appear in progress output. All entry details remain in the
local SQLite `entries` table. `--limit` counts attempted audio files, including failures; it
does not limit directory enumeration. Ctrl-C pauses at a durable checkpoint. Resume skips
finished entries; `list` recovers scan IDs after a crash. Concurrent writers are rejected.
Exit codes: 0 for complete/paused, 2 for complete with entry or annotation errors, 1 for a failed operation,
130 for Ctrl-C. Invalid CLI arguments also use argparse's exit code 2.

Symlinks and nonregular files are skipped. Queued paths cannot follow substituted directory
symlinks; missing/replaced roots fail visibly. Files changed during discovery/read are errors,
not trustworthy metadata. A scan observes each entry at its read time, not one atomic library
snapshot. Start a **new scan** to observe later edits, additions/removals, changed files or
previous entry errors; only interrupted/unfinished work resumes. Counts are paths, not unique
recordings.

After a scan completes, create a reproducible dataset snapshot with local file hashing:

```bash
.venv/bin/python engine/dataset_splits.py --database web-data/inventory.sqlite3 \
  freeze SCAN_ID --out "/path/outside-source-roots/dataset-v1.json" \
  --seed "benchmark-v1" --ratios 0.7,0.15,0.15
```

This reads each completed audio file to compute its SHA-256; it does not decode, copy, upload,
or modify audio, tags, or inventory rows. Byte-identical copies become one track record with all
source paths retained. Distinct files with the same normalized artist/title (after recognized
mix/version suffixes) are grouped so they cannot leak across splits. The deterministic hash-based
assignment is reproducible for the same metadata, files, seed and ratios; actual split percentages
can vary with group sizes. The output path must be new and outside every scanned root.

Metadata-based groups are candidates, not verified song identity. Inspect the JSON `groups`
before using the split for evaluation; the manifest marks these groups as requiring review.
Start from a new completed inventory scan to capture library changes, and use a new output path
for each frozen snapshot.

### Versioned beat/bar feature cache

After selecting and checking the dataset split, extract local per-bar energy features using either
a constant-tempo imported Serato grid or an explicit grid JSON keyed by track SHA-256:

```json
{
  "<64-character-audio-sha256>": {
    "t1": 0.25,
    "bar_s": 1.875,
    "offset_ms": 0,
    "source": "beat_this_downbeats",
    "confidence": 0.9,
    "human_verified": false
  }
}
```

`t1` and `bar_s` are in seconds; the effective grid origin is `t1 - offset_ms / 1000`.
The JSON grid overrides imported grids for matching tracks. Without an override, only a
constant-tempo Serato BeatGrid with a supported tempo can be used, and it remains explicitly
unverified. Missing, conflicting, malformed or variable-tempo grids are skipped with a quality
flag instead of guessed.

```bash
.venv/bin/python engine/feature_cache.py \
  --database web-data/inventory.sqlite3 \
  --cache-dir "/path/outside-source-roots/features-v1" \
  run --dataset "/path/outside-source-roots/dataset-v1.json" \
  --grids "/path/outside-source-roots/grids.json" --limit 20
.venv/bin/python engine/feature_cache.py \
  --database web-data/inventory.sqlite3 \
  --cache-dir "/path/outside-source-roots/features-v1" status
```

Extraction decodes locally through FFmpeg and reuses the existing 50 fps RMS, peak and
low/mid/high-band feature calculation. Each compressed NPZ artifact contains bar indices, start
times, frame coverage and per-bar mean energy/peak values, plus its grid, extractor, FFmpeg and
base-feature version and quality metadata. A locked SQLite checkpoint tracks
queued/running/done/skipped/error states; rerunning the same command resumes unfinished tracks,
while `--retry-errors` explicitly retries failures. `--limit` bounds attempted tracks.
Input-manifest, grid, extractor settings and FFmpeg version
are pinned to the cache directory; changed inputs require a new directory. Feature caches are
local only, do not modify audio/tags/inventory, and are never uploaded. This is a deterministic
feature extraction layer, not a musical event classifier or a benchmark result.

### Existing cue/grid evidence

New scans retain `metadata_json.imported_anchors` in each audio entry:

- Native ID3 GEOB fields in MP3/AIFF/WAV: Serato Markers2 v1.1 cues/loops and BeatGrid v1.0
  markers, including variable-tempo grids. FLAC `SERATO_MARKERS_V2` and M4A
  `----:com.serato.dj:markersv2` wrappers also expose Markers2 cues/loops.
- Cue indices, seconds, names, colours and loop endpoints are preserved, **not** mapped to
  AutoCue pad meanings. An unset timestamp stays null, not a cue at song end.
- Every source records its container/field, extractor version, original payload and SHA-256.
  Unknown marker entries are preserved. Unsupported legacy `Serato Markers_` and CuePoints JSON
  (raw or base64) remain visible as unsupported evidence. CuePoints schema/units are not guessed.
- All imported authorship remains `unknown`, with `human_verified: false`, even when a label
  says “Mixed In Key” or “DROP.” A Serato field identifies storage format, not who set the cue.
  Imported cues are neither training answer keys nor replacements for generated/reviewed cues.
- Malformed versions, truncated tags, conflicting duplicate fields/indices and out-of-duration
  markers report annotation errors without discarding the readable track metadata or original tag.
  Tags above 1 MiB report an error and retain only size/hash, not the oversized raw payload.

This is a **local inventory**, not yet a web-editor imported-cue overlay or trained detector.
Metadata extractor v2 adds this evidence without changing the SQLite schema. Finished v1 inventory
entries are not retroactively reread; start a new scan to collect their annotations. The importer is
verified against generated container/tag fixtures, not yet against your actual Serato/MIK library.

### Switchable imported-anchor candidate experiment

`engine/anchor_detector.py` compares a deterministic audio-only candidate pool with that pool
augmented by imported Serato Markers2 cue positions. It reads a frozen dataset and completed B04
feature cache; it does not decode audio again, alter tags, infer event types from marker labels, or
train a model. The audio baseline scores boundaries with four preceding bars and two following
bars; it uses RMS and low-band energy ratios with a fixed threshold of 1.3. Incomplete bar coverage
is skipped. Imported cue times are retained exactly and accompanied by the nearest grid bar and
their local audio evidence, when available. Identical candidate positions merge while retaining
audio and all imported-marker provenance. Imported loops and unsupported formats are not
classified as cues.

```bash
.venv/bin/python engine/anchor_detector.py \
  --database web-data/inventory.sqlite3 \
  --dataset "/path/outside-source-roots/dataset-v1.json" \
  --cache-dir "/path/outside-source-roots/features-v1" \
  --reviews "/path/autocue-experiments.json" \
  --detector both \
  --out "/path/outside-source-roots/candidate-report.json"
```

`--detector` accepts `audio-only`, `anchor-assisted`, or `both` (the default). `--reviews` is
optional and accepts the schema-2 **Export all data** experiment export. Only explicit
`human_review` answer keys are used, and review tracks are joined to the frozen manifest by
full-file SHA-256; files must have identical bytes for the hashes to match. Candidate recall
defaults to within four beats of a positioned reviewed cue and can be changed with
`--tolerance-beats`. The report also compares coverage of targets changed from the saved baseline
(meaning differs, position differs by more than one beat, or baseline position is absent).
Exports without a saved baseline mark correction status as unknown rather than treating targets
as uncorrected.

These are candidate-coverage diagnostics, not event classification, measured reductions in review
work, held-out accuracy, or proof that review targets were independent of imported markers.
Imported labels are preserved as provenance only. No real-library run is included in validation;
actual sample compatibility, grid quality, and musical benefit still require human review.

### Human-reviewed event labels

In the local library, open **Review** for a track and use the waveform/playhead to add one of
`drop`, `build`, `breakdown`, `chorus`, or `other`. Each point records its exact time in seconds,
optional bar position, a snapshot of the active grid/offset, certain/uncertain certainty, a note,
human-review provenance, and a UTC timestamp. Labels are independent of cue edits and imported
Serato markers. They remain local in the library database.

Track review states are **Not reviewed**, **In progress**, **Complete**, **Uncertain**, and
**Skip this track**. Complete means the reviewer listened through the track; zero labels is an
explicit decision that no target event was found. Uncertain and skipped tracks remain explicit
states, not negative examples. Retracted annotations are hidden from active review but retained in
export history with their retraction timestamp. The **Export labels** link downloads a versioned
JSON snapshot suitable for later dataset building; it never uploads audio or labels. Removing a
track from the local library deletes its annotations too, so export first if they should be kept.

### Held-out event benchmark

After collecting completed event reviews on the test portion of a frozen B03 dataset, export the
event labels from the local app and create a B05 candidate report with `--detector both`. Then run:

```bash
.venv/bin/python engine/event_benchmark.py \
  --dataset "/path/to/frozen-dataset-v1.json" \
  --reviews "/path/to/autocue-event-labels.json" \
  --candidates "/path/to/candidate-report.json" \
  --pools audio_only,anchor_assisted \
  --out "/path/to/event-benchmark.json"
```

The benchmark joins only by exact full-file SHA-256 and scores only tracks assigned to the frozen
test split whose event-review state is **Complete**. Related-mix group membership is retained and
reported; the manifest still flags metadata-derived groups for human review. It excludes uncertain
event labels from positive targets and excludes candidate points near those uncertain labels from
false-positive counts. A completed review with no certain labels is explicit and contributes
unmatched candidates as false positives; incomplete, skipped, and uncertain tracks are excluded,
not treated as negatives. Timing tolerance defaults to four beats from the review grid, or two
seconds for gridless reviews, and both settings are recorded in the report.

This measures generic candidate coverage, timing error, and unmatched candidates—not event-type
classification or model promotion. Candidate pools must not be tuned against the held-out test
results. A software fixture passing is not evidence of musical accuracy; without completed
human-reviewed test tracks, the report explicitly has no benchmark estimate.

### Local learning dataset preparation

`engine/event_dataset.py` combines frozen B03 split assignments, verified B04 cached bar features,
and a B06 event-label export. It reads existing local artifacts only: no new audio decode, models,
uploads, or tag writes. Builds include at most 20 feature-ready tracks by default:

```bash
.venv/bin/python engine/event_dataset.py \
  --database web-data/inventory.sqlite3 \
  --dataset "/path/to/frozen-dataset-v1.json" \
  --cache-dir "/path/to/features-v1" \
  --reviews "/path/to/autocue-event-labels.json" \
  --out "/path/outside-source-roots/learning-dataset-v1.json" \
  --limit 20
```

The immutable JSON snapshot records input/artifact hashes, builder/feature versions, grouped
train/validation assignments, grid quality, RMS/low-band energy/coverage and local energy-change
evidence. Test tracks are reserved: their features, labels and imported markers are not included.
`--limit` bounds included feature tracks; deferred and unavailable tracks are explicitly counted.
Larger runs still need a measured storage/compute budget.

Each bar has separate weak and human label records. Audio energy changes and imported markers
are **weak, untyped candidates**, never automatic drop labels. Only certain, active annotations
from completed human reviews produce `event_target` values; original seconds, notes, timestamps
and annotation grids remain intact when mapped to the feature grid. Uncertain annotations mask
their nearest bar, incomplete reviews are preserved without a target, and skipped/uncertain
tracks are excluded. Partial bars below 95% frame coverage cannot be learning/evaluation samples.

Training and validation eligibility are separate; weak labels cannot become validation targets.
Unmarked bars always have `event_target: null`, even on a completed zero-label review. **No
negative examples are generated.** This is positive/weak/unlabeled dataset preparation, not an accuracy result. The C02 ranker below
uses only explicit positives and treats all other bars as unknown, not reviewed negatives.

### Positive-only event candidate ranker

`engine/event_ranker.py` trains a deterministic, lightweight class-wise prototype ranker using
only eligible, certain human-positive training labels. Weak candidates, unlabeled bars and
validation/test labels do not fit the prototypes. Training writes a new immutable JSON artifact;
validation diagnostics rank weak candidate points against positive targets:

```bash
.venv/bin/python engine/event_ranker.py train \
  --dataset "/path/to/event-learning-v1.json" \
  --seed "event-ranker-v1" \
  --out "/path/to/event-ranker-v1.json"

.venv/bin/python engine/event_ranker.py rank \
  --dataset "/path/to/event-learning-v1.json" \
  --model "/path/to/event-ranker-v1.json" \
  --split validation --top-k 20 \
  --out "/path/to/event-candidates-v1.json"
```

The model records the dataset hash, feature schema, parameters, training groups and group-count
sufficiency warnings. Its log-energy features use robust training-positive scaling; class
prototypes are diagonal positive-only distributions. Similarity values are uncalibrated ranking
scores—not probabilities, event classifications or confidence—and the ranking report intentionally
does not emit event predictions. Validation recall@k is a development diagnostic, not held-out
accuracy. Ranking requires the exact dataset snapshot used for training, and test groups remain
reserved and feature-free. No model promotion or musical-accuracy claim is supported until
completed human reviews exist for a held-out B07 benchmark.

## Gemini configuration and local spending controls

Gemini remains opt-in and sends structured text only, never music/audio. Both the built-in
pipeline and `engine/gemini_v6.py --enable` use the same credential resolver and request ledger.
Precedence is shell environment, then `.env.local`, then `.env` in the repository root.
`AUTOCUE_ENV_FILE=/absolute/path/to/local.conf` selects an explicit file instead of the two defaults.
Only `GEMINI_API_KEY` and `GEM_MODEL` are read; files are parsed as data, never sourced or executed.
The normal `./run.sh` launch needs no shell `source` step. Settings shows configuration source,
model and spending status, never the key. Configured does not mean valid or live-tested.

Check status **without a network request**:

```bash
.venv/bin/python engine/gemini_assist.py --status
```

The current supported request/pricing profile is `gemini-3.1-pro-preview`: standard input
$2/million and output (including thinking) $12/million for these bounded short prompts.
Pricing was checked against [Google's model-specific pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-pro-preview)
on October 21, 2026; verification expires December 1, 2026. Unknown models or expired pricing
disable requests rather than silently assuming the same prices. This is not a claim that this
preview model remains available to every account.

Assistance runs only for eligible uncertainty or map ambiguity. Requests include cue meanings,
beat/bar units, grid, mix preferences, vocal-boundary evidence and compact energy neighborhoods.
They use low thinking, bounded output, a structured response schema and a versioned cache.
Rules-only plans are retained for comparisons; a cache hit reports zero newly incurred cost.

The default $3 limit is **local lifetime spending per cache directory**, not per song or batch.
Built-in state is under `~/Library/Application Support/AutoCue/cache/gemini` (or
`AUTOCUE_CACHE_DIR/gemini`). A process lock serializes requests and a durable ledger reserves
input/output/reasoning cost before a call. Actual usage is recorded even for invalid JSON;
overruns block further paid work. Missing/corrupt accounting, uncertain transport failures and
interrupted writes fail closed. No automatic paid retries occur. External programs, other
cache directories, changed provider prices and provider billing remain outside this limit.
Set provider-side billing controls too; the application is not a billing guarantee.

After a failed/uncertain request, check provider usage before releasing its reservation:

```bash
.venv/bin/python engine/gemini_assist.py --status --cache-dir "/path/to/cache/gemini"
.venv/bin/python engine/gemini_assist.py --reconcile-cost 0.012 --cache-dir "/path/to/cache/gemini"
```

Use the **verified cost**, not the example amount. Zero is valid only if no charge was confirmed.
The reconciliation is retained in the ledger. Actual-cost/pricing overruns remain blocked for
investigation; never delete accounting files to get around a spend limit.
No live request is part of these status/reconciliation commands.


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

## v6 (separate, switchable plan)

`engine/rules_v6.py` reads cached v5 analysis and writes a separate eight-cue DROP or VOCAL plan. The switch defaults and genre phrase lengths are in `config/genre_params_v6.json`. It does not change v5 defaults or write to Serato/audio. Synced LRCLIB lookups and WhisperX word timings are cacheable from a manifest; Gemini is an explicit opt-in (`engine/gemini_v6.py --enable`) and is disabled by default. Review data can be built beside v5 with `engine/build_v6_review.py`; usage and scoring are in `docs/ANALYSIS-FORMAT.md`.


## Fresh-audio pipeline (new songs, no cached analysis)

`engine/analyze.py` takes one audio file and produces a v6 plan with no manual steps. The web app calls it for every imported song, so a dropped MP3 goes straight to **Cues ready** or **Needs a look**.

Steps (the chips on the page): Beats (Beat This) › Sections (all-in-one) › Vocals (Demucs stems) › Lyrics (WhisperX word timing + LRCLIB synced lyrics) › Drops (kick/bass energy) › Cues. Results are cached by content hash in `~/Library/Application Support/AutoCue/cache/<hash>/` using the v5 cache layout, so nothing is analyzed twice (override with `AUTOCUE_CACHE_DIR`).

- **Install:** `./setup.sh` (Apple Silicon; creates `.venv`, uses MPS where supported). Check what is present with `.venv/bin/python engine/analyze.py --check`. It needs Python 3.10+ (macOS's built-in 3.9 is too old for Beat This) and ffmpeg.
- The installer also pins NumPy 2.1–2.2 and SciPy 1.14.1: SciPy 1.15's PROPACK binary fails to load on this macOS 27 runtime. `madmom` is built with Cython; allin1's older NATTEN and NumPy APIs are adapted at runtime, so no system Python packages are patched.
- **Disk / downloads:** about 3.5 GB of packages, plus first-run models: Beat This ~80 MB, Demucs ~80 MB, WhisperX ~0.6 GB, all-in-one ~0.2 GB. Roughly 5-6 GB total without SongFormer.
- **Missing analyzers:** the page and CLI name the analyzer and the install command. Without the beat model there is no bar grid, so the song fails with that message. Without Demucs, all-in-one, or WhisperX the step is skipped, the plan is flagged, and cue confidence is capped (0.55 without sections or stems, so the song lands in "Needs a look"; 0.9 without word timings). Cues are never invented.
- When no outro section is labeled, a sustained energy or vocal fade in the last 32 bars can supply lower-confidence **OUTRO** and **GO -32** cues; otherwise those cues remain blank. The C/D fallback labels follow the selected map, and legacy `CHORUS` labels are replaced rather than written.
- **Speed:** a 3-minute synthetic click track on this Apple Silicon Mac (MPS) took about 9 s for beats and about 73 s for Demucs, roughly 87 s total before all-in-one and WhisperX. A real 3:39 song required about 116 s for beats, 83 s for Demucs, 140 s for WhisperX (including model load), and 121 s for all-in-one sections on the first run; later runs reuse the hash cache. These are one-song observations, not a performance guarantee. Batches run one song at a time. Demucs on MPS is given a time limit (`AUTOCUE_DEMUCS_TIMEOUT`, default 600 s) and then retried on the CPU.
- Beat This retries on CPU if MPS reports out-of-memory; it does not disable PyTorch's MPS memory limit. Downbeat fitting rejects local false/duplicate detections before building the beat grid.
- **SongFormer license:** SongFormer's MuQ weights are licensed for non-commercial use only. They are optional and not installed by `setup.sh`; the pipeline runs fully without them, and a sellable version must keep them off.

## Algorithm experiments (compare ideas on your own songs)

Every analysis keeps the baseline v6 plan plus one plan per switched-on experiment, in `plan.variants`
(see `engine/experiments.py`): low-band timing fix, fuzzy lyric matching, kick-snapped grid, stem-energy
sections, and optional RoFormer vocals (slow; `pip install audio-separator`). Per-bar stem energies are saved
in `plan.bar_energy`. In the review panel, toggle variant lanes and click a variant's time to use it. Save your
review; saved cues are the answer key. **Experiments** in the top bar scores each variant (within one beat),
split by VOCAL and DROP, with fixes and breaks vs baseline, and exports everything as JSON. Variants only exist for
songs analyzed after an experiment is on, so re-analyze older songs. `rapidfuzz` is used for fuzzy matching when
installed, with a `difflib` fallback. Genius lyrics are not included: they need an API key and have no timestamps.

## Serato writes

Writes need a saved review, Serato closed, and a verified backup of the `_Serato_` folder and each song's original file. Supported formats: MP3, AIFF (ID3 `Serato Markers2` **and** `Serato Markers_`, written in lockstep because Serato prefers `Markers_` when present), FLAC (`SERATO_MARKERS_V2`) and M4A (`----:com.serato.dj:markersv2`). Other formats show "Write not supported yet". Every existing hot cue on pads 1–8 (including Mixed In Key `Energy N` cues) is replaced; loops, cues 9+ and other foreign entries are preserved, and the audio data is hash-checked before and after. **Undo this batch** restores one write; **Restore everything** (typed `RESTORE`) restores the song files and the `_Serato_` folder from a backup. FLAC and M4A writing follows the published Serato tag layout and is verified by read-back and audio-hash checks, but has not been opened in Serato itself.

Real `.crate` copies can be placed in `tests/fixtures/crates/` (gitignored); the tests parse them read-only when present.
