# Selected-file read-only Prep pilot

This opt-in pilot removes the synthetic-path restriction **only for individually
selected files**. It is not approval of real Serato compatibility or a general
production release. No real library or signed-in provider account was available
for this implementation's validation; automated tests generate their own audio
containers, tags and Serato snapshots.

The existing main app, synthetic demo and default launcher are unchanged.
The pilot starts only the loopback Prep server. It does not initialize scan,
analysis, playback or tag-writing workers, enumerate music folders, access
provider accounts or write Serato/crate/tag/audio files.

## Guided setup (recommended)

Run `./run-read-only.sh` from the repository. This dedicated launcher checks
Python 3.10+ and the core metadata dependency, then guides you in the terminal:

1. Choose **new** or **resume** explicitly.
2. Select an existing event JSON or enter an event name, must-play songs and
   do-not-play songs. Version text is optional, not inferred.
3. Enter exact individual initial-library and optional acquisition paths, one
   per line. Blank lines finish each list; folders are not accepted.
4. Choose a new `06-*` output directory, review every selected path and type
   `YES` to write the configuration and launch.

Enter `:cancel` at any prompt to stop without writing configuration or starting
HTTP. Invalid inputs are explicit errors; rerun setup to correct them.
The wizard is keyboard/terminal based, not a native file-picker dialog.
It writes `06-pilot-launch.json` only after confirmation and retains the same
selection/event schemas as the explicit CLI. Keep this app-owned file private
alongside the session; it contains local paths and event metadata.

Resume or preflight a guided session without hand-written manifests:

```bash
./run-read-only.sh --resume "$HOME/Desktop/MASTER-TODO/outputs/06-read-only-event"
./run-read-only.sh --resume "$HOME/Desktop/MASTER-TODO/outputs/06-read-only-event" --check
```

`--help` lists explicit-input, recovery and backup/restore modes. Do not mix
guided/resume flags with explicit selection/event/output flags.

## Select inputs explicitly

Create a local JSON manifest using **exact absolute paths**, not folders:

```json
{
  "schema_version": 1,
  "library_files": [
    "/Selected/Music/Existing Clean.wav"
  ],
  "acquisition_files": [
    "/Selected/Acquired/New Dirty.wav"
  ]
}
```

These are illustrative paths, not discovered files. Choose only material you
authorize for read-only inspection. Neither list is expanded recursively.
Canonical local paths are required; symlinks, traversal, duplicate selections,
non-regular files and unsupported extensions are rejected. Each list has a
1,000-path limit. The acquisition list permits later verification; its files
are not added to the library at startup. They may be absent until obtained
externally. The shared session permits at most 100 accepted acquisition records.
Restarting with a changed selection requires a new output directory.

Select an event JSON using the existing event schema, for example:

```json
{
  "event_name": "Selected event",
  "music_lists": {
    "must_play": [
      {"title": "Requested Song", "artist": "Requested Artist", "version": "Dirty"}
    ]
  }
}
```

Missing title/artist tags are explicit errors. Filenames are not treated as
metadata. The shared catalog reader extracts tags; the shared setlist engine
alone decides identity, requested versions, ranking, exclusions and fulfillment.
Tag verification does not certify licensing, audible content or audio quality.

## Launch and preflight

From the repository, using the existing environment:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m web.read_only_prep \
  --selection /Selected/pilot-selection.json \
  --event /Selected/pilot-event.json \
  --output "$HOME/Desktop/MASTER-TODO/outputs/06-read-only-event" \
  --check
```

`--check` verifies the selected library and any restored acquired files, checks
session compatibility and loopback port availability, creates the output
directory/binding when new, then exits without HTTP. It is not a read-only
filesystem dry run: a new output binding is written. Omit `--check` to start at
`http://127.0.0.1:8769/`; choose `--port`
if that port is in use. Stop with Ctrl-C.

All generated artifacts must go into a non-symlink `06-*` directory directly
under `~/Desktop/MASTER-TODO/outputs`. Do not use any existing music, Serato or
main-app data directory. Source files are opened read-only; only the selected
pilot output directory is written.

No new dependency or analyzer/model installation is needed for an environment
already running the app/tests. This is a repository-module launcher, not a
packaged installer, automatic updater or migration of the main application's
production database.

## Import a selected Serato playlist

In Prep, select a static `.crate` and matching `database V2` snapshot through the
file picker. Disposable copies are recommended. Choose the correct original
volume root for relative references. Snapshots are uploaded as bytes; the
server does not open the media paths they contain.

- Snapshots are bounded to 32 MiB each; crates to 1,000 memberships.
- Strict TLV decoding rejects malformed/truncated/unsupported data explicitly.
- Exact paths join crate memberships to database metadata and selected verified
  files. There is no basename, fuzzy-path or automatic root reassociation.
- Missing metadata, absent/unselected files and metadata conflicts stay distinct.
- Unselected database references are never probed, even when paths exist.
- Original order, repeated memberships, source hashes and raw paths are retained.
- Preview is read-only. Explicit acceptance replaces the working source, not a
  previously saved/exported set.
- The engine's version/exclusion policy is unchanged. An imported file that is
  not the eligible selected version stays blocked rather than being silently
  substituted. Remove or resolve blocked occurrences explicitly before saving.

Synthetic samples and synthetic portable CSV/M3U/catalog intake are disabled in
this pilot on both the UI and server. Their existing demo behavior is unchanged.

## Verify an externally acquired file

Obtain the file independently through your normal authorized provider workflow.
Choose a pending request and one of the launch manifest's acquisition paths.
No audio upload or browser-supplied title/artist/version is accepted.

Preview reads tags, hashes the complete selected file, verifies a stable file
identity and uses the existing engine to show resolution changes. It does not
save, fulfill or add a file merely because it was previewed.

Acceptance re-reads/re-hashes the file and rejects a changed preview. Wrong or
unavailable versions remain unresolved; ambiguous requests still need the
existing explicit confirmation. Imported missing memberships each require their
own explicit reconnect. Previous saved/exported artifacts remain unchanged
until the DJ saves and exports again.

Active files are checked for device/inode/size/mtime/ctime changes before views,
commands and export verification. A removed, replaced, symlinked or changed file
fails closed with an error; stale files cannot continue to fulfill a request
through a successful server response. These checks are point-in-time evidence,
not a guarantee that a file remains online after export or is audibly correct.
Metadata-only filesystem changes can also invalidate the strict fingerprint.
Validation is reused only within one locked workflow operation, never cached
across requests. Metadata-only ctime changes were observed on generated files
on this development machine; they remain explicit failures rather than being
silently treated as safe. Stop/review the inputs before starting a new session.

## Restart and update safety

Keep the manifest, event file and output directory for restart. The binding
records schema version, selected path lists, event identity and verified initial
library. Acquired additions are fully verified again on restore. Changed inputs,
changed media, incompatible sessions or artifacts without a pilot binding are
rejected instead of reset or silently migrated.

Browser reload obtains a fresh local token and restores the durable server
session. The existing revision checks and request receipts support exact-action
retries after lost acknowledgments. Saved JSON/M3U8 bytes and order are verified
before download. Live guest actions do not overwrite saved Prep artifacts.

Before trying another code version:

1. Stop the pilot and preserve its manifest/event/output as a unit.
2. Run the new version's `--check` against the preserved selection and output.
3. If rejected, retain the old artifacts. Use a new `06-*` directory and explicitly
   preview/import again. Do not edit the binding or force a reset/migration.
4. Verify the saved export and selected paths in disposable DJ-software testing
   before relying on them at an event.

### Interrupted saves and exports

Save/export changes multiple app artifacts. Before the first artifact change,
the pilot durably records their prior bytes in `06-pending-write.json`; directory
updates are fsynced. The journal is removed only after the session commit.
Other commands keep their single durable session write.

If interrupted, startup and further operations block explicitly. Stop the
server, preserve its output directory, then run the same launch command with
`--recover --check` (or use `--resume DIRECTORY --recover --check`).
Recovery restores the exact state **before the unacknowledged save/export** and
revalidates selected files. It does not repair malformed journals or ignore
binding/checksum failures. A failed recovery keeps its journal for another
attempt; do not delete it to force startup. A server crash after commit but
before journal removal can therefore roll back that unacknowledged action.

### Backup and restore

Stop the server before these CLI operations to avoid another process changing
the session. Use a new backup filename and a new restore directory:

```bash
./run-read-only.sh --resume "$HOME/Desktop/MASTER-TODO/outputs/06-read-only-event" \
  --backup "$HOME/Desktop/MASTER-TODO/outputs/06-event-backup.json"
./run-read-only.sh --restore "$HOME/Desktop/MASTER-TODO/outputs/06-event-backup.json" \
  --output "$HOME/Desktop/MASTER-TODO/outputs/06-event-restored"
./run-read-only.sh --resume "$HOME/Desktop/MASTER-TODO/outputs/06-event-restored" --check
```

The bounded JSON bundle contains only allowlisted app artifacts, configuration,
checksums and selection/event metadata—not music, Serato files or dependencies.
Keep backups private. Restore validates checksums, schemas, binding, selected
file content and saved/export integrity in a new staging directory before
activation. It never overwrites a session or retargets a missing media path.
Explicit-input sessions without a guided launch configuration resume using
their original `--selection` and `--event` paths plus the restored `--output`.

There is no automatic update, destructive migration or signed/notarized
installer. The dedicated launcher and preflight support manual repository
updates and rollback with preserved compatible artifacts; they do not prove
another machine's compatibility. Main-app production integration, live-file
DJ-software acceptance and unattended deployment remain release gates.

## Generated format and performance checks

See the [generated format matrix](READ-ONLY-FORMATS.md) for what was actually
tested; generated-container parsing is not a real-library compatibility claim.

The opt-in development benchmark generates individual tagged WAVs and profiles
the actual read-only flow over loopback HTTP:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m web.benchmark_read_only \
  --output "$HOME/Desktop/MASTER-TODO/outputs/06-readiness-generated" --tracks 1000 --prepare-only
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m web.benchmark_read_only \
  --output "$HOME/Desktop/MASTER-TODO/outputs/06-readiness-measured" --tracks 1000 \
  --fixtures "$HOME/Desktop/MASTER-TODO/outputs/06-readiness-generated"
```

Use new directories; the reuse flag is only for previously generated benchmark
fixtures. `--seconds 60 --tracks 100` prepares a bounded larger-container case.
Reports include raw 200-sample latency, cold/warm distributions, startup,
restart, acquisition timings, bytes and a top-30 cumulative profile. Profiling
overhead is included; no threshold or file-integrity checks are disabled.
Generation can be separated from measurement because this machine exhibited
metadata-only changes on newly created fixtures. Any integrity failure is
reported rather than retried or omitted inside the benchmark.

## Logged-in provider search verification

All 25 browser destinations remain available; the pilot does not add provider
network requests. Two are public searches, two are observed sign-in-required
search links, and 21 remain copy/open fallbacks.

To improve a fallback, use an authorized normal browser session and a synthetic
query. Observe the actual search form, reopen its resulting URL, and verify
query/version retention through login. Never record passwords, cookies, tokens,
account IDs or personal queries. Do not bypass eligibility screens or disabled
controls. Only a verified stable, credential-free URL can become a template.

No signed-in provider sessions were supplied for this work, so Heavy Hits/XMiX
post-login behavior and the remaining fallback upgrades are **unverified**, not
claimed complete. See [provider evidence](PROVIDER-SEARCH-LINKS.md).
