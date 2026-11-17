# Serato export follow-up

## Changes

- `StudioApp.swift`: the save panel suggests the verified crate's name; Unicode is retained, path separators and controls are removed, blank names fall back safely, and the filename stem is bounded to 100 characters. Completion instructions distinguish direct `Subcrates` saves from portable exports and remind users to keep the referenced music available.
- `AutoCueStudioUITests/AutoCueStudioUITests.swift`: checks the named output and portable-export instructions while retaining byte, audio preservation, permissions and no-overwrite assertions.
- `test_native_workflow.py`: added production-Swift filename safety cases and a 10,000-entry simulated external-volume playlist with a repeated membership.
- `SERATO-EXPORT.md`: updated workflow instructions.
- This report records verification and unresolved blockers.

Existing worktree changes were preserved. No backend APIs, library operations, data contracts or planner behavior were changed. No commits or pushes occurred.

## Verification

Evidence root: `/tmp/booth-serato-acceptance/`.

- Unit tests: **41 passed**, 7.489 seconds (`unit-followup.log`).
- Synthetic Node tests: **100 passed, 0 failed** (`node-followup.log`).
- Native build for testing: **TEST BUILD SUCCEEDED**; Xcode analysis: **ANALYZE SUCCEEDED** (`build-followup.log`).
- Release build: **BUILD SUCCEEDED**, exit 0 (`release-followup.log`). Development artifact preflight: **passed**, exit 0 (`preflight-followup.json`).
- Updated macOS UI test: **did not execute**. Runner initialization failed with `com.apple.LocalAuthentication Code=-2`, “Authentication canceled. Canceled by user.” (`ui-followup.log`, `UI-followup.xcresult`). Authentication was not bypassed or repeatedly prompted. The prior full 25-test pass belongs to the prior implementation; it is not claimed as validation of this change.
- `git diff --check`: exit 0.

## Visual and consumer acceptance

Serato's current screenshot still shows the BOOTH BUDDY acceptance crate in the sidebar. Its accessibility surface only exposes a dialog and menu, and a screenshot-directed click returned `noWindowsAvailable`. Thus visible membership order and playback of each exported track remain blocked. No additional library modification was needed in this follow-up. No new app screenshot was available because the updated UI test could not initialize.

The 10,000-entry case validates encoding and ordering with simulated `/Volumes/` paths; it does not establish actual external-drive mounting, Serato import performance or playback compatibility. Existing checker coverage includes missing/changed audio, duplicates, Unicode, path-root escape and malformed inputs. Physical external-drive acceptance and additional software versions remain pending.

Signing/notarization, dependency rights, clean-machine installation and commercial operations remain the release gates documented in `COMMERCIAL-READINESS.md`.
