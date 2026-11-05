# Catalog snapshots and read-only version matching

## Milestone boundary

This is the first usable slice of roadmap stage 3, not completion of the full
real-catalog import or playlist workflow. It extends the existing local web app
without changing cue rules, exports, Serato tags or the packaged worker.

Implemented plan:

1. Immutable, validated catalog snapshots with source crate/song IDs, raw
   attributes, ordered memberships and explicitly unavailable crates.
2. Read-only selected-folder indexing with separate physical file identities,
   exact-file SHA-256, incremental metadata parsing and offline-root handling.
3. Conservative version candidates and acknowledged, retryable human decisions.
4. An integrated catalog browser, synthetic regression fixtures and CI.
5. Explicit verified staging of reviewed files into the existing analysis/cue
   editor, retaining match provenance and idempotent staging receipts.
6. Durable background index jobs with retry receipts, persisted progress,
   cooperative cancellation and explicit restart recovery.
7. Deliberate offline-root reassociation with full file-byte proof, preserved
   identities and atomic history. This does not enable or retarget tag writes.

The actual saved CSV/catalog exports were not supplied at a known path for this
milestone. Their headers, preambles and identity/link fields have **not** been
guessed. The new importer accepts the explicit JSON contract below; it does not
yet claim compatibility with the vendor's original CSV or index format.
The synthetic 1,423-crate / 81,153-membership test is a volume/reconciliation
check, **not** evidence that the actual collection has been imported.

## Try it without your library

Use the existing test environment; core dependencies suffice.
From the repository root, generate only disposable data:

```bash
DEMO="$(mktemp -d /tmp/autocue-catalog-demo.XXXXXX)"
export DEMO
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from web.fixtures import tagged_wav
root = Path(os.environ["DEMO"]) / "music"
root.mkdir()
for version in ("Clean", "Explicit"):
    (root / (version + ".wav")).write_bytes(tagged_wav(version))
print("Folder to index:", root)
PY
AUTOCUE_WEB_DATA="$DEMO/data" AUTOCUE_PORT=8771 ./run.sh
```

Open `http://127.0.0.1:8771`. Under **Find your exact versions**:

1. Import [the synthetic snapshot](../tests/fixtures/catalog/synthetic.json).
2. Enter the generated `music` folder path and choose **Index folder**. Wait for
   the scan job to show `completed`; indexing continues across browser reloads.
3. Choose the available **Synthetic warmup** crate. The other same-title crate
   remains separately identified and explicitly unavailable.
4. Review the Clean and Explicit candidates. No choice is preselected.
   Matching tags still require human review. The Explicit candidate requires
   an intentional override checkbox and note.
5. Accept a reviewed file or mark missing. Reload and verify the same saved
   decision. Rescan to observe unchanged files being reused.
6. For an accepted match, **Stage reviewed copy** makes a verified copy in the
   app's disposable data folder. It does not start inference. See the staging
   contract below before choosing **Analyze staged copy**.

The generated WAVs are silence with invented tags, not a listening/accuracy
test. No catalog match action copies audio into the analysis library, invokes
models, uploads audio, or writes tags. Staging is a separate explicit action.
Do not run model analysis or Serato controls as part of this lightweight demo.
Stop the server before deleting
only the specific disposable directory created above.

Before using a real app database, stop its server and back up the **whole data
directory**. Startup adds separate catalog/index/receipt tables. Existing cue
tracks, correction journals and analysis caches are not migrated into the index
or rewritten by these operations.

## Snapshot contract

```json
{
  "schema_version": 1,
  "source": "An explicit source/snapshot label",
  "crates": [
    {
      "id": "source-crate-id",
      "title": "Crate title",
      "available": true,
      "curator": "Optional provenance",
      "songs": [
        {
          "id": "source-song-version-id",
          "artist": "Artist",
          "title": "Title",
          "version": "Clean intro edit",
          "bpm": 0,
          "key": null,
          "link": "https://example.invalid/reference"
        }
      ]
    },
    {"id": "unavailable-source-id", "title": "Unavailable source", "available": false, "songs": []}
  ]
}
```

Required: schema version 1 (not a boolean), nonempty source, a nonempty crates
array, distinct crate IDs, titles, explicit availability and songs arrays.
Every song requires a source ID, artist and title. Version defaults to unknown
empty text, not an inferred edit. Optional BPM/duration are finite nonnegative
numbers or null; zero is retained. Extra JSON attributes are retained as source
metadata, including links/category/curator. Links are not fetched or executed.

A repeated source song ID must have identical source metadata everywhere in
one snapshot. Do not invent a shared song ID that collapses two edits. Duplicate
memberships are allowed and keep their original array positions. Duplicate
crate **titles** are allowed; duplicate crate **IDs** are rejected. Unavailable
crates must have an empty song list. An empty available crate remains distinct
from an unavailable crate.

Identity is SHA-256 of canonical JSON: object key ordering/whitespace do not
matter, array order and attributes do. The complete document is retained.
An identical import returns the same ID/counts with `added: false`. Changed
content creates another immutable snapshot; old snapshots and their decisions
remain intact. Decisions are not silently carried across changed snapshots.
All rows commit together, or none do. Validation names malformed fields/IDs;
there is no partial-success importer or silently skipped input row.

## Storage and identities

[catalog.py](../web/catalog.py) owns:

- `catalog_snapshots`, `catalog_crates`, `catalog_songs`,
  `catalog_memberships`: imported source state, independent of user choices.
- `library_roots`, `library_files`: selected folder and physical location UUIDs.
  Two copies with identical bytes have separate file IDs and the same content
  hash. File identity at the same indexed path persists through byte changes;
  that does not make old content decisions valid.
- `catalog_match_decisions`, `catalog_match_receipts`: revisioned decisions and
  immutable retry receipts. Receipts retain the submitted note/override body;
  new decisions do not edit the imported source.
- `catalog_staged_tracks`, `catalog_staging_receipts`: immutable decision-to-hash
  provenance and acknowledged staging operations. Historical links deliberately
  do not reference the mutable/deletable `tracks` row with a foreign key.
- `catalog_scan_jobs`: original submission/receipt, captured folder identity,
  lifecycle/progress, terminal result and errors. Admission receipts remain
  replayable after the job finishes; they never start another scan.
- `catalog_root_reassociations`: immutable job/root linkage, previous root
  record, replacement identity/revision, proof-file count and commit time.

These are not a recording-fingerprint graph. A catalog source song/version is
not the staged `tracks.hash` entity. Renames currently appear as a missing old
location plus a new file location; no automatic relocation or cross-path merge
is attempted. Tag edits change the content hash.

## Scan contract and limitations

Only a caller-selected absolute folder (or leading `~`) is indexed. No automatic
whole-computer scan, copy, rename, delete, audio decode or network lookup occurs.
Supported extensions match the current app. Symlink files/directories are
skipped and reported; overlapping roots are rejected rather than creating
duplicate location identities.

The first scan hashes bytes and reads Mutagen tags. WAV/AIFF ID3 tags and normal
easy-tag metadata are supported. Missing artist/title remain unknown: filenames
are **not** converted to claims about a song. Parsing/read errors are stored and
visible under **Indexed files and metadata errors**, not silently omitted.

Size plus nanosecond mtime/ctime detects changed files for incremental refresh.
Unchanged successful entries reuse hash/metadata; errored entries are retried.
A successfully enumerated folder marks unseen locations missing. Missing or
identity-changed roots are offline and retain all previous file records.
Disconnection/enumeration errors during a scan abort publication.
File status also checks current paths/stat values when viewed.

Root identity currently uses resolved path + device/inode, **not a stable volume
UUID**. This deliberately fails closed on a replacement folder/drive. Drive
reattachment that changes device identity requires explicit reassociation as
described below. Root removal and automatic relocation are not implemented.
Do not edit SQLite to bypass an offline identity check.

Hashing and tag parsing happen outside the SQLite write lock needed by review
saves. A short final transaction publishes the scan and detects another scan
that finished against the same/overlapping root. Partial scans never replace
the previous index. An integer scan revision detects competing scans even with
identical timestamps. Unrelated database writers can proceed during parsing.

Initial scope: at most **10,000 audio paths per root**, held in memory during
enumeration. This is a declared safety bound, not a measured performance target.
Production-scale latency, parser memory usage and network/removable-volume
behavior remain unbenchmarked. The bound has not been raised.

### Durable scan jobs

The supported browser no longer holds a long HTTP scan request open or blocks
other catalog actions while hashing. A dedicated worker polls durable SQLite
jobs independently of the analysis worker. Only one scan runs at a time; other
non-overlapping folders queue in submission order. Pending overlapping scans
are rejected, rather than doing competing disk work. No inference, staging,
tag-writing or network lookup is scheduled by an index job.

`POST /api/catalog/scan` requires exactly:

```json
{
  "request_id": "new-client-generated-UUID",
  "dataset_id": "current-dataset-UUID",
  "path": "/absolute/path/to/selected/music"
}
```

Admission validates the dataset and checks the folder/device/inode outside the
write lock. A short transaction rechecks receipt/overlap and records the job.
`202` acknowledges `ok: true`, every submitted field, and a
server-generated `job_id`. It means **queued**, not successfully indexed. The
receipt and job are one durable record. Retry the identical body after a lost
response: it returns the same admission receipt without checking the source or
rescheduling work, even after cancellation, failure, completion or disconnection.
Request-ID reuse with different content or wrong dataset returns `409`.
Malformed/absent paths return `400`; database failures return `503`.
The former synchronous path-only request shape is intentionally rejected:
clients must supply dataset/request identity and poll for the outcome.

`GET /api/catalog/scans` returns all active jobs plus the 20 most recent terminal
jobs, with `id`, normalized `path`, `status`, `progress`, `result`, `error` and
timestamps. Historical admission receipts remain in SQLite even when no longer
in this recent list. No automatic history/receipt deletion is implemented.
Progress includes phase (`queued`, `walking`, `indexing`, `publishing`), audio
paths encountered, unchanged files reused, error/symlink counts and current
relative path. It is not a percentage or an assertion that encountered files
have already been indexed. Updates are throttled to approximately 250 ms except
phase transitions. Completion carries the full scan result, including errors
and skipped paths; individual tag errors are retained in the file index and
do not masquerade as successfully parsed metadata.

Lifecycle:

- `queued` -> `running` -> `completed` or `failed`.
- Cancelling queued work immediately records `cancelled`.
- Cancelling running work records `cancelling`, then `cancelled` when observed.
- A stopped server marks unfinished running work `interrupted` on next startup;
  pending cancellation becomes `cancelled`. Queued work survives and runs.

`POST /api/catalog/scans/cancel` requires exactly `dataset_id` and `job_id` and
acknowledges the current job record. It is safe to retry. Cancellation after a
terminal result leaves that result unchanged; it does not undo a published
index. Checks occur between directory/file entries, each 1 MiB hash chunk,
after tag parsing and at the final transaction gate. A blocking filesystem
read/directory enumeration or Mutagen call cannot be forcibly interrupted.
Shutdown waits up to five seconds for cooperative completion, then logs if
work remains; the durable startup recovery covers an abrupt exit.

The final transaction commits the complete index and the completed job/result
together. Cancellation and publication serialize at that transaction: whichever
passes the gate first wins. Failed/cancelled/interrupted scans never mark
previously indexed files missing or publish a partial replacement. Root
identity is captured at admission and revalidated when execution starts and
before publication, so a queued request cannot silently adopt a replacement
folder. Existing root/scan-revision guards remain in place.

Failed or interrupted work is **not** automatically rerun: inspect the error,
correct the cause and choose **Index folder** or **Rescan** to submit a new
request ID. Retrying an old admission only retrieves its historical receipt.
Run only one supported server per data directory; multi-process ownership/
leases and forcibly interrupting hung storage are outside this milestone.
A storage failure that also prevents terminal status persistence is logged by
the worker; restart recovery handles any retained running/cancelling row.

The browser keeps a separate dataset-scoped scan outbox before admission and
validates request/dataset/path/job identity before clearing it. A reload retains
unacknowledged submission and automatically discovers active/recent jobs.
Use **Retry scan request**, or download/discard only the browser retry state
after reconciling with the listed jobs. Discarding does **not** cancel admitted
server work. Status polling failures remain visible and retry automatically;
they never imply completion. Browser polling is every 1.5 seconds and refreshes
the root/file/match views after a terminal transition, including completion
while the page was closed. It does not alter pending match or staging outboxes.

### Explicit offline-folder reassociation

Plan for this increment: reuse durable scan admission/recovery, prove the
replacement bytes outside the review write lock, publish the root/index/audit
atomically, then exercise the existing match/staging/cue-review surfaces with
synthetic moves. No music files are moved by the app.

Reload to refresh offline folder status. Under that root, enter its new absolute
folder path and select **Verify and reassociate**, then confirm the explicit
warning. This is intentionally only for an **offline** old root, not merging two
online copies or moving a working library automatically.

The existing `POST /api/catalog/scan` contract optionally accepts **both**
`root_id` and `base_revision` (the root API's `scan_revision`) in addition
to request/dataset/path. No other fields are accepted. Admission rejects an
unknown/stale/online root, a replacement already belonging to another indexed
root, overlapping indexed/pending paths, or another pending reassociation for
the same root. It captures the replacement device/inode and returns the same
durable `202` receipt including root/revision. The scan outbox validates both
extra identities before clearing. The normal scan contract is unchanged.

The worker requires a nonempty previously available index whose files all have
successful known hashes. An empty index or previously errored/unhashed available
file cannot prove identity and fails visibly. A complete walk rehashes **every**
encountered audio file; it never reuses a size/timestamp match for reassociation.
Every previously available indexed file must appear at the same relative path
and produce the same SHA-256 with successful metadata parsing. Missing files,
symlinks in place of proof files, changed bytes (including tag edits), metadata
errors or renamed files fail the whole job. This is deliberately stricter than
an ordinary incremental scan; there is no skip/force override.

Additional audio files can be indexed with new file IDs, using the existing
normal metadata-error reporting. Previously missing locations may remain
missing. If such a location reappears, its previously known bytes must match
before its old file ID is restored; unknown or changed returned bytes fail.
Symlinks not replacing required proof files remain skipped/reported as usual.
This proves only the indexed subset, not the identity/completeness of every
non-audio or formerly missing file on an entire physical volume.

Before publication, the worker rechecks file signatures/containment, replacement
root identity, original-root offline status and the captured root scan revision.
If the old folder returns online, a proof file changes/disappears, or another
scan wins, the reassociation is not applied. The final transaction changes the
root path/device/inode, refreshes the index and increments its revision, adds
the immutable reassociation history, and completes the durable job together.
Cancellation/failure/interruption leaves all those records unchanged.
An exact admission retry remains historical and never reassociates again.
After a failure, reconcile first and submit a new explicit request ID.

Root IDs, file IDs, content hashes and accepted match revisions survive a valid
move; catalog selected-file paths now resolve through the new root. Existing cue
plans, corrections and track records remain untouched. App-local staged copies
can already work offline and are not recopied or analyzed. Their historical
catalog/source provenance remains the path originally observed; neither
`tracks.source_path` nor existing Serato write/backup associations is changed.
Use the existing separate import/write workflow to deliberately establish any
new original-file association; reassociation itself never grants that.

The job list labels reassociation separately and shows its verified-file count.
The history table retains the previous/new root identity and linked job even
after it ages out of the recent list. This is not a stable volume UUID or an
atomic filesystem snapshot: concurrent source mutations after the final check
remain possible. Current availability checks and later acceptance/staging/
analysis byte verification still apply. Keep the source stable during proof.
The same 10,000-file bound, cooperative I/O cancellation limits, single-server
requirement and unbenchmarked production/removable-drive limits still apply.

## Matching and decision acknowledgment

Candidate lookup uses Unicode NFKC, case folding and whitespace normalization.
Artist must agree. Full title/version agreement ranks first. Removing bracketed
title annotations is used **only to find song-family candidates** with visible
version conflicts; it never certifies a match. No fuzzy title threshold,
audio fingerprint or AI guess is treated as ownership evidence.

States:

- `missing`: no candidate, or an explicit user-cleared/missing decision.
- `unreviewed`: one candidate but no human acceptance.
- `ambiguous`: multiple copies/versions; no silent choice.
- `exact`: a human accepted a particular file hash and that indexed location
  still appears available/unchanged. This is a **human-attested selection**, not
  an algorithmic guarantee that metadata proves the recording.
- `stale`: accepted file is changed, errored, missing or offline. Rescanning does
  not automatically accept its new bytes.

Acceptance rehashes the selected file and checks its stat values again.
Metadata/version disagreement requires `override: true` and a nonempty note.
The UI shows the selected path/hash and the decision note/revision.
An explicit null file/hash clears a match; omitting a file is not a valid clear.

`POST /api/catalog/decide` requires dataset ID, unique request ID, snapshot/song
IDs, base revision, file ID/content hash (or both null), and optional note/
override. Decision + receipt commit in one `BEGIN IMMEDIATE` transaction.
Exact retries replay the original acknowledgment even after newer decisions,
without restoring an earlier selection. Stale revisions, wrong datasets,
changed/unavailable files or request-ID reuse with different content return 409.
Malformed input returns 400; database failures return 503 and roll back.

[catalog-state.js](../web/catalog-state.js) retains one pending decision per
dataset in localStorage before sending it. A response must acknowledge matching
request/dataset/snapshot/song/file/hash and exactly the next revision before
the outbox clears. Reload can retry the identical request. New choices cannot
replace a pending request, and stale tabs cannot overwrite another tab's outbox.
Failures remain visible instead of becoming a success toast.

Recovery: retry transient failures. For a permanent conflict, download the
pending decision, explicitly discard only the browser retry request, reload the
latest crate and reconcile manually. Discarding does not undo a server commit.
Browser storage is not a backup; clearing it/changing origin removes access to
that retry request. A prior receipt acknowledges a historical commit, not the
current availability of its file.

## Reviewed match to cue review

This bridge completes an independent part of the catalog workflow while the
actual CSV/export adapter still awaits representative input.

Implementation plan:

1. Revalidate the exact accepted match and streamed source hash before staging.
2. Share existing copy/import helpers; commit the analysis track, immutable
   provenance and staging receipt together, without altering a prior review.
3. Keep staging retries in a separate dataset-scoped browser outbox.
4. Expose explicit stage / analyze / review controls and test synthetic
   transaction failures, source changes and the full offline review flow.

**Stage reviewed copy** is available only for a current `exact` human selection.
The server checks dataset, snapshot/song, accepted decision revision, file ID,
root availability and indexed file state. It streams into an owned temporary
file, computes SHA-256 during the copy, flushes/fsyncs, and rejects bytes that
differ from the accepted hash. After copying it rechecks the match revision and
source status under `BEGIN IMMEDIATE`, preventing a newer match or source
mutation from being silently staged. Input paths are resolved from the index,
not supplied by the staging caller.

An existing app-local copy must pass hash verification; corruption is reported,
not silently replaced. The content-addressed copy is promoted before the
SQLite transaction inserts the track (if absent), provenance and receipt.
A failed transaction rolls back **all database records**, but a promoted valid
audio file may remain unreferenced. This is not a filesystem/SQLite distributed
transaction: retries safely verify/adopt the orphan. Hard termination may leave
owned `.tmp` files; no automatic deletion/scavenging was introduced.

If the exact hash already exists in the analysis library, staging preserves
the entire existing track row: cue plan, answer key, reviewed/edited flags,
revision, errors, name and earlier source association. Multiple catalog songs
may therefore share one analysis track and its cue corrections; those are not
independent per-membership reviews.

For a **new** track, `source_path` intentionally remains empty. Original path,
file ID, decision note/revision, dataset, catalog snapshot/song and selected
hash are retained separately in immutable provenance. This prevents staging
from enabling the existing original-file Serato write path. A track previously
imported/write-linked through another workflow keeps that existing association;
staging neither enables nor disables earlier permissions.

The cue editor displays this catalog origin; `GET /api/library` includes
`catalog_sources`. Reanalysis preserves these independent source links and the
existing reviewed answer-key behavior. Staging does not mark cues reviewed.

`POST /api/catalog/stage` accepts exactly:

```json
{
  "request_id": "new-client-generated-UUID",
  "dataset_id": "dataset-UUID",
  "snapshot_id": "snapshot-SHA256",
  "song_id": "source-song-version-id",
  "decision_revision": 1,
  "file_id": "indexed-file-UUID",
  "content_hash": "accepted-exact-file-SHA256"
}
```

Success acknowledges every field, `ok: true`, `track_hash` equal to the accepted
content hash, and boolean `added`. A repeated exact request replays that receipt
without copying or changing anything, even if the original subsequently goes
offline, the match changes or the staged track is removed. Reusing the request
ID with other content or staging a stale match returns 409. Malformed input/
copy failures return 400; database failures return 503 and roll back.

The receipt proves a **historical commit**, not present availability.
Catalog song responses expose `staged_track` only for the current decision
revision, with available/unavailable/removed state, analysis status, errors and
plan/review flags. Removing a staged track leaves provenance/receipts intact;
an old retry never recreates it. A new explicit staging request may recreate a
removed copy only after revalidating the current original and decision.
The lightweight status view checks location/existence; the actual staging and
built-in analysis paths verify the byte hash, rather than treating that status
label as an integrity certificate.

The browser stores staging requests before sending and validates every identity,
decision revision and returned track hash before clearing the separate outbox.
It never interprets an unacknowledged staging request as a match change.
After a failure/reload, use **Retry staging request**. For a permanent conflict,
download the pending request, discard only the browser retry state, refresh and
reconcile. Discarding does not remove a copy committed on the server.
Resolve pending staging before changing a match, and pending matches before
staging. Cross-tab changes fail visibly rather than overwriting either outbox.

**Analyze staged copy** is a separate confirmed action through the existing
job API. It uses saved analysis settings, including the Gemini toggle and
LRCLIB behavior; it may download weights or make permitted requests. No such
model/AI request was made during implementation. Do not select it for the
lightweight demo unless your environment is explicitly configured for offline
prepared analysis. Staging retries do not enqueue analysis jobs.
Job submission retains its previous protocol; this change does not add
exactly-once receipts to analysis job creation or force already-reviewed tracks
to rerun.

Watch batch progress, then choose **Refresh match and cue status**.
**Review cues** opens the current staged track in the existing editor after
fetching the latest library. Review saves retain their transactional correction
journal/receipts/durable drafts. A retained staged copy can still be analyzed or
reviewed with its original offline; the catalog match then shows stale/offline
independently. No new copy can be made from an offline original.

## API and verification

All mutation routes use the existing loopback/origin/session-token checks.

- `POST /api/catalog/import`: document above, max 64 MB.
- `POST /api/catalog/scan`: acknowledged durable admission contract above (`202`).
- `GET /api/catalog/scans`: active and recent terminal progress/results.
- `POST /api/catalog/scans/cancel`: acknowledged cancellation contract above.
- `POST /api/catalog/decide`: acknowledged match protocol above.
- `POST /api/catalog/stage`: explicit acknowledged staging protocol above.
- `GET /api/catalog/snapshots`, `/roots`.
- `GET /api/catalog/crates?snapshot_id=...&q=...`: crate-title search.
- `GET /api/catalog/songs?snapshot_id=...&crate_id=...`: ordered memberships,
  candidate explanations, saved decision and availability.
- `GET /api/catalog/files?offset=0&q=...`: 100-file pages, including errors.

The UI additionally filters artist/title/version within the selected crate.
It is not a global BPM/key query UI. Parallel startup shares one session
handshake; the local server accept backlog now covers the larger static/API
burst (the default backlog caused reproducible reset connections).

```bash
.venv/bin/python -m unittest web.test_catalog web.test_scan_jobs web.test_server
.venv/bin/python -m unittest discover -s engine -p 'test_*.py'
node --test web/test_*.cjs
```

CI runs the catalog tests alongside the previous suites/wheel installation
check. Tests use synthetic metadata, generated tag containers, temporary
databases and disposable directories. They include a generated 1,423-crate/
81,153-membership reconciliation/reimport test, original-byte/mtime checks,
offline/replacement roots, failed traversal, scan concurrency, parse errors,
version conflicts, changed bytes, receipt rollback and concurrent retries.

Browser verification used two generated tagged WAVs and a disposable database:
imported/reindexed, inspected ambiguous Clean/Explicit candidates, rejected an
unapproved conflict, deliberately dropped a post-commit acknowledgment, reloaded,
and retried without advancing the saved revision. No original library, music
file, production database or Serato tag was modified.

Current reassociation results: **345 engine, 132 web/catalog/scan and 47 Node cases
passed**.
A fresh Python 3.10 environment with only core dependencies also passed both
Python suites (three optional model-compatibility checks skipped). Hosted CI
has not run because this work has not been pushed.

Bridge verification additionally covers concurrent staging retries, match/source
changes during copy, wrong datasets, corrupt destinations, failed promotion,
receipt rollback, orphan adoption, preservation of earlier reviews/source
associations and retries after removal/match changes/offline originals.
The integration test executes the real worker's prepared profile on a generated
48-second WAV and saves a correction through HTTP. Browser validation uses that
same **offline prepared adapter**, not the normal model pipeline: match, stage,
drop acknowledgment after commit, reload/retry, explicitly analyze, open cue
review, change B and save/reopen. The source hash stays unchanged, exactly one
staging receipt remains, and original-file writes remain disabled.

Scan regression coverage includes acknowledged concurrent admission/replay,
separate browser outboxes, queued/running cancellation, chunk checkpoints,
final-publication races, failed admission/completion writes, progress/read
responsiveness during hashing and slow admission stat calls, folder replacement,
file bounds and source
invariance. A separate disposable server process verifies real startup wiring:
running work becomes interrupted, queued work runs, and the previous index is
retained. Node tests execute the actual catalog controller, including polling
failure/retry and completion while the page was closed.

Browser scan validation used 60 generated tagged WAVs and **artificially delayed
tag reads**, not a performance benchmark. A dropped response after durable
admission survived reload/exact retry without duplicating the job. Cancellation
published no partial root/index; a new explicit scan completed across another
reload. Final state was one cancelled job, one completed job, 60 exact unchanged
source files, no staged copies and no analysis jobs. Production databases,
real tracks, external volumes and paid/network AI were not exercised.

Reassociation regressions cover moved folders and a same-path replacement with
new inode, forced rehashing, original/target identity changes, stale revisions,
duplicate/overlapping requests, missing/changed/symlink/errored proof files,
early-file mutation during a later hash, additional and formerly missing
entries, cancellation and terminal-write rollback of the root/index/audit.
HTTP integration saves a cue correction before relocation and verifies that the
accepted match, cue track, correction journal and historical staging provenance
are unchanged afterward; a new staging request resolves through the new root.
Node tests validate explicit confirmation and root/revision acknowledgment.

Browser verification used 12 generated tagged WAVs with delayed reads. After
the fixture folder was moved, the offline-root control admitted reassociation;
a deliberately lost response survived reload and exact retry. Exactly one
completed job and audit row retained the original root identity, all 12 indexed
files were hash-verified/unchanged, and no staged tracks or model jobs existed.
This tests local disposable folders, not actual external-drive reconnection.

## Next concrete work

1. Inspect a representative **actual** CSV plus catalog index/export manifest at
   a user-supplied path. Build a deterministic adapter (including preambles,
   source IDs and unavailable entries) and reconcile the real 1,423/81,153 counts.
2. Durable cancellable scans and strict offline-folder reassociation are
   implemented. Benchmark on a disposable representative library before raising
   the initial bound; broader volume identity/partial relocation remains future
   work.
3. The reviewed-match/cue-review bridge is implemented. Next add ordered personal
   crates and verified M3U8/CSV export manifests after the real adapter and scan
   acceptance gates. No production tag-writing or AI expansion here.
