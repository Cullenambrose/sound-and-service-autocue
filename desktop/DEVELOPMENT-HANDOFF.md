# BOOTH BUDDY development handoff — 2026-11-17

## Resume on the next computer

**Destination is the DJ laptop with a real working Serato library.** Use a separate macOS test account before any Serato acceptance work. Do not copy, merge, replace or export into that laptop's production `_Serato_` folder. Prior authorization to modify disposable test songs/library does not authorize changes to the real DJ library. Synthetic verification does not need that library or any song transfer.

The local companion archive `/tmp/BOOTH-BUDDY-machine-transfer/BOOTH-BUDDY-test-audio.zip` contains three authorized disposable audio copies, a hash manifest and instructions. It is not in Git. Transfer it separately through a trusted channel before leaving this machine; temporary storage is not a durable backup. Size: 28,504,702 bytes. SHA-256: `893577b39776de48c79d63b67d8abdea923de4e6a47ef573f5b81b5365cbc803`. The archive's ZIP integrity and each member hash were checked. It contains no Serato database, crate file, app database or credentials. After extraction in the test account, scan only its dedicated test-audio folder into a fresh BOOTH BUDDY workspace and regenerate exports for the new paths.

For testing on the destination account, use `verify_native.sh` first: it creates temporary databases and synthetic audio. A normal Xcode Run uses the default app workspace for that macOS account; do not use it in the production account for Serato acceptance. The Serato export panel can suggest `~/Music/_Serato_/Subcrates`; only use that destination inside the isolated test account. Save portable test exports to a separate staging folder otherwise. Keep real music outside all test roots.

The development branch is `feat/prep-live-flow` in `Cullenambrose/sound-and-service-autocue`. Use a native Apple Silicon Mac with full Xcode selected, Node and native arm64 Python 3.10+. The current build constraints are a tested dependency set, not a reproducible-build lock or supported-platform matrix.

From a fresh checkout of this branch:

```sh
python3 -m venv .venv
PIP_CONSTRAINT="$PWD/desktop/build-constraints.txt" bash scripts/prepare_macos_xcode.sh
open desktop/AutoCueStudio.xcodeproj
bash desktop/verify_native.sh
```

Select the `AutoCueStudio` scheme and My Mac destination. The prepare script installs into `~/autocue-mac-build/build-env` by default. `AUTOCUE_MAC_BUILD_ROOT` can change that location; when doing so, set `AUTOCUE_BUILD_PYTHON` to its `build-env/bin/python` for verification. Run native UI tests from an unlocked visible macOS session, accept the normal Xcode automation authorization yourself when requested, and avoid interacting with other apps during the suite. Do not disable macOS security settings to make tests pass.

## What the repository carries

Native SwiftUI shell, local Python service source, Xcode project/scheme, original teal/green icon source and asset, native workflow and filesystem tests, synthetic backend/Node tests, release preflight, build constraints, CI configuration and roadmap/acceptance notes. The migration checkpoint includes preexisting uncommitted project changes as well as the native/Serato continuation, so those edits are not stranded on the old computer.

Generated apps, DerivedData, virtual environments, runtime databases, credentials, model caches and personal music are local data. They are not transferred by Git. Historical `/tmp/booth-*` paths in verification reports refer to evidence on the old computer and will not exist after cloning. Re-run verification to create fresh evidence on the new machine; do not treat old test counts as a clean-machine pass.

For existing application data, use the app's explicit database backup/restore workflow and transfer the private backup separately through a trusted channel. Backups do not include audio, models, export artifacts or native recovery journals. Preserve original music locations or rescan and revalidate before export; absolute playlist/crate paths from the old computer are not automatically portable. The authorized Serato acceptance library and temporary fixture copies are not required for synthetic tests and are not included here.

## Current behavior and next work

Current scan-history checkpoint: `NATIVE-SCAN-HISTORY-VERIFICATION-2026-11-23.md` (finalized November 24). All returned queued/running/cancelling jobs now stay visible above inactive history; the initial five inactive rows can expand/collapse up to the service’s existing 20 recent inactive jobs. Backend APIs, polling, cancellation and media safety are unchanged. Verification: **70 native/path/save/preflight/Serato units, 36 backend scan regressions and 100 synthetic Node tests passed**. Four selected native UI scenarios passed across the retry and corrected run, including the new six-scan expand/collapse/source-preservation test (33.163 seconds). The first runner attempt timed out before executing tests; the retry found a label-versus-value assertion issue in the new test, corrected with exported accessibility evidence. Test/Release builds, Xcode analysis and development preflight passed. Expanded dark history, dark indexed evidence and light keyboard-focus screenshots were reviewed. The previous full 31-test UI pass below is historical; re-run the full suite for release and on the destination machine.

Previous library checkpoint: `NATIVE-LIBRARY-VERIFICATION-2026-11-22.md` and `NATIVE-LIBRARY-INDEX.md`. Native Library detail now exposes indexed folders, missing title/artist records, metadata/errors/same-hash evidence, explicit refresh and offline-folder warnings without discarding indexed records. Filters cover only the existing inbox’s maximum 100 prioritized loaded records. Visible queued/running scan jobs support explicit cancellation through the existing API. No backend contracts or source-media writers changed. At that checkpoint, verification: **68 native/path/save/preflight/Serato units, 91 backend regressions, 100 synthetic Node tests and the full 31-test native macOS UI suite all passed**; UI duration 929.730 seconds, zero failures. Test/Release builds, Xcode analysis and development preflight passed. Dark index, light keyboard-search and resized overview screenshots were inspected. Physical-drive, larger-library, destination-machine and commercial gates remain open.

Previous transition checkpoint: `NATIVE-TRANSITION-VERIFICATION-2026-11-18.md` (finalized November 22) and `NATIVE-TRANSITION-REVIEW.md`. Native alternatives now show backend transition evidence and support exact-hash-verified, memory-only individual-file audition up to 64 MiB, with a single player and alternative-specific controls. At that checkpoint, checks passed: 64 native/path/save/preflight/Serato units, 5 transport tests, 4 build diagnostics/inventory tests, 100 Node tests, 65 backend/transition tests and 3 selected native UI tests. Serial test/Release builds, analysis and development preflight passed. Run packaging sequentially when sharing a build environment; each freeze now isolates its PyInstaller cache and prints captured failure diagnostics. This is not mixed-timing editing, synchronized pair playback or musical certification. The existing Serato and commercial release gates remain open.

Earlier intake checkpoint: `CLIENT-REQUEST-VERIFICATION-2026-11-17.md` and `CLIENT-REQUEST-INTAKE.md`. Native title/CSV/TSV request intake now supports editable previews and explicit append/discard while preserving event context. The existing planner resolves the added requests; the importer does not infer matches. At that checkpoint, unit suite: 61 passed; Node: 100 passed; selected native UI tests: 3 passed. Test/Release builds, analysis and development preflight passed. The current full-suite result is above; destination-machine acceptance must still be rerun before release claims.

Earlier checkpoint: `V1-PLANNING-MILESTONE-2026-11-17.md`. Native exact-file requirements/exclusions/pins, phase budget/reorder editing and the crate pre-gig panel are implemented. At that checkpoint, unit suite: 52 passing tests; Node: 100 passing tests. Three changed UI flows passed across the focused run and corrected export retry. Test/Release builds, static analysis and development preflight passed. The pre-gig panel separates backend file verification from manual DJ acknowledgements; it does not automatically certify event obligations or playback. Serato row-order/playback acceptance remains blocked by the control-service window error.

Export to Serato saves a new verified `.crate`, preserves exact memberships, uses private atomic no-overwrite publication and blocks recognized Subcrates destinations while Serato is open. Suggested names preserve Unicode within byte limits. Existing audio, tags, cues and Serato database files are not edited by the product.

See `SERATO-DESTINATION-VERIFICATION-2026-11-17.md` for the earlier export checkpoint. Earlier full macOS UI suite: 25 passing tests; focused export test at that checkpoint: 1 passing test. Native/preflight/Serato/filesystem unit suite at that checkpoint: 47 passing tests; Node: 100 passing tests. Release/test builds and Xcode analysis passed. Re-run the full suite on the next computer.

Next: confirm Serato visible row order and playback of every exported acceptance track, validate actual external drives and other target versions, then complete clean-machine installation/upgrade/recovery acceptance. Signing/notarization, full dependency-rights clearance and commercial policies remain gates in `COMMERCIAL-READINESS.md`. Do not claim paid-distribution clearance.

Before the migration commit, broader synthetic checks also passed: 331 backend/catalog/scan/planner/inventory unittest tests (69.138 seconds); 517 engine unittest tests (33.455 seconds); and 239 pytest tests plus 105 subtests (3.03 seconds). The initial engine attempt in the packaging environment failed for missing test dependencies; the successful retry used the existing development environment. Logs are local under `/tmp/booth-serato-acceptance/` (`backend-handoff.log`, `engine-handoff-retry.log`, `pytest-handoff.log`).

Historical reports saying nothing was committed/pushed describe their original checkpoints. The user subsequently authorized committing and pushing this handoff; the final Git commit and remote branch are the transfer record.
