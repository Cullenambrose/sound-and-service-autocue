# Synthetic Prep / Live workflow

This is an isolated local demo, not a production library integration. The
existing library-prep application is unchanged. The screens still need the
95/100 design review before anything ships.

## Run safely

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m web.setlist_demo \
  --output ~/Desktop/MASTER-TODO/outputs/05-flow-demo
```

Open the printed `http://127.0.0.1:8767` URL. Stop with Ctrl-C.
`--shuffled` selects the shuffled copy of the same synthetic demo library.
The server loads only the checked-in `mock-wedding-event.json` and one of the
two demo libraries. It never imports the existing app server, initializes its
database, scans folders, opens music paths, or calls tag/crate/Serato writers.
Its CLI accepts only a non-symlink `05-*` directory directly inside the requested
Desktop outputs directory. Music paths in the playlist are synthetic references,
not files to read or create. Do not use this demo with customer or music data.

## Workflow contract

- Prep calls `rank_prep_crate`; Live calls `rank_next_song`. JavaScript only
  renders returned order, notes, reasons, exact files, versions and actions.
  The shared engine remains authoritative for matching, version priority,
  do-not-play, played exclusions, request conflicts, boosts and badges.
- Review-only candidates are held outside the displayed crate/recommendations,
  unless another valid request independently fulfills that same identity.
  Ambiguous, missing, unavailable-version and conflicted requests retain the
  original typed request and show up to three candidates. A block cannot be
  confirmed. Missing requests can only be dismissed.
- Confirming explicitly substitutes the selected candidate as the effective
  request, keeping the typed request for display. The engine validates
  `metadata.confirmed_file_id` against the requested identity/version and
  chooses that intact record. This human choice can resolve identity ambiguity,
  but never bypasses bans, played exclusions or higher-priority versions.
  Confirming a fallback version is an explicit choice, not automatic fulfillment
  of the originally unavailable version.
- Every Prep review row must be confirmed or dismissed before Save. Save writes
  `05-prep-saved.json`; Export derives `05-prep-export.m3u8` strictly from that
  saved snapshot, never reranking a library. Verify compares exact saved JSON
  and ordered playlist bytes and reports the mismatch count and SHA-256 hashes.
  Prep is complete only with all decisions resolved and a matching export.
- `05-session.json` stores the synthetic session and retry receipts atomically.
  Revision checks reject stale tabs. Failed commands remain explicit in the UI;
  retry resends the identical request ID/body, or reload the saved session to
  reconcile. Do not run two server instances against the same output directory.
- Live guests enter a first name, optional email and a song, including inline
  version text such as `Summer (Dirty)`. No guest account or sign-in is added.
  Demo guest inputs must also be synthetic. Live commands never write either
  saved Prep artifact; tests hash those files before and after a matched request.
  Mark played excludes every file of that song identity for the event.

## Validation

```bash
PYTHONPATH=engine .venv/bin/python -m pytest web/test_setlist_flow.py -q -s
node --test web/test_setlist_ui.cjs
PYTHONPATH=engine .venv/bin/python -m unittest setlist.test_demo -v
.venv/bin/python -m pytest -q
node --test web/test_*.cjs
```

The numbered Python tests cover acceptance checks 1-12, including an actual
loopback HTTP benchmark with 1,000 invented library records and 20 persisted
guest requests. It includes ranking, session persistence, serialization and
reading the response. It reports p50/p95, cold/max latency, and whether every
sample met 200 ms; a missed performance target is reported, not disguised as a
functional failure.
Set `SETLIST_BENCHMARK_OUTPUT` to an explicit `05-*.json` path to retain the
samples. Full-suite pass counts are reported separately from this target.

Latency improvements cache bounded immutable version/identity/similarity
parsing in the shared matcher (callers still receive independent mutable
version objects), avoid irrelevant different-artist fuzzy comparisons, and
use a single available file directly as a song representative. They do not
change the inclusive 0.6 setlist or separate 0.7 crate confidence cutoffs.

The six original demo fixture files remain byte-identical. Additional tiny
synthetic in-memory test records cover Shake It, Septemer, unavailable versions,
duplicate exact files, and Summer without creating any music files.
Before screenshots show the old library-prep screen twice: event Prep and
Live screens did not exist before this change. They are baseline evidence,
not invented mockups of nonexistent prior screens.
