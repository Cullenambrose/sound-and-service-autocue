# BOOTH BUDDY → Serato local-library acceptance

Status: **partially exercised in Serato; BOOTH BUDDY playlist import remains pending**. Prepared for an owner-authorized sample library. Synthetic ZIP/backend/UI passes do not replace this acceptance. Do not mark cases passed until observed in the named Serato version.

The official [download page](https://serato.com/dj/pro/downloads) and [OS compatibility matrix](https://support.serato.com/hc/en-us/articles/204865694-Serato-DJ-Pro-Lite-operating-system-compatibility) are the source for installation compatibility. Serato's [5.0 release information](https://the-drop.serato.com/announcements/lucid-beatgrids-just-dropped-in-the-serato-dj-5-0-beta/) describes restored M3U/M3U8 playlist import; record the actual installed version rather than assuming identical behavior across versions.

## Sample library

Use a separate folder with 10–20 locally available files you own or can use, and a backup of the sample. Streaming-only catalog items cannot exercise BOOTH BUDDY's exact-owned-file workflow. Useful cases include MP3/WAV, Unicode/spaces in names, same title/different artist or version, and clean/explicit metadata. Make two or three Serato crates with deliberate orders and some shared tracks across crates. No modification of a main DJ library is needed for these tests.

## Evidence header

Record BOOTH BUDDY version/build and release-preflight artifact fingerprint; macOS/hardware; Serato version; sample location and permissions; file formats/count; target crate names; expected ordered files. Keep this local acceptance evidence private: it can contain music filenames and paths. Record screenshots only with permission and review before sharing.

## Cases

| Case | Action | Evidence / pass condition | Result |
|---|---|---|---|
| Plain local playlist | Scan sample, generate a feasible set, inspect exact files, approve, preview and save verified ZIP. Extract ZIP and import M3U8 via the installed Serato version's supported UI. | Record import method and current sort mode. Every expected file resolves; count and original playlist order agree with the manifest. | Pending |
| Exact versions | Include title collisions, clean/explicit and alternate versions in the sample. | Loaded files match the reviewed source paths/versions, rather than title-only matches. | Pending |
| Unicode and spaces | Export paths containing Unicode and spaces. | All resolve and load; no filename corruption or unintended substitution. | Pending |
| Shared crate membership | Use files shared across several sample crates. | Record how import handles shared membership and existing library tracks; source audio is unchanged. | Pending |
| Playback | Load and audition each accepted playlist entry. | No missing-file or unsupported-format substitution. This checks target-software loading/playback, not musical quality certification. | Pending |
| Changed/offline source | After a successful export, temporarily move one sample file, then request a fresh BOOTH BUDDY export. Restore the sample afterward. | Fresh export blocks unavailable/changed files. Historical recovery remains clearly labeled and does not claim current availability. | Pending |
| Revision and undo | Rename/reorder/remove memberships in BOOTH BUDDY via preview/approval, export that revision, then review/approve undo and export again. | Each exported order matches its approved revision; Serato imports the intended playlist version. | Pending |
| Restart and recovery | Quit/reopen BOOTH BUDDY; restore its database to a new directory and explicitly open it. | Current crate identities/revisions survive; original app workspace and source audio remain intact. | Pending |

Record failures with exact build/version, operation and expected versus observed result. Do not silently sort or repair output to turn a failed case into a pass. A new artifact fingerprint requires reviewing affected evidence.

The original matrix did not authorize external writes. Subsequent explicit user authorization permits disposable-test changes; the native export now permits a new, explicitly selected crate file. Serato database, source-audio, tag, cue and loop writers are not part of this feature. Direct Serato adapters, cue/loop semantics, external reference repair and musical-quality assessment remain separate workstreams. User-created crates are useful authorized fixtures for future read-only adapter evaluation; their existence alone does not implement such an adapter.

## Installed-library baseline

Serato DJ Pro 5.1.0 (build 5.1.0.25) is installed. Read-only inspection found one crate with 94 memberships, 94 available file references. Source paths point into the existing Master Music folder. Only crate/database bytes and filesystem availability metadata were read; audio files were not opened or modified. The aggregate, path-free baseline is recorded in `SERATO-BASELINE.json`. A running Serato session may itself update its metadata, so these hashes are a point-in-time baseline, not proof that later differences were caused by BOOTH BUDDY.

Native UI inspection timed out. Import, playlist-order and playback cases remain pending; no acceptance case has been marked passed.

## Authorized disposable-library exercise

The user explicitly authorized changing the Serato test application, test library and test songs as needed. No deletion was needed. BOOTH BUDDY created three temporary copies (M4A and MP3, including a Unicode filename) under `/private/tmp/booth-serato-acceptance/run-usihauqo/`. The frozen Release backend scanned them, generated a pinned three-track feasible plan, proposed and approved the exact alternative, and published a three-entry verified playlist ZIP. The production Swift ZIP verifier also passed against this real-song export (exit 0). Manifest order and M3U8 order agree; original selected-source hashes were unchanged at export verification. The temporary backend was stopped afterward.

Artifacts:

- [Import-ready M3U8](</private/tmp/booth-serato-acceptance/run-usihauqo/BOOTH BUDDY Serato Acceptance.m3u8>)
- [Verified ZIP](</private/tmp/booth-serato-acceptance/run-usihauqo/BOOTH BUDDY Serato Acceptance.zip>)
- [Fixture report](/private/tmp/booth-serato-acceptance/run-usihauqo/report.json)

The first fixture-harness attempt used the wrong scan-status field (`job_id` instead of `id`); the harness was corrected and the fresh rerun passed. No backend API was changed.

### Serato observation

Using supported keyboard shortcuts in Serato DJ Pro 5.1.0, the test searched for `One More Time`, loaded the Daft Punk track to deck 1, observed the title/artist, 05:21.70 duration and waveform, started playback, and observed the counter advancing with deck/master meters active. Playback was then paused at approximately 00:19.1; the deck remained loaded. Serato's visible play count became 1. This is a basic load/transport check on an existing library track, not acceptance of the newly exported playlist, all formats or musical/audible quality. No cues or loops were edited.

Serato initially exposed only a custom dialog in accessibility inspection. Screenshots and keyboard commands work, while pointer actions return `noWindowsAvailable`. Finder access was not approved. No alternative UI technology was used to bypass these restrictions. Playlist import, imported order/path resolution and playback of the exported three-track fixture remain pending; no full matrix case has been marked passed on that basis.

Changes in this exercise: this acceptance document and `SERATO-BASELINE.json`, plus temporary fixture/export/report files outside the repository. Application/backend source and APIs were unchanged; existing build/test results are historical and were not rerun for documentation-only changes. Nothing was committed or pushed.

## Native crate-export acceptance update

The user requested **Export to Serato** as a native app capability and confirmed the installation and songs are disposable fixtures. A macOS `NSWorkspace` document-open prototype reported `HANDOFF_ACCEPTED com.serato.seratodj`; no resulting imported crate was observed. That route was removed from the app UI rather than presented as a successful import.

The app now implements [native `.crate` export](SERATO-EXPORT.md). In the disposable test installation, the existing repository writer first generated a three-entry acceptance crate from the verified manifest. The installed Serato test process did not complete a graceful quit request. A test-only native application-management harness force-stopped that exact bundle, confirmed zero remaining processes, and a non-overwriting copy added `BOOTH BUDDY Native Acceptance.crate` to its Subcrates folder. The app feature never performs this forced shutdown and refuses library-folder export while Serato is running.

After restart, a Serato screenshot visibly showed the new BOOTH BUDDY crate in the sidebar. The read-only checker then compared the installed file against the manifest: **3 expected / 3 observed memberships, exact sequence, zero missing, changed, unreadable or out-of-scope files**. The production Swift crate encoder generated **byte-identical output** to this installed acceptance file, SHA-256 `207dbc048f365b13cb8390505c730d507cf9ef1a6f1cb9a3d38d814cc2544065`. Evidence is in `/tmp/booth-serato-acceptance/installed-crate-check.json` and `run-usihauqo/native-crate-evidence.json`.

This establishes format output, installed crate presence and file-based membership/hash agreement for these authorized fixtures on Serato DJ Pro 5.1.0. The automated UI did not successfully select the new crate to inspect its three rows; it navigated to the five-track Music playlist instead. UI sort order and playback of all three exported tracks therefore remain pending. The earlier basic load/transport observation used an existing-library track and remains separate evidence.

Changed project files for this continuation: `StudioApp.swift`, `AutoCueStudioUITests/AutoCueStudioUITests.swift`, `test_native_workflow.py`, `verify_native.sh`, new `verify_serato_acceptance.py`, `test_serato_acceptance.py`, `SERATO-EXPORT.md`, and this acceptance document. Preexisting worktree changes are retained; no commit or push is performed.

Current test/build artifacts are under `/tmp/booth-serato-acceptance/`. Earlier commercial-milestone verification counts describe the preceding source snapshot; final continuation results will be recorded separately after the new UI suite and Release build finish.
