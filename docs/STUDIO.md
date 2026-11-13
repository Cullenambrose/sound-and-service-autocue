# AutoCue Studio local alpha

Studio is an opt-in, catalog-backed workspace for all six delivery areas in
the [commercial plan](COMMERCIAL-PRODUCT-PLAN.md). It is not a declaration that
the commercial release, connected providers or broad competitor parity are done.

## Launch

From a checkout with the existing dependencies:

```sh
./run-studio.sh --data "/absolute/canonical/path/to/new-studio-data" --port 8772
```

Default app-owned data is `~/Library/Application Support/AutoCue Studio`,
independent of the original app and read-only pilot. `--check` initializes and
checks the app database without starting HTTP; it is not a filesystem dry run.
`--demo` additionally creates/indexes 12 generated WAVs in a new app-owned folder.
Existing demo directories are never overwritten.

Open `http://127.0.0.1:8772`. Stop with Ctrl-C. No analysis/media/tag writer
starts. A catalog scan worker processes selected-folder jobs submitted explicitly
or admitted by an explicitly enabled monitor; queued jobs resume and interrupted jobs are reported on restart.
Use **Refresh scan progress** for progress/error details.

**Demo evidence:** generated audio is two seconds of silence. Its titles,
genres, BPM, keys, energy and 180-second planning durations are deliberately
invented index metadata. This tests software contracts, not actual musical
analysis, audible cleanliness or performance timing. Do not mix it with a real
library or present the demonstration as listening evidence.

An existing app catalog can be used only by explicitly selecting its data
directory, after stopping the other server and backing it up. Studio adds
separate tables and reuses the catalog/personal-crate tables, not a second
authoritative library. Keep a single Studio process per data directory.

### Private database backup and non-destructive restore

**Download private database backup** exports a consistent SQLite snapshot,
including committed WAL state, with a SHA-256 manifest, schema digest and exact
table counts. The endpoint requires the same local session/origin checks as
other Studio actions. Source media is not read or changed.

The ZIP contains only `library.sqlite3` and `manifest.json`. It includes saved
crates/rules, receipts, conversations, feedback, indexed history, inbox and
monitor settings. It contains private metadata, paths and conversation text;
keep it private. It is **not encrypted**, and checksums detect corruption, not
the authenticity of an archive from another party. Restore only your own trusted
backups. Audio, models, existing exported ZIPs, the ephemeral session token and
browser pending-action outbox are not included. Download a pending browser
action separately before changing datasets.

From an installed wheel or checkout:

```sh
autocue-studio --data "/absolute/canonical/path/to/existing-data" \
  --backup "/absolute/path/to/new-backup.zip"
autocue-studio --data "/absolute/canonical/path/to/NEW-restored-data" \
  --restore "/absolute/path/to/new-backup.zip"
autocue-studio --data "/absolute/canonical/path/to/NEW-restored-data" --check
```

For a checkout, replace `autocue-studio` with `./run-studio.sh`. Backup can read
committed state while the app is running; restore never replaces an existing
directory, even an empty one. It verifies ZIP bounds/entries, database bytes,
SQLite integrity/references, schema digest and table counts before publication.
Unsupported backup format versions fail explicitly. Archives are limited to
64 MiB and uncompressed databases to 256 MiB. Disk errors never overwrite the
old data directory or an existing backup path.

Launch Studio against the restored directory to inspect state. Availability
remains historical index evidence; original audio paths must still be available,
and approvals/exports still require fresh byte checks. This is database recovery,
not a complete media-library backup, in-place rollback or physical quarantine
restoration. Restore is deliberately CLI-only, not an active-server mutation.

## 1. Grounded AI and library search

The filter builder always works without AI. Supported fields are:

```json
{
  "q": "",
  "artist": "",
  "genre": "R&B",
  "bpm_max": 95,
  "clean_only": true,
  "never_played": false,
  "limit": 100,
  "sort": "bpm"
}
```

Optional filters: `bpm_min`, `energy_min/max` (0..1), `scope_ids` (exact file
IDs). Sort by title, BPM, energy, explicit DJ `preference`, or oldest observed last-played time, with
unknown values last. The result limit is 1..1,000. Search currently evaluates
bounded indexed metadata, up to 50,000 indexed files; it is not a new FTS or
vector index and has no claimed 100k-file performance certification.
Warm search reuses at most two process-local, committed database snapshots.
SQLite generation triggers invalidate them on file/root, history, cue-analysis,
exclusion or feedback writes, including other connections. Each read transaction
retains its own snapshot; provisional writes and schemas missing any required
trigger bypass caching. Returned tracks are copied independently. Derived text,
clean-tag and ordering evidence is prepared once per snapshot; filters still
recompute, with no media probes or relaxed source-verification requirements.

Queries never stat or hash music. Indexed availability is explicitly historical;
selected files are freshly checked/hashed on proposal approval and export.
Clean-only means explicitly tagged clean with dirty/explicit taking precedence,
not an audible-content guarantee. Missing numeric metadata cannot satisfy a
numeric bound. Requests to index folders are separate, explicit operations.

### Optional local intent extraction

If Ollama and a suitable model are **already installed**, save its exact tag in
**Conversational local assistant > Save local model setting**. This persists in
the app database and works in both the browser and native shell. Empty disables
AI. Saving does not validate model quality, start a runtime or download weights.
The environment remains an initial fallback until an app setting is saved:

```sh
AUTOCUE_STUDIO_MODEL="your-installed-model-name" ./run-studio.sh
```

Studio contacts only `http://127.0.0.1:11434/api/chat`. It disables proxy
inheritance and redirects. The one-shot translators send the user's prompt plus the intent schema,
not library files, file paths or a library dump. The conversation workflow below
can share explicitly opted-in, bounded index evidence. This app makes no direct cloud
API request, model download or paid request. Use local model weights and disable
runtime cloud routing; Studio cannot control a separately administered runtime.
Known cloud-tagged model names are rejected.

The search translator supports search/build and the documented filters. The
separate timed-set translator described below supports reviewed planning
drafts. The model must flag unsupported requirements. Results are displayed
for human correction; they cannot save, export, delete or expand permissions.
Actual synthetic model evaluations now exist; compact models failed semantic
requirements. AI remains experimental, not a production-quality promise.
The normal filter form remains available when the runtime is missing or fails.
There is no silent fallback pretending an AI request succeeded.

Known explicit release-date phrases (for example `released in 2007`,
`release year: 2007` and `songs from the 1990s`) are rejected by the application
**before inference**, for one-shot searches/plans and every conversation tool,
including follow-ups. No turn/receipt is saved for a rejected request. The
evaluator labels these `application-capability-check`, not model successes.
This intentionally narrow veto is not a general natural-language parser:
other unsupported wording can still be missed. Matching phrases are blocked
even inside quoted search text or a negated instruction; remove the phrase or
use the typed filter form for literal text. Artist names containing a year,
ordinary numeric durations and names such as `Year of the Ox` are not blanket
blocked. No release-date metadata filter was added.

### Conversation, retrieved evidence and personal preference

The assistant stores up to 100 revision-checked turns per conversation ID.
Resume by ID or start a new conversation. Every message returns one draft;
there is no autonomous tool loop. An Auto first turn may classify the tool then
draft (at most three local-model calls for a new timed plan, two for other
tools). Explicit new timed plans use two calls; compatible follow-ups use one.
Auto follow-ups keep the current tool;
use the explicit tool selector to change it, or start a new conversation.
Search/build drafts copy into the filter form;
plan drafts go through the existing reviewed-plan controls; organize drafts copy
into the batch editor and still require preview plus approval.
Applying an organization draft preserves the current explicit source scope in
every proposed crate; it cannot silently broaden a scoped selection.

Refinements now use application-owned patches: omitted fields retain the prior
rule/policy. Explicit field removal must be represented in the checked
`clear_rule_fields`/`clear_policy_fields` lists; contradictory set-and-clear
output is rejected. Prior phase budgets and nondefault tolerance/track limits
are preserved unless replaced explicitly. Actual before/after filter/policy
changes are shown separately for review. Changing tool does not import an
incompatible old policy.

The model returns native typed JSON objects;
numeric/boolean strings, duplicate JSON keys, nonfinite numbers and unknown
fields are rejected. New timed plans use separate timing and source-quoted
constraint extraction, assembled into the existing native rule/policy APIs.
Follow-up search/build/plan calls use a patch-only schema:
the app owns the action and existing draft name. Timing defaults are applied
by the validator, not required again in a partial policy. No syntax-repair loop
or success-shaped fallback is used.
JSON-mode generation is **not** accepted as schema or semantic proof: the same
application validators run before any draft is stored. Actual compact/mid-size
model quality still fails expanded tests; typed tools remain the dependable path.

By default the model sees no catalog metadata. The explicit local-evidence
checkbox retrieves at most 12 scoped records for the application receipt
(bounded text metadata, exact IDs, observed-play counts, explicit DJ ratings and
cue-review summaries), plus at most four adjacent transition cards in retrieval
order. The model sees only the first three metadata examples on initial/tool-change
search/build/organize drafts. Timed-plan extraction receives no catalog data.
Compatible follow-ups send no metadata, old filters or old user messages;
only the newest message, patch schema and bounded prior planning policy are sent.
Citations must reference the three supplied examples; patch-only follow-ups have
no model citations. Transition cards and explanation facts are computed and
displayed by the application, **not sent to the model**. They are not a generated
set, listening evidence or safe-mix certification. Paths, source bytes and
private feedback notes are omitted. User messages themselves are sent as typed.
No database writer lock is held during inference. Changed library/conversation
evidence rejects a stale result.

Per-file Prefer/Neutral/Avoid-softly feedback is explicit, editable and durable.
It is not crowd-response learning. `sort:"preference"` opts into rating order.
Planning `use_feedback:true` adds a bounded five-point preference adjustment;
the default remains unchanged and hard exclusions, pins, clean and timing rules
still win. The model can misunderstand or drop requirements: inspect the entire
draft and keep using typed filters when it fails.

### Exact-file proposals

Search -> select files -> preview -> **Approve exact files and save new crate**.
Proposals bind dataset, index/history revision, selected metadata/hashes, order,
name and expiry. Approvals reject stale evidence and reverify selected bytes.
They atomically create an immutable catalog snapshot, explicit reviewed
exact-file decisions, and an existing personal crate plus action receipt.
They never write audio, tags or third-party crates.

Match approval here confirms the exact indexed selections shown in the preview;
it does not assert that a different client-requested edit sounds equivalent.
Static proposals do not implicitly enforce event constraints; use the constrained
planner when event policy is required.

## 2. Smart crates

Save named typed rules in an optional folder/category. Updates require the last
revision. Load/refresh reruns the same rule against current indexed evidence.
Freezing results produces a separate approval-bound static crate; existing
static crates never silently change. Original dinner/house/rediscovery recipes
are editable rules, not licensed music collections or competitor content.

**Bulk crate organization** accepts 1-8 `{name,rule}` entries. Preview displays
every exact file; approve creates all new static crates in one transaction
(at most 500 total entries). Any stale/expired proposal or changed source rolls
back the entire batch. Existing crates and original audio are untouched.
Durable batch receipts reconcile a lost acknowledgment without duplicating saves.

### Existing-crate edits and undo

Use a saved crate's **Draft reviewed rename/reorganization** button to load its
current revision, exact occurrence IDs and editable JSON examples. Edit
operations are sequential within one preview and bind the original revision
of every affected crate:

```json
[
  {"action":"rename","crate_id":"existing-id","base_revision":3,"name":"Dinner"},
  {"action":"merge","source_id":"other-id","source_revision":2,
   "target_id":"existing-id","target_revision":3,"mode":"copy"}
]
```

Other supported operations:

- `merge` with `mode:"move"` appends all source occurrences then empties the
  source crate; it never deletes the crate.
- `move` uses `source_id/source_revision/target_id/target_revision` plus
  `entry_ids` to move only selected occurrences in source order.
- `reorder` uses `crate_id/base_revision/entry_ids`, listing every current
  occurrence exactly once in the desired order.
- `remove` uses `crate_id/base_revision/entry_ids` to remove selected
  memberships only, not audio.

Up to eight operations/eight affected crates and 500 resulting memberships
are supported. Copy/merge retain repeats rather than deduplicating versions.
New target occurrence IDs cannot retarget existing membership IDs.

Preview shows before/after state and indexed membership evidence. Approve
commits every crate and recovery receipt together; a stale revision, expired
preview or failed later save rolls back all changes. Browser receipts bind the
exact reviewed names, revisions and ordered references across reload/retry.

The durable history has a downloadable before/after journal and **Undo this
entire edit if unchanged**. Undo restores names, membership IDs and order while
incrementing revisions; it refuses if any affected crate has newer changes.
Undo itself has an exact replayable receipt. Older exports/creation journals
remain historical and are not rewritten; current export previews must be
regenerated after a revision change.

These are app-state edits, not fresh source-byte or playback certification.
Existing unavailable/unreviewed memberships can still be organized. Original
files, tags, match decisions and external DJ databases remain unchanged.
The assistant's organize tool still drafts **new** crates; existing-crate
operations require the explicit editor, not autonomous AI execution.

HTTP endpoints: `edit/preview` accepts `dataset_id,operations`; `edit/approve`
and `edit/undo` accept `request_id,dataset_id,edit_id` and optionally
`expected_crates` for exact receipt binding. They use the same loopback
authentication, transaction ownership and durable-outbox protections.

## 3. Whole-set planning

The planner uses the current search scope plus the existing event schema and
shared matcher. Do Not Play, exact must-play versions and conflicts are resolved
before the new [pure whole-set planner](SET-PLANNER.md).

Explicit policy specifies target/tolerance, maximum count, exact required IDs,
excluded files, pinned positions and optional BPM/clean bounds and energy
preference. Every returned alternative passes the hard policy. Search exhaustion
is reported as unknown, not proof that no feasible set exists.

**Preview this exact plan** recomputes the plan on the server and records its
policy/event/alternative evidence in the proposal. The client cannot manufacture
a verified plan by submitting a list and a success label. Approving freezes its
exact order. The proposal journal retains the planning evidence; the saved
personal crate references that journal through its proposal-based ID.

### Ordered phases and energy arcs

Open **Build an ordered phase schedule**, add the event phases, then set each
label, target/tolerance, optional energy target, clean/BPM bounds and exact
must-play IDs. **Apply phase schedule to policy** validates the complete policy
on the server before replacing the JSON draft. It updates the global target
to the sum of phase targets, preserving global tolerance, exclusions,
must-plays and pins. The visual editor uses tenths of a minute; advanced JSON
supports finer decimal budgets. Editing the visual draft alone does not change
the policy used for generation.

Every phase must contain at least one full track and meet its own budget.
Phase requirements add to global requirements, never weaken them. Repeated
open-dancing phases with different energy targets express warm-up, peak and
closing steps. Returned schedules show exact order, relative start/end times
and target differences. Unknown metadata cannot satisfy a hard bound.
See the [planner contract](SET-PLANNER.md) for phase schemas and limits.

### Optional AI set briefs

**Translate a timed set brief with local AI** uses that same loopback-only
runtime and privacy boundary. Describe an intended duration and optional
ordered phases, metadata bounds and energy preferences. For example:
"Plan 30 minutes: dinner 10 minutes, then open dancing 20 minutes, using
tagged-clean tracks." Named song/version obligations, year filters, cue-derived
timing and external operations are unsupported by this translator; keep exact
event requests in the existing event form.

Translation returns a visible JSON draft, not tracks or a feasibility claim.
The model has no library evidence and is not allowed to supply source IDs,
must-play IDs, exclusions, pins or event lists. Defaults when unspecified are
3 minutes global tolerance, 20 tracks maximum and 1 minute per-phase tolerance.
Review them explicitly; they are application defaults, not user requirements.

New briefs use two bounded calls: timing first, then literal music constraints.
The app sums explicitly supplied stage budgets if no total was given, checks
numeric timing against the source request (including numeric hour conversion),
and requires exact source-text quotes for phase budgets and music constraints.
Text/numeric filters must also match their quoted values. Unknown fields,
duplicate assignments, invalid phase indices and ungrounded output reject the
whole draft; nothing is silently discarded or repaired. Each call has a
90-second timeout, and the second call is skipped if timing fails.
Phase-budget quotes must also occur in source order without overlap; repeated
identical budgets need distinct occurrences in the request. Boolean constraints
must have native boolean values and relevant quoted words (`clean`, `never
played`/`unplayed`, or feedback/preference/rating). These lexical checks reject
unrelated evidence but do not certify negation or phase meaning.

Source quotes and numeric checks are **not semantic certification**: the model
can still omit a requested filter, misread a negation, associate a valid number
with the wrong field or reject a valid request. Word-only timing such as "half
an hour" is not certified. The returned `constraint_evidence` is for inspection,
not proof of completeness. The proposed name is application-owned
`AI set draft`; rename it in the form. Follow-up policy patches still use the
existing one-call refinement workflow and require separate review.

**Apply reviewed translation** replaces unsaved search/timing preferences,
while retaining the selected source IDs, event lists and existing global
exact-file must-plays, exclusions and pins. It refuses to overwrite a schedule
containing phase-specific exact must-plays. Incompatible retained pins cause
validation errors rather than being dropped. Applying never searches,
generates or saves; those remain separate user actions. Real-model checks
demonstrated both accepted translations and semantic failures; fixture
contracts alone are not evidence of real-model accuracy.

### Pins, repair and saved-plan evidence

**Pin position** adds an exact-file pin without replacing conflicting pins.
**Replace position; keep all other positions** excludes that exact file and
pins all other selected positions before generating again. It does not ban
all alternate edits of the song, erase required tracks, relax phase/global
constraints or rewrite the saved crate. Required/pinned replacements are
blocked; event requirements remain enforced by the shared matcher. Repairs
can be infeasible, and bounded search exhaustion means unknown, not impossible.
Changed inputs require regeneration before old result controls can be used.

After saving and reloading, **Inspect saved creation evidence** retrieves the
digest-checked historical proposal, including its policy/event and exact phase
schedule. Later crate edits do not rewrite that journal; a comparison reports
whether current indexed file IDs/hashes and order still match it. This is not
a fresh source-byte verification. Download the evidence JSON separately from
the playlist ZIP when needed. **Load as new planning draft** restores those
inputs for regeneration against current evidence, without changing the saved
crate. Historical inspection works after proposal expiry; new approvals still
require an unexpired, freshly checked proposal.

### Cue evidence and explicit playback timing

Search results expose **Existing cue evidence** for any analysis already in
this app's `tracks` table with the same indexed whole-file SHA-256. Studio
does not start analysis, read audio/stems, probe media or guess a match by title.
Evidence includes review revision/state, plan digest, cue times/confidence,
grid diagnostics, flags and existing stem-window evidence. Bar-based cue
times use the shared offset-aware conversion. Invalid grid conversions are
shown as unresolved timing, not zero. Missing analysis stays missing.
Analysis/review changes invalidate unapproved proposals, even when source
bytes and the music index have not changed.

Open **Reviewed play windows and transition evidence** for an optional explicit
timing overlay. The JSON has exactly `windows` and `overlaps` lists, each at
most 60 entries:

```json
{
  "windows": [
    {"file_id": "exact-file-a", "content_hash": "64-lowercase-hex-characters",
     "entry_s": 10, "exit_s": 130, "reviewed": true},
    {"file_id": "exact-file-b", "content_hash": "64-lowercase-hex-characters",
     "entry_s": 10, "exit_s": 130, "reviewed": true}
  ],
  "overlaps": [{"from_id": "exact-file-a", "to_id": "exact-file-b", "seconds": 16}]
}
```

Replace example IDs/hashes with actual search evidence. You can prepare a
full-file draft from an alternative, but every generated window is explicitly
**unreviewed** and overlaps are zero cuts, not inferred mixes. Inspect and
audition before setting `reviewed: true`; that field records your assertion,
not independent proof of listening. Existing cue review never automatically
approves a whole play window or transition.

The report keeps full-file subtotal, valid reviewed-window subtotal and
estimated elapsed time separate. It uses decimal-exact arithmetic:
`sum(exit-entry) - sum(explicit adjacent overlaps)`. Every selected window
needs matching hash, review and bounds inside a known file duration. Each
adjacent pair needs an explicit overlap, including zero for an intentional
cut. Individual overlaps cannot exceed either window; combined incoming and
outgoing overlaps cannot exceed the middle window. Missing, stale, unreviewed
or out-of-bounds evidence leaves elapsed time **unavailable**, not full-file
runtime disguised as a mixed estimate. Unused IDs/pairs are reported.

Transition cards show both edits, raw BPM/Camelot evidence, tempo difference
and explicit overlap. Cue/grid/stem warnings retain their original provenance.
Detected vocal activity is not a clean intro; a stem-derived candidate is
not proof of vocal absence, phrase alignment, grid accuracy or a clean loop
seam. All transitions still require audition. The overlay performs no audio
rendering, time stretching, key shifting, automatic cue selection or safety
certification.

The server recomputes the report before creating a proposal. Preview, saved
creation evidence and the playlist ZIP's `manifest.json` retain the same
report. The M3U8 remains a full-file playlist: it does **not** apply the windows
or overlaps in DJ software. File-export readiness does not mean musical or
timing readiness. Later cue edits are flagged as changed historical analysis;
they never rewrite an older journal/export. Current analysis revisions also
bind an export preview, so a changed review requires a new preview.

Full-track phase feasibility remains unchanged by the overlay. An elapsed
estimate of 224 seconds does not certify a six-minute mixed target merely
because the full files sum to 360 seconds. Mixed-duration optimization and
automatic wall-clock timeline conversion remain follow-through; the phase
schedule is still an explicit ordered full-track budget.

## 4. Operations and history

See [library operations](LIBRARY-OPERATIONS.md) for indexed health, exact-hash
versus related-edit groups, and explicit observed-play history. Record plays
manually against exact file IDs. Rediscovery means no observation in this app,
not certainty a song has never been played. No delete, flatten, rename, repair,
quarantine or original-tag writer is enabled.

Select displayed exact observations, then **Preview selected history removal**.
Review their IDs, timestamps, notes and current removal revisions before
**Approve reviewed history removal**. Approval removes those observations from
active history/rediscovery only. Refresh or selection changes invalidate the
browser preview; server-side stale evidence is also rejected.

**Undo these history removals** restores only that batch if unchanged.
New observations made afterward remain intact. Both operations use the durable
browser outbox and exact receipt replay after reload/lost acknowledgements.
The original play receipt is retained, so replaying an old record-play request
cannot recreate a removed observation. Preferences and saved crates are untouched.

This is reversible logical removal, **not permanent privacy erasure**: private
event text and recovery receipts remain in the database and backups. The UI
shows up to 100 active observations and 30 recent recovery journals. Download
the displayed active history or an exact recovery journal as private JSON;
the active-history download is explicitly bounded, not a complete history export.
The API supports 1..500 selected observations per reviewed batch; no AI history
mutation tool is enabled. See [history contracts](LIBRARY-OPERATIONS.md#reviewed-history-removal-and-undo).

The acquisition inbox shows up to 100 new/changed evidence records first,
metadata gaps, exact-version text, availability/errors and whole-file hash
group counts. The initial catalog also enters the inbox; this does not claim
every indexed file was newly purchased. Reviewed/ignored status is reset to new
if published hash/metadata/availability/error evidence changes.

Enable monitoring separately for each already-indexed root, at intervals of
60-86,400 seconds. No roots are monitored by default. Monitoring runs only while
Studio is open and admits the existing recoverable incremental scan jobs;
unchanged signatures reuse hashes/metadata. Refresh the inbox/monitor state
and scan progress to inspect admission errors, job progress and restart state.
Disabling prevents future admissions but does not cancel an already admitted job.

Exact hash-bound Studio exclusions are reversible app-owned selection filters.
They do **not** move/delete/quarantine audio or alter saved crates and external
DJ references. A changed hash invalidates the old exclusion; restoration uses
current index evidence. Physical recoverable cleanup/reference repair remains
an unimplemented release gate, not a disguised delete button.

The missing-song form reuses the existing 25-provider navigation registry.
It preserves the requested artist/title/version in copyable search text and
distinguishes verified search URLs from login/homepage fallbacks. Generating
links contacts no provider and changes no fulfillment state. Acquisition,
subscription eligibility and exact-version checking remain your responsibility;
index an explicitly selected folder after obtaining files legitimately.

## 5. Distribution and commercial readiness

The wheel now contains the Studio entrypoint and web assets as well as the
existing worker:

```sh
python -m pip wheel --no-deps . --wheel-dir /your/disposable/wheels
# Install the resulting wheel into a dedicated environment with dependencies.
autocue-studio --data /your/canonical/app-data --check
autocue-studio --data /your/canonical/app-data --port 8772
```

`scripts/verify_studio_install.py` checks an installed, non-editable wheel
outside the checkout, including generated inputs, actual HTTP search and static
assets. This is a repository/Python distribution, **not** a signed native
installer or automatic updater.

The readiness report is path-free and explicitly says `commercial_ready: false`.
It inventories external release gates, not a mock billing/signing success.
No billing, cloud account, service secrets or update server is fabricated.

### Browser and downloadable macOS versions

The current browser UI talks to a local Python/SQLite backend; it is not a
hosted multi-user service. The same HTML/CSS/JavaScript and deterministic
backend are reused by the new Swift AppKit/WKWebView shell with a frozen Python
sidecar, rather than a second implementation of planning, catalog or export logic.
See [desktop build and verification](DESKTOP.md). An Apple Silicon development
`.app` and DMG have been built and smoke-tested; this is not a signed release.

A production release still needs signing/notarization, signed updates,
clean-machine certification and independent permissions/usability testing.
The development shell bundles the backend, exposes a native folder picker,
owns its private loopback process and guards app-data ownership. It does not need
a separate Python install. Local model installation remains a separate,
explicit capability, not an implicit bundled dependency.

A remotely hosted website cannot freely scan the user's local music folders
or write DJ-library files. It needs limited browser file access or an explicitly
installed local companion, with permission/authentication boundaries designed
for that connection. Hosted accounts, cloud storage and synchronization are
additional work, not provided by wrapping the current local UI. Shared business
logic does not imply shared filesystem access or existing cloud support.

## 6. Destination parity and isolated Live

The capability registry distinguishes verified local ZIP export, manual M3U
destinations and unavailable connected services. Saved crates use the existing
two-stage playlist byte verification and historical integrity-checked downloads.
Serato/rekordbox/VirtualDJ need actual software import/count/playback acceptance;
there is no new direct database writer.

Manual Live suggestions reuse `rank_next_song` and the actual selected phase,
event context, current file and played IDs. They never persist changes or
rewrite saved Prep/history. This is not a now-playing detector, guest QR service,
native drag bridge or sample-pad player.

## Recovery and privacy

Smart-rule saves, approvals, batch approvals, model configuration, conversation
turns, inbox reviews, monitor settings, exclusions, feedback and observed-play entries use request receipts.
The browser retains an exact pending durable request before sending, checks
matching dataset/request receipts and offers retry/download/explicit discard.
Discarding a browser retry does not undo a committed action.

App state and browser drafts contain local file references and may contain
event details. Keep them private and back up the complete app-owned data
directory with the app stopped. Export ZIPs contain paths; the path-free
readiness report does not include them. No remote telemetry is added.

## Validation

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine .venv/bin/python -m pytest -q \
  web/test_studio.py web/test_studio_product.py web/test_library_operations.py engine/setlist/test_planning.py
node --check web/studio.js
node --test web/test_*.cjs
```

Full repository regression and installed-wheel checks remain part of the
release verification. Synthetic and rendered results are not independent
musical/commercial certification.

Synthetic-only real-model and scale checks (no private library required):

```sh
PYTHONPATH=engine .venv/bin/python scripts/evaluate_studio_ai.py \
  --model qwen3:1.7b --output /your/disposable/model-report.json
PYTHONPATH=engine .venv/bin/python scripts/benchmark_studio.py \
  --output /your/disposable/scale-report.json
```

Nonzero exits preserve reports and mean the semantic checks or five-second
per-operation scale budget failed. They are not silently reclassified as passing.
These scripts require no new packages; the model check requires an already
running local-only Ollama service with installed weights.

`PYTHONPATH=engine` ensures checkout modules precede any older worker wheel in
the test environment; installed-wheel verification separately runs without it.

See the [delivery checklist](SIX-STAGE-IMPLEMENTATION.md) for the recorded
implementation results and outstanding work, including independently buildable
features as well as external release gates.
