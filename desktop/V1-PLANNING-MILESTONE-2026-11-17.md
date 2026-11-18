# Native planning and pre-gig milestone

## Delivered

The native planner now has searchable exact-file requirements, exclusions and pins. It displays the exact indexed file/path, uses positions starting at 1 in the UI and converts to the backend's unchanged zero-based pin contract. Conflicting exclusions/requirements and occupied positions are refused rather than silently removing a constraint. Existing advanced fields and phase requirements are retained. Global constraints can be explicitly cleared per file. Search shows at most 100 files and tells the user to narrow broader results; required files must still fit the planner's candidate scope.

Existing phase budgets are editable and ordered phases can move both up and down. Backend validation remains authoritative for feasibility, timing and unsupported fields. Editing inputs invalidates previously generated alternatives through the existing native draft lifecycle.

Each crate export now includes pre-gig checks: current revision, verification status/time, blocked memberships and pending-operation warnings. Explicit DJ acknowledgements cover event requests and external drive/order/playback review. They are local to the current view, reset on verification or crate/revision changes, and do not certify an event or playback. Saving continues to revalidate source bytes through the existing backend. Export history remains labeled as historical evidence, not current availability.

## Scope and files

- `StudioApp.swift`: pure policy draft adapter, visual constraints, editable phase budgets/reordering and pre-gig panel.
- `AutoCueStudioUITests/AutoCueStudioUITests.swift`: real synthetic scan/plan test for visual constraints; expanded phase and export/checklist assertions.
- `test_native_workflow.py`: production Swift policy edits, conflict handling, exact pin positions, preservation and malformed-input coverage.
- `DEVELOPMENT-HANDOFF.md`: transfer instructions protecting the destination laptop's production library and details of the separate local audio archive.
- This checkpoint.

Backend APIs, planner ranking, library operations and data contracts are unchanged. Teal/green branding is retained. No Metal or competitor assets were added.

## Verification

Evidence is local under `/tmp/booth-serato-acceptance/` and will not be transferred by Git.

- Unit suite: **52 passed**, 14.477 seconds (`unit-v1-planning.log`).
- Synthetic Node: **100 passed, 0 failed** (`node-v1-planning.log`).
- Xcode: **TEST BUILD SUCCEEDED**, **ANALYZE SUCCEEDED** (`build-v1-planning.log`); updated UI tests also built successfully (`build-v1-tests-final.log`).
- Release: **BUILD SUCCEEDED** (`release-v1-planning.log`); development artifact preflight **passed**, exit 0 (`preflight-v1-planning.json`).
- First focused UI run: phase editing **passed**, 46.353 seconds; visual exact-file constraints reaching backend planning **passed**, 60.049 seconds. Export test failed on its checkbox accessibility-value cast before completing the test (`ui-v1-planning.log`, `UI-v1-planning.xcresult`). The assertion was corrected to reveal the control and accept numeric or string accessibility values. Subsequent results are recorded below.
- Corrected export/checklist test: **1 passed, 0 failures**, 58.444 seconds; **TEST EXECUTE SUCCEEDED** (`ui-v1-export-retry.log`, `UI-v1-export-retry.xcresult`). The corrected tests built successfully (`build-v1-retry.log`). All three changed flows have passing coverage across these runs; a new full-suite pass is not claimed.
- `git diff --check`: exit 0.

Reviewed app screenshots: `v1-planning-screenshots/75463CAF-57E9-45F6-A286-363306DCAF37.png` shows the planner navigation, collapsed constraint entry point and feasible result; `v1-export-screenshots/7AE76A08-BE90-4216-BF0E-E63C456C2A70.png` shows manual review checkboxes, export controls and the deliberate no-overwrite refusal. Teal branding is consistent, explanatory text wraps and controls do not overlap in these states. The export screenshot is scrolled within the crate view, so the pre-gig heading is above the visible area. Expanded controls are exercised by the tests; these images are not a full accessibility audit.

## Acceptance and transfer limits

Serato acceptance was retried. Its screenshot still shows the test crate, but pointer input again returned `noWindowsAvailable`; only a custom dialog/menu is exposed to accessibility. Visible row order and playback of every exported track remain blocked. No alternate UI mechanism bypassed this limitation, and no installed Serato library files were changed in this milestone.

The companion archive `/tmp/BOOTH-BUDDY-machine-transfer/BOOTH-BUDDY-test-audio.zip` was created with mode 0600, ZIP integrity checked and all three member hashes checked. It holds only authorized disposable audio copies, README and manifest. It is not in Git. Transfer separately into a dedicated macOS test account; do not merge into the DJ laptop's working library. Old absolute-path crate exports are deliberately excluded; rescan and regenerate on the new machine.

This milestone advances roadmap priorities 2 and 3; it does not complete Serato acceptance, automatic event-obligation reconciliation, full musical acceptance or commercial-distribution clearance. Physical external drives, clean-machine/version/upgrade/recovery testing, signing/notarization and dependency-rights clearance remain required. Earlier full 25-test UI results are historical; this checkpoint runs focused changed-flow coverage.
