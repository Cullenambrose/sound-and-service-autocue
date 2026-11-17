# BOOTH BUDDY commercial setup verification — November 16, 2026

The dirty worktree was inspected first. Preexisting user/staged changes were retained; no reset, staging, commit, push or publication was performed. All exercised libraries and databases are generated temporary fixtures. No personal music, Serato database, provider account or model download was used.

## Changed files in this continuation

- `StudioApp.swift`: native current-crate preview/approval/undo/recovery, backup/restore/workspace switching, event/phase forms, redacted support and Help integration, canonical paths, private atomic saves, isolated backend launch environment, original branding colors and appearance coverage.
- `AutoCueStudioUITests/AutoCueStudioUITests.swift`: new workflow coverage, temporary workspace placement, canonical assertions, viewport-aware reveal, startup diagnostics and robust resize setup.
- `AutoCueStudioUITests/UITests.entitlements` and `AutoCueStudio.xcodeproj/project.pbxproj`: explicit development UI-runner sandbox configuration; production app permissions are not changed by these test-target settings.
- `Info.plist`, `Branding/BoothBuddy.icns`, `Branding/generate_icon.swift`, `Branding/README.md`: original BOOTH BUDDY icon, generated with AppKit.
- `../scripts/build_macos_app.py`: one additional icon-copy operation in the common packaging function; all existing dirty build-script changes preserved.
- `build-constraints.txt`, `verify_native.sh`, `../.github/workflows/macos-native.yml`: tested-version constraints, synthetic verification entry point and prepared arm64 macOS CI. Hosted CI has not run or been pushed.
- `release_preflight.py`, `test_release_preflight.py`, `test_native_paths.py`: read-only artifact gates and independent tests of release rejection/path identity.
- `COMMERCIAL-READINESS.md`, `SERATO-ACCEPTANCE.md`, this report, plus appended `NATIVE-WORKFLOW.md` and `ROADMAP-AUDIT-2026-11-15.md`: scope, acceptance and evidence.

No backend API, set-planner ranking/behavior, library-operation contract or source-media writer is changed. The native journal gains an optional pending-edit field, preserving older journals.

## Final results

Release artifact content fingerprint: `885a211975098208fb8f5dff63c22fd9e35a659c5393807f705acac618105b92`.

- Release build: **BUILD SUCCEEDED**, exit 0 — `/tmp/booth-commercial/release-final.log`.
- Release static analysis: **ANALYZE SUCCEEDED**, exit 0 — `/tmp/booth-commercial/analyze-final.log`.
- Native Foundation ZIP/path and release-preflight unit tests: **22 passed**, 0 failures — `/tmp/booth-commercial/native-final-tests.log` (10 ZIP, 4 path, 8 preflight).
- Backend/planner/backup/release-inventory regressions: **149 passed**, 0 failures — `/tmp/booth-commercial/backend-final.log`.
- Synthetic Node: **100 passed**, 0 failures — `/tmp/booth-commercial/node.log`.
- Release frozen-backend packaging smoke: **PASS**, exit 0 — `/tmp/booth-commercial/release-package-smoke.log`; covers bundled assets, settings, create/edit/replay/restart/undo, planning/transitions, backup/restore and reviewed history.
- Development artifact preflight: **PASS**, exit 0 — `/tmp/booth-commercial/release-preflight.json`.
- Distribution artifact preflight: **FAIL by design**, exit 1 — `/tmp/booth-commercial/distribution-preflight.json`: development bundle identity, missing Developer ID/hardened runtime, missing stapled ticket, Gatekeeper rejection and uncleared dependency rights. Icon, exact inventory/notices hashes and ad-hoc signature verification pass. This is not a distribution-cleared app.
- Shell syntax, scoped whitespace and workflow YAML syntax: **PASS**. Hosted workflow execution is unverified. Verification phases were run individually; the revised all-in-one script was syntax-checked, not rerun end to end.
- Final full macOS UI suite: **24 passed, 0 failures**, exit 0; **TEST EXECUTE SUCCEEDED** — `/tmp/booth-commercial/UI-complete.xcresult` and `ui-complete.log`. The test suite took 785.171 seconds; Xcode reported 804.890 seconds for the operation.

## Failures investigated

Earlier attempts remain in `/tmp/booth-commercial/` and are not counted as passes. They include a restore-dialog selection mistake; runners killed by macOS `CacheDeleteAppContainerCaches` (confirmed in narrowly filtered system logs); runner permission failures after moving fixtures outside its container; the native `/private/tmp` canonical-path defect; test-only shortened-path expectations; and SwiftUI reporting a clipped button as hittable. Each was addressed in client/test setup while retaining strict backend validation. Some build-and-launch UI attempts also timed out enabling automation before any test ran. Verification now separates build-for-testing/analysis from serial test-without-building; the script does not suppress failed exit status or silently retry failed tests.

The canonical-path fix was independently exercised with the production Swift helper. Unicode path tests check actual directory identity because macOS may return decomposed Unicode. Backup/restore tests check both the new database and retention of the original workspace/source bytes. Lost edit-response tests verify one revision increment after restart/replay.

## Visual findings

A visible display was available and XCTest captured actual native screenshots. Reviewed light/dark library and restored-crate layouts show intact navigation, wrapping, teal branding and keyboard focus. Fixed teal was replaced with separate light/dark foreground colors; prominent buttons use a darker teal fill with white labels. The original equalizer icon was rendered and inspected. Resize testing handles a window already reopened at minimum size; scroll automation now verifies a control's center is inside the viewport.

Passing screenshots already exported under `/tmp/booth-commercial/verified-screenshots/` and `/tmp/booth-commercial/canonical-screenshots/` include light appearance, restored workspace and resized navigation. Final passing-run screenshots are exported under `/tmp/booth-commercial/final-screenshots/`; selected light, restored-workspace and resized captures are reviewed below.

The final run used serial `test-without-building` against the corrected Debug UI-test build. Build-and-launch automation initialization remains intermittently unreliable on this host; this is documented rather than suppressed.

Final reviewed captures:

- [Light appearance and keyboard focus](/tmp/booth-commercial/final-screenshots/D4D1D14F-8447-4ED1-87ED-39BCBD246131.png)
- [Restored current crate](/tmp/booth-commercial/final-screenshots/ACE3C65E-E376-4E02-97BB-CE50F41F4DFD.png)
- [Resized native navigation](/tmp/booth-commercial/final-screenshots/8B6B8690-10D1-4668-A2F9-9D323B4E3B8E.png)

These captures preserve normal scrollable content below the fold; no overlapping navigation or clipped toolbar labels were observed. The light capture includes an in-progress synthetic search indicator, rather than a fabricated empty/success state.

## Remaining release blockers

No Serato acceptance has been performed; the user is considering preparing an authorized sample library. See `SERATO-ACCEPTANCE.md`. No cue-loop, musical-quality, clean-machine/upgrade/rollback, supported-hardware matrix or broader VoiceOver acceptance is claimed. Developer ID/production identity/notarization, dependency/content rights, public terms/privacy/support/pricing and any chosen paid entitlement/billing flow remain external release gates. There is no automatic updater or commercial entitlement implementation in this setup.

Nothing was committed, pushed or published. No custom Metal or competitor artwork/branding was introduced.
