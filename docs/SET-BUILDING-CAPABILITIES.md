# Synthetic set-building capabilities and release gates

The local Prep/Live workspace uses the shared setlist engine. It does not scan
audio, write Serato/tag/music files, connect provider accounts or download audio.
All selectable paths must be invented `/Demo/` references.

A separately launched [selected-file read-only pilot](READ-ONLY-PREP-PILOT.md)
now permits individually selected real-path files without a folder scan or
source writes. It reuses this workflow and the shared engine, but does not
enable production mode in the main app. Validation still uses generated audio
and snapshots; actual Serato compatibility and signed-in searches remain gated.

## Supported input matrix

| Input | Support | Boundary |
|---|---|---|
| Static Serato `.crate` plus matching database snapshot | Selected-byte preview and explicit accept | Strict synthetic TLV parser; not real-format compatibility approval |
| CSV metadata | Preview/accept, exact ordered occurrences | Header exactly `id,title,artist,path,version`; UTF-8; 1–1,000 memberships |
| M3U/M3U8 | Preview/accept ordered absolute `/Demo/` paths | Metadata comes only from exact supplied library paths; missing paths stay unknown; no filename guessing |
| Catalog JSON | Existing schema_version 1 snapshot, explicit available crate ID | Immutable metadata/provenance retained; no remote URLs fetched |
| Main-app personal set | Same-origin preview or downloaded catalog handoff | Explicit opt-in mount, selected saved revision, synthetic paths only |
| Arbitrary text or provider CSV | Unsupported | Convert explicitly to the declared CSV schema; no heuristic row parsing |
| Spotify, SMPL, DJ Event Planner | No connected adapter | Access, policies, account authorization and schemas must be approved first |

Portable uploads are bounded to 512 KiB. Acquisition intake remains one synthetic
JSON record of at most 8 KiB. Unsupported inputs produce explicit errors.

## Main-app mount and personal-set handoff

Run only against new synthetic output directories:

```bash
AUTOCUE_WEB_DATA="$HOME/Desktop/MASTER-TODO/outputs/06-main-synthetic" \
AUTOCUE_SYNTHETIC_PREP="$HOME/Desktop/MASTER-TODO/outputs/06-main-prep" \
AUTOCUE_PORT=8770 .venv/bin/python -m web.server
```

The opt-in requires both directories directly under the specified outputs
directory, with `06-` names and no directory symlinks. Default main-app launches
do not enable the mount or change existing behavior.
In synthetic mode the main app starts no audio/scan workers, does not enumerate
Serato folders and rejects audio, tag, scan and cue-review mutations.

The workspace is at `/setlist/` and uses its own session token and API prefix.
It shares the existing static UI/flow implementation, not a second matcher or
ranking implementation. The main app keeps its normal token and APIs.

For a selected synthetic personal crate, **Preview selected personal set in
Prep** reads its stored immutable catalog metadata and membership order.
The link contains the selected revision; changed revisions reject preview.
Preview does not replace the working set until explicit acceptance.
The download handoff provides the same supported catalog contract for the
standalone demo. Neither handoff stats or hashes selected audio paths.

This is a one-way, explicit handoff into a separate event working session.
Editing Prep never silently overwrites the original personal crate.
Saving a Prep event produces the verified event JSON/M3U8 artifacts; it is not
a reverse synchronization API for the main personal-crate store.

## Build modes

- **Start from playlist order:** preserve imported memberships and duplicate
  positions; append matched event requests/cues under the existing contract.
- **Recommend from imported source only:** rank only the selected source's exact
  available file IDs with the shared engine. Matched requirements outside that
  source are labeled out-of-pool, not missing, and cannot be silently exported.
- **Use available synthetic library:** rank the wider supplied synthetic library.
  The imported source remains intact for switching back.

An out-of-pool event requirement blocks save until the DJ explicitly chooses a
mode that includes it. Hard exclusions, requested versions and confirmed exact
files cannot be bypassed by switching scopes. Live ignores Prep scope.

## Planning and discovery evidence

Ordered starting sets support occurrence-level phase assignment, pinned
positions and per-phase duration targets. Unknown durations stay unknown; known
seconds are shown as a subtotal rather than a fabricated complete duration.

Suggested sequence is a read-only preview: phase grouping then shared-engine
rank, with pinned positions fixed. Explicit acceptance is revision-bound and
retry-safe. No transitions, beat matching, key compatibility or energy curve
are invented. This is not a complete performance-ready mix.

Library snapshot counts and a read-only multiple-file report group files using
the shared song identity helper. Versions are preserved; the report offers no
delete, move, flatten or cleanup action. Repeated playlist memberships remain
separate from multiple physical-file metadata records.

## Provider access findings

Public pages checked on 2026-11-12. These findings do **not** establish API rights
or a subscription's permission to automate access.

| Destination | Public evidence | Enabled here |
|---|---|---|
| [Direct Music Service](https://www.directmusicservice.com/) | Professional-DJ qualification gate | Manual homepage only |
| [Late Night Record Pool](https://www.latenightrecordpool.com/) | Public application shell; access contract not established | Manual homepage only |
| [BPM Supreme](https://bpmmusic.io/supreme) | Public site responds; no authorized catalog API verified | Manual homepage only |
| [DJcity](https://www.djcity.com/) | Public search form and direct URL replay verified | Browser search link |
| [Beatport](https://www.beatport.com/) | Public search form and direct URL replay verified | Browser search link |
| [Beatport Open Format](https://www.beatport.com/open-format) | Public open-format catalog | Manual homepage only |

All 23 reference pools now have verified public destinations in the missing-song
queue. Together with two Beatport destinations this gives 25 provider actions.
DJcity/Beatport have public browser search links. Heavy Hits/XMiX have observed
search links labeled sign-in required; post-login results remain unverified.
The other 21 actions explicitly provide copy/open fallbacks, not connected
search. See the [complete evidence matrix](PROVIDER-SEARCH-LINKS.md), including
Latino Music Pool's HTTP 500 search and eligibility restrictions.

No undocumented endpoints, fabricated provider search URLs, authenticated
scraping, subscriptions, purchases or direct downloads are implemented.
Provider listings never count as local fulfillment.
See [browser search compatibility](PROVIDER-SEARCH-LINKS.md). Ordinary verified
search links require no API/account connection; the authorization gates below
apply to connected access, not manually opening these public browser searches.

Spotify's [playlist metadata documentation](https://developer.spotify.com/documentation/web-api/reference/get-playlists-tracks)
exists, but its [developer policy](https://developer.spotify.com/policy) imposes
significant restrictions, including streaming/mixing and business-use rules.
A metadata-only import needs product-specific policy/access review; documentation
is not authorization. Do not use Spotify audio in this DJ workflow.
[DJ Event Planner](https://www.djeventplanner.com/) has a public product site,
but no authorized adapter contract or test account was established. SMPL access
has not been verified. No credentials were collected.

## External gates that cannot be completed by synthetic implementation

| Gate | Required evidence before enablement |
|---|---|
| Connected providers / logged-in search verification | Authorized test access and official search behavior; APIs/downloads additionally require permitted access, terms, scopes, rate limits and rights |
| Spotify/SMPL/DJEP | Approved use case, documented authorized schema/API, consent, attribution, disconnect/deletion and secure credential handling |
| Licensed curated crates / charts | Permission/license to use metadata and collections; source provenance and refresh contract |
| Real Serato/library compatibility | Separately authorized selected read-only files and disposable DJ-software compatibility validation |
| Real acquired-file reconciliation | Authorized selected file scope and established metadata/availability verification; no filename/provider-listing equivalence |
| Independent 95/100 design approval | An independent review of rendered screens and usability; automated accessibility checks are not a design score |
| Audio cleanup, connected downloads, packaging/accounts | Separate opt-in scope and safety/rights review; prohibited by current no-media-write scope |

These are blocked release gates, not completed features. There is deliberately
no production-ready or full Crate Hackers parity claim.
