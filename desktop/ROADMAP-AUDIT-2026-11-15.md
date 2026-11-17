# BOOTH BUDDY roadmap and implementation audit — November 15, 2026

**Assessment:** a substantial local-alpha service/backend and browser product, with an early native SwiftUI front end. Several Release A/B capabilities exist in software, but the native primary workflow is incomplete. This is not an accepted Release B or a Release C paid product. More visual polish is currently less valuable than completing a reliable native event-to-export journey.

**Audit scope:** repository roadmaps, research/development boards, feature handoffs, current dirty-worktree source, CI configuration and the recent native verification artifacts. This audit ran a synthetic endpoint-field-validator check; it did not rerun the full regression suite, start models, access accounts, scan personal music or perform installed-DJ-software acceptance. The only new repository file from this audit is this report. Existing edits are preserved. No commit or push was performed.

## 1. Which plan describes the current product?

| Document family | Authority and interpretation |
|---|---|
| [COMMERCIAL-PRODUCT-PLAN](../docs/COMMERCIAL-PRODUCT-PLAN.md) | Product identity, differentiation, P00–P26 work packages, Releases A–E and proposed acceptance criteria. BOOTH BUDDY is the product; AutoCue is a component. The November 12 baseline gap table is historical, not an inventory of everything still missing today. |
| [SIX-STAGE-IMPLEMENTATION](../docs/SIX-STAGE-IMPLEMENTATION.md), [STUDIO](../docs/STUDIO.md) | Implementation checkpoints and recorded evidence for the current local-alpha backend/browser product. Appended checkpoints can supersede earlier claims. Implemented service functionality is not necessarily reachable from SwiftUI. |
| [v7 completion board](../docs/v7-completion-plan.md), [PM audit](../docs/PM%20BOT%20READ%20ME.md), [cue logic](../docs/cue-logic.md), [analysis format](../docs/ANALYSIS-FORMAT.md) | Separate cue-analysis, grid, event-model and musical-validation workstream. A completed software story is not algorithm promotion or listening acceptance. Later cue-logic/diagnostic additions supersede some older technical limitations in the PM audit. |
| [FOUNDATION](../docs/FOUNDATION.md), [WORKER](../docs/WORKER.md), [CATALOG](../docs/CATALOG.md), [LIBRARY-OPERATIONS](../docs/LIBRARY-OPERATIONS.md), [SET-PLANNER](../docs/SET-PLANNER.md), [SETLIST-ENGINE](../docs/SETLIST-ENGINE.md) | Persistence, worker, matching, planning and operations contracts. Use current code to resolve stale next-step wording. |
| [DESKTOP](../docs/DESKTOP.md), current [StudioApp.swift](StudioApp.swift), [UI tests](AutoCueStudioUITests/AutoCueStudioUITests.swift) | Current native implementation must take precedence over Desktop's remaining WKWebView-shell descriptions. The native root now mounts native detail editors. |
| [READ-ONLY-PREP-PILOT](../docs/READ-ONLY-PREP-PILOT.md), [PREP-LIVE-DEMO](../docs/PREP-LIVE-DEMO.md), [SET-BUILDING-CAPABILITIES](../docs/SET-BUILDING-CAPABILITIES.md), [READ-ONLY-FORMATS](../docs/READ-ONLY-FORMATS.md), [PROVIDER-SEARCH-LINKS](../docs/PROVIDER-SEARCH-LINKS.md) | Separate pilot/demo contracts. Their synthetic-path restrictions and missing adapters must not be generalized to all Studio functionality. Conversely, their features are not automatically native app features. |
| [ROADMAP](../docs/ROADMAP.md), [STATUS](../docs/STATUS.md), [SPEC](../docs/SPEC.md), [RESEARCH-SPEC](../docs/RESEARCH-SPEC.md), [ALL-IN-ONE-v5](../docs/ALL-IN-ONE-v5.md), [historical architecture handoff](../docs/google-handover/CONVERSATION_AND_ARCHITECTURE.md), historical SCORE documents | Preserve as research/history. ROADMAP's v4/v5-next and native-app-planned language is not the current product position; STATUS already labels itself historical. Early cue-slot meanings and third-party research numbers do not certify the current engine. |
| [INSTALL](../docs/INSTALL.md), [SONGFORMER-ON-MAC](../docs/SONGFORMER-ON-MAC.md), [SERATO-CUE-LOOP-QA](../docs/SERATO-CUE-LOOP-QA.md) | Environment/model experiments and format-specific acceptance. An old installation plan is not authorization to download models, a measurement of today's disk space, or proof of redistribution rights. Cue Loop serialization remains explicitly unverified. |

The intended product is a local-first DJ preparation assistant that connects owned exact files, booking requests/playlists, constrained set planning, reviewed cue/transition evidence, verified exports and personal feedback. Its defining workflow remains: understand → retrieve evidence → plan → preview → approve → execute → verify → recover.

## 2. Where we are in the six stages

| Stage | Current backend/browser implementation | Current native surface | Assessment |
|---|---|---|---|
| 1. AI library management | Typed search, scoped evidence, tools, retained conversations, explicit preferences, local-model adapter/evaluator | Text search and tagged-clean filter; no native assistant workflow | Deterministic foundation implemented. AI remains experimental; semantic quality gate not met. |
| 2. Smart crates | Rules, static snapshots, batch creation, revision-bound edits, approval/replay and undo | Crate listing, entry inspection and rename preview | Backend alpha implemented; native durable editing/creation/undo incomplete. |
| 3. Whole-set planning | Planner 1.2, phases, hard constraints, pins/repair, timing overlays, saved evidence, browser audition/manual clock reforecast | Raw JSON inputs, generation request and result counts | Backend significantly developed; native generation has a confirmed request-schema blocker and cannot present/review/save actual alternatives. |
| 4. Library operations | Durable scans, cancellation/recovery, explicit root reassociation, opt-in polling/inbox, indexed health/history, exclusions and exact-hash AutoCue reference repair | Scan submission/status, search and summary health counts | Partial native implementation. Physical quarantine/deletion and external DJ-reference repair are not implemented by the current exclusions/repair layer. |
| 5. Commercial readiness | Packaged worker, installed-wheel checks, database backup/CLI restore, dependency notices/inventory, development Mac app/DMG | Working SwiftUI shell, lifecycle, shortcuts, basic recovery and readiness display | Development build, not a release candidate. Signing/update/clean-machine/support/business acceptance remains. |
| 6. Broader parity | Capability registry, original rule templates, verified playlist ZIP mechanism, provider navigation, manual isolated Live | Capability/readiness display; no native Live or connected providers | Foundations only. Windows, authorized streaming/pool connections and licensed feeds are not delivered. |

**Release interpretation:** meaningful portions of Releases A and B are present in the backend/browser product. Neither their complete product acceptance nor native workflow completion is established. Release C, the first paid macOS product, is still ahead. Releases D/E contain optional or broader-parity work and should not block finishing the core local macOS workflow.

No defensible single completion percentage exists: the plans mix implemented code, native reachability, musical evidence, outside permissions and commercial operations.

## 3. Concrete findings from the current app

### Blocking native planner mismatch

`BackendController.generatePlan` sends `dataset_id` to `api/studio/plan`. That endpoint calls `exact_fields` with required `rule`, `policy`, `event` and optional `playback`, `optimize_playback`, `event_start_time`; it does not accept `dataset_id`.

The audit passed the native payload's keys directly to the same validator and obtained:

> Expected fields: event, policy, rule; optional: event_start_time, optimize_playback, playback

Removing the extra field satisfied that field contract. This is a verified schema defect, not an inference about model quality or an empty test library. Repair the native request, rather than weakening server validation. Successful native plan generation needs a regression test.

### Native planner discards the useful result

`PlanResult` contains only `feasible`, alternative count and unresolved count. The backend returns exact ordered tracks, durations, scores/reasons, phases, diagnostic status/search limits, revision and event/transition evidence. SwiftUI cannot currently render the set, compare alternatives, inspect required-song conflicts or select one for approval. It also collapses proven infeasibility and exhausted search into one summary state.

The native request hard-codes a search limit of 100 before event obligations are resolved. Candidate coverage is implicit; required files elsewhere in a larger library can fall outside that candidate pool. The product needs an explicit, supported scope/coverage policy, not silently raised backend limits.

### Browser functionality is not a native fallback

`WebWorkspaceView` is defined, but the current root and detail routes mount native views. Defining a WKWebView wrapper does not make browser-only approval, export, assistant, cue-review, monitor or recovery controls accessible in the desktop product. Desktop documentation still describes the older web-shell architecture.

### Crates stop before the durable step

The native crate editor inspects entries and previews a rename. Its own boundary note says native approval is incomplete. There is no native create/propose/approve, complete edit/undo or verified-export journey. The backend already has most of these contracts; this is predominantly integration and product workflow work, not a reason to build a second store.

### Event differentiation is not end to end

The shared engine understands booking requests, exclusions, client playlists and timeline context. Legacy event-sync code exists. Current documentation explicitly says Studio does not fetch events from the booking service and the upstream API must emit the expected contract. Native input is raw event JSON; no native event intake/reconciliation workspace completes that handoff.

### Persistence is uneven

Native path, planner and search drafts survive navigation/refresh within the running root view. This is not durable draft recovery across process death. Native approval workflows will need exact retained requests/receipts and conflict handling equivalent to the browser's tested mechanisms; an in-memory form object is not a durable mutation outbox.

### Product evidence and supported-platform claims need tighter scope

The native Xcode target is arm64 with macOS 12 minimum; the UI-test target is macOS 14 minimum. Testing the development host does not certify the declared minimum or other Macs. CI currently runs Linux Python/Node and wheel checks, not the native Xcode/UI pipeline. Native build instructions require the prepared external Python environment.

The capability registry correctly says `local-alpha` and `commercial_ready: false`, but some gates mix first-macOS-release scope with Windows, providers and physical cleanup. Those latter capabilities are explicitly later/broader scope in the commercial plan. Separate release profiles are needed before using the eight pending gates as a completion score.

## 4. Full commercial package map

Status below describes implementation evidence, not customer acceptance.

| Package | Current position | Main remaining gap |
|---|---|---|
| P00 capability/platform/rights registry | Partial | Surface-specific availability, first-macOS versus broad-parity profiles, current documentation and exact shipped-component rights inventory. |
| P01 shared services/identity/persistence | Substantial backend foundation | Native client consistency, event handoff and one authoritative save/recovery path across surfaces. |
| P02 indexed search/provenance | Implemented to an explicit 50k indexed-file boundary | Rich native filters/scope, metadata-repair visibility, scale/concurrency acceptance; 100k is not supported. |
| P03 proposals/approval/receipts | Backend/browser implemented | Native proposal review, explicit approval, durable retry reconciliation and recovery. |
| P04 assistant workflow | Browser alpha | Native reachability and consistently correct user outcomes; no production model acceptance. |
| P05 model/runtime evaluation | Harness and actual-model experiments exist | Held-out quality/hardware gate; latest comparable documented 22/24 smoke is not the proposed 95%-correct 200-prompt acceptance. |
| P06 smart crates/bulk recipes | Backend/browser implemented | Native rule builder, snapshots/diffs, bulk approval and manual-run recipe UX. |
| P07 client list intake | Several typed catalog/CSV/playlist paths exist | Unified native mapping/review, request categories/versions/order, uncertain OCR if included, representative input acceptance. |
| P08 event context | Engine/Studio payload support, legacy sync | Native event workspace, upstream/downstream booking reconciliation and real connected acceptance. |
| P09 set planner | Deterministic phase-aware backend with oracles | Repair native request; expose actual alternatives, constraints, repair and durable selection. |
| P10 transitions/cue readiness | Evidence overlay, explicit estimates and browser audition/manual reforecast | Native inspection/audition, musical validation; automatic adaptation is separate future work. |
| P11 history/memory | Explicit observations, feedback, removal/undo, event assessments | Native history/recovery and richer validated personal/event memory; no automatic play-success inference. |
| P12 duplicate evidence | Indexed exact-hash/related groups and AutoCue link repair | Native per-file evidence/keeper review; external DJ references are not inventoried. |
| P13 physical file operations | Not implemented as physical cleanup | Journaled quarantine/restore and supported external-reference round trips; current logical exclusions do not satisfy this. Later scope. |
| P14 ingestion/scale | Durable jobs, reassociation, opt-in root polling/inbox | Native root/monitor/inbox management and supported-device/concurrency validation. |
| P15 missing-song/provider workflow | Browser/pilot queue and outbound navigation foundation | Unified native missing-request review and acquisition verification; navigation is not connected access. |
| P16 export compatibility | Byte/revision-verified playlist ZIP and QA tooling | Native preview/save/recovery and installed-target import/order/playback acceptance per advertised version. |
| P17 authorized connectors | Unavailable | Legitimate APIs/accounts/permissions and tested lifecycle. Later scope unless explicitly promised. |
| P18 native distribution | Ad-hoc Apple Silicon development app/DMG | Complete workflow, supported-machine install/upgrade/uninstall, production signing/notarization and finished distribution assets. |
| P19 accounts/billing/offline license | No complete deployed commercial lifecycle established | Trial/purchase/cancel/refund/offline behavior, business/provider setup and privacy/support decisions before paid launch. |
| P20 CI/update/diagnostics | Linux regression CI, local native script, recovery/package tests | Native CI/artifacts, migration/rollback matrix, signed updates and redacted support bundle. |
| P21 independent acceptance | Synthetic harnesses and preliminary research evidence | Working-DJ musical/export testing, independent accessibility/usability, clean-machine and rights/privacy review. |
| P22 Windows | Not delivered | Separate filesystem/process/installer parity and Windows acceptance. Later scope. |
| P23 licensed content | Original templates and user-owned imports | Licensed feeds/creator rights, provenance and takedown/revocation lifecycle. Later scope. |
| P24 Live companion | Manual isolated backend/browser Live | Native event/deck identity, now-playing adapter, compact UI/drag/QR if chosen, actual integration acceptance. Later scope. |
| P25 sampler | Not a delivered native module | Separate product decision, rights/audio-routing/latency work. Not a core-release prerequisite. |
| P26 sync/team collaboration | Not delivered | Separate threat model, identity/conflicts/revocation/key recovery. Not a core-release prerequisite. |

## 5. Original cue-research goals still matter

The original research track has not been completed by the Studio/native progress:

- **v7 Milestone A:** many safeguards, timing/editor/cache/path/scorer stories are implemented. Outro quality, actual grid interpretation, fresh-audio runtime, Serato playback/end-marker acceptance and some live-model checks remain gated.
- **Milestone B:** inventory, imported anchors, grouped dataset/feature infrastructure, event-label UI and benchmark machinery exist. The documented real sample is 45 tracks with 24 completed plans. The board does not establish a completed human-reviewed accuracy benchmark. Twenty-three low-confidence EXIT cues are uncertainty evidence, not twenty-three proven musical errors.
- **Milestone C:** dataset builder and lightweight candidate-ranker scaffolding have synthetic tests. Rights-cleared real training, pretrained comparison, switchable model integration, active learning and untouched held-out evaluation remain.
- **Milestone D:** bounded experiment/job operations, optional annotation assistance and promotion/rollback remain separate backlog. Cloud use, model downloads and paid inference are not prerequisites for a useful deterministic app.
- **Serato Cue Loops:** the desired pad linkage is not established by ordinary CUE/LOOP byte round trips. Preserve the current proposal-only gate until genuine target-software fixtures/acceptance exist.
- **Historical scores:** the v4/v5 tie on 18 marked cues and comparisons against Mixed In Key are not a current v7 accuracy score. Do not infer release quality from them or turn imported automatic markers into human ground truth.

Continue this track as a measured evaluation program alongside product integration; do not promote a model or expand bulk analysis because a UI or unit-test milestone passes.

## 6. Verification evidence and its limits

Most recent development evidence from the preceding implementation work:

| Check | Recorded result | What it establishes |
|---|---|---|
| macOS full native UI run | 11 passed, 0 failures | Launch/recovery/navigation/resize, draft retention, search/filtering, generated scan/search, readiness and quit/relaunch. |
| Final focused native run | 2 passed, 0 failures | Affected scan/search and planner input-retention screens after display corrections. |
| Xcode analysis | ANALYZE SUCCEEDED | Static build analysis for those changes. |
| Release build | BUILD SUCCEEDED | Current native shell/backend can be packaged in the prepared development environment. |
| Synthetic Node | 100 passed | Browser-state/logic regression coverage, not all native workflows. |
| Targeted Python | 63 passed | `web.test_studio` and `web.test_library_operations`; not a freshly executed full repository suite. |

These passing UI tests did not press Generate on a valid, feasible native planner fixture, approve a native crate, export it, lose an approval acknowledgement or restore a native backup. The schema defect above demonstrates that gap. The earlier implementation report must not be read as native planner completion.

The six-stage document records older full-suite results up to 1,189 tests, plus multiple earlier counts. Those are historical checkpoint evidence, not a new full pass against every current uncommitted change. Likewise, documented 50k engine/search measurements, successful Live HTTP runs and a heavily contended failed run describe different conditions. Establish a new same-build/hardware performance baseline rather than selecting whichever old number is most favorable.

Screenshots reviewed from the recent verification show the teal native search/scan flow, readiness and a genuinely resized library window. They establish readable dark-mode layouts for those views; they do not establish VoiceOver, light-mode, minimum-OS or independent accessibility certification.

## 7. Next ten priorities, in delivery order

### 1. Repair the native contract and make tests prove completed outcomes

**Why first:** the planner's primary action is currently rejected despite green UI tests. This blocks the product journey.

**Deliver:** the correct request schema, typed response/error/diagnostic decoding, explicit candidate coverage and native-generated feasible/infeasible/search-exhausted fixtures. Add the successful native plan-generation test missing today. Preserve strict backend validation and all safety rules.

**Done when:** generated tagged audio can be scanned and a valid native request produces a displayed exact-track alternative; stale data, required files outside the selected candidate scope and search exhaustion have distinct, tested outcomes.

**Roadmap:** P01–P03, P08–P09. Mostly independent engineering.

### 2. Finish the native set-planning workspace

**Deliver:** ordinary controls for duration/tolerance, phases, clean/version constraints, must-plays/exclusions and pins; exact track alternatives with reasons, duration deltas and phase/timing evidence; repair that retains protected commitments. Keep advanced JSON optional rather than the everyday UI.

**Done when:** a DJ can prepare, compare and revise a multi-phase set without writing JSON, with no invented duration or softened policy and with visible distinction between full-file time and reviewed-playback estimates.

**Roadmap:** P08–P10. Reuse the existing planner, not a new ranking implementation.

### 3. Complete native crate creation, approval, edits and undo

**Deliver:** exact proposal review → explicit approval → saved crate → reopen/restart → edit/undo. Include smart-rule snapshots and membership diffs as the next slice. Persist exact pending mutation requests and reconcile receipts after interrupted responses.

**Done when:** a lost approval response followed by quit/relaunch/retry yields exactly one crate/action; stale revisions refuse safely; undo restores the reviewed state without overwriting newer work.

**Roadmap:** P03, P06. Backend support already exists; native durable integration is the missing piece.

### 4. Deliver verified native export and pre-gig preflight

**Deliver:** current-source/revision-bound export preview, blocked-item explanations, native save destination, M3U8/CSV/manifest ZIP, retained export history and clear current-versus-historical status. Show missing, ambiguous and excluded obligations before export.

**Done when:** a saved native plan exports the exact selected membership/order, changed bytes or stale revisions block it, and downloaded artifacts can be recovered and verified. Actual Serato import/playback is a separately recorded acceptance step.

**Roadmap:** P10, P15–P16. This completes the first useful scan-to-export vertical milestone with priorities 1–3.

### 5. Make booking/event intake a first-class native workflow

**Deliver:** event selection/import, must-play/do-not-play/special-moment/client-playlist review, version ambiguity resolution and provenance-preserving request mapping. Begin with selected local fixtures/exports; reconcile the supported upstream booking API schema before enabling an authenticated connection.

**Done when:** one event's request categories, versions, repeated memberships and timing survive import → reconciliation → plan → saved evidence → export without omission or silent substitution. Native event changes do not rewrite saved Prep or Live state implicitly.

**Roadmap:** P07–P08, P15. The key product differentiator. Connector engineering can proceed independently; real connection acceptance needs permitted access.

### 6. Complete native library and ingestion management

**Deliver:** a usable track table and typed metadata/source filters, visible records needing title/artist metadata, root/offline states, scan cancellation/recovery, deliberate reassociation, inbox review and opt-in monitor management. Add native smart-rule/duplicate-evidence inspection using existing backend facts.

**Done when:** unmount/restart/cancel/rescan tests preserve a coherent index and exact identities; users understand indexed versus current availability; metadata-incomplete indexed files do not appear to have vanished merely because search requires title and artist.

**Roadmap:** P02, P06, P12, P14. No physical media cleanup in this milestone.

### 7. Bring cue and transition review into the native workflow

**Deliver:** reviewed cue/grid provenance, waveform/transport or bounded local audition, explicit play-window/overlap inspection and readable warnings. Reuse existing time/offset/window contracts and the review journal.

**Done when:** the same exact-file evidence agrees across review, plan, saved crate and export; unknown/stale/unreviewed windows cannot become confirmed timing or mixing claims. Establish a rights-cleared held-out listening program for v7/grid/outro/transition quality; do not auto-promote a model.

**Roadmap:** P10 and v7 A08/A11/A13/A16, B06–B07. Native engineering is independent; actual musical acceptance requires qualified reviewers and suitable permitted audio, who need not be the user.

### 8. Make optional AI reliably draft the supported actions

**Deliver:** a fixed held-out semantic suite, explicit source context, preserved restrictions across turns, correct timed/phase briefs, unsupported-action abstention and model/hardware latency/memory reports. Integrate native chat only through the existing propose/review authority boundary; retain full typed workflows without AI.

**Done when:** the proposed 200-prompt, at-least-95%-fully-correct intent gate and zero-unauthorized-mutation checks pass on the declared configuration, including unseen/adversarial cases. Safe rejection alone is not successful completion. Do not treat 22/24 pipeline checks, including application vetoes, as model certification.

**Roadmap:** P04–P05. Work on evaluation can run alongside priorities 1–7; do not make it block the deterministic product.

### 9. Complete native recovery, diagnostics and continuous acceptance

**Deliver:** native database backup/restore-to-new-location, operation/history/recovery receipts and redacted support export; a macOS build/UI CI lane plus full backend/Node/wheel checks; migration/fault/restart/offline, minimum-window, light/dark/keyboard/VoiceOver and scale tests on a declared matrix. Break the large shell into focused native client/model/view modules as functionality grows.

**Done when:** a clean test environment completes the whole event-to-export path, recovers from lost responses/restart and proves backup restoration without touching originals. Artifacts identify the exact build, dependency inventory, host, dataset and pass/fail outcomes. Fresh same-build performance checks meet the supported-scale targets under disclosed contention conditions.

**Roadmap:** P00–P01, P18, P20–P21. Infrastructure and synthetic cases are independent; independent accessibility and machine coverage require testers/equipment.

### 10. Produce an honestly scoped, installable macOS beta and paid-release plan

**Deliver:** a native/backend capability matrix and one current roadmap; separate core macOS, paid-release and broad-parity gates; finished app identity/assets, production signing/hardened-runtime/notarization/update/rollback design, clean-machine installation and support instructions, shipped-component rights/privacy inventory and bounded beta acceptance. Plan and test entitlement/offline/billing lifecycles before paid launch.

**Done when:** a supported clean Mac installs/upgrades/uninstalls without developer setup; the narrow advertised destination/software matrix has observed import/playback evidence; distribution/privacy/rights and independent usability reviews support the actual claims. Paid/trial/canceled/refunded/offline states must be accepted before charging. Windows/providers/physical cleanup remain explicitly outside the first beta unless separately delivered.

**Roadmap:** P00, P05, P16, P18–P21. Software preparation is independent. Organization-controlled signing/payment identities, commercial decisions and outside acceptance are real external dependencies, not tasks to fabricate or label complete.

## 8. Execution recommendation and what to defer

**Next delivery milestone:** complete priorities 1–4 as a native vertical slice:

Select generated music → scan → supply a supported event → generate → inspect exact alternatives → approve one crate → quit/reopen → export verified ZIP → prove recoverable acknowledgement/retry behavior.

Run event/ingestion/review integration next; conduct AI and acceptance preparation alongside it. This order earns a genuinely usable product before adding another large surface area.

Do not make Windows, connected streaming/pool downloads, physical cleanup, sample pads, QR services, cloud sync, a foundation-model training project or expanded heavyweight analysis batches prerequisites for that milestone. Their roadmap value remains, but they do not repair the current native funnel.

The documentation should be reconciled as part of this delivery, with per-feature states for backend, browser, native, automated evidence, independent acceptance and target release. The native-app-planned/WKWebView descriptions, old adapter-next statements and undated benchmark progression should be linked to historical checkpoints rather than reused as current status.

## Implementation continuation

The subsequent [native workflow milestone](NATIVE-WORKFLOW.md) fixes the native plan request contract, retains real alternatives/diagnostics, adds basic native planner controls, exact proposal approval with durable receipt recovery, and verified export/history. Its acceptance now includes 17 passing native UI tests and an extended offline-history recovery check. The findings above describe the audit-time baseline; the broader priority list remains open as qualified in the milestone document.

## November 16 native/commercial setup update

The preceding audit is historical. Native plan approval/export was subsequently completed as recorded in `NATIVE-WORKFLOW.md`. The commercial continuation adds current-crate edit approval/recovery/undo, event-request/phase forms, native backup/restore/workspace switching, redacted support, original icon/appearance work, pinned build constraints, prepared native CI and artifact release preflight. See [COMMERCIAL-READINESS.md](COMMERCIAL-READINESS.md) for delivered scope and the next release gates. This advances local Release A/B reachability and Release C preparation; it does not establish paid Release C acceptance or broader provider/Windows/live parity.
