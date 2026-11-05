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
8. Ordered personal crates referencing immutable catalog song/version identities,
   with revision-checked saves and independent browser recovery.

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

Current personal-crate results: **345 engine, 142 web/catalog/scan and 54 Node cases
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
3. Ordered personal crates now work independently of the blocked real adapter.
   Verified M3U8/CSV export previews/manifests now explicitly recheck
   source bytes and match/crate revisions. Next verify extracted playlists in the
   actual target DJ software on disposable representative files. Do not equate this synthetic workflow
   with actual vendor compatibility or production-scale acceptance.
   No production tag-writing or AI expansion here.

## Ordered personal crates

This independent increment enables set preparation while actual export examples
are still unavailable. Implementation plan: preserve source memberships, store
ordered personal copies with revision/receipt transactions, connect deliberate
catalog additions and editing controls, then verify concurrency and lost-response
recovery on disposable data.

Use **Arrange your set** to create a named crate, select it, then browse the
imported catalog and choose **Add to selected personal crate** on a song. Return
to the personal section to move entries up/down, remove one membership or rename
the crate. Repeated songs are intentional: each addition has a separate entry ID.
Names need not be unique; crate identity is its client-generated ID, not its name.
This slice has no whole-crate deletion/archive, drag/drop, global song search,
bulk source-crate copying or notes. Empty crates are supported, but not exportable.

Each action commits immediately only after acknowledgment; changes to name
textboxes are not saved until **Create**/**Rename** is clicked and are not draft
backups. Refresh/reload or selecting another crate can replace unsubmitted name
text. Submitted changes are persisted in the separate browser outbox before
sending. New changes cannot replace an unresolved request. A stale tab cannot
overwrite/delete another tab's retry state.

`GET /api/catalog/personal` lists IDs, names, current revisions, entry counts and
timestamps. `GET /api/catalog/personal/detail?crate_id=...` returns a consistent
SQLite read snapshot of the crate and its ordered entries, immutable source-song
metadata and live selected-file status. Memberships store `(snapshot_id,song_id)`
and a stable entry `id`; they do not copy source metadata or freeze a file match.
The detail view uses `unreviewed` when no human decision exists (it does not run
candidate ranking), `missing` for an explicitly cleared match, and the same
shared `exact`/`stale` reviewed-state rules as catalog browsing. These labels
are human-attested/stat-checked availability, **not export-time byte proof**.
Choose **Refresh personal crates** after changing matches or source availability;
the personal view is not continuously polled.

`POST /api/catalog/personal/save` requires exactly:

```json
{
  "request_id": "new-client-generated-UUID",
  "dataset_id": "current-dataset-UUID",
  "crate_id": "stable-client-generated-crate-ID",
  "base_revision": 0,
  "name": "Warmup",
  "entries": [
    {
      "id": "unique-membership-ID-within-this-crate",
      "snapshot_id": "immutable-snapshot-SHA256",
      "song_id": "source-song-version-ID"
    }
  ]
}
```

`base_revision: 0` creates a crate. Subsequent saves submit the full replacement
name/ordered entry list against its current revision. At most **1,000 entries**
per personal crate are supported; exactly 1,000 is tested. Duplicate entry IDs,
unknown song/snapshot references, blank names, boolean/noninteger revisions,
unknown fields and oversized lists reject the entire save. An existing retained
membership ID cannot be retargeted to another song; use a new membership ID.
Repeated song references with distinct entry IDs are allowed. Removing an entry
does not delete the underlying catalog song, match, staged track or cue review.

Crate metadata, order/membership replacement, next revision and immutable retry
receipt commit in one `BEGIN IMMEDIATE` transaction. A receipt-insertion failure
rolls all changes back. Concurrent edits against the same revision allow one
commit and reject the others with `409`; there is no silent merge.
`200` success echoes the complete submitted body plus `ok: true` and exactly the
next `revision`. Wrong datasets or reused request IDs with different bodies also
return `409`; malformed references/input return `400`; storage failures are logged
and return `503`. All mutations use the existing origin/session checks.

Retrying an identical request returns its **historical** receipt even after later
edits; it never restores an old name/order or recreates removed entries. The
browser validates the crate/dataset/request/base revision, name and every ordered
membership before clearing the pending request, then fetches current state rather
than rendering an old receipt as the latest crate.
After a lost response/reload, use **Retry crate save**. For a permanent revision
conflict, download the pending full change, discard only browser retry state,
refresh and reconcile manually. Discarding does not undo a committed server save.
Browser storage is not a backup; clearing it/changing browser origin loses local
recovery. Selecting another crate does not discard the outbox. Reload selects the
first crate; retry selects the request's target before fetching current state.

Tables: `personal_crates`, `personal_crate_entries` and
`personal_crate_receipts`. There is no automatic history pruning or whole-crate
deletion in this milestone. Original catalog snapshots, source memberships,
reviewed match decisions, staging provenance and cue corrections are unchanged.
No audio copy/decode, library scan, tag write, external request or model job is
triggered by personal-crate edits. Export verification and actual vendor CSV
compatibility remain separate acceptance gates.

Verification: the existing CI module selectors pick up the HTTP regressions and
the Node wildcard picks up `test_personal.cjs`. Tests cover concurrent exact
retries, competing revisions, consistent read snapshots during concurrent saves,
rename/reorder/remove/clear, duplicate songs with distinct membership IDs,
source-snapshot preservation, source offline/cleared match states, invalid
references, exact 1,000/1,001-entry boundary, receipt rollback and historical
retry after newer edits. Controller/outbox tests cover reload recovery,
storage/stale-tab failures, failed-detail refresh, deliberate conflict recovery,
ordered receipt validation and catalog filtering alongside personal cards.

Browser verification used an invented imported snapshot and disposable database,
without music files or a library index: create, add with a deliberately lost
post-commit acknowledgment, reload/exact retry, add the same song again, reorder,
rename, remove one membership and reload. The final crate had revision 6 and
six receipts, with its original first membership retained in the expected
position. All three source memberships remained unchanged; there were no scan,
audio-track or model jobs. This is not a real-library export/accuracy test.

## Verified playlist export

Implementation plan: capture a consistent crate/match/index snapshot, verify
reviewed source bytes outside database locks, gate publication on a second
snapshot and final file identities, then round-trip and atomically publish a
single bundle. No source audio, original tags, cue plans or source catalogs are
written by this operation. No model or external request is made.

In **Arrange your set**, click **Verify export preview**. Each ordered membership
shows its source artist/title/version, selected path and either byte verification
or an explicit blocker. Duplicate memberships are retained. Empty crates and any
unreviewed, cleared, stale, offline, changed, unreadable or symlinked source block
the whole export. There is deliberately no partial-export/skip option and no
automatic replacement file. Hash agreement proves the reviewed file bytes, not
the correct recording/edit or cue quality. Cue review is available separately;
this playlist does not embed or approve cues.

Only a completely eligible preview enables **Reverify and download playlist ZIP**.
That action repeats the full hash pass and rejects any changed crate revision,
match revision, index/root metadata or final source identity. A prior preview is
not permission to use a cached path/hash. Refresh, crate selection and edits clear
the preview; pending crate saves block export. Other tabs and removable drives
may invalidate a displayed preview; failures require a new explicit preview.

### API and artifact contract

Both POST routes use existing same-origin/session protection:

- `/api/catalog/personal/export/preview` takes exactly `dataset_id`, `crate_id`
  and positive integer `base_revision`.
- `/api/catalog/personal/export/download` additionally requires `preview_id`.

Preview returns `ok`, a SHA-256 `preview_id` over the canonical manifest and the
manifest. Manifest schema 1 records dataset and crate identity/name/revision,
ordered source membership/song metadata, decision revisions, file IDs, absolute
paths, expected/verified SHA-256, reasons, indexed root records and final
device/inode/size/mtime/ctime proof identities. `requested`, `eligible`, `blocked`
reconcile the whole membership list; `included` is the proposed complete playlist
size if `ready`, otherwise zero. A preview writes no artifact. Empty crates have
an explicit top-level blocker. The manifest contains **local paths and source
metadata**: keep it private and do not upload it as diagnostic data.

Download returns the same manifest/token plus `filename` and `bundle_base64`.
The server saves the ZIP under `<app-data>/exports/<preview_id>.zip` via a unique
temporary file, flush/fsync and atomic replacement. No caller-supplied output path
is accepted. All three files are in one atomic bundle:

- `playlist.m3u8`: UTF-8, `#EXTM3U` and one absolute original-file path per entry.
  Order and duplicates are exact; no EXTINF/tag assumptions. Paths with playlist
  line delimiters/NUL are blocked instead of escaped into ambiguous entries.
- `playlist.csv`: ordered position/membership/source IDs, artist/title/version,
  path/file ID/hash and match revision, using standard CSV quoting. Text that
  could become a spreadsheet formula is prefixed with an apostrophe; raw source
  values remain unchanged in the manifest. This is a generic interchange format,
  **not** an implementation of any particular vendor CSV import contract.
- `manifest.json`: the inspected/revalidated proof described above.

M3U8 and CSV are reparsed to reconcile path order/count; every ZIP member is
reopened and compared before publication. ZIP bytes are deterministic for the
same manifest. A lost download response can safely be retried with the identical
preview while all state/bytes remain unchanged; it re-verifies instead of
replaying a historical receipt. If state changed, the retry returns `409` and
never restores an older playlist as current. Previously saved bundles remain
historical artifacts, not continuing availability guarantees.

`400` rejects malformed requests; `409` rejects wrong datasets, stale crate or
preview state and blocked downloads. A source-read error during preview appears
as an entry blocker; database/publication failures are explicitly reported
(`503` for storage errors, with server logging). A failed atomic replace removes
its temporary file and preserves any previous complete artifact. There is no
automatic export retention/pruning. The saved-artifact browser is described below.

Extract the ZIP before opening `playlist.m3u8`. The browser reports **download
requested**, not that the browser saved it successfully. If the browser blocks a
download, allow downloads and use the explicit historical recovery link below,
or preview/download again for a current verification; the server's complete
bundle remains in its app-local exports directory. Moving the playlist
does not relocate audio. Absolute paths remain tied to this machine/mount.

### Verification and limits

Current results: **345 engine, 154 web/catalog/scan and 55 Node tests passed**.
New HTTP regressions cover duplicate order and deterministic retries, byte-hash
mismatch despite agreeing index signatures, unreadable/offline/cleared/unreviewed
and empty blockers, Unicode/spaces/quotes/newlines, spreadsheet-safe CSV, stale
preview/crate/match/index state, writers committing during hash reads, mutation
during hashing or bundle serialization, missing sources after a prior successful
export, symlink rejection and atomic-publication failure/retry. Controller tests
execute the actual preview/download code, enforce preview invalidation and
verify that failures never initiate an unverified download.

Browser verification used two generated tagged WAVs and three ordered entries
(first, second, first). The real UI verified all three and published one complete
bundle; independently reopening it confirmed exact order, Unicode/quoted paths,
duplicate membership identity, source hashes and no analysis/staged-track jobs.
Removing the second disposable source after a new preview made download fail
closed; a new preview showed two eligible and one blocked, with download disabled.
The browser automation did **not** receive a download-completion event, so actual
browser filesystem delivery remains unverified even though the persisted
app-local ZIP was checked. Actual Serato/DJ-software import/playback is untested.

Hash reads are synchronous HTTP work (bounded by the existing 1,000-entry crate
limit), not durable/cancellable jobs; each distinct selected file is hashed once
per action. Slow/disconnected filesystem calls can delay the request. No audio
read holds the SQLite writer lock; final publication briefly does. The filesystem
cannot be locked transactionally: stat/identity/root checks detect normal changes
up to the publication gate, but cannot prevent a concurrent external edit or
disconnect afterward. Export only stable folders; regenerate after changes.
Production-size performance, external-drive behavior and target-software
compatibility require disposable representative acceptance before production use.

## Saved export recovery

Implementation plan: use the existing content-addressed artifacts as the history,
validate their structure and deterministic bytes, add paged summaries and a native
attachment route, then exercise lost-response/reload/corruption recovery without
source audio. No new database table or schema migration is required.

**Saved exports · historical copies** is independent of the selected personal
crate and its pending save. It shows the saved name/revision, entry count, local
save timestamp, size and manifest ID. Click **Download historical ZIP** to recover
the exact saved bytes, then extract the ZIP. A newer crate name/order or missing
source files does not rewrite or invalidate this historical artifact.
The history warning is deliberate: bundle integrity is **not current source
availability**, cue approval, recording identity or DJ-software compatibility.
Choose a new verified preview for a current set.

History loads on startup and after acknowledged publication, with a manual
**Refresh saved exports** control for lost responses or external changes.
Previous/Next use 20-item offset pages, newest filesystem modification time first
(manifest ID breaks ties). External changes/publications can shift pages; refresh
starts from the first page. A timestamp is the artifact's filesystem modification
time, not an immutable export audit timestamp. Listing failures clear old links
and show an explicit error without preventing independent personal-crate edits.
Unavailable/corrupt ZIPs show errors instead of links; `.tmp` files are ignored.

- `GET /api/catalog/personal/exports?offset=0` returns `exports`, `offset`, `total`.
  Only the requested page is opened/validated. Each item has `export_id`,
  `filename`, and either `state: "historical"` plus crate/count/size/time, or
  `state: "unavailable"` plus an error. Foreign-dataset artifacts expose no crate
  metadata. Negative/noninteger offsets return `400`; a missing exports directory
  is an empty history.
- `GET /api/catalog/personal/exports/download?dataset_id=...&export_id=...`
  validates the current dataset query and a lowercase 64-hex manifest ID, rereads
  and validates the whole artifact, then returns `application/zip` with an
  attachment filename, exact content length, `no-store` and `nosniff`. It never
  rehashes/stats audio or reads current crate/match state. This is a read route:
  no session secret is placed in the URL. Existing localhost Host/origin rules
  apply, and cross-site Fetch Metadata requests are rejected. No cross-origin
  access is enabled. The native link avoids the automatic Blob download path.

Validation rejects symlinked export directories/files, nonregular artifacts,
unsupported compression/encryption, extra/missing members, malformed/incomplete
schema-1 manifests, a manifest hash inconsistent with the filename, foreign
datasets, CRC/content corruption and ZIP/playlist/CSV bytes inconsistent with
deterministic reconstruction. No archive member is extracted on the server.
The bytes checked in memory are the exact bytes delivered, even if the disk copy
changes afterward. This is integrity checking, not authenticity against a local
actor who can rewrite artifacts and their content-derived names.

Both new publication and recovery enforce a **64 MiB ZIP limit**; expanded
member sizes are also bounded. Exactly 64 MiB and one byte over are tested.
Oversized older artifacts are listed with an error; they are neither changed nor
deleted. Use a smaller personal crate if metadata makes a new export too large.
There is no automatic pruning/deletion, live status validation or cache of
integrity results. Listing enumerates filenames and sorts timestamps, then reads
up to 20 full bundles; large histories may be slow. History is local filesystem
work, not a durable/cancellable job.

Historical download errors return `400` for invalid IDs/integrity failures,
`409` for the wrong dataset query, `404` for a removed artifact and logged `503`
for filesystem/storage errors. A stale link never returns unchecked ZIP bytes.
An earlier complete artifact survives failed replacement publication. There is
no acknowledgment that a native browser download reached the user's disk.

Regression coverage now includes source removal/newer crate revisions/restart
without audio reads, attachment headers and exact response bytes, pagination,
missing/corrupt/forged archives, foreign datasets, bad manifests, compression,
symlinks/nonregular files, exact size boundaries, origin rejection and rollback
preserving an earlier complete ZIP. Controller tests execute lost publication
acknowledgment/reload recovery, empty-crate history access, paging, corrupt-link
suppression and history-failure isolation.
Current results: **345 engine, 162 web/catalog/scan and 58 Node tests passed**.

Browser acceptance used generated tagged WAV data only: after publication the
source was removed and the current crate advanced to an empty revision 2.
The UI still offered the original two-entry revision-1 artifact. Its native
endpoint returned a ZIP attachment; an independent HTTP download was written to
a disposable file and reopened byte-for-byte, retaining duplicate order despite
missing audio. Corrupting the saved fixture then removed the link and showed the
integrity error through refresh/reload. The browser automation again produced no
download-completion event; browser filesystem delivery is still unverified.
No production audio, tags or library data were touched.

## Manifest-backed CSV adapter checkpoint

`python -m web.vendor_import --directory "/explicit/export/folder" --output
"/outside/source/catalog-snapshot.json"` converts the actual manifest/reference
layout into the existing schema-1 JSON upload format. It reads only the reference,
manifest and declared CSVs; earlier unreferenced duplicate exports are not
silently merged. Output is atomic, outside the source folder and never overwritten.
Source files remain read-only. CSV row order, repeated memberships, source crate
IDs, preambles/links, raw fields and numeric zero/unknown distinctions are retained.
CSV rows have no vendor song ID: their IDs hash exact metadata excluding order,
not recording identity. Missing artists remain blank, with visible warnings;
title-only match candidates require an explicit override and note.

This checkpoint is intentionally fail-closed and **not yet real-collection
acceptance**. Conversion of the supplied real folder discovered blank titles
after handling blank artists. It currently rejects those rows rather than
dropping memberships or inventing metadata; complete reconciliation is pending.
Cloud-materialized files whose stats change during reading require an identical
second read with stable stats and emit a warning, otherwise conversion fails.
Synthetic adapter/catalog/web tests: **170 passed**; Node tests: **58 passed**.
No actual export metadata or music is committed. Next preserve the newly observed
unknown-title cases explicitly and rerun full real-count reconciliation before
claiming the adapter complete.
