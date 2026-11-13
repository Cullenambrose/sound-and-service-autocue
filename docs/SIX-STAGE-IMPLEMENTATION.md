# Six-stage delivery checklist

Approved scope: the six stages in [the commercial plan](COMMERCIAL-PRODUCT-PLAN.md).
This checklist distinguishes implemented software from externally gated release
claims. A local alpha is not the finished commercial product.

| Stage | Implementation slice | Acceptance | Status |
|---|---|---|---|
| 1. AI library management | Shared search, durable multi-turn drafts, optional bounded retrieval/transition evidence and explicit DJ feedback | Stale evidence/citations rejected; writes still need review/approval | Experimental alpha; real compact-model evaluation failed the production quality gate |
| 2. Smart crates | Durable rules/snapshots, atomic bulk creation and reviewed existing-crate rename/merge/move/reorder/removal with undo | Stale previews/undo cannot overwrite newer edits; failures roll back every crate | Local alpha implemented; independent real-library/UX certification remains |
| 3. Whole-set planning | Constrained phase-aware planner, exact-file pins/repair, cue provenance and explicit playback-timing overlay | Hard-policy and phase oracles; exact overlap arithmetic; rendered save/reload/export | Full-track planning and reviewed-window estimates implemented; mixed-duration optimization and automatic timeline conversion remain |
| 4. Library operations | Indexed health, inbox, opt-in incremental root polling, reviewed play-history removal/undo and reversible Studio exclusions | No source writes; retry/restart/ambiguity cases | Alpha implemented; physical recoverable cleanup and reference repair remain |
| 5. Commercial readiness | Lightweight Studio, bundled Swift/Python Mac shell, distribution/diagnostics and release gates | Wheel/HTTP, native lifecycle and development DMG smoke checks | Apple Silicon development app/DMG built; signing, updates, billing and clean-machine QA remain |
| 6. Broader parity | Destination capability registry, original templates, export, provider navigation and isolated Live foundation | Unsupported integrations stay visibly unavailable; saved Prep unchanged by Live | Local foundations implemented; connected services, Windows and licensed feeds remain |

## What this implementation delivers

The opt-in [Studio workspace](STUDIO.md) connects actual indexed files to
reviewed, durable crates and verified exports. Rules, planning, history and
manual Live use that same catalog. Durable saves retain exact browser requests
and reconcile receipts after lost acknowledgements instead of silently
duplicating crates. The default application and source media are unchanged.

This includes the first integrated wave across all six areas plus subsequent
phase-aware planner follow-through, **not
completion of the six-stage commercial roadmap**. Remaining work includes
software we can build independently, not only accounts, private files or
certifications.

## Reviewed play-history recovery

Manual observations can now be selected, previewed and logically removed as
one atomic reviewed batch, with exact receipt replay and conflict-checked undo.
Original observation rows remain durable: retrying the original record-play
request does not resurrect removed history. Undo preserves newer independent
observations, preferences, saved crates and source media.

Active history and rediscovery exclude revisioned removal markers. Search
generation triggers invalidate snapshots across connections on removal/undo.
Approval verifies every captured event and marker before writing anything;
expired previews, changed observations, changed removal revisions and wrong
dataset/request identities are rejected. Marker, journal and receipt writes
share one transaction, including injected second-write/receipt failures.
Database backups retain the exact recovery evidence and can restore it into
a fresh directory before undo.

The browser supports exact observation selection, preview/approval,
recoverable journals and undo, plus a private bounded active-history download.
Limits are explicit: 100 active observations/30 recent journals in the UI,
1..500 distinct selected observations per API preview. This is reversible
logical removal, **not permanent privacy erasure** or a complete history export.
Private event text remains in journals/database/backups. No AI history writer
or source-audio operation is enabled.

An actual rendered-browser test lost the approval response after server commit.
Reload preserved the pending action; retry reconciled its exact receipt without
duplicate removal, and undo restored the same observation/note. The installed
wheel's worker and Studio checks passed outside the checkout, including
removal/replay/undo and backup recovery.

Verification for this continuation:

- Final targeted regression: **98 passed**, **120 subtests**, including the
  reviewed-history, HTTP, operations, product, backup and exact ffmpeg
  source-descriptor tests. Browser-state regression: **91 passed**.
- After the earlier interrupted attempt, an unchanged complete Python suite
  passed: **1,165 tests**, **684 subtests**, 13 dependency warnings. The
  ffmpeg check that had timed out previously passed in this run.
- The new noneditable wheel passed installed worker/Studio verification.
  The rebuilt unsigned Apple Silicon app passed bundled removal/replay,
  restart/undo, database recovery, crate/planning/settings checks and all
  packaged notice/native-binary hashes. DMG integrity passed.
- The targeted backup CLI and first frozen check hit their unchanged 30-second
  startup deadline under contention; final targeted/frozen reruns passed
  without changing application code or time limits.
- A follow-up profile exposed redundant deep copying in the Studio Live view
  and heap selection even when the caller requested the full result. The view
  now relies on the ranking API's already-detached prepared result, and full
  limits use deterministic full ordering while smaller limits retain top-k.
  Focused mutable-ownership and ranking-order tests pass. A post-change full
  200-request retest again timed out under ongoing simulator contention before
  collecting a report; it provides no p95 and does not resolve the latency
  blocker.
- Full software regression is now verified, but the latest unchanged 200-request
  live latency gate **failed**: p95 **3,136.87 ms**, p99 **4,286.10 ms**,
  maximum **4,636.97 ms**, with **1/200** requests under the existing 200 ms
  target. A fresh same-run host sample showed active simulator/system processes
  using substantial CPU and over 11 million pageouts. This is not a representative
  clean-host certification and is far worse than prior passing runs; it is
  recorded as a failure, not discarded or relabeled. Retest on a quiescent host
  before release acceptance. No latency thresholds or subprocess deadlines were
  relaxed. Verification backends were stopped.

## Scale, recovery and distribution evidence continuation

Implemented and verified:

- Long-set planner budget allocation now reserves capacity through the requested
  final position, including pins and phase branching. Previously, a feasible
  unpinned 60-position request stopped at the expansion limit. It now produces
  checked alternatives without increasing the 200,000-expansion limit.
  Small exhaustive feasibility/phase oracles, exact timing and hard policy
  remain unchanged; actual beam/candidate pruning is still disclosed.
- Warm Studio search caches two committed SQLite snapshots and derived
  selection evidence. Generation triggers cover file/root, observed history,
  analysis/review, exclusions and feedback. Cross-connection changes invalidate
  snapshots; old read transactions stay consistent; provisional writes,
  rolled-back work and missing-trigger schemas cannot poison the cache.
- Ranking groups resolved requests and uses exact bounded top-k selection
  instead of formatting/sorting every returned recommendation. Required Prep
  songs still overflow the limit safely; prepared output tracks are copied
  independently so consumer mutation cannot alter snapshot evidence.
- The expanded synthetic benchmark records raw repeated samples and strict
  percentiles, checks actual selected counts/IDs and 60-position feasibility,
  and explicitly verifies the unsupported 100,000-file boundary.
  An earlier 50,000-track ranking failure remains in its original report.

Final isolated scale measurements on this local Apple Silicon machine:

| Operation | Scope | Measured p95 | Gate |
|---|---|---|---|
| Warm indexed search | 10,000 tracks, 20 fresh-connection requests | 20.62 ms | <300 ms |
| Warm indexed search | 50,000 tracks, 20 fresh-connection requests | 108.08 ms | <300 ms |
| Next-song ranking | 10,000 tracks, 20 engine-only calls | 24.60 ms | <200 ms |
| Next-song ranking | 50,000 tracks, 20 engine-only calls | 119.69 ms | <200 ms |
| Full-track set planning | 5,000 candidates, 60 positions, three calls | 3.24 s | <5 s |

Engine-only figures do **not** include HTTP, JSON transport or durable saves.
The unchanged 1,000-track, 200-request loopback HTTP/durable-save gate has
passed in earlier runs at p95 **74.71 ms** and **144.91 ms**; both are historical
measurements, not the current result. The latest isolated benchmark measured
p95 **3,136.87 ms** and failed the strict **<200 ms** gate under severe
shared-host contention (see verification above). Do not use the historical
passes as a substitute for a fresh quiescent-host run. This is not an
all-requests-under-200-ms guarantee; no durability or percentile threshold
was weakened.
These are synthetic local measurements, not large-library concurrency, varied
now-playing, filesystem or musical-quality certification. Studio still rejects
more than 50,000 indexed files explicitly.

### Database recovery and release notices

Studio now downloads private database backups through the session/origin-gated
UI. SQLite's backup API includes consistent committed WAL state; the ZIP has
only the database and a hash/schema/count manifest. CLI restore verifies bounds,
integrity, references, schema and counts, then publishes only into a new data
directory. Existing state/media is never replaced. Tests cover WAL persistence,
pending-write rejection, corrupted/duplicate/future archives, path refusals,
disk/publication faults and restore/restart. This is database-only recovery,
not encryption, an authenticity signature, audio backup, in-place update
rollback or a physical cleanup/reference-repair implementation.

The development Mac build includes installed dependency/runtime notice files
and a path-redacted inventory with notice/native-binary hashes. Missing/unsafe
notice paths stop packaging. **Mutagen declares GPL-2.0-or-later**; the product
license and applicable source/distribution obligations must be resolved before
commercial publication. The inventory deliberately does not claim complete
transitive SPDX coverage, model rights or legal clearance.

The earlier regression was **1,156 Python tests**, **679 subtests**, 13
dependency warnings and **90 browser-state tests**. The latest continuation
adds its separate **1,165-test/684-subtest full pass** and **91 browser-state
pass** above. Rendered keyboard activation delivered a 12,161-byte ZIP Blob
matching the verified HTTP response, with twelve synthetic indexed files and
zero saved crates. Browser-host download handling did not expose a Playwright
download event, so confirmed filesystem delivery is not claimed by that check.
The final noneditable wheel passed worker/Studio verification outside checkout,
including backup/restore/restart with preserved crates. The rebuilt unsigned
Apple Silicon app/DMG passed bundled edit/replay/restart/undo, planning,
transitions, model-setting persistence and database recovery. Every packaged
notice and recorded native-binary hash matched, and DMG integrity passed.
Temporary Studio/Ollama helpers were stopped after verification.

The next actual-model probe constrained source quotes to exact prompt clauses
without changing the semantic evaluator. **Both known phase cases still
failed**: an invented text-query constraint and invalid phase-budget evidence.
No failed experimental decoder was adopted; the previous **22/24** comparable
gate remains the latest complete run, with application veto attribution and
both residuals preserved. AI is still experimental.

**Not finished:** physical cleanup/reference repair, mixed-duration planning,
independent real-library/listening/software QA, production AI quality,
authorized integrations, Windows certification, signed installers/updates,
billing publication and rights/privacy/business approvals. These are not
reclassified as complete because this continuation passes software tests.

## Existing-crate editing and AI reliability follow-through

### Live-response latency follow-through

Repeated live commands were rescanning the same immutable library for unchanged
event requests. `PreparedLibrary` now caches only matcher evidence, bounded to
512 exact request/version/confirmation keys per snapshot. Request metadata,
categories, hard exclusions, phase, scoring and version priorities still
recompute. Evidence is copied before use, and library additions rebuild the
snapshot. No source writes, ranking thresholds, durability or benchmark
thresholds were changed.

Verification:

- Focused ranking, workflow and recovery: **140 passed**, **103 subtests**.
  Cache tests cover full raw/prepared result equality, current guest metadata,
  exclusions and version priorities, confirmed-file separation, mutable
  diagnostic isolation, changed-source snapshots and bounded eviction.
- Ten exact live commands over 1,000 synthetic tracks reduced matcher scans
  from **250 to 25**, with **225 cache hits**.
- Unchanged loopback HTTP benchmark includes durable session save and response
  serialization: isolated p95 **47.36 ms**, full-suite p95 **92.80 ms**, both
  under the strict **200 ms** threshold with **200/200** measured requests under
  target. These are local synthetic observations, not larger-library or
  clean-machine performance certification.
- Full Python regression: **1,140 passed**, **638 subtests**, 12 existing
  dependency warnings. Browser-state tests: **90 passed**. This supersedes the
  live-latency failures recorded in the preceding capability block below.
- Current noneditable wheel verified outside checkout, including bounded
  matcher reuse and ranking equality. Worker and Studio smoke checks passed.
- Updated unsigned Apple Silicon app/DMG built from that wheel; bundled
  edit/replay/restart/undo, settings, inbox, planning and transition checks
  passed, and DMG integrity verified. Verification helpers stopped.

The two compact-model multi-phase failures below remain unresolved; this
latency fix does not make the AI production-ready. Signing/notarization,
clean-machine/DJ QA and commercial integrations remain release gates.

### Request-capability and evidence safeguards

The next block fixes the known silently omitted release-year requirement with
a shared **application-owned**, pre-inference veto. Explicit numeric wording
such as `released in 2007`, release-year/date fields and `songs from the 1990s`
is rejected before search/routing/inference in every conversational tool and
one-shot translator. No rejected turn or receipt is saved; existing receipts
still replay without inference. This is a narrow capability boundary, not a
replacement language parser or a newly supported release-date filter. Matching
phrases in quotations/negations are conservatively rejected too; unrelated
artist names containing years and ordinary durations remain allowed.

Timed evidence validation also now requires phase quotes in nonoverlapping
source order. Repeated identical phase budgets need distinct source
occurrences. Boolean music/policy fields require native booleans and relevant
quoted words; unrelated stage text cannot stand in for clean/play-history/
feedback evidence. These checks do not prove negation, phase identity or
complete capture of the user's requirements.

The comparable Qwen3 4b integration gate passed **22/24**; **three** release-year
cases are explicitly attributed to `application-capability-check`, not model
quality. Both multi-phase cases still failed (invented music source quote;
invalid phase-budget source quote). Failed reports remain intact and the
production AI quality gate remains unmet. Smaller-model and short-prompt/
constrained-schema probes did not reliably resolve those semantics; no
automatic retries, relaxed tests or fabricated successes were introduced.

Verification:

- Targeted Studio/product: **70 passed**, **74 subtests**, including every tool,
  before-model/no-catalog-access rejection, HTTP errors, replay preservation,
  no saved state on rejection, repeated phase budgets and boolean evidence.
- Browser-state tests: **90 passed**.
- Rendered release-year request with Ollama stopped: explicit unsupported
  capability error (not runtime-unavailable), apply disabled, policy unchanged
  and no saved crates.
- Full suite: **1,136 passed**, **624 subtests**, 12 existing warnings, with
  **one unchanged live-latency benchmark failure** in both runs. First full-run
  p95 was 358.78 ms; second was 200.92 ms against the strict 200 ms threshold.
  The isolated benchmark passed at p95 182.52 ms over 200 requests. This is
  load-sensitive evidence, not a fully green regression or proven performance
  reliability. No benchmark thresholds or unrelated live code were changed.
- Installed wheel worker/Studio workflows passed outside checkout, including
  release-filter errors without inference.
- Updated development Apple Silicon app/DMG built from the current wheel.
  Bundled one-shot/conversational release vetoes, no saved rejected turn,
  settings persistence, edit/replay/restart/undo, deterministic planning and
  transition smoke passed; DMG integrity verified. Signing/notarization and
  real DJ/clean-machine QA remain open.
- Temporary local Ollama and Studio helpers stopped after verification.

### Grounded timed-plan continuation

New timed briefs now use two separate local calls: literal timing extraction,
then source-quoted music constraints. The app assembles the typed rule/policy,
applies existing defaults, checks numeric timing against the request and checks
phase budget quotes. Music filters require exact quotes and quoted text/numeric
values. Invalid/duplicate fields, missing source quotes, unavailable phase
indices and unsupported requirements reject the entire draft. No ungrounded
field is silently removed to make an output pass.

Initial conversational plans use that same translator; compatible refinements
retain the existing one-call patch workflow. A first Auto timed request has at
most three calls including routing, explicit new plans two. No catalog metadata
is sent for timed extraction. Evidence is visible in the translation and saved
conversation receipt. Source quotes are an inspection aid, not completeness or
semantic certification; negations and association of numbers can still be
misread. Application-owned `AI set draft` naming is editable.

The final synthetic Qwen3 4b run passed **21/24** strict checks, including the
four crate-drafting conversation turns, single-set clean/genre/tolerance limits,
numeric hour conversion and non-clean inclusion. Added cases broadened the gate
beyond the prior 19; the results are not independent DJ or musical QA.
Three cases still failed:

- Ordered phase budgets: invented music-constraint source quote, safely rejected.
- Per-phase energy: invalid phase-budget quote, safely rejected.
- A timed request containing a release-year filter: unsupported year requirement
  omitted while the other fields were accepted. **This remains a real semantic
  failure**, not solved by source evidence. Human review and typed tools remain
  required; AI is still experimental and not production-ready.

Earlier 17/19 and focused failure reports remain preserved. One local runtime
500 was traced to `prediction aborted, token repeat limit reached`. Compact
extraction now uses a smaller 4,096-token context and explicit repeat penalty;
bounded HTTP error bodies expose the actual cause. The final run had no such
500, but universal runtime reliability is not claimed. Each call still has a
90-second timeout; there is no autonomous retry or syntax-repair loop.

Verification:

- Full Python: **1,131 passed**, **593 subtests**, 12 existing warnings.
- Targeted Studio/product: **64 passed**, **45 subtests**, including source
  bounds, numeric hour conversion, phase policy preservation, split-call
  conversation/replay and second-call rejection without a saved turn/receipt.
- Browser-state tests: **90 passed**.
- Actual-model rendered UI: 24-minute tagged-clean House draft, zero tolerance,
  max eight songs and two source quotes displayed; explicit apply populated
  only unsaved forms. Missing-duration rejection disabled apply and retained
  the prior policy. No generated test crates were saved.
- Installed wheel: offline worker and Studio HTTP workflows passed outside the
  checkout, including current three-call UI contract.
- Development Apple Silicon app/DMG rebuilt from the current wheel. Bundled
  UI/backend, settings persistence, creation/edit/replay/restart/undo and
  deterministic planning/transition smoke passed; DMG checksum verified.
  Models remain separately installed, not bundled. Signing/notarization,
  clean-machine QA and real DJ listening/acceptance remain open gates.
- Temporary Studio and local Ollama helpers stopped after verification.

### Native JSON and patch-only continuation

The next reliability pass removed the obsolete field/value transport and uses
native JSON with strict duplicate-key, nonfinite-number and type rejection.
Compatible conversation refinements now receive a smaller patch-only tool;
the application owns the current action/name and retention of unchanged
filters. Model schemas no longer require timing defaults in partial policies.
Only bounded prior planning fields are sent on refinements, not old filters,
old messages, source identities or repeated metadata examples.

The application still retrieves up to 12 records and four adjacent transition
cards for deterministic review receipts. Initial/tool-change model drafts see
only three metadata examples; their citations are bound to that supplied subset.
Compatible follow-ups send no metadata or citations. The UI privacy label and
Studio guide now distinguish model context from application-only evidence.

The final synthetic `qwen3:4b` run passed **14/19** strict semantic checks,
including **all four** conversation turns (initial exact filters, changed count,
removal of just the maximum BPM, then allowing non-clean versions). Three of
four newly added held-out checks passed. This is a single bounded smoke run,
not independent quality certification. Earlier failed reports remain preserved.

**Timed AI planning remains blocked.** Five planning cases failed: invented
genre/text/tempo/energy/phase restrictions, invented duration on a request
without timing, extra filters on an otherwise correct timed request, and a
90-second timeout. The evaluator now compares the full normalized planning
policy and rule, not just the requested duration or phase names. Valid JSON,
correct timing combined with invented restrictions, and safe timeouts do not
count as semantic success. Use the deterministic typed planner instead.
No model is bundled or recommended as commercially certified.

Continuation verification:

- Full Python: **1,125 passed**, **574 subtests**, 12 existing warnings.
- Targeted Studio/product: **58 passed**, **26 subtests**, including patch-only
  persistence, preserved zero/nondefault phase budgets, citation bounds and
  tests proving the evaluator rejects invented planning constraints.
- Browser-state tests: **90 passed**.
- Rendered unavailable-runtime request: visible 503/error, conversation remained
  at revision zero, nine generated test crates remained nine; the pending
  request was explicitly discarded and recovery state cleared.
- Installed wheel: offline worker contract and Studio HTTP workflows passed
  outside checkout, including current privacy-label assets, edit/undo replays
  and persisted assistant settings.
- Updated development Apple Silicon app/DMG: bundled assets/backend,
  creation/edit/replay/restart/undo, planning and transition smoke tests passed;
  DMG integrity verified. Signing/notarization and real DJ/clean-machine QA
  remain separate gates. Temporary Studio and Ollama services were stopped
  after verification.

### Prior existing-crate editing block

The next block added atomic reviewed operations for existing personal crates:
rename, copy/move merge, move exact occurrences, reorder and membership removal.
Previews show before/after state. Approvals and undo have durable exact receipts;
undo restores names/IDs/order at new revisions and refuses to overwrite newer
edits. Repeated songs are preserved. Original media and third-party references
are untouched.

Conversation refinements now merge checked patches in application code instead
of relying on the model to repeat every old restriction. Missing fields retain
prior constraints; explicit clear lists remove selected fields and contradictory
set/clear results fail. Narrow tool routing, an explicit tool selector and visible
filter/policy diffs make changes inspectable. Existing phase restrictions and
nondefault budgets survive omitted-field refinements.

**AI reliability is still not finished.** Expanded evaluation now checks exact
normalized filters, including invented extra restrictions, additional unseen
wordings, missing duration and four conversation refinements. Model/transport
experiments did not meet this stricter quality gate. The final mid-size
`qwen3:4b` full run passed **7/15** before the last route/metadata prompt fixes;
the focused final four-turn refinement run passed **1/4**, with failed/unsaved
turns preventing later refinements. Neither number is a production acceptance.
These are different test sets from the previous 5/8, not a comparable improvement
claim. Additional compact models and required-key/JSON transport variants also
failed; their evaluation reports were retained rather than reclassified.

Installed model responses confirmed Apache 2.0 for Qwen3 candidates.
The tested `qwen2.5:3b` response instead reported a research license; its isolated
weights were removed and it is not a commercial recommendation. No models are
bundled, paid inference invoked or private libraries shared.

Verification for this block:

- Full Python: **1,119 passed**, **570 subtests**, 12 existing warnings.
- Final targeted Studio/product checks: **53 passed**, plus **22 subtests**,
  including the exact 500-accepted/501-rejected edit boundary.
- Browser-state tests: **90 passed**, including exact edit receipt identities and
  key-order-independent recovery checks.
- Installed wheel: worker and Studio fixtures passed, including actual HTTP
  existing-crate edit/approve/undo and both lost-ack replays outside checkout.
- Rendered exact-receipt test: rename approval committed, response was deliberately
  lost, reload/retry reconciled it, and undo restored the original crate without
  changing the total of nine generated test crates.
- Development Apple Silicon app/DMG rebuilt with current assets; bundled backend
  edit/replay/restart/undo smoke and DMG checksum checks passed. No new signing/notarization, clean-machine,
  native-picker interaction or real DJ acceptance is claimed.

The checked application-level retention/undo mechanisms work. Actual-model
semantic reliability remains an explicit blocker; do not treat a validated JSON
draft, safe rejection or passing software regression as successful AI semantics.
Use typed search/planning and the explicit crate editor until that gate passes.

## Ingestion, organization, conversational AI and Mac build follow-through

Implemented after the phase/transition block:

- Acquisition inbox: new/changed index evidence, exact-version metadata,
  missing fields, hash groups and durable reviewed/ignored status.
- Explicit root monitors: persistent opt-in intervals, existing incremental
  scanner reuse, admission errors/backoff, progress and restart recovery.
  Disabling stops future admissions, not already queued scans.
- Atomic bulk creation: preview 1-8 named rules and approve at most 500 entries
  together. Changed source bytes in a later crate roll back earlier crate writes
  and every receipt. Lost acknowledgments reuse the committed batch.
- Resumable revision-checked conversations: optional bounded local metadata,
  cue summaries and four transition cards; application-computed explanation
  facts; retrieved-ID citation checks; drafts never approve themselves.
- Persistent model-tag configuration and explicit DJ ratings. Feedback sorting
  and planner scoring are opt-in and never relax hard constraints.
- Reversible exact-hash Studio exclusions with recovery receipts. This is
  app-state cleanup only, **not physical quarantine/deletion or reference repair**.
- Apple Silicon Swift/AppKit/WKWebView development app and DMG with a frozen
  Python backend, native folder picker, private loopback readiness,
  app-data locking and owned-child shutdown/crash cleanup. Runtime/model weights
  remain optional and external. See [desktop documentation](DESKTOP.md).

Verification:

- Full Python suite: **1,110 passed**, **570 subtests**, 12 existing dependency
  warnings. Targeted product/planning/transition checks: **78 passed** plus
  **208 subtests**. All **88 Node tests** passed.
- Final non-editable wheel: worker fixture and installed Studio search,
  phase/transition arithmetic, inbox review, atomic batch/replay, assistant
  settings and packaged UI passed outside checkout.
- Rendered workflows: batch approval created exactly two new crates; inbox
  review persisted; lost monitor acknowledgment survived reload/retry without
  duplicate admission; disabling remained visible; exclusion/restore preserved
  source files; explicit DJ feedback persisted. An actual local-model draft
  applied the exact House/118-124 BPM/clean rule and found the expected two
  generated records, without saving a crate. Conversation and model settings
  survived reload. A focused rendered organization-draft check verified that
  every drafted crate retained the selected source scope and approval stayed
  disabled until a fresh exact-file preview.
- Native packaging/lifecycle fixtures and DMG checks passed. The native picker
  was implemented but **not interactively exercised**; clean-machine,
  Gatekeeper/notarization and external DJ-software acceptance are not certified.
- Synthetic 10k/50k index benchmark: final search took **0.6922s / 3.5362s**;
  unchanged inbox reconciliation **0.1370s / 0.4669s**. Every measured operation
  was below the explicit five-second smoke budget after unloading local models.
  A prior contended 50k search took **6.9498s**: this is not a p95, concurrent-load
  or real-library performance guarantee.

### Real-model outcome: not production-ready

Installed an isolated official Ollama runtime and evaluated actual local
weights on synthetic prompts/metadata. No private library, credentials or paid
cloud inference was used. The runtime has been stopped after evaluation;
weights are not bundled in the app.

- `qwen2.5:1.5b`: **3/8** initial semantic checks.
- `qwen3:1.7b`: **5/8** final checks after schema-ordered few-shot prompt
  improvements. It translated exact clean/BPM/count and energy/history filters,
  rejected unsupported year/deletion requests and produced a grounded first
  conversation draft. It still failed both timed-set briefs and retained-filter
  follow-up semantics. A valid JSON schema is **not** semantic correctness.
- A `qwen2.5:7b` attempt timed out on this shared 8 GB Mac. Its evaluation weights
  were removed; a successful large-model evaluation is not claimed.
- Installed model license responses identify Apache 2.0. That is evidence about
  those tags, not completed legal/distribution review of the entire product.

The repeatable [model evaluator](../scripts/evaluate_studio_ai.py) preserves
individual outputs/errors/latencies and exits nonzero when semantic checks fail.
The [scale benchmark](../scripts/benchmark_studio.py) checks exact result counts
and its explicit operation budget. AI remains experimental/review-only; no
default model is silently certified or installed for users.

### Remaining work and release dependencies

1. Improve and independently evaluate a model/prompt/tool strategy that reliably
   retains restrictions across turns and translates timed briefs. Include
   unseen/adversarial prompts, richer grounded explanations and hardware tiers.
2. Validate the new existing-crate move/rename/merge/undo workflows on authorized
   real libraries; richer personalized histories and independent UX certification
   remain beyond the current explicit preference mechanism.
3. Implement physically recoverable cleanup only after reference inventory,
   journaled quarantine/restore and supported DJ-reference round trips can be
   tested on authorized disposable libraries. Current exclusions are not that.
4. Perform native picker/permissions, clean-machine installation, independent
   accessibility/usability and real DJ export/playback tests; add signed updates,
   Developer ID signing/notarization and additional platforms.
5. Connect providers only with actual permitted APIs/accounts and test access.
   Deploy billing/entitlement/refund/offline-license lifecycles only with selected
   business accounts, credentials and approved commercial/legal requirements.
   No fake integration, entitlement or paid success path was added.
6. Hosted accounts/sync remain separate from this local browser/native product.

These are real outstanding items. Neither a passing software regression suite
nor a built development DMG means the requested commercial product is finished.

## Initial integrated-wave verification

- Full Python suite: **1,070 passed**, plus **489 subtests**, with 12 existing
  dependency warnings.
- All Node tests: **85 passed**, including shared-outbox recovery/stale-tab
  checks. Studio JavaScript syntax check passed.
- Targeted Studio, planner and operations suite: **45 passed**, plus
  **141 subtests**. Existing deterministic demos: **21 passed**.
- Rebuilt wheel installed non-editably into a dedicated environment. Worker
  fixture/JSONL verification and Studio HTTP/static-asset verification passed
  outside the checkout.
- Rendered checks: search, saved rule, proposal approval, plan alternatives,
  export generation, observed-play rediscovery and missing-AI error handling.
  Final shared-outbox test injected a lost approval acknowledgement, reloaded,
  retried the retained request and confirmed **four crates before and after**.
  No horizontal document overflow at 390px or 1,440px.
- HTTP ZIP bytes and manifests were verified. A browser download event did
  not complete reliably in the test browser; filesystem delivery and actual DJ
  software import/playback are **not** claimed as verified.
- No local Ollama executable/runtime was available for a real-model test.
  Adapter schema/network/error contracts were tested with fixtures; actual
  model quality and hardware performance remain unverified.

Generated audio and metadata test contracts only; they do not prove musical
quality, real-library performance or commercial readiness.

## Phase planning and AI-draft follow-through

Implemented after the initial wave:

- Planner 1.1 jointly searches exact-file order and nonempty contiguous phase
  partitions. Each phase has hard duration/BPM/clean requirements, optional
  exact must-plays and a shared-scoring energy target. Global constraints,
  no-repeat identities and absolute pins still apply.
- Studio has an ordered visual phase editor, exact-position pinning and
  replacement that retains all other positions. Conflicts and search-budget
  exhaustion are visibly distinct; neither silently relaxes requirements.
- Approved plans retain their policy/event/phase evidence in the creation
  journal. Historical inspection, JSON download and loading a new planning
  draft work after reload and do not rewrite the saved crate.
- The optional local-AI set-brief translator produces a reviewed draft only.
  It cannot invent source/file obligations or event lists. Applying preserves
  source scope, existing event lists and global exact-file commitments, and
  refuses to overwrite phase-specific must-plays. Deterministic generation
  and approval remain separate.

Verification:

- Final full Python suite: **1,084 passed**, plus **545 subtests**, with
  12 existing dependency warnings.
- Targeted planner/Studio/operations: **59 passed**, plus **197 subtests**.
  Small independent oracles enumerate sequences and every contiguous
  two-/three-phase partition, including pins, alternate edits and exact timing.
- All **85 Node tests** and Studio syntax check passed.
- Final non-editable wheel: worker fixture and installed Studio
  HTTP search/phase-planning/static-asset checks passed outside the checkout.
- Rendered phase edit -> generate -> replace one file while retaining the
  other position -> preview -> approve -> reload -> inspect/replan succeeded.
  Protected replacement focused an explicit error and left policy unchanged.
  Lost phase-plan acknowledgement -> reload -> exact retry retained
  **six crates before and after**, with phase evidence intact.
- A **fixture-injected**, not real-model, AI browser test verified draft
  review/application retained must-plays, pins, exclusions, source scope and
  event lists, and made **zero search/generation calls** before an explicit
  generate action. Existing phase-specific must-plays blocked application
  without modifying the draft. The real unconfigured-runtime error remained
  explicit and left Apply disabled.
- No document overflow at 390px. Synthetic audio, model fixtures and indexed
  durations remain software evidence only.

One intermediate full run hit the existing concurrent staging test's
intermittent "Staged audio is not a regular file" response. That test passed
in isolation and both subsequent full runs passed. Its unrelated staging
implementation was not changed; this result is not proof the race is fixed.

Still independently buildable: mixed-duration optimization and transition
audition, automatic timeline adaptation, ingestion/watchers, recoverable cleanup,
bulk organization and native distribution/licensing work. The roadmap is
not reduced to externally gated accounts or certification.

## Cue and transition evidence follow-through

- Read-only hash-bound existing analysis now supplies cue times/confidence,
  review revisions, grid flags and existing stem-window evidence. Shared
  offset-aware time conversion is reused. No analysis/media worker starts.
- An explicit reviewed-window/adjacent-overlap overlay separates full-file
  runtime, play-window subtotal and estimated elapsed time. Missing/unreviewed/
  stale/out-of-bounds data never becomes a success-shaped timing default.
  Incoming plus outgoing overlaps are bounded against each middle window.
- Transition cards retain exact edits, raw BPM/Camelot, tempo difference,
  overlap assumptions, vocal/grid warnings and mandatory audition caveats.
  Neither existing cue review nor stem candidates certify a musical transition.
- The server computes identical reports for plan, proposal, saved creation
  evidence and the existing three-file ZIP manifest. Exports remain normal
  full-file playlists; DJ software does not apply the windows/overlaps.
  Changed cue reviews stale unapproved proposals and export previews; old
  journals and downloaded exports remain explicitly historical.

Verification:

- Full Python suite: **1,095 passed**, plus **570 subtests**, with 12 existing
  dependency warnings. All **85 Node tests** and Studio syntax check passed.
- Tests cover decimal-exact estimates, omitted versus explicit-zero overlaps,
  stale hashes, unreviewed/unknown/out-of-file windows, combined overlap limits,
  analysis revisions, malformed stored plans, and byte-verified historical
  ZIP round trips with identical embedded transition reports.
- Rendered generated fixture: **360s full files -> 240s reviewed windows ->
  224s estimated elapsed** with 16s explicit overlap. Report, approval preview,
  saved/reloaded journal and export preview all agreed. An automatically
  prepared draft set both windows to `reviewed: false`; no automatic audition
  claim was made. No horizontal overflow at 390px.
- Installed-wheel checks now exercise incomplete versus complete explicit
  transition arithmetic outside the checkout, in addition to search/phases
  and existing worker verification.

These are synthetic contract tests, not listening, real-model or actual DJ
software playback certification. Mixed-duration target optimization, automatic
cue selection, audio transition audition/rendering and wall-clock event
adaptation remain future work.

## Boundaries

Implementation uses generated fixtures, app-owned state and existing selected
catalogs only. No personal-library scan, account access, source-media changes,
paid request, signing, purchase, push or publication is implied.

Real-world musical acceptance, production cleanup/reference repair, connected
providers, streaming approvals, independent usability evaluation, platform
certification, signing identities, deployed billing and licensed content remain
release work even if their adapters/protocols have local tests.

## Order

1. Define typed library/query/proposal contracts over the existing catalog.
2. Build independent constrained-planner and health/history services.
3. Add saved smart rules, explicit approvals and source-evidence revalidation.
4. Wire an opt-in Studio UI and routes to these services.
5. Add export, original templates, Live and readiness/launch surfaces.
6. Exercise generated workflows through HTTP/browser and run regression suites.
7. Record exact implemented capabilities and remaining gates; do not relabel
   scaffolding or mock providers as working commercial connections.
