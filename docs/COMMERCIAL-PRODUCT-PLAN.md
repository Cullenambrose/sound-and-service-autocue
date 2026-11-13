# Commercial product plan: AI library management, crates and set building

Research and repository audit: November 12, 2026.
Implementation baseline: `570b7b9`, branch `feat/prep-live-flow`.
Status: proposed roadmap, not an implementation or compatibility certification.

An initial integrated local alpha now spans all six delivery areas. See the
[implementation checklist and verification results](SIX-STAGE-IMPLEMENTATION.md)
and [Studio launch guide](STUDIO.md). This does not mark the roadmap complete.

## 1. Executive recommendation

Build a **local-first DJ preparation and performance assistant** whose AI can
understand an instruction, search the actual library, propose useful changes,
and operate the existing application through narrow, audited tools.

The missing capability is not simply a chat box or another recommendation
weight. It is the complete loop:

**Understand -> retrieve evidence -> plan -> preview -> approve -> execute ->
verify -> recover.**

Use the existing Python matcher, setlist engine, catalog and revisioned saves.
Do not replace them with a language model that invents tracks, paths or
successful exports. Add a whole-set planner around the shared recommendation
services, not a second identity/matching implementation.

Our differentiator should be the connection between:

1. The music and exact edits the DJ actually owns.
2. The client's event, requests, exclusions and timeline.
3. Crate organization and explainable whole-set planning.
4. Reviewed cue, phrase, vocal and transition evidence.
5. A verified pre-gig package and useful, isolated Live recommendations.
6. Personal learning from the DJ's explicit feedback and actual play history.

**Correction to the previous handoff:** the seven completed workstreams made
the selected-file read-only pilot more usable and robust. They did not complete
the commercial product backlog. There is substantial independent implementation
work left; missing personal files or signed-in accounts need not block all of it.

### Product and release boundaries

- **First paid release:** macOS, local library, Serato-first preparation,
  AI search/organization, smart crates, constrained set planning, reliable
  reviewed exports, offline core and commercial installation/support.
- **Full Crate Hackers feature parity:** additionally includes validated Windows,
  other DJ destinations, authorized streaming metadata import/export,
  recoverable cleanup, lawful content offerings and provider connections where
  commercially permitted. Do not claim full parity until these pass.
- **Beyond parity:** evidence-backed transition/cue planning, event readiness,
  personal DJ memory, counterfactual explanations, alternatives and optional
  performance assistance.
- **Separate companion-app scope:** Banger Button features are not all Crate
  Hackers desktop features. Track them separately rather than inflating the
  competitor or confusing cue markers with a live sample-pad player.

This plan does not authorize scanning personal libraries, modifying originals,
connecting accounts, uploading data, downloading models, incurring AI charges,
or publishing the application. Those are implementation/release gates, not
reasons to stop designing and testing the underlying services.

## 2. What Crate Hackers demonstrably does

Research used public, first-party product pages and maintained help articles.
The detailed guides identify Crate Hackers 12.0.8, checked September 23, 2026;
the homepage advertises v12. These establish documented behavior, not an
independently exercised paid-account test.

### 2.1 AI assistant: documented workflow

The official [Ask Crate Hackers guide][CH-AI] documents:

1. Scan local folders and connect the music drive.
2. Open the Home-screen assistant.
3. Use `@` to attach an available track, DJ-software source or streaming source.
   Screenshots can also be attached or pasted.
4. Ask it to find an existing curated crate, build a new list, find compatible
   songs, search the library by metadata, inspect missing paths or show duplicate
   groups.
5. Review the song-list card. A chat suggestion is not necessarily saved.
6. Ask it to save the reviewed list to Your crates or export it to a destination.
7. Check the real resulting crate/playlist and local matches.
8. Save repeatable instructions as an automation and rerun them on demand.
   These are not scheduled background jobs.

Documented examples include wedding preparation, BPM/key/genre filters,
screenshot transcription with separate Must Play and Do Not Play lists,
wordplay transitions, local-file locations and duplicate-space estimates.

Important documented limits:

- Some export requests can execute without a second confirmation.
- Suggestions cannot guarantee a good transition or correct edit.
- Missing-file inspection checks known paths; it does not recover deleted
  files or automatically locate moved ones.
- Chat cannot download audio from a song list.
- Streaming export creates a new playlist, up to 200 tracks; it cannot append
  to an existing playlist.
- Chat cannot permanently empty the app's Recycle bin.
- A chat answer does not prove that a write/export succeeded.

**Our improvement:** mutations should always have a visible, revision-bound
proposal and a verified receipt. An explicit approval can authorize exactly
that proposal; natural-language intent alone must not grant broad file access.

### 2.2 Library, crates and cleanup

The maintained guides document:

- User-selected music folders, software selection, local metadata indexing,
  watched-folder change detection, explicit rescan/sync and external-drive
  support. Scanning is separate from copying or recycling files. [CH-SCAN]
- A crate builder starting from Spotify, a track, text, CSV or curated crates;
  saved results appear in Your crates. The documented builder limit is
  200 tracks. [CH-BUILD]
- Text import with artist/title preview and swap; CSV separator and column
  settings. These import song information, not audio. [CH-TEXT]
- Spotify playlist-link import, connection for private lists, rate-limit
  handling and local matching after import. [CH-SPOTIFY]
- Duplicate review using location, length, quality, cues and version; keeper
  choice, skip, recoverable recycling and restore. The guide explicitly says
  grouping is not proof of identical audio. [CH-DUP]
- Curated and regularly refreshed DJ crates marketed as part of the subscription.
  Their content is not ours to copy or redistribute. [CH-HOME]

### 2.3 Export and acquisition

- Serato export with local-file checks and Serato closed; M3U is a documented
  fallback. Counts and actual track loading must be checked afterward.
  [CH-SERATO]
- VirtualDJ export and rekordbox via M3U import. Thus the homepage's broad
  integration wording should not be read as proof of direct native database
  integration for every destination. PDF/CSV are reference lists. [CH-EXPORT]
- Connected Spotify, Tidal and Apple Music playlist export is documented in
  the AI guide. That is not audio ownership or universal DJ playback rights.
  [CH-AI]
- The homepage advertises connected DMS and Late Night downloads for active
  subscribers, while other providers are described as search-supported.
  The public sources do not establish the authentication, implementation,
  commercial agreements or an API we can reuse. [CH-HOME]
- The installation page describes signed/notarized desktop distributions,
  updates and macOS/Windows setup. This remains a vendor claim, not our
  installer verification. [CH-START]

### 2.4 Companion app, not core desktop parity

The separate Banger Button guides document:

- Serato/VirtualDJ now-playing detection and next-song recommendations.
- BPM/key filters, local-versus-streaming selection and local drag-and-drop.
- Always-on-top and several compact layouts.
- QR request sessions, optional voting, played/dismissed state and QR retirement.
- Eight sample pads, per-pad start/end and single/loop playback.
- Do Not Play warnings, rather than a universal deck playback block.
- Cached offline behavior and a limited offline sign-in grace period.
  [BB-START, BB-RECS, BB-SERATO, BB-REQUESTS, BB-CUES, BB-OFFLINE]

AutoCue's A-H cue markers are not equivalent to this sample player.

### 2.5 What public research cannot establish

No reviewed source discloses the precise LLM, prompts, tool schemas, embedding
index, ranking weights, fingerprint algorithm, hosting layout, AI data flows
or training data behind Crate Hackers.

The workflow is consistent with a tool-using assistant over indexed sources,
but that is an architectural inference, not a verified description of their
internals. We should reproduce useful outcomes independently, not attempt to
copy proprietary code, prompts, assets or licensed crate collections.

Two especially important evidence distinctions:

- Homepage duplicate marketing uses waveform-like examples; the support guide
  only establishes information-based grouping and explicitly disclaims proof
  of identical audio. Do not claim verified acoustic fingerprinting.
- The homepage advertises offline capability. That does not establish that
  conversational AI, new recommendations or connected services run offline.

## 3. Where our repository actually stands

The following is a code/documentation audit, not a new test execution. Prior
reported results were 1,025 Python tests, 79 Node tests and 21 demos; those are
software-regression evidence, not commercial or musical acceptance.

| Capability | Current evidence | Remaining work |
|---|---|---|
| Folder indexing | Selected-folder catalog index, durable jobs, cancellation/recovery and explicit root reassociation | Unified production workflow, watcher lifecycle, large-library/device validation |
| Catalog/crate intake | Immutable JSON snapshots, selected Serato snapshot parser, strict portable intake | Broader source adapters and real-format/software certification |
| Vendor CSV | Manifest-backed converter exists in `web/vendor_import.py` | Verify each actual export variant and permitted content use; older docs claiming no adapter are stale |
| Exact local matching | Shared matcher, conservative version handling, reviewed catalog decisions | Unify identity/availability projections across supported surfaces; held-out match calibration |
| Personal crates | Revisioned ordered memberships, retry receipts and verified playlist ZIPs | Smart rules, folders/tags, reusable planning, AI commands and reverse handoff |
| Event Prep/Live | Shared ranker, requests, exclusions, scopes, pins, phase targets and separate Live state | Full event-context wiring and one production state model |
| Set sequencing | Phase grouping plus shared-engine rank; pinned positions retained | Whole-set optimization, duration feasibility, energy arcs and transition evidence |
| AI | Opt-in Gemini second opinion for cue plans | No equivalent library-chat/tool-execution product |
| Acquisition | Outbound actions for 25 destinations; selected-file verification pilot | Better verified search coverage, acquisition inbox and permitted connected providers |
| Duplicate handling | Read-only multiple-file metadata groups; research grouping/splits | Evidence levels, keeper review, reference repair and recoverable cleanup |
| DJ software | Serato read/write helpers and safeguards exist | Supported app integration and real closed-app/import/rollback certification |
| rekordbox | Adapter is explicitly a stub | M3U compatibility first; native features only if separately supported |
| Cue/analysis engine | Substantial worker, review, diagnostics and benchmark scaffolding | Held-out human musical evidence, model licensing and unresolved v7 gates |
| Installation | Worker package and repository-run pilot launcher/preflight | Complete desktop distribution, signing, updates, entitlements and support |
| Performance | Generated 1,000-selected-file Live warm p95 185.44 ms in prior report | Production-size indexed benchmarks; large media and actual storage behavior |
| Commercial operations | Not established by these milestones | Billing, privacy, licensing, release processes and customer operations |

Primary repository evidence:
[catalog](CATALOG.md), [setlist contract](SETLIST-ENGINE.md),
[supported set-building boundaries](SET-BUILDING-CAPABILITIES.md),
[read-only pilot](READ-ONLY-PREP-PILOT.md),
[v7 backlog](v7-completion-plan.md),
[current sequence implementation](../web/set_planning.py),
[workflow state](../web/setlist_flow.py),
[vendor converter](../web/vendor_import.py),
[rekordbox stub](../engine/library/adapters/rekordbox.py),
[cue-specific AI](../engine/gemini_assist.py).

## 4. Recommended AI architecture

### 4.1 Keep AI outside the trust boundary

```text
Chat / forms / command palette / approved automation
                 |
       Structured intent proposal
                 |
      Schema + permission validation
                 |
    Library search / shared matcher / set planner
                 |
     Evidence-backed proposal + exact change diff
                 |
      User approval bound to proposal and revisions
                 |
       Existing durable application commands
                 |
        Verified receipt + history / recovery
```

The AI interprets and explains. Trusted application services decide identity,
availability, policy, permissions and durable results. No model-generated SQL,
shell commands, arbitrary filesystem paths or arbitrary network destinations
are executable tools.

### 4.2 Unify the data, not every UI in one rewrite

Extend the catalog's durable identities and projections incrementally:

| Entity | Purpose |
|---|---|
| Song/work identity | Search/request grouping; not proof of the same recording |
| Recording/edit identity | Original, remix, clean, explicit, intro, extended and other variants |
| Physical file identity | Exact location, root, stat evidence, content hash and availability |
| Analysis evidence | Extractor/model version, measured values, confidence and provenance |
| Source membership | Immutable imported occurrence, ordinal and source revision |
| Personal/smart crate | Editable collection or versioned query definition |
| Event set membership | Exact occurrence, selected file/edit, phase, pin and planned play window |
| Play/feedback event | Observed history or explicit DJ assessment, with source and time |
| Proposal/action receipt | Approved change, revisions, result, recovery and audit evidence |

Do not merge recordings because normalized titles match. Do not confuse a
tag-sensitive whole-file SHA-256 with an audio fingerprint. Preserve the
existing exact-file guarantees while adding separate audio-equivalence evidence.

The catalog and Prep pilot currently have different stores and a one-way
handoff. Define an application service boundary, then migrate through an
explicit, backed-up import with reconciliation counts and rollback. Avoid
dual authoritative stores or silent reverse synchronization.

Wire full event phase, special moments, requests and timeline through the
supported APIs. In particular, audit the workflow's currently fixed
`phase="first_dance"` context rather than treating engine-level phase support
as complete event integration.

### 4.3 Search before embeddings

1. Parameterized SQLite queries for exact IDs and numeric/range filters.
2. SQLite FTS5 for title, artist, tags, notes and crate text. [SQLITE]
3. Existing matcher for noisy requests and supported version parsing.
4. Curated/explicit mood and use-case tags with provenance.
5. Optional local semantic retrieval only if a measured benchmark shows
   improvement on mood, similarity or natural-language search.

Start without a vector database, distributed services or a knowledge graph.
An embedding match may retrieve an idea, but cannot prove a clean version,
local availability, recording identity or safe transition.

### 4.4 Model/runtime strategy

- A provider-neutral interface accepts a bounded context and returns a strict
  intent schema. A schema-valid response is still untrusted.
- Start with a deterministic fake provider and a golden intent suite so all
  application tools can be implemented and tested without paid requests.
- Benchmark a small, commercially permissible local instruction model on
  actual target hardware before choosing weights, minimum RAM or download size.
  Ollama documents local schema-constrained output; this is a useful evaluation
  path, not a required customer-installed production dependency. [OLLAMA]
- Keep core search, crates, constraints, export and recovery fully usable
  without any model. Show the difference between offline application features
  and unavailable offline conversation.
- Optional cloud AI is separately opt-in, with a visible data preview,
  provider disclosure, minimal selected metadata, spending limit and timeout.
  Do not send audio, full paths, private client details or the whole library.
  Use app-local opaque IDs where possible.
- Existing Gemini cue assistance is a separate capability; its presence does
  not mean library chat or cloud billing is already solved.
- No foundation-model training or autonomous experiment swarm is needed for v1.
  Fine-tuning is justified only by a measured failure that simpler tools cannot
  solve, with rights-cleared examples.

### 4.5 Tool catalog and authority levels

| Authority | Initial tools | Execution contract |
|---|---|---|
| Read | `search_library`, `get_track_evidence`, `list_crates`, `get_crate`, `library_health`, `get_event`, `get_play_history` | Selected dataset/root scope, bounded results, explicit unknowns |
| Propose | `propose_crate`, `propose_smart_rule`, `propose_set`, `propose_reorder`, `propose_cleanup`, `preview_export` | No persistent user-library changes; return proposal ID, evidence, diff and source revisions |
| Apply app-owned state | `apply_crate_proposal`, `apply_set_proposal`, `save_automation` | Exact approval, stale-revision rejection and idempotent request/receipt |
| External writes | `publish_playlist`, `apply_tag_plan`, `quarantine_files`, `restore_files` | Separately enabled permission, fresh preflight, backup/journal, app/process checks and verification |

Do not expose permanent deletion, purchases, deck control or unrestricted
download tools to the initial assistant.

An action proposal includes dataset/event IDs, base revisions, selected exact
file IDs and evidence, constraints, added/removed/reordered membership IDs,
unknowns, destination, expiration and an integrity digest. Approval authorizes
only that digest. Revalidation may reject it, never expand it.

Treat imported text, metadata, lyrics, screenshots, notes and provider results
as untrusted data. They cannot change tool permissions or become instructions.
Validate result IDs against the retrieved set, validate semantics after schema
parsing, bound tool loops/tokens/time, and log failure without leaking private
content. An AI outage must not interrupt a saved set or playback software.

## 5. AI library and crate management

### 5.1 Read-only assistant

Support concrete questions grounded in indexed evidence:

- "Show clean 90s R&B below 95 BPM that I haven't played in the last year."
- "What is missing from Saturday's ceremony and dance sets?"
- "Which files are on an offline drive?"
- "Show alternate edits of these songs without replacing anything."
- "Find forgotten dinner tracks in these three crates."
- "Why was this song selected, and why wasn't that one included?"

Unknown metadata remains unknown. "Never played" must say whether it means no
observations in imported history, not lifetime certainty. A genre/year/clean
restriction cannot be guaranteed from an unknown or AI-guessed tag.

### 5.2 Smart crates and organization

Build a typed, versioned rule language usable by both a visual builder and AI:

- Genre/style, release-year range, BPM/key, measured energy, duration.
- Reviewed clean/explicit state and edit type.
- Selected roots/crates, file availability, rating and user tags.
- Last played, play count, last added, never observed in history.
- AND/OR groups with a bounded nesting depth, stable sort and optional limit.
- Include/exclude specific identities or exact versions.

Natural-language requests compile into a visible rule, not opaque generated
code. Distinguish:

- **Smart crate:** recomputed from a saved rule against current evidence.
- **Static crate:** explicit saved membership/order.
- **Event snapshot:** frozen selected versions and file evidence for a gig.

Add folders/categories, batch membership edits, rename, copy, merge and split.
Show membership diffs and source provenance. Removing from a crate never means
deleting audio. Smart-crate changes show when/why membership changed.

### 5.3 Library health, duplicate review and file organization

Separate evidence tiers:

1. Identical whole-file hashes.
2. Identical decoded-audio evidence using an approved implementation.
3. Similar acoustic fingerprints or matching metadata/duration.
4. Related but intentionally distinct edits.

Only the first two can support an exact-equivalence statement, and audio
equivalence does not make one file disposable: cues, grid, quality, history and
crate references may differ. Never label an upscaled bitrate as better quality
from the bitrate tag alone.

Deliver keeper comparison, audition links, cues/grid presence, quality evidence,
reference counts, protected roots, skip and reversible quarantine. Preview
every affected crate and reference. Never recycle the only available copy or
an unresolved alternate edit. If references cannot be safely repaired, block
cleanup rather than leave broken crates.

For folder flattening, rename, consolidation and metadata cleanup:

- Plan every source/destination and collision before touching files.
- Copy -> verify -> update only supported references -> verify again.
- Retain originals until the user separately approves retirement.
- Never silently relocate music or regenerate third-party databases.
- Journal each step; cross-device moves and third-party writes are not one
  atomic transaction. Recovery must report partial completion explicitly.
- Undo may conflict with later edits; detect that instead of overwriting.

All source-mutating features remain opt-in and disabled during performances.

### 5.4 History, memory and repeatable workflows

- Import selected play histories with provenance, timezone, repeat/dedup rules
  and recording/version identity review.
- Store explicit DJ tags such as floor filler, dinner-safe and avoid-for-event;
  distinguish these from observed playback and AI suggestions.
- Learn transition preferences from actual sequences, but do not infer crowd
  success from a play count alone.
- Provide editable explanations, reset/export/delete controls and no
  cross-customer learning by default.
- Save reviewed pre-gig checks and crate recipes as typed workflow templates.
  Start with manual runs; scheduling needs separate permissions, maintenance
  windows and recovery. No unattended source mutation.

## 6. AI-assisted whole-set construction

### 6.1 Input contract

Accept an event brief, approved request lists, phase schedule, selected
library/crates, intended set length and musical preferences. Import text/CSV,
screenshots and provider playlist metadata through independent adapters with
preview and provenance. OCR/transcription is not matching or approval.

Expose hard constraints separately from preferences:

| Hard constraints, only when explicitly selected | Soft preferences |
|---|---|
| Do Not Play, exact must-play edit, verified-clean-only | Genre continuity, variety, rediscovery |
| Available local files, chosen source scope | Harmonic compatibility and manageable tempo changes |
| Required moments, locked/pinned positions | Energy arc and DJ transition memory |
| Explicit BPM bounds, duration tolerance | Avoid artist clustering and repetitive selections |
| Explicit repeat/version policy | Prefer intros/outros with sufficient evidence |

The current engine treats BPM/key as soft compatibility terms and has specific
existing hard exclusions. New hard policies must be an explicit, versioned
planner contract behind an opt-in gate. Do not silently reinterpret the current
ranker or change the 0.6 matcher / 0.7 event-sync cutoffs.

Unknown clean state fails a strict verified-clean filter. Unknown BPM cannot
satisfy a strict maximum. Unknown duration cannot prove a time target. Surface
these as data gaps; do not make up values or silently relax the requirement.

### 6.2 Planning algorithm

1. Resolve requests with the shared matcher and retain ambiguity/version errors.
2. Materialize candidate exact files from the chosen scope and fresh evidence.
3. Apply hard policies, preserve must-play/pinned occurrences and calculate
   obvious capacity/duration conflicts before optimization.
4. Reuse shared scoring terms for candidate utility and pairwise compatibility;
   expose a reusable scoring contract instead of copying formulas.
5. Use bounded beam search with deterministic tie-breaking, then local
   swaps/insertions for the sequence. Start with this inspectable approach.
6. Score whole-set diversity, energy targets, required coverage and transition
   costs. Recheck all hard constraints after every generated proposal.
7. If benchmarks show beam search falsely rejecting feasible cases, add a
   narrowly scoped constraint solver for feasibility; never describe heuristic
   failure as mathematical proof of impossibility.
8. Return two or three distinct, feasible alternatives when possible:
   conservative, higher-energy, discovery-focused. Explain the tradeoffs.

Freeze planner version, settings, library revision and seed in each proposal.
The LLM may translate the brief and summarize evidence; it does not choose
fictional files or certify a sequence by narrative.

### 6.3 Timing and transitions

Track three different quantities:

- Full-file runtime subtotal.
- Planned play windows using reviewed entry/exit positions.
- Estimated elapsed set time after explicitly modeled overlaps.

For simple sequential overlaps, elapsed time is the sum of play windows minus
the overlap between adjacent windows. Bound each overlap against both tracks'
playable windows. Unknown or invalid windows mean timing is approximate or
unavailable, not an exact 90-minute certification.

Add transition cards showing:

- Both exact edits and measured/tagged BPM/key evidence.
- Proposed in/out cues, phrase lengths, vocal overlap and grid warnings.
- Tempo/key change assumptions and expected overlap.
- Evidence confidence and an audition/review requirement.
- Alternatives: clean cut, echo-out, wordplay, tempo reset or no recommendation.

BPM/key compatibility is not proof of a good transition. Wordplay suggestions
need lyric/title evidence with rights and uncertainty labels; do not invent
lyrics or claim the songs have been auditioned.

### 6.4 Interactive revision and final readiness

Support instructions such as:

- "Keep the first four songs, replace the next two, and end with this request."
- "Make dinner more soulful without going above the selected BPM cap."
- "Fit the remaining must-plays into 25 minutes; show the conflicts first."
- "Show what changes if I exclude this drive or this explicit version."

Pin positions and selected files; constrain repair to the selected scope.
Keep the original plan and display the exact diff. A changed event/request/
library revision invalidates affected approvals.

The pre-gig checklist reports required coverage, excluded conflicts, unresolved
versions, unknown durations, unavailable media, cue/grid warnings, destination
compatibility and export status. It cannot certify licensing, audible cleanliness
or venue readiness solely from metadata.

Live may read a frozen Prep snapshot and current played/request state, but it
must not rewrite the saved plan. A future now-playing adapter is observational
first; no automatic deck loading or playback.

## 7. Full parity and commercial gap matrix

| Workstream | First paid release | Full parity / later expansion | External acceptance needed |
|---|---|---|---|
| AI library search and actions | Grounded read/propose/apply tools and explanations | Richer personal workflows | Model/hardware quality and privacy review |
| Library ingestion | Selected roots, incremental indexing, progress, offline states | Watchers, huge/removable/cloud libraries | Representative permitted libraries/devices |
| Crate organization | Static/smart crates, folders, batch changes and snapshots | Sharing and collaborative edits | Customer usability evidence |
| Client list intake | Text/CSV preview and correction; optional local OCR | More formats and authorized planner connectors | Representative exports and access |
| Whole-set planning | Hard/soft constraints, timing, pins and alternatives | Rich transitions and learned preferences | Working-DJ audition benchmark |
| Cue preparation | Existing reviewed workflow and honest warnings | Promoted local event model and active learning | Held-out labels/listening and model rights |
| Cleanup/organization | Health reports and protected review | Recoverable cleanup, consolidation, flattening, tag plans | Disposable real DJ-library round trips |
| Serato publication | Validated M3U path plus gated native crate publication | More formats/versions and reviewed cue writes | Serato app/version verification |
| Other DJ software | State exactly what M3U destinations were tested | VirtualDJ, rekordbox, djay/Denon as documented per adapter | Hardware/software and format permissions |
| Pool acquisition | Verified outbound links, copy/open, missing queue and local intake | Authorized DMS/Late Night connections if obtainable | Provider terms, agreement and test accounts |
| Streaming services | Hide unavailable connectors; no unsupported playback claims | Authorized Spotify/Apple/Tidal metadata/playlist adapters | API approval, scopes, commercial and AI policies |
| Curated content | Original templates or user-owned imports | Licensed curator feeds/charts and creator tools | Licenses, contributor agreements and takedown process |
| Live assistance | Manual Live inputs with saved-Prep isolation | Now-playing, native drag, overlays and QR requests | Software integration and venue/network tests |
| Sample pads | Not required for core Crate Hackers desktop parity | Optional separate Banger Button-equivalent module | Audio routing, latency and sample rights |
| Platforms | Packaged macOS with published supported configurations | Signed Windows installer and tested updates | Signing accounts and test machines |
| Commercial service | Entitlements, offline behavior, billing, privacy and support | Team accounts, sync and collaboration | Business/legal/payment accounts |

No development task can guarantee a provider will grant access. If an agreement
cannot be obtained, ship the honest fallback and state the remaining parity gap.

## 8. Implementation work packages and dependencies

Each package includes its directly related tests, migrations, docs and measured
acceptance. Scope estimates are relative: **S** = a few focused engineering days,
**M** = roughly 1-2 engineer-weeks, **L** = roughly 3-5, **XL** = split after a
technical spike. These are planning bands, not calendar commitments; account
approval and real-world validation can take longer or remain unavailable.

### Foundation and AI product

| ID | Deliverable | Prerequisites | Size | Done when |
|---|---|---|---|---|
| P00 | Maintained capability registry, supported-platform contract, rights inventory and evaluation fixtures | None | M | Every advertised action maps to a tested implementation or visible unavailable state |
| P01 | Catalog/Prep service boundary, identity projections and backed-up state migration | P00 | L | One authoritative save path; imported order, receipts, provenance and revisions reconcile after restart |
| P02 | Indexed search, typed filters, metadata provenance and availability projection | P01 | M | Golden queries return exact eligible IDs, unknowns are explicit, 100k-file scale measured |
| P03 | Tool schemas, policy enforcement, proposal journal, approvals and verified receipts | P01 | L | Invalid/stale/injected/duplicate requests cannot bypass policy or duplicate mutations |
| P04 | First AI chat UI, source attachments and deterministic provider/evaluation harness | P02, P03 | L | Search -> proposal -> approved saved crate -> reload works end to end without a paid request |
| P05 | Local model bakeoff, optional provider adapter, privacy/cost controls | P04 | M | Published intent/quality/latency/RAM results and failure behavior; no unapproved data transfer |
| P06 | Smart crates, folders, bulk organization and manual-run recipes | P02, P03 | L | Same rule works through visual and AI interfaces; membership diffs and static snapshots verified |

### Set planning and library operations

| ID | Deliverable | Prerequisites | Size | Done when |
|---|---|---|---|---|
| P07 | Text/CSV mapping, screenshot/OCR preview and request-category intake | P03 | M | Uncertain transcription needs review; exclusions, order and versions survive acceptance |
| P08 | Full event-context/API wiring, typed planner policy and feasibility diagnostics | P01, P03 | M | Actual phase/timeline drives results; explicit hard policies do not change legacy defaults |
| P09 | Whole-set planner, timing model, pins, alternatives and repair | P02, P08 | L | Exhaustive small-fixture oracle checks, determinism and all hard constraints pass |
| P10 | Transition evidence and cue-readiness integration | P09 | L | Entry/exit/window warnings agree across UI/export and no unsupported safety claims appear |
| P11 | History intake, explicit DJ memory and rediscovery | P01, P02 | M | Observed history is distinct from preferences; recording/version ambiguity and deletion/export controls work |
| P12 | Duplicate evidence and keeper-review UI | P01, P03 | L | Alternate edits remain distinct and exact/likely/related groups have demonstrable evidence |
| P13 | Quarantine/restore, reference repair, copy/consolidate and metadata plan executor | P12, P16 | XL | Generated crash matrix passes and disposable DJ-library round trips restore files and supported references |
| P14 | Watchers, acquisition inbox and large-library/per-device performance | P01, P02 | L | Scoped updates survive unmount/restart; no full-library rehash on ordinary chat queries |
| P15 | Missing-song queue, provider capability labels and adapter test harness | P03 | M | Search/listing/acquired/verified/playable remain distinct; provider failures do not imply fulfillment |

### Compatibility and commercial release

| ID | Deliverable | Prerequisites | Size | Done when |
|---|---|---|---|---|
| P16 | Versioned export adapters, readback verification and compatibility suite | P01, P03 | L | Export counts/order/files and rollback pass per advertised software/version |
| P17 | Authorized pool and streaming connectors | P15 plus external permission | XL | Official allowed access, account lifecycle, rate limits and round-trip tests pass |
| P18 | Native macOS shell, bundled worker, file pickers and complete installer | P00, P01 | L | Clean-machine install/launch/upgrade/uninstall works without developer setup |
| P19 | Accounts/entitlements, billing events, offline license and optional cloud-AI budget service | P00, P03 | L | Paid/trial/canceled/refunded/offline states work; no embedded service secrets |
| P20 | CI matrix, migration/fault tests, signing/update pipeline, diagnostics and support bundle | P18; uses P03 action evidence | L | Candidate can install, upgrade, roll back and submit a redacted diagnostic on supported systems |
| P21 | Usability/accessibility study, musical evaluation, privacy/legal review and beta program | Feature-specific dependencies above | L + external time | Independent evidence supports each release claim; critical issues have owners and fixes |
| P22 | Windows packaging and cross-platform filesystem/installer parity | P18, P20 | L | Clean Windows machines pass the same contract plus Windows-specific paths/locking cases |
| P23 | Licensed curated feeds, creator publishing and metadata provenance | P00, P03 plus licenses | L + external time | Content is original/authorized, versioned, attributable and revocable |
| P24 | Optional Live companion: now-playing, native drag, compact views and QR request service | P08, P11, P16, P19 | XL | Correct event/deck identity, offline degradation and request abuse/privacy tests pass |
| P25 | Optional sampler module and supported audio routing | P18; P22 for Windows claim | XL | Device/latency/start-stop/loop/drive-loss tests pass without affecting DJ playback |
| P26 | Optional end-to-end encrypted sync/team collaboration | P01, P19, threat model | XL | Conflict/revocation/key-recovery behavior verified; do not claim zero knowledge without design evidence |

P13 is deliberately not on the minimum paid-release critical path. Read-only
duplicate review can ship first; destructive parity must wait for actual
reference-repair and recovery evidence. P17, P22 and P23 remain required before
claiming the respective connected-service, Windows and licensed-content parity.

### Cue/model backlog that must not disappear behind the new AI UI

Keep the existing [v7 board](v7-completion-plan.md) authoritative for algorithm
stories, especially A08/A11/A13/A16, B06/B07, C03-C06 and D01-D05:

- Build a rights-cleared, recording-grouped, human-reviewed evaluation corpus.
- Benchmark imported-anchor assistance and audio-only behavior separately.
- Evaluate commercially permissible local embedding/event models; record both
  code and weight licenses.
- Integrate only models that beat the frozen baseline on held-out evidence.
- Add active learning and dataset/model/version provenance.
- Keep cloud experimentation/annotation optional, bounded and separately
  approved. This is not a prerequisite for useful library chat.

Generated tests can prove job recovery and contracts; they cannot prove cue
accuracy, vocal safety or musical quality. No automatic production promotion.

## 9. Delivery sequence and checkpoints

### Release A: useful AI crate-management alpha

Build P00-P04, then P06 and the essential P08 event wiring.
P05 can evaluate local inference alongside this work without blocking tools.

Demo: select a generated library/event -> ask for a constrained crate ->
inspect exact files and unknowns -> adjust through chat or forms -> approve ->
save -> restart -> find the same crate and verified receipt.

This is the highest-value next implementation milestone. It closes the
explicitly identified AI-management gap rather than repeating pilot polish.

### Release B: complete planning workflow

Add P07-P12, P14-P16 in dependency order. Begin P18/P20 packaging early.
Stage P10 transition claims behind the cue evidence gates.

Demo: ingest a client request list -> resolve variants -> generate a timed,
phase-based set -> compare alternatives -> pin/edit -> inspect missing songs ->
verify acquired test files -> freeze and export the selected exact plan.

### Release C: first paid macOS product

Finish P05, P18-P21 and P15/P16 acceptance on disposable real software/data.
Publish a narrow compatibility list. Ship only features that clear their gates.

No payment launch based solely on a synthetic demo. Full competitor parity is
not the release label for this narrower product.

### Release D: broad parity

P13, P17, P22 and P23 plus other validated export destinations.
Each connection/platform is a separate capability with its own acceptance.
Continue useful work if one external provider does not approve an integration.

### Release E: differentiated performance assistant

P24, stronger P10/P11 learning and cue/model promotion. P25/P26 only if demand
and reliable implementation justify their ongoing operational cost.

Do not make sync, sample playback, deck control or training a new foundation
model prerequisites for finishing the core product.

## 10. Commercial engineering and operating requirements

### Desktop distribution and deployment

Recommended first shell: a thin native macOS host around the existing web UI
and Python services, with native file dialogs and a controlled bundled-worker
lifecycle. Preserve Python analysis rather than rewriting the engine in Swift.
Time-box the packaging/file-access/native-drag spike before locking this choice.
The cross-platform shell choice should follow measured Windows requirements,
not force an immediate frontend rewrite.

Bind any local HTTP service to loopback with authenticated mutation routes,
origin/host validation, controlled static paths and private app-owned storage.
Treat webview/native bridge APIs as privileged capabilities. Limit filesystem
access to selected roots and explicitly granted write destinations.

Bundle a reproducible Python/runtime environment. Separate lightweight library
features from optional heavy analyzers/models. Declare storage/RAM requirements,
download sizes, model version/checksums, cancellation and removal.

Use Developer ID signing, hardened runtime, notarization and stapling for the
Mac distribution, with verified update signatures and staged channels.
Apple's notarization requirements apply to bundled executables too. [APPLE]
Windows needs its own signing, installer, permissions, Defender/SmartScreen
behavior and clean-machine QA; a Python script that runs on Windows is not
Windows product support.

### Accounts, billing and offline operation

- Hosted account/billing service only where needed; music remains local.
- OS credential storage and OAuth/system-browser authorization where supported.
  Never collect pool passwords in ordinary chat or logs.
- Server-validated entitlements and idempotent signed billing webhooks, including
  refund, cancellation, renewal, failed payment and chargeback transitions.
- Document a trial, device activation/revocation and offline grace policy.
  Do not lock access to existing local sets/exports during a gig because a
  remote license check is temporarily unavailable.
- Separate remote AI credits from basic local functionality. Hard spend/rate
  limits, usage reconciliation, current price versions and visible failures.
- Customer self-service, invoices/receipts, cancellation, data export and
  deletion; obtain legal/accounting review for applicable taxes/refund rules.

Recommended business model to validate: paid local core plus subscription
services for continuing curated content/sync and an explicitly budgeted AI
allowance. This is a hypothesis, not a pricing decision. Do not promise unlimited
cloud AI or perpetual cloud services financed by a one-time purchase.

### Rights, privacy and compliance

- Inventory shipped Python/native dependencies, model weights, training data,
  codecs, fonts/assets and content. Produce notices and an SBOM per release.
- Existing optional analyzers being installable is not proof that all code,
  model weights and transitive dependencies are cleared for this product.
- Essentia's published pretrained model terms include noncommercial licensing
  and a commercial licensing route. Do not select a convenient audio model
  without checking its exact artifact license. [ESSENTIA]
- Spotify's policy explicitly restricts AI ingestion/training and various
  commercial/streaming uses, while providing a particular playlist-metadata
  transfer exception. Keep any approved Spotify adapter deterministic and
  isolated from AI prompts/training; obtain product-specific review rather
  than claiming all metadata import is forbidden or all DJ usage is allowed.
  [SPOTIFY-POLICY]
- Review Apple Music, Tidal and each provider independently. Competitor access
  is not permission for us. Streaming playlist export is not a public
  performance license or evidence of deck support.
- Define local/cloud data flows, purpose, retention, subprocessors, consent,
  deletion and support-access policies. Minimize guest and client data.
- No default audio upload, private-library telemetry or use of customer
  libraries to train shared models. Redact paths, names and tokens in crash logs.
- Licensed curator material requires agreements, provenance, revocation and
  takedown procedures; do not redistribute competitor crate collections.

These are release-planning requirements, not legal advice or a completed
security/compliance review.

### Reliability, support and ongoing operations

- Schema/version migrations with before/after counts, backups and supported
  rollback; newer-state rejection without destructive downgrade.
- Crash/disk-full/unplug/network-loss/corruption testing for every write family.
- Bounded jobs, progress, cancellation, resume/retry and fair scheduling so
  background analysis cannot freeze search or Live.
- Opt-in redacted diagnostics, local operation history and export manifests.
- Status page and incident/runbook coverage for any hosted service.
- Support workflow, response expectations, refund path, compatibility docs,
  in-app help and troubleshooting without destructive database resets.
- Dependency/update monitoring, signed release provenance and rollback drills.
- Independent accessibility testing, keyboard/screen-reader support, high-DPI,
  long paths, Unicode, compact layouts and usable progress/error states.

## 11. Measurable acceptance and release gates

Targets below are proposed, not measurements already achieved. Record hardware,
storage, dataset, cold/warm conditions and software versions for each result.

| Area | Proposed gate |
|---|---|
| Identity/version safety | All golden exact-version/ambiguity/exclusion cases pass; zero silent substitution or fabricated local fulfillment |
| AI authority | Zero unauthorized mutations across prompt-injection, forged IDs, malformed tools and stale-approval fixtures |
| Intent extraction | At least 95% fully correct structured intent on a held-out 200-prompt suite; failures visibly abstain and never weaken hard policy |
| Grounding | Every displayed local result and factual file/metadata claim traces to returned evidence; unknown facts are not asserted |
| Smart crates | Visual and AI-generated equivalent rules return identical ordered IDs; incremental updates match clean recomputation |
| Planner feasibility | All returned sets satisfy every hard constraint; compare bounded small cases against exhaustive enumeration and report heuristic search exhaustion honestly |
| Planner musical usefulness | At least three independent working DJs review at least 30 varied set briefs; target 80% rated usable with minor edits, with no critical safety violations; report disagreements |
| Musical cue quality | Publish held-out per-event/genre precision, recall, timing and abstention; agree thresholds before model promotion |
| Data integrity | Fault tests show prior coherent state or explicit resumable partial-operation state; no acknowledged save lost and no false verified export |
| Export compatibility | Exact membership/order and playback checks on each advertised software/version; unresolved inputs block rather than silently disappear |
| Recovery | Backup/restore, update rollback and quarantine restoration tested on actual supported filesystems/software with disposable data |
| Core scale | Benchmark 10k/50k/100k file catalogs; target warm search p95 <300 ms and deterministic next-song p95 <200 ms at the claimed supported scale |
| Whole-set latency | Initial target: 60-position plan from 5k eligible candidates in <5 s warm, excluding AI; bounded quality-preserving candidate selection and measured memory |
| Local AI | Hardware-specific intent p95 target <5 s after warm-up; disclose cold-load latency/RAM and retain full non-AI functionality if unmet |
| Offline | Saved crates/sets, local search, manual edits and supported exports work without internet; remote features clearly show unavailable/cached status |
| Installation | Clean-machine install/upgrade/uninstall, signing/notarization and rollback pass across the published platform matrix |
| Commercial operations | Test trial/purchase/cancel/refund/offline transitions; privacy/terms/licenses and incident/support ownership approved |

Separate **software correctness**, **real file/software interoperability**,
**musical quality**, **commercial permission**, and **customer usability**.
One passing category does not substitute for another.

## 12. Work that can proceed independently

### Can be implemented and tested without personal files or paid accounts

- P00-P04: capability map, service/identity contracts, search, tools, proposal
  safety, AI-provider stub and first chat-to-crate end-to-end workflow.
- P06-P09: smart crates, visual rule builder, typed list intake, event wiring,
  planner and exact small-case feasibility oracle.
- Generated-data portions of P10-P16: transition contracts, history adapter
  fixtures, duplicate evidence UI, quarantine executor fault tests, watcher
  simulation, acquisition inbox and export readback tests.
- Package preparation, clean disposable environment checks, migration tests,
  CI, update protocol and redacted diagnostic tooling.
- Billing/entitlement mock workflows, local offline behavior and hosted-service
  contracts without creating accounts or exposing customer data.
- Accessibility automation, keyboard workflows and generated large libraries.
- License inventory, public policy research, original templates, user docs,
  evaluation harnesses and beta/release checklists.
- Build local-model adapter/evaluation tooling without downloading weights,
  sending private data or spending money.

### Requires external inputs eventually, not necessarily the user's labor

| Gate | Why it is external | Can someone other than the user provide it? |
|---|---|---|
| Real library/export compatibility | Synthetic parsers do not prove vendor/software behavior | Yes: consenting beta DJs and disposable test collections |
| Musical/listening labels | Software tests cannot judge phrasing, edit cleanliness or crowd suitability | Yes: contracted/volunteer working DJs with a rights-cleared corpus |
| Provider account/search/API acceptance | Requires legitimate access and permitted product use | Yes: authorized partner/test account owner |
| Developer signing and payment publication | Requires organization-controlled identities, agreements and funds | Yes: designated release/business owner |
| Legal/privacy/content licensing | Requires business decisions and appropriate professional review | Yes: counsel and licensors |
| Platform/hardware acceptance | Requires supported machines/drives/software | Yes: QA team or managed test lab |
| Independent usability/accessibility | Self-review is insufficient evidence | Yes: outside testers/reviewers |

Do not treat these as reasons to stop all development, and do not claim they
have been satisfied by generated fixtures. Keep each blocked gate visible.

## 13. Immediate next implementation batch

1. **P00:** publish a machine-readable capability list and a generated
   library/event benchmark with explicit unsupported features.
2. **P01:** introduce the shared application-service boundary and read models;
   specify migration/reconciliation before changing persistent ownership.
3. **P02:** implement typed library search with provenance and availability.
4. **P03:** implement read/propose/apply schemas, approval-bound diffs, journal,
   revision checks and retry receipts using existing persistence patterns.
5. **P04:** add source-aware chat plus a deterministic test provider and
   complete "build -> inspect -> approve -> save -> resume" acceptance flow.
6. **P06/P08:** ship persistent smart crates and genuine event context, then
   begin P09 whole-set planning.

Commit coherent, verified slices; do not bundle a UI rewrite, new model,
state-store migration and media writer into one unreviewable change.

This batch can make substantial progress without the user's music or accounts.
It is the recommended continuation after approval of this research/plan.

## Sources

Public sources accessed November 12, 2026. Competitor guides are first-party
documentation, not an independently verified account session.

- **CH-HOME:** [Crate Hackers v12 product page](https://www.cratehackers.com/).
- **CH-START:** [Installation and four-step onboarding](https://www.cratehackers.com/ch12).
- **CH-AI:** [Ask Crate Hackers 12: prompts, use cases and limits](https://help.cratehackers.com/support/solutions/articles/69000883869-ask-crate-hackers-12-prompts-use-cases-and-limits).
- **CH-SCAN:** [Choose your DJ software and scan your music library](https://help.cratehackers.com/support/solutions/articles/69000878645).
- **CH-BUILD:** [Build your first crate](https://help.cratehackers.com/support/solutions/articles/69000878646-build-your-first-crate).
- **CH-TEXT:** [Import a song list from text or CSV](https://help.cratehackers.com/support/solutions/articles/69000862878-import-a-song-list-from-text-or-csv).
- **CH-SPOTIFY:** [Import a Spotify playlist](https://help.cratehackers.com/support/solutions/articles/69000878648).
- **CH-DUP:** [Review duplicates and restore removed tracks](https://help.cratehackers.com/support/solutions/articles/69000878651).
- **CH-EXPORT:** [Export your crate to DJ software](https://help.cratehackers.com/support/solutions/articles/69000878650).
- **CH-SERATO:** [Export a crate to Serato DJ](https://help.cratehackers.com/support/solutions/articles/69000872602-export-a-crate-to-serato-dj).
- **CH-MISSING:** [Fix missing songs, empty exports or a stalled library scan](https://help.cratehackers.com/support/solutions/articles/69000878653).
- **BB-START:** [Get started with Banger Button](https://help.cratehackers.com/support/solutions/articles/69000878042-get-started-with-banger-button).
- **BB-RECS:** [Find better recommendations](https://help.cratehackers.com/support/solutions/articles/69000883875-find-better-recommendations-in-banger-button).
- **BB-SERATO:** [Connect Banger Button to Serato DJ](https://help.cratehackers.com/support/solutions/articles/69000883873).
- **BB-REQUESTS:** [Guest song requests with a QR code](https://help.cratehackers.com/support/solutions/articles/69000883876).
- **BB-CUES:** [Use Cues to play samples](https://help.cratehackers.com/support/solutions/articles/69000883878).
- **BB-OFFLINE:** [Updates and offline use](https://help.cratehackers.com/support/solutions/articles/69000883880).
- **SQLITE:** [SQLite FTS5](https://www.sqlite.org/fts5.html).
- **OLLAMA:** [Structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
- **APPLE:** [Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
- **ESSENTIA:** [Library, model and dependency licensing](https://essentia.upf.edu/licensing_information.html).
- **SPOTIFY-POLICY:** [Spotify Developer Policy](https://developer.spotify.com/policy).

[CH-HOME]: https://www.cratehackers.com/
[CH-START]: https://www.cratehackers.com/ch12
[CH-AI]: https://help.cratehackers.com/support/solutions/articles/69000883869-ask-crate-hackers-12-prompts-use-cases-and-limits
[CH-SCAN]: https://help.cratehackers.com/support/solutions/articles/69000878645
[CH-BUILD]: https://help.cratehackers.com/support/solutions/articles/69000878646-build-your-first-crate
[CH-TEXT]: https://help.cratehackers.com/support/solutions/articles/69000862878-import-a-song-list-from-text-or-csv
[CH-SPOTIFY]: https://help.cratehackers.com/support/solutions/articles/69000878648
[CH-DUP]: https://help.cratehackers.com/support/solutions/articles/69000878651
[CH-EXPORT]: https://help.cratehackers.com/support/solutions/articles/69000878650
[CH-SERATO]: https://help.cratehackers.com/support/solutions/articles/69000872602-export-a-crate-to-serato-dj
[BB-START]: https://help.cratehackers.com/support/solutions/articles/69000878042-get-started-with-banger-button
[BB-RECS]: https://help.cratehackers.com/support/solutions/articles/69000883875-find-better-recommendations-in-banger-button
[BB-SERATO]: https://help.cratehackers.com/support/solutions/articles/69000883873
[BB-REQUESTS]: https://help.cratehackers.com/support/solutions/articles/69000883876
[BB-CUES]: https://help.cratehackers.com/support/solutions/articles/69000883878
[BB-OFFLINE]: https://help.cratehackers.com/support/solutions/articles/69000883880
[SQLITE]: https://www.sqlite.org/fts5.html
[OLLAMA]: https://docs.ollama.com/capabilities/structured-outputs
[APPLE]: https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
[ESSENTIA]: https://essentia.upf.edu/licensing_information.html
[SPOTIFY-POLICY]: https://developer.spotify.com/policy
