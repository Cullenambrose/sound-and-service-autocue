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
session compatibility, creates the output directory/binding when new, then exits
without HTTP. Omit `--check` to start at `http://127.0.0.1:8769/`; choose `--port`
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

There is no automatic update, schema migration or rollback manager yet. Main-app
production integration, live-file DJ-software compatibility and unattended
deployment remain release gates.

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
