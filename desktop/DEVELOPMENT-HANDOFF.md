# BOOTH BUDDY development handoff — 2026-11-17

## Resume on the next computer

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

Export to Serato saves a new verified `.crate`, preserves exact memberships, uses private atomic no-overwrite publication and blocks recognized Subcrates destinations while Serato is open. Suggested names preserve Unicode within byte limits. Existing audio, tags, cues and Serato database files are not edited by the product.

See `SERATO-DESTINATION-VERIFICATION-2026-11-17.md` for the latest native checks. Earlier full macOS UI suite: 25 passing tests; latest focused export test: 1 passing test. Latest native/preflight/Serato/filesystem unit suite: 47 passing tests; Node: 100 passing tests. Release/test builds and Xcode analysis passed. Re-run the full suite on the next computer.

Next: confirm Serato visible row order and playback of every exported acceptance track, validate actual external drives and other target versions, then complete clean-machine installation/upgrade/recovery acceptance. Signing/notarization, full dependency-rights clearance and commercial policies remain gates in `COMMERCIAL-READINESS.md`. Do not claim paid-distribution clearance.

Before the migration commit, broader synthetic checks also passed: 331 backend/catalog/scan/planner/inventory unittest tests (69.138 seconds); 517 engine unittest tests (33.455 seconds); and 239 pytest tests plus 105 subtests (3.03 seconds). The initial engine attempt in the packaging environment failed for missing test dependencies; the successful retry used the existing development environment. Logs are local under `/tmp/booth-serato-acceptance/` (`backend-handoff.log`, `engine-handoff-retry.log`, `pytest-handoff.log`).

Historical reports saying nothing was committed/pushed describe their original checkpoints. The user subsequently authorized committing and pushing this handoff; the final Git commit and remote branch are the transfer record.
