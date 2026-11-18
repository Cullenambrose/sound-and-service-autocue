# Client-request intake checkpoint

## Delivered and files

- `StudioApp.swift`: bounded Foundation-only title/CSV/TSV parser, editable native preview, read-only file chooser, explicit append/discard and preservation of existing event fields. Matching continues through the existing backend planner; no new matcher, endpoint or media writer was added.
- `test_native_workflow.py`: production Swift intake coverage against independent CSV parsing; Unicode/BOM/CRLF/quotes/newlines, duplicate ordering, invalid shapes/categories/control characters/limits and event preservation. Nine added tests.
- `AutoCueStudioUITests/AutoCueStudioUITests.swift`: CSV/category preservation, unknown-column refusal, discard without append, editable artist, shared event draft and imported request reaching real backend planning with synthetic audio.
- `CLIENT-REQUEST-INTAKE.md`: supported formats, limits, draft lifecycle and matching boundary.
- `DEVELOPMENT-HANDOFF.md`: current checkpoint and validation totals.
- This verification record.

Requests remain draft data until explicitly added; adding them does not approve a crate or establish a match. Unapplied previews are temporary view state. The existing event/policy invalidation behavior requires generating again after draft changes. Backend contracts, planner ranking and library operations are preserved. No installed Serato library or actual DJ music was accessed or changed in this checkpoint. Original teal/green branding is retained; no Metal or competitor assets were introduced.

## Exact results

Artifacts: `/tmp/booth-serato-acceptance/` on the development machine.

| Check | Result | Evidence |
| --- | --- | --- |
| Native/paths/filesystem/preflight/Serato unit suite | 61 passed, 6.813 seconds | `unit-request-intake-retry.log` |
| Synthetic Node | 100 passed, 0 failed | `node-request-intake.log` |
| Xcode test build | TEST BUILD SUCCEEDED | `build-request-intake.log` |
| Static analysis | ANALYZE SUCCEEDED | `build-request-intake.log` |
| Native UI selected tests | 3 passed, 0 failures, 146.803 seconds; TEST EXECUTE SUCCEEDED | `ui-request-intake.log`, `UI-request-intake.xcresult` |
| Release build | BUILD SUCCEEDED | `release-request-intake.log` |
| Development artifact preflight | passed, exit 0 | `preflight-request-intake.json` |
| Whitespace check | git diff --check, exit 0 | local check |

The selected UI tests were existing event/phase preservation (49.044 seconds), CSV preview/category preservation (39.766 seconds), and reviewed intake reaching planning (57.993 seconds). Existing synthetic audio bytes were checked unchanged. This is changed-flow coverage, not a fresh full native suite run. The first unit attempt could not pass an embedded null byte as a process argument; the test harness was corrected to transport the source as JSON text and the parser then refused that input as intended. All 61 tests passed on the retry.

Xcode reported the existing AppIntents metadata skip and test-launch debugger lookup warnings; tests executed successfully. No system security setting was changed.

## Visual findings

Reviewed `request-intake-screenshots/A120736F-D617-4517-839F-A6B24B343FCE.png`: native source editor, format/default-category selectors, preview controls and three-request count are readable, wrapped and spaced consistently in teal/green. The editor and controls are inside the workspace scroll view; per-row editors continue below this screenshot and were exercised by the UI tests.

Reviewed `request-intake-screenshots/D584CF31-565C-434A-A247-9A8ACD74D21A.png`: after applying the reviewed import and navigating back, the planner generates a feasible alternative. Header clipping in these captures is the current scroll position, not overlapping controls. These screenshots do not establish a full VoiceOver/contrast/platform audit.

## Remaining work

Arbitrary vendor-column mapping, OCR, durable event/booking storage, a dedicated ambiguity-resolution workspace and connected-provider acquisition remain outside this intake slice. Exact-version/missing/conflict handling still belongs to the existing planner resolver. Imported text must not be described as automatically matched or fulfilled.

Serato visible row-order/playback acceptance remains blocked by the previously observed control-service window error. Physical external-drive/version coverage, clean-machine upgrade/recovery, production signing/notarization, dependency-rights clearance and commercial policies remain open. Destination-laptop testing must use the isolated account/test-library process in `DEVELOPMENT-HANDOFF.md`; authorization for disposable fixtures does not include its real working DJ library.
