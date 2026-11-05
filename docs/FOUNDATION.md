# Personal DJ app foundation

## Implementation plan (November 4, 2026)

Primary plan: the AutoCue-specific foundation roadmap supplied for this milestone.
The checkout has advanced since that review: the supported surface is `web/`,
with `engine/analyze.py` as the Python worker and SHA-256 file identities already
in use. Do not replace these with the older numbered research pipeline.

1. Preserve commit `cf38ec3` as the starting behavior and work on
   `dev/personal-dj-foundation`. Add synthetic regression evidence; do not infer
   real-world cue accuracy from those tests.
2. Commit cue plans, correction events and retry receipts in one SQLite
   transaction. Detect stale revisions and preserve existing correction files.
3. Keep dataset-scoped browser drafts and pending requests until a matching
   committed receipt is received. Expose failures and recovery.
4. Share correction ordering/clear/re-mark semantics with scoring and learners.
   Begin explicit project configuration without rewriting historical experiments.
5. Add lightweight CI, document supported commands and limitations, run the
   regression suites and commit locally. No push, merge, audio upload, paid AI
   request or production music/tag write.

## Baseline before implementation

Clean worktree, branch `v6`, starting commit `cf38ec3`.
Executed using the existing `.venv`: 311 engine unittest cases, 42 web unittest
cases and 16 Node tests passed. The original rule script also printed 13 passes.
Heavy model inference and listening accuracy are not covered by this baseline.

Historical `review-ui/`, fixed-path research supervisors, old scorers and
`docs/google-handover/` remain available as research material, not supported app
entry points. In particular, do not run the old review server to collect new
corrections. Current musical behavior remains a review candidate, not an
approved production auto-cue algorithm.

## Implemented boundary

The Python cue algorithms, labels, timing and export rules have not been retuned.
`tests/baselines/cf38ec3-pop.json` freezes the starting commit's grid and all eight
pads (including the old label on empty C) using an invented lyric/envelope recipe
from `engine/test_v61_pop_cues.py`. It requires no library files, models or network.
Existing synthetic tests still cover older rule generations. Plain v2 test
functions now participate in unittest discovery; original rule-script failures
now fail the runner instead of merely printing.

New `engine/review_store.py` owns the cue-review journal and receipts. The web
server commits the edited plan, answer key, revision, ordered events and receipt
together under `BEGIN IMMEDIATE`. A database trigger advances the revision when
any plan changes, including reanalysis and map changes. Reanalysis preserves the
previous human answer key; it does not mark new proposals reviewed.

Tracks retain their existing exact-file SHA-256 identity. Renaming or relocating
unchanged bytes does not create another track. A persistent dataset UUID lives
in SQLite settings; copying the whole library retains it, while a new library
gets a different UUID. Proposal hashes (`analysis_id`) bind new review events to
the analyzed file and proposal, rather than numbered research fixture names.
This is **not** audio fingerprinting: editing tags changes the file hash.
Separate recording/version entities and multiple physical copies belong to the
catalog/library milestone, not this schema migration.

## Save and recovery contract

`GET /api/session` returns the local session token and dataset UUID.
`GET /api/library` includes each track's `review_revision`.
`PUT /api/tracks/<hash>` requires:

```json
{
  "request_id": "a-unique-client-generated-UUID",
  "dataset_id": "UUID-from-session",
  "base_revision": 1,
  "cues": {"B": {"name": "START", "bar": 8}},
  "map_type": "VOCAL"
}
```

Success returns `ok`, the same `request_id`, `dataset_id`, `track_hash`, committed
`revision` and last event `sequence`. The exact same request returns its original
receipt even after a restart or later edits, without reapplying the edit.
Reusing an ID with different content, changing datasets or saving against a stale
revision returns **409**, never an overwrite. Storage errors return **503** and
roll back the entire save. Invalid input returns **400**. Older clients without
request IDs must be updated; reload the app after updating.

The browser stores drafts and pending requests under dataset + content identity.
It checks HTTP errors and matching receipts. A dropped response keeps the exact
pending request for **Save review** to retry, including after reload. New edits
made after a failure are sent only after resolving the old request. Saving,
unsaved and persistent failure states are distinct. Save/next/close do not discard
failed saves; leaving warns. Storage quota/access failures are visible.

**Recovery:** reopen the same app origin and track to recover a draft. Retry a
transient failure with Save review. For a revision conflict, use **Download draft**,
then close and explicitly discard the browser draft, reopen the track to inspect
the latest saved cues, and manually reconcile the downloaded changes. There is no
silent rebase or auto-merge. Keep the download until the reconciled save succeeds.
If another tab changes the browser draft, the stale tab reports the conflict
instead of deliberately replacing/deleting that saved draft; download its edits.
Prefer one editing tab per track. Browser storage is a recovery aid, not a backup:
clearing it or changing origin/port makes drafts unavailable.

## Correction migration and shared semantics

Stop the existing server and back up the **whole data directory** before first
launch. Startup imports `data_dir/corrections.json` once, transactionally, in
stable historical order. The original file is never rewritten or removed.
Invalid JSON/array shape aborts startup rather than reporting a successful import.
Files changed after migration are not automatically reimported. Historical review
folders are not scanned or merged automatically.

New events are authoritative in SQLite, not that old JSON snapshot.
`GET /api/corrections` returns a portable ordered JSON array for scoring; download
it to an explicitly chosen file if needed. Journal history survives removal of
tracks for audit; a removed track is not silently recreated by a retry.

`engine/marks.py` is the shared reducer used by current scoring and historical
mark learners/scorers/few-shot consumers. Sequence order wins over clocks.
Legacy timestamp ties preserve input/file order (lost historical ordering cannot
be reconstructed). Only `correct` and `set_here` confirm truth; nudges remain
drafts until confirmed; `clear` removes truth; a later confirmation re-marks it.
All eight pads are supported; empty clears remain in the journal and answer key.
Legacy numeric aliases `1`/`01` resolve together. Unscoped legacy events cannot be
silently combined with other datasets. Select a dataset explicitly when calling
the reducer on multiple datasets; contradictory IDs/sequences fail visibly.

## Explicit configuration

Default app data remains `<checkout>/web-data`; the default analysis cache remains
`~/Library/Application Support/AutoCue/cache`. Existing `AUTOCUE_WEB_DATA` and
`AUTOCUE_CACHE_DIR` overrides continue to work. Alternatively copy
`config/project.example.json` outside Git and set:

```bash
AUTOCUE_PROJECT="/absolute/path/to/project.json" ./run.sh
```

JSON keys: `data_dir`, `cache_dir`, `research_root`. Relative paths are relative
to the configuration file, not the current directory. Environment overrides win.
Malformed or missing configured files fail explicitly. The shared marks loader
requires explicit paths or `research_root` / `AUTOCUE_RESEARCH_ROOT`; it no longer
silently reads an old `/workspace` tree.

This begins path configuration; the supported worker is now packaged separately
(see [the worker handoff](WORKER.md)). Many historical
research scripts still embed their original paths and import-time execution.
Do not run those supervisors, paid Gemini scripts or Google handover writers as
part of the supported workflow.

## Verification commands

Python 3.10+ and Node 22+; no model packages are required for lightweight tests.
Use a disposable virtual environment:

```bash
python3 -m venv /path/to/disposable-test-env
/path/to/disposable-test-env/bin/python -m pip install -r requirements-test.txt
/path/to/disposable-test-env/bin/python -m unittest discover -s engine -p 'test_*.py'
/path/to/disposable-test-env/bin/python -m unittest web.test_server
node --test web/test_*.cjs
```

FFmpeg enables the optional real-decoder synthetic test; model compatibility
checks skip when their optional packages are absent. Tests use synthetic audio,
temporary databases and disposable tag containers. No original library changes
or AI calls are required. The GitHub Actions workflow runs the lightweight suites
on Python 3.10/3.12 and Node 22 without credentials or model downloads.

Validation includes simultaneous independent saves, duplicate concurrent retries,
restarts, stale tabs, reanalysis conflicts, request-ID collisions, wrong datasets,
rollback after event insertion, migration/relocation, clear/re-mark ordering,
malformed receipts and browser storage failures. Browser integration was also
checked against a disposable synthetic library: a response deliberately dropped
after commit showed a persistent failure, reload recovered the edits, and retry
cleared the draft without adding journal events.

Foundation milestone local results: **330 engine cases, 49 server cases and 25 Node cases
passed** in the existing environment. In a separate clean Python 3.10 virtual
environment installed only from `requirements-test.txt`, the engine suite passed
with three optional model-compatibility checks skipped, and all 49 server cases
passed. The clean dependency check caught and corrected a missing SciPy
requirement for waveform tests before finalizing CI.

### Remaining limits and next milestone

- CI is configured but has not run on GitHub (nothing was pushed). A clean local
  environment verifies the lightweight dependency manifest; no heavy model
  install, real-library accuracy evaluation, listening or installed-Serato
  round trip was performed.
- Historical learner/scorer scripts now share correction semantics, but their
  full research runs still need the unavailable historical artifacts.
- The independent musical event annotation API (`event_annotations` /
  `event_reviews`) retains its previous behavior; this retry protocol is for
  **cue correction saves**, not annotation mutations or Serato writes.
- Physical disk-full/power-loss and browser crash recovery are represented by
  injected transaction/storage failures, not destructive system tests.
- No production database was migrated during development. Back up before the
  first real launch; do not use the historical server concurrently.

**Worker milestone:** implemented the root dependency/package contract,
versioned messages and provenance, per-track locks, unique job workspaces,
atomic cache/result replacement, cooperative cancellation and an offline
one-fixture CLI. The web built-in processor now uses the same worker function.
See [WORKER.md](WORKER.md) for implementation boundaries, verification and resume
instructions; the original synthetic cue baseline remains unchanged.

**Next concrete milestone: catalog snapshots and exact-version matching.**
The first slice is now implemented: immutable schema-v1 snapshots, selected-folder
read-only indexing, offline/error states and a version-candidate review surface
with durable decision receipts. See [CATALOG.md](CATALOG.md) for a disposable demo,
contracts and remaining stage-3 work. The actual vendor CSV adapter, real-export
reconciliation, durable scan jobs and volume reassociation are not yet complete.
Keep catalog song/version identities separate from exact-file hashes; never
select a remix, clean/explicit version or intro edit from title similarity alone.
Next inspect representative actual exports, then connect reviewed current hashes
to cue review before personal crates, verified playlist export or AI chat.
