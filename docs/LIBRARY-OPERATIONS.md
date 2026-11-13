# Library operations: indexed health and explicit play history

`web/library_operations.py` provides an initial, non-destructive operations
layer over the existing catalog index. It does not move, delete, rename, or
retag files. It does not scan media folders, inspect audio bytes, poll a player,
or infer that a play was successful.

## Initialization and indexed health

Call `initialize(db)` after the catalog schema is initialized. It creates the
separate `library_play_events` event/receipt table and indexes; it does not
alter the catalog schema.

`health(db)` returns a JSON-friendly object with catalog and indexed-file
counts, persisted root/file/metadata errors, root states, and two bounded
evidence group lists. It is intentionally a **database-only indexed snapshot**:

- `state: "ok"` means the report completed; it does not mean all roots are
  currently available or all indexed files are healthy.
- `basis` is `indexed_snapshot_only`; `fresh_validation_performed` is false.
- `root_states.last_scan_online` means only that the last successful index
  completed without a persisted root error. `offline_at_last_check` means a
  prior attempted scan recorded an offline/error state. Neither reports the
  volume's current physical state.
- `available_at_last_scan`, `missing_at_last_scan`, and
  `files_under_roots_offline_at_last_check` are stored index facts, not fresh
  validation.
- `errors` includes persisted root and file errors and malformed indexed
  metadata, capped at 100 details; `errors_omitted` reports the remainder.
- `duplicate_groups` contains up to 100 groups keyed by identical indexed
  whole-file SHA-256. Each group includes every indexed `file_id` and a full
  per-file record. Identical hashes prove only that the complete bytes matched
  when indexed; they do not prove audio equivalence and never make a copy
  disposable.
- `related_edit_groups` contains up to 100 groups using the catalog matcher's
  normalized artist and base-title identity. The group is metadata-family
  evidence, not an equivalence claim. It retains all exact file IDs and does
  not merge or discard clean, explicit, radio, extended, or other versions.
  Exact-hash and related-edit groups can overlap.
- Each group list has a corresponding `*_omitted` count when it exceeds its
  bound. IDs within a returned group are not truncated.

Only rows already indexed in SQLite are considered. No media path is probed,
and hashes or tags are not refreshed by a report. A current availability or
byte-integrity decision requires a separately selected operation that performs
fresh validation.

## Explicit play history

Record a manual observation with:

```python
receipt = record_play(db, {
    "request_id": "unique-operation-id",
    "dataset_id": "current-dataset-id",
    "file_id": "exact-indexed-file-id",
    "played_at": "2026-11-12T18:50:00Z",
    "note": "Optional DJ-entered context",
}, dataset)
```

The request object must contain exactly those five fields. IDs must be
nonempty (maximum 256 characters); `note` is text up to 1,000 characters.
`played_at` must be a valid ISO-8601 timestamp with an explicit UTC timezone
(`Z` or `+00:00`); it is normalized to UTC with a `Z` suffix. The `dataset_id`
must equal the caller's current dataset, and `file_id` must identify an indexed
file. Use exact file IDs, not a title or a metadata guess; duplicate or related
files remain separately addressable. This checks indexed identity only, not
current path availability or current file bytes.

`record_play` begins an SQLite `BEGIN IMMEDIATE` transaction when it is not
already inside one and leaves commit/rollback to its caller. The request ID is
an idempotency key: an identical normalized request replays its saved receipt;
reusing the ID with different content raises `catalog.Conflict`. The event and
receipt are stored atomically. Invalid fields, timestamps, dataset IDs, and
unknown file IDs raise `ValueError`; a dataset mismatch or request-ID conflict
raises `catalog.Conflict`.

`play_history(db, limit=100)` returns newest observations first, with
`request_id`, `dataset_id`, `file_id`, `played_at`, and `note`. `limit` must be
an integer from 1 through 500 (booleans are not accepted as integers).
`history_summary(db)` returns a mapping keyed by exact `file_id`, for example:

```json
{
  "exact-file-id": {
    "play_count": 2,
    "last_played": "2026-11-12T18:50:00.000000Z"
  }
}
```

Only active, explicitly recorded events contribute to these counts. There is no
automatic now-playing listener, play-success inference, recording identity
guess, cross-dataset merge policy or source-file cleanup control in this module.

## Reviewed history removal and undo

`web/studio_history.py` previews 1..500 distinct exact observation IDs from the
current dataset, retaining their original rows and current removal revisions.
Approval binds the exact ordered IDs to an unexpired, digest-checked preview.
The entire batch commits atomically with a durable receipt. Changed rows,
foreign datasets, stale removal revisions and altered selections abort it.

Approved events get revisioned logical removal markers, not deleted rows.
`play_history` and `history_summary` omit active removals; Studio rediscovery and
last-played sorting update accordingly. Original `record_play` requests still
replay their original receipts and cannot resurrect removed events.
Fresh observations have independent IDs and remain independent.

Undo restores only the selected observations, refusing changed evidence or
newer removal state. It does not replace the history of a file wholesale or
discard observations recorded afterward. Undo/reapproval cycles advance removal
revisions so an old preview cannot overwrite newer work. Approval/undo retries
reuse exact receipts, including after restart.

The loopback Studio endpoints are `history/preview` (`dataset_id,event_ids`),
`history/approve` and `history/undo` (both require
`request_id,dataset_id,edit_id,event_ids`). History recovery journals are included
in database backups. UI history and recovery lists show up to 100 active events
and 30 recent edits respectively; the API removal boundary is 500 events.
The displayed-history JSON download is explicitly a bounded view, not a complete
history export.

This is **not permanent privacy erasure**: original event text, receipts and
recovery journals remain in the app database and its backups. Audio, saved
crate membership, external DJ software and explicit preference ratings are
unchanged. There is no model-operated history mutation tool.

## Validation

Run the focused synthetic database tests from the repository root:

```bash
python3 -m unittest web.test_library_operations web.test_studio_history
```

They cover database-only reporting, hash-vs-metadata evidence, changed
metadata, indexed missing/offline states, history durability and retries,
dataset/file/timestamp validation, limits, and transaction rollback.
