# Native Export to Serato

The primary native action is **Export to Serato…**. It exports a new Serato `.crate` file from the currently reviewed, fully verified personal-crate revision. It uses the same download/revalidation and ZIP verification as Save verified ZIP. No backend API, planner or library-operation contract changes are required.

## App flow

1. Review a saved personal crate and select **Preview verified export**.
2. All memberships must be eligible with zero blockers.
3. Select **Export to Serato…** and choose a new `.crate` filename. The suggestion uses the planned crate's name, preserves whole Unicode characters, removes path separators/control characters and bounds the stem to 100 characters and 240 UTF-8 bytes. A blank or oversized single character falls back to BOOTH BUDDY Set.
4. The app revalidates the export against current source bytes, produces an ordered UTF-16BE/TLV crate, and atomically publishes a private mode-0600 file.
5. If saving into a `Subcrates` directory, Serato must be closed. The app checks before revalidation and immediately before writing. It never quits or force-quits Serato.
6. Reopen Serato to load a crate saved into its `_Serato_/Subcrates` directory. Saving elsewhere creates a portable crate file; the completion message explains how to quit Serato, move the new file into that folder and reopen it. Keep the referenced music in its original location. Exporting a crate does not copy its music.

The save panel initially offers the standard `~/Music/_Serato_/Subcrates` directory if it exists. The user selects the destination explicitly. Existing files are refused even if the macOS save panel offers Replace. Atomic hard-link publication refuses a destination created concurrently; no existing crate is overwritten. Files contain membership paths, not audio, cues or tags. The app does not edit Serato's database.

The running-Serato guard and completion instructions recognize `Subcrates` case-insensitively, including destinations reached through canonicalized parent paths. The guard is conservative on case-sensitive volumes. Real temporary-filesystem tests cover concurrent publication, broken destination symlinks, existing files, private permissions and cleanup after failed publication.

The verified ZIP remains available as the general M3U8/CSV/manifest export. A prototype macOS document-open handoff was accepted by Launch Services but did not produce an observable imported crate. That inconclusive route was removed from the product UI; API delivery is not called import success.

## Verification and acceptance scope

Production Swift encoding is checked with the independent existing strict Python Serato parser, including Unicode and repeated memberships. The optional read-only `verify_serato_acceptance.py` engineering tool compares an installed crate with a version-1 export manifest and hashes only expected source paths inside an explicit audio root. Reports contain membership counts, mismatch positions and artifact digests, without paths. It does not infer UI sort mode, playback, cues, loops or musical quality.

```sh
python3 desktop/verify_serato_acceptance.py \
  --crate '/path/to/Serato/Subcrates/Export.crate' \
  --manifest '/path/to/export/manifest.json' \
  --audio-root '/path/to/authorized/test/audio'
```

A matching crate returns 0; malformed inputs, missing/changed bytes, wrong order/count, or a path outside the selected root return 1. Duplicate memberships are preserved and compared in sequence. The tool never writes Serato files or changes source audio.

See [SERATO-ACCEPTANCE.md](SERATO-ACCEPTANCE.md) for actual installed-application evidence. A new crate appearing in Serato and passing the byte checker does not establish all exported-track playback or musical acceptance. External-volume path conventions, other Serato versions and live-library concurrency need separate acceptance before expanding support claims.
