# BOOTH BUDDY native planning and verified-export milestone

Continuation of [the roadmap audit](ROADMAP-AUDIT-2026-11-15.md). The audit remains a historical checkpoint: its native `/plan` payload defect and count-only result limitation are addressed by this milestone. The broader roadmap is not complete.

## Working native journey

1. Choose or enter an absolute music-folder path and explicitly start a read-only scan.
2. Open Set planner. Set target/tolerance minutes, optional candidate search, candidate limit and tagged-clean requirement.
3. Generate and inspect exact ordered tracks, source paths, durations/deltas, score/reasons, phase summaries and search diagnostics.
4. Name the crate and preview an alternative. The client verifies the regenerated proposal has the same library revision, exact ordered tracks and full alternative evidence as the result reviewed.
5. Explicitly approve the proposal. The existing backend validates and creates the static app-owned crate; source audio, tags and external DJ libraries remain unchanged.
6. Reopen the app and find the retained native approval receipt under Crates → Detailed workspace.
7. Preview export. Every membership must be currently verified; stale revisions, changed bytes and blocked entries fail closed.
8. Choose a destination in the native save panel. The backend rechecks the preview, publishes its app-local artifact, and returns the ZIP. The native verifier checks stored-ZIP structure/CRC, exact manifest agreement, dataset, M3U8 order and CSV path order before writing the destination file.
9. Saved export history can recover a byte-identical historical artifact without treating it as proof of current source availability. Extract the ZIP before importing its M3U8 into DJ software.

## Planning scope and diagnostics

The read-only `/api/studio/plan` request no longer sends `dataset_id`. Strict server schemas are unchanged. New typed response models retain alternatives and required diagnostics rather than replacing missing fields with success-shaped defaults.

Candidate scope defaults to at most 1,000 matching files, within the existing search contract. A preflight search refuses a result exceeding the selected limit instead of silently taking the first 100 files. Changing scope or draft inputs invalidates the displayed plan/proposal. A library revision change between preflight and planning also requires regeneration.

Engine candidate/beam limits remain unchanged and visible. Proven `infeasible` and `search_exhausted`/unknown outcomes have different messages. A feasible bounded result does not claim global optimality. This native milestone uses full-file timing; advanced event requests, phase budgets, exact-file pins and exclusions remain editable through the existing JSON contracts. Reviewed-playback optimization is not newly enabled by the native form.

## Approval recovery

`native-workflow-v1.json` lives inside the same canonical app-data directory as the backend. It contains private exact-file paths/hashes, a bounded recent native-receipt list and, when needed, one pending approval. It is not a second crate database: SQLite and server receipts remain authoritative.

Before sending an approval, the client atomically writes and synchronizes its exact request ID, dataset, proposal and request bytes. On a lost/unknown response, this pending request survives quit/relaunch. **Retry exact approval** sends those same bytes using the new session token and checks every receipt identity/count before clearing pending state. It does not generate a replacement approval or automatically execute anything on launch.

A definite HTTP 400/409 rejection can be explicitly discarded. An uncertain network/receipt outcome cannot be discarded through that control; it must be reconciled. An unreadable, wrong-dataset or unsupported journal is preserved and blocks new native approvals instead of being silently overwritten. Native journal files are written with owner-only permissions; a storage/synchronization failure does not authorize sending an unretained request.

The recent native list retains up to 50 creation receipts. It describes historical approved revisions, not later edits made elsewhere. A stale export revision is refused; inspect the current personal-crate entry for its current revision. The native export-history view shows up to 20 recent backend artifacts.

## Verification recorded for this milestone

- Debug build: `BUILD SUCCEEDED`.
- Xcode analysis: `ANALYZE SUCCEEDED`.
- Release build: `BUILD SUCCEEDED`.
- Full macOS UI suite: **17 passed, 0 failures**.
- Extended export/history focused rerun: **1 passed, 0 failures**, including offline-source historical recovery and exact ZIP-byte comparison.
- Native Foundation ZIP verifier: **10 passed**. Independent fixtures cover Unicode/quotes/multiline CSV, repeated memberships, corrupt bytes, wrong datasets, changed manifests, reversed M3U8/CSV order, extra/truncated members and central-directory identity mismatches. The harness compiles the production verifier, not a reimplementation.
- Synthetic Node suite: **100 passed, 0 failures**.
- Backend/planner contract suite: **140 passed** across Studio, product, indexed operations, planning and transitions.
- Shell syntax and scoped whitespace checks passed.

The native UI suite now exercises successful Generate → exact proposal → explicit approval → restart → verified save-panel export. It also checks corrupt recovery storage, scope overflow, infeasibility without saving, source changes after preview, and lost-acknowledgement replay after restart with **exactly one** saved backend crate and the original request ID.

Fault injection requires `--ui-testing`, `--synthetic-data` and `--test-drop-approval-ack` together. It interrupts the client only after validating a real backend approval receipt. It neither fabricates a receipt nor bypasses approval. UI tests use isolated temporary app data and generated tagged PCM audio; no personal library, account, model, paid inference or external DJ database is used.

Use `./verify_native.sh` from this directory for native verifier tests, Node tests, Xcode UI tests/analysis, Release packaging and screenshot export. It requires the existing prepared native Python build environment. The script retains logs/result bundles/screenshots under a new `/tmp/booth-buddy-verify.*` directory and preserves failure status while attempting screenshot export.

Nonblocking tooling warnings remain: App Intents metadata extraction is skipped because the app has no App Intents dependency, and XCTest reports internal QoS diagnostics on some UI interactions. No compiler source warnings or test failures remain in the final verified runs.

## Remaining roadmap work

This completes a useful native vertical slice, not all of roadmap priorities 1–4 in their broadest sense. Next gaps include visual phase/pin/event editing, complete existing-crate edit/approval/undo, smart-rule/bulk interfaces, richer intake/booking integration, native backup/restore, cue/audition integration and wider lifecycle/accessibility/platform acceptance.

Actual Serato import/playback, musical quality, model quality, signing/notarization, clean-machine acceptance, distribution rights, connected providers and commercial operations remain separate gates. The native ZIP and approval tests do not certify them. No Metal, third-party competitor branding/assets, backend API changes, source-media writers, commits or pushes are introduced by this milestone.

## November 16 commercial-readiness continuation

The native surface now includes current-crate rename/reorder/remove preview and approval, exact-request recovery after a lost edit response, reviewed undo, event-request and ordered-phase forms, private database backup, new-folder restore and explicit opening of the restored workspace. The existing advanced editor remains available for pins and unsupported visual controls. Current crate revisions are read from the backend; retained creation receipts remain historical.

Database downloads are verified by the bundled backend's existing compressed-backup restore validator in an isolated temporary directory before saving. The native playlist verifier remains specific to the stored playlist ZIP format; the two archive contracts are not conflated. Private exports are written to mode-0600 temporary files before atomic publication. Restore never replaces an existing directory. The native pending-operation journal must be reconciled before backup/switch; it is deliberately not part of the database-only archive.

The workspace boundary now uses POSIX `realpath`, including a canonical existing parent for a new restore directory. macOS Foundation's `/private/tmp` → `/tmp` shortening is incompatible with the Python backend's strict canonical-path contract. The fix is in the native client; the backend check is preserved. Independent Swift harness tests cover temporary aliases, new nested paths, symlinked parents and Unicode directory identity.

Native support reports are explicitly saved from Readiness or the Help menu and include only app/OS/build metadata, numeric indexed counts and recovery availability flags. They omit paths, names, tokens, environment variables and raw logs. Nothing is automatically uploaded. Synthetic UI runs can retain a private backend-stderr log for launch diagnosis; these are separate from user support reports and are not uploaded by CI.

The UI-test target has explicit development-only runner entitlements, uses unique `/private/tmp` synthetic workspaces, and runs serially. Earlier macOS cache cleanup killed container-based runners; subsequent permission and canonical-path failures were investigated rather than counted as passes. The resize test grows an already-minimum window before testing reduction. Appearance coverage uses a synthetic/UI-test-only light-mode override, without changing system preferences.

Original teal/green icon artwork and appearance-aware foreground colors are included. See [commercial-readiness setup](COMMERCIAL-READINESS.md) for the ten work packages, release-preflight commands and remaining external acceptance gates. A passing technical preflight is not commercial clearance. The [dated continuation verification report](COMMERCIAL-VERIFICATION-2026-11-16.md) contains final results; the 17-test milestone above is historical.
