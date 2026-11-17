# Serato destination reliability

## Change and files

`StudioApp.swift` now uses the same case-insensitive `Subcrates` destination check for the running-Serato guard and completion instructions. Previously, a lowercase folder spelling could bypass that guard on a case-insensitive volume. Canonical parent resolution remains in place. The check is intentionally conservative on case-sensitive volumes.

`test_native_workflow.py` verifies mixed-case destination names and consistent completion instructions. New `test_native_saves.py` compiles the exact production private-file writer and tests real temporary filesystem behavior: mode 0600, exact bytes, refusal to replace existing files or dangling symlinks, cleanup after publication failure, and eight concurrent creators producing exactly one complete winning file. No duplicate implementation of the writer is used. Existing native test discovery in `verify_native.sh` includes this new file automatically.

`SERATO-EXPORT.md` documents the behavior. This report records the results. Preexisting worktree changes were preserved. Backend APIs, planner behavior, library operations and data contracts were untouched. No source audio or installed Serato library was modified. Nothing was staged, committed or pushed.

## Verification

Artifacts: `/tmp/booth-serato-acceptance/`.

- Unit suite: **47 tests passed**, 13.194 seconds (`unit-destinations.log`).
- Synthetic Node: **100 passed, 0 failed** (`node-destinations.log`).
- Xcode test build: **TEST BUILD SUCCEEDED**; analysis: **ANALYZE SUCCEEDED** (`build-destinations.log`).
- Release build: **BUILD SUCCEEDED**; development preflight: **passed**, exit 0 (`release-destinations.log`, `preflight-destinations.json`).
- Native Serato export UI test: **1 passed, 0 failures**, 50.018 seconds; **TEST EXECUTE SUCCEEDED** (`ui-destinations.log`, `UI-destinations.xcresult`).
- `git diff --check`: exit 0.

Reviewed screenshot: `/tmp/booth-serato-acceptance/destination-screenshots/462CD838-4ACC-4B31-ADA9-D5FD41FE37B3.png`. Teal branding, wrapped copy, visible export controls and readable existing-file refusal are retained without overlapping controls. This is the deliberate refusal state after a successful initial export; it is not a playback screenshot.

## Remaining limits

These tests establish filesystem publication behavior on this Mac and case-insensitive destination classification. Physical external-drive behavior and additional filesystem types still require acceptance. The running-process guard reduces the risk of writing while Serato is open but cannot eliminate a separate application's launch racing the final file publication. No process termination is performed by the product.

Visible Serato row order and exported-track playback remain pending due to the previously observed Serato computer-control window error. Signing/notarization, clean-machine/version acceptance, dependency-rights clearance and commercial operating decisions remain open gates. Prior full-suite results remain historical evidence and are not counted as a fresh full-suite run for this change.
