# Native export hardening checkpoint

## Files changed

- `StudioApp.swift`: suggested Serato crate filenames now obey a 240-byte UTF-8 stem budget as well as the existing 100-character bound, without splitting composed Unicode characters. A single oversized grapheme safely uses the fallback name.
- `test_native_workflow.py`: exercises emoji, CJK, family emoji sequences and oversized combining-character input by creating and reading temporary files with the production Swift-generated names.
- `SERATO-EXPORT.md`: records filename limits.
- `COMMERCIAL-READINESS.md`: replaces the stale “unexecuted” Serato acceptance claim with the actual bounded evidence and links newer verification reports.
- This checkpoint.

All preexisting changes were retained. No backend API, data contract, library operation or planner behavior changed. No Metal, competitor branding or assets were introduced. Nothing was staged, committed or pushed.

## Results

Evidence: `/tmp/booth-serato-acceptance/`.

| Check | Result | Log |
| --- | --- | --- |
| Unit tests | 42 passed, 13.806 seconds | `unit-hardening.log` |
| Synthetic Node | 100 passed, 0 failed | `node-hardening.log` |
| Xcode test build | TEST BUILD SUCCEEDED | `build-hardening.log` |
| Xcode diagnostics/static analysis | ANALYZE SUCCEEDED | `build-hardening.log` |
| Release build | BUILD SUCCEEDED, exit 0 | `release-hardening.log` |
| Development artifact preflight | passed, exit 0 | `preflight-hardening.json` |
| Native Serato export UI test | 1 executed, 0 failures, 54.737 seconds; TEST EXECUTE SUCCEEDED | `ui-hardening.log`, `UI-hardening.xcresult` |
| Whitespace | git diff --check, exit 0 | local check |

The AppIntents metadata skip warning remains expected because there is no AppIntents dependency. All test audio/database work uses temporary synthetic data. This follow-up did not modify the installed Serato test library.

The previously blocked XCTest runner successfully initialized on this attempt. The focused test verifies the crate-based filename, portable-export completion instructions, private 0600 permissions, unchanged source audio and refusal to replace an existing crate. XCTest emitted a runner QoS priority-inversion warning; it did not prevent execution or assertions.

Reviewed screenshot: `/tmp/booth-serato-acceptance/hardening-screenshots/457D484E-3312-44AC-833A-CA97E14F9B00.png`. Teal actions and sidebar selection remain consistent, explanatory copy wraps, the no-overwrite error is readable, and the export action is visible without overlap. This screenshot captures the deliberate refusal state after the second export; completion instructions were checked by the test before that state. It does not replace a full accessibility audit.

## Remaining gates

Visible row order and playback of the three exported tracks remain unverified because Serato's screenshot-visible window is not a usable click target through the available control service. Physical external-drive acceptance, other software versions, clean-machine upgrades/recovery, signing/notarization, distribution rights and commercial operations remain open. Large simulated `/Volumes/` fixtures do not establish physical-drive compatibility. The historical full UI suite passed 25 tests; later focused results must be read separately.
