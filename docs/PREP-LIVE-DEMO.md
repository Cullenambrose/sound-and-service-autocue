# Synthetic Prep / Live workflow

This is an isolated local demo, not a production library integration. The
existing library-prep application is unchanged. The screens still need the
95/100 design review before anything ships.

## Run safely

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m web.setlist_demo \
  --output ~/Desktop/MASTER-TODO/outputs/06-flow-demo
```

Open the printed `http://127.0.0.1:8767` URL. Stop with Ctrl-C.
`--shuffled` selects the shuffled copy of the same synthetic demo library.
The server loads the checked-in `mock-wedding-event.json` and one of the
two demo libraries, plus explicitly uploaded synthetic snapshot bytes if selected.
It never imports the existing app server, initializes its
database, scans folders, opens music paths, or calls tag/crate/Serato writers.
Its CLI accepts only a non-symlink `05-*` or `06-*` directory directly inside the requested
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
- Unavailable-version buttons name the substitution ("Use Clean instead").
  Matched Live rows show the resolved title, artist, version and exact path
  alongside the original request, with a distinct success state.
- Prep uses an event-first source/set/decision workspace on wide screens.
  The source rail previews synthetic Serato snapshots and filters eligible
  shared-engine recommendations by displayed title, artist or version.
  Filtering preserves server order and never resolves requests, changes
  ranking, searches providers or claims access to the whole library.
  Adding a displayed exact file requires an accepted imported starting set.
  The working crate remains central; occurrence-level ordering controls expand
  separately, and unresolved requests and missing-song lookup remain alongside.
  Section jump links and a stacked narrow-screen layout retain keyboard access.
  Live
  recommendations sit beside the guest form and request queue, rather than
  below them. Narrow screens retain document/keyboard order and provide a
  jump link to the scrollable recommendation table.
- Completion combines Saved, Exported and Verified status beside the actions.
  Review actions restore keyboard focus after rows change; errors receive
  focus and pending actions retain the existing explicit retry/reconcile flow.
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
  "Add guest request" opens the form; a successful submission clears and closes
  it so the DJ queue remains visible. Failures leave the form and inputs intact.
  Demo guest inputs must also be synthetic. Live commands never write either
  saved Prep artifact; tests hash those files before and after a matched request.
  Mark played excludes every file of that song identity for the event.
- New CLI sessions use `06-session.json`, `06-prep-saved.json` and
  `06-prep-export.m3u8`; explicitly selecting an old `05-*` output directory
  preserves its existing filenames. No Serato crate or music file is written.

## Synthetic acquisition reconciliation

Missing-song rows retain separate, durable progress: **Not started**,
**Searching**, or **Acquired externally; not verified**. These states never
fulfill a request, add a badge, or change ranking.

**Reconcile synthetic acquired metadata** previews one explicitly selected JSON
object with `id`, `title`, `artist`, `path` under `/Demo/`, and optional `version`.
The invented-sample button provides a metadata-only walkthrough. No audio path
is opened, scanned, created or validated as a real playable file. This is not
an authorized real-library acquisition adapter.

Preview reruns the shared engine against a draft snapshot without persistence.
Acceptance is explicit, revision-bound and retry-safe; it adds metadata to the
demo session and rebuilds the prepared library. Wrong versions and ambiguous
titles remain unfulfilled until their normal review decision. Do-not-play and
Live played exclusions still apply. Provider progress is never used as evidence
of a local match. Existing sessions migrate with empty acquisition state.

Saved Prep JSON and exports stay byte-identical across preview/acceptance and
Live requests. Accepted metadata makes the working snapshot stale relative to
the saved crate; save/export again to publish the new set. Old export downloads
continue to represent the old saved snapshot exactly.

## Validation

```bash
PYTHONPATH=engine .venv/bin/python -m pytest web/test_setlist_flow.py -q -s
node --test web/test_setlist_ui.cjs
PYTHONPATH=engine .venv/bin/python -m unittest setlist.test_demo -v
.venv/bin/python -m pytest -q
node --test web/test_*.cjs
```

The numbered Python tests cover acceptance checks 1-12, including an actual
loopback HTTP benchmark with 1,000 invented library records. It measures the
cold first request separately, discards one warm-up, then records 200 persisted
submissions. The seeded in-memory session is reset outside each timed interval
so inputs remain comparable and the existing 100-guest limit is unchanged.
Timing includes ranking, durable session persistence, serialization and reading
the response. It reports p50/p95/p99/max and cold latency, and fails unless warm
p95 is strictly below 200 ms. Set `SETLIST_BENCHMARK_OUTPUT` to an explicit
`06-*.json` path to retain every sample.

Latency improvements cache bounded immutable version/identity/similarity
parsing in the shared matcher (callers still receive independent mutable
version objects), avoid irrelevant different-artist fuzzy comparisons, and
use a single available file directly as a song representative. They do not
change the inclusive 0.6 setlist or separate 0.7 crate confidence cutoffs.
The workflow builds a caller-owned `PreparedLibrary` snapshot once per load;
rebuild that snapshot when library metadata changes. Plain iterable callers
retain their original uncached behavior. Original typed diagnostics are cached
per mode until event/guest requests or accepted library metadata change, rather
than reranking for each confirmation. Recommendation serialization copies the
known result fields without recursively converting entire track dictionaries. Both screens are
warmed before the CLI server starts accepting requests. Guest submission still
performs exactly one durable session write; retry receipts prevent duplicate
writes. No matching, scoring, tie-breaking or exclusion rules change.

The six original demo fixture files remain byte-identical. Additional tiny
synthetic in-memory test records cover Shake It, Septemer, unavailable versions,
duplicate exact files, and Summer without creating any music files.
Before screenshots show the old library-prep screen twice: event Prep and
Live screens did not exist before this change. They are baseline evidence,
not invented mockups of nonexistent prior screens.

## Synthetic Serato starting sets

Prep now accepts an explicitly selected static `.crate` plus database snapshot.
Only invented `/Demo/` references are accepted. Use **Preview synthetic sample**
to exercise it without selecting files. Uploads are bounded to 512 KiB each
and 1,000 crate memberships. This is not yet a real-library compatibility claim.

The strict byte parser supports nested track containers and legacy flat markers,
retains occurrence order/duplicates and unknown track fields, and rejects
truncation, invalid text, conflicting metadata and unsafe references explicitly.
No referenced path is opened. Preview is read-only; **Use as starting set** is a
revisioned, retry-safe explicit replacement of the working source.

Imported memberships can be moved, removed, or supplemented from shared-engine
recommendations. Engine-selected files are the only playable entries: unavailable,
blocked or version-conflicting imports remain visible and prevent saving until
removed. Matched event requests and cues are appended without disturbing imported
order. Imports never mean "played this event." Save/export retains exact working
membership order, including duplicates; Live cannot alter those saved artifacts.
The source hashes and original catalog snapshot remain in the session. Download
the catalog JSON to reuse the existing catalog/personal-set import contract;
this demo does not initialize a second catalog database.

**Find missing songs** provides manual lookup text and six provider destinations
from [Crate Hackers' public page](https://www.cratehackers.com/): Direct Music
Service, Late Night Record Pool, BPM Supreme, Beatport, Beatsource and DJcity.
Crate Hackers advertises connected downloads for the first two and search for the
others. Those are their capabilities, not this application's integrations.
[Beatsource](https://www.beatsource.com/) now announces migration to Beatport.
Store/streaming destinations are distinguished from record pools. Public API
authorization has not been established; no endpoints or logged-in scraping are
assumed. This app opens provider homepages only, sends no song text automatically,
connects no accounts, and performs no purchases or downloads. Provider listings
never fulfill requests or become playable files.

The supplied reference UI additionally shows 23 selectable record pools. Those
names are a research backlog, not verified integrations here; the six manual
destinations above are not a full feature-parity claim.

Remaining roadmap: real snapshot compatibility approval, phase-based set editing,
authorized provider search APIs, acquired-file reconciliation, and opt-in history
learning. Independent design approval is still required.
