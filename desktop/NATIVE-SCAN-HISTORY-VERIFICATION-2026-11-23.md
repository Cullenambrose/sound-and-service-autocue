# Native scan history checkpoint

The scan panel previously truncated the service response to five rows. An older queued/running/cancelling job could disappear behind newer inactive jobs even though polling continued. It now keeps every returned active job visible in a separate section, with five inactive rows initially and explicit expand/collapse for the remaining returned inactive history. The UI states the existing service boundary: all active jobs plus up to 20 recent inactive jobs, not complete historical pagination. Backend APIs, request/response contracts, polling and explicit cancellation behavior are unchanged.

## Files changed

- `StudioApp.swift`: Foundation-only scan-history projection, active/inactive sections, snapshot counts and accessible history toggle; existing scan cards extracted without changing cancellation semantics.
- `test_native_workflow.py`: tests against production Swift for older active jobs, all three active states, history expansion/order, inactive failure/cancellation/interruption/unknown states and an empty snapshot.
- `AutoCueStudioUITests/AutoCueStudioUITests.swift`: six sequential scans of a temporary generated WAV, five/six/five history rows, toggle labels, screenshot and source-byte preservation.
- `NATIVE-LIBRARY-INDEX.md`: current scan-history scope.
- `DEVELOPMENT-HANDOFF.md`: latest checkpoint.
- This record.

## Verification

Evidence is local under `/tmp/booth-serato-acceptance/` and is not included in Git. Generated test audio/databases use temporary roots; no real Serato library or source media was modified.

- Native/path/save/preflight/Serato unit suite: **70 passed**, 7.889 seconds (`unit-scan-history.log`).
- Existing backend scan regressions: **36 passed**, 0.516 seconds (`backend-scan-history.log`).
- Synthetic Node suite: **100 passed, 0 failed**, 194.634 ms (`node-scan-history.log`). The first Node command used an incorrect relative glob and did not run; the corrected command above passed.

- Final test build: **TEST BUILD SUCCEEDED**; static analysis: **ANALYZE SUCCEEDED** (`build-scan-history-final.log`).
- Release build: **BUILD SUCCEEDED** (`release-scan-history.log`). Both builds reported only the expected AppIntents metadata extraction skip because there is no AppIntents dependency.
- Development artifact preflight: **passed**, exit 0 (`preflight-scan-history.json`); SHA-256 `ec8f8169b44c6d0afb56dac29c9aa85e9966e8f213f86d4b18ef672e3eb7db94`.
- Initial UI attempt: **TEST EXECUTE FAILED**, exit 65, zero test cases executed: runner timed out enabling automation mode (`ui-scan-history.log`, `UI-scan-history.xcresult`). Diagnostic inspection found an on-console logged-in session, the intended unsandboxed test-runner entitlements, and no surviving app/test runner. No security settings were changed; a new run initialized successfully.

- UI retry: **4 tests executed, 1 failed**, 101.174 seconds (`ui-scan-history-retry.log`). Indexed-folder/offline evidence (39.739 seconds), light appearance/keyboard navigation (9.502 seconds) and resizing (8.336 seconds) passed. The new scan-history test failed its first completed-count predicate because macOS exposes this SwiftUI static text as an accessibility **value**, not a label. Exported element diagnostics confirmed the scan completed and the summary was correct. The test now accepts label or value and verifies the exact temporary folder path before scanning.
- The corrected test build and analysis also passed (`build-scan-history-test-fix.log`). App source and Release artifact are unchanged by this test-only correction.

- Corrected scan-history scenario: **1 executed, 0 failures**, 33.163 seconds; **TEST EXECUTE SUCCEEDED**, exit 0 (`ui-scan-history-final.log`, `UI-scan-history-final.xcresult`), completed November 24. All four selected scenarios have passed across the retry and corrected run. The previous checkpoint’s full 31-test suite remains historical; the full suite was not rerun for this change.
- `git diff --check` passed.

## Visual findings

Passing scan-history attachment `/tmp/booth-serato-acceptance/scan-history-final-screenshots/5601D6F9-9151-4772-B0A9-FF67CD365A7B.png` was inspected: six completed scan rows, summary and scope explanation have consistent spacing, no row overlap and preserved teal/green branding. Lower content extends below the scroll viewport; the UI test successfully reached and used the collapse control.

The retry’s dark indexed-file evidence (`scan-history-retry-screenshots/7080B202-1CA6-4D1E-BADA-6233CD8A70A5.png`) and light keyboard-focus view (`scan-history-retry-screenshots/07DD87A9-16DE-46BE-8DC3-A1C2A546F6B2.png`) were also inspected. Offline warnings and keyboard focus are readable. A system screen-sharing banner overlays part of those two captures; it is outside app layout. No app spacing/color defect requiring another source change was found. Resizing passed its existing UI scenario. This is not comprehensive VoiceOver, all-size or measured-contrast certification.

## Remaining limits

No complete scan-history pagination, inbox pagination, native review-state mutations, tag repair, folder reassociation or duplicate keeper workflow is added. The scan UI scenario does not deterministically exercise cancellation of a long-running physical drive; selected-job receipt validation and backend cancellation remain covered separately. Actual external drives, Serato ordered-row/playback acceptance, clean-machine upgrade/recovery, broader accessibility/performance, signing/notarization, dependency-rights clearance and commercial operations remain release gates. This checkpoint is not commercial-release clearance.

Use the separate macOS test account on the DJ laptop. Never merge the disposable test library with its working Serato library.
