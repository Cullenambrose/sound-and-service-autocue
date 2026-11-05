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
2. Enter the generated `music` folder path and choose **Index folder**.
3. Choose the available **Synthetic warmup** crate. The other same-title crate
   remains separately identified and explicitly unavailable.
4. Review the Clean and Explicit candidates. No choice is preselected.
   Matching tags still require human review. The Explicit candidate requires
   an intentional override checkbox and note.
5. Accept a reviewed file or mark missing. Reload and verify the same saved
   decision. Rescan to observe unchanged files being reused.

The generated WAVs are silence with invented tags, not a listening/accuracy
test. No catalog match action copies audio into the analysis library, invokes
models, uploads audio, or writes tags. Existing analysis/Serato controls remain
separate; do not use them as part of this demo. Stop the server before deleting
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
reattachment that changes device identity may need future explicit reassociation;
root relocation/removal/reassociation UI is not implemented. Do not edit SQLite
to bypass an offline identity check.

Hashing and tag parsing happen outside the SQLite write lock needed by review
saves. A short final transaction publishes the scan and detects another scan
that finished against the same/overlapping root. Partial scans never replace
the previous index. An integer scan revision detects competing scans even with
identical timestamps. Unrelated database writers can proceed during parsing.

Initial scope: at most **10,000 audio paths per root**, held in memory during
enumeration. This is a declared safety bound, not a measured performance target.
Scanning runs on the HTTP request thread, not a durable job queue; the UI
disables its catalog actions until completion. Closing the page does not
guarantee server cancellation. Server restart before commit preserves the prior
index; repeating the scan is safe. Progress/cancellation, crash receipts and
production-scale latency benchmarks remain stage-3 follow-up work.

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

## API and verification

All mutation routes use the existing loopback/origin/session-token checks.

- `POST /api/catalog/import`: document above, max 64 MB.
- `POST /api/catalog/scan`: `{"path": "/absolute/selected/folder"}`.
- `POST /api/catalog/decide`: acknowledged match protocol above.
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
.venv/bin/python -m unittest web.test_catalog web.test_server
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

Local results: **345 engine, 78 web/catalog and 33 Node cases passed**. A clean
Python 3.10 environment with only core dependencies passed the same Python
suites (three optional model-compatibility checks skipped). Hosted CI has not
run because this work has not been pushed.

## Next concrete work

1. Inspect a representative **actual** CSV plus catalog index/export manifest at
   a user-supplied path. Build a deterministic adapter (including preambles,
   source IDs and unavailable entries) and reconcile the real 1,423/81,153 counts.
2. Add durable cancellable scan jobs and explicit volume reassociation; benchmark
   on a disposable representative library before raising the initial bound.
3. Bridge human-selected current hashes into staged cue review with revalidation
   and preserved provenance. Then add ordered personal crates and verified
   M3U8/CSV export manifests. No production tag-writing or AI expansion here.
