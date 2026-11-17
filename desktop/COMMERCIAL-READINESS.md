# BOOTH BUDDY macOS commercial-readiness setup

This is a local-first macOS product in development. It is **not cleared for paid distribution**. The native core now reaches the established backend contracts for planning, exact approval, verified playlist exports, personal-crate edits/undo and database recovery. No backend API, planner ranking, source-media writer, provider integration or data contract is changed by this continuation.

## Ten work packages advanced

| Work package | Delivered locally | Limit / remaining acceptance |
|---|---|---|
| 1. Native current-crate management | Current revisions and exact memberships, rename/reorder/remove drafts, preview and explicit approval | Smart-rule creation, merge/move and bulk interfaces remain future work |
| 2. Durable edit recovery | Persist exact request before sending, reconcile lost acknowledgements after restart | Pending changes must be reconciled before workspace backup/switch |
| 3. Native undo | Review and approve revision-bound backend undo | Concurrent/stale revisions fail; this does not undo source-media changes |
| 4. Native database backup | Native destination panel, private atomic output; downloaded compressed ZIP verified by bundled restore validator in temporary storage | Contains private database only, excludes audio/models/export artifacts/native recovery journal; not encrypted |
| 5. Native restore/workspace recovery | New-folder-only restore, explicit open of restored workspace, safe default-workspace fallback | Independent interrupted-restore/clean-machine testing still required |
| 6. Event requests and phases | Native forms preserve event/policy JSON and advanced constraints | Exact-file pins and advanced requirements still use advanced JSON |
| 7. Support and onboarding | Native quickstart guidance and explicitly saved redacted JSON report, private permissions, no automatic upload | Public support channel, privacy policy and support commitments need owner decisions |
| 8. Native branding/accessibility | Original teal/green AppKit-generated icon and appearance-aware teal; existing resizing/navigation coverage retained | Broader VoiceOver, light/dark/increased-contrast and hardware acceptance still required |
| 9. Repeatable verification | Pinned tested build constraints, local verification entry point, arm64 macOS PR/manual CI workflow | CI is prepared locally, has not run on GitHub; constraints are not a hash lock or reproducible-build proof |
| 10. Release preflight | Read-only inventory/notices/hash checks; distribution profile additionally checks identity, icon, Developer ID/runtime, signature, stapled ticket, Gatekeeper and recorded rights flag | A green technical report does not certify legal, musical, customer or commercial acceptance |

## Build and verify without personal data

Use a native Apple Silicon Mac with Xcode, Node and Python 3.10+. The minimum OS in the app metadata is not a tested support matrix. No models or provider accounts are required for local core workflows.

```sh
# From repository root; downloads build dependencies into the existing isolated build environment.
PIP_CONSTRAINT="$PWD/desktop/build-constraints.txt" bash scripts/prepare_macos_xcode.sh
bash desktop/verify_native.sh
```

`verify_native.sh` creates a unique temporary results directory and runs production Foundation ZIP verifier tests, release-preflight unit tests, synthetic Node tests, backend/planner/backup/inventory regressions, Xcode UI tests and analysis, Release build and development artifact preflight. It exports XCTest attachments even on failure and retains the original failure status. Test data is generated; no real library is scanned. Do not share raw result bundles without review; the CI workflow uploads only synthetic screenshot PNGs.

The standalone builder also copies the original icon. Legacy `.app`/DMG artifact filenames still use AutoCue Studio; the visible native app and icon use BOOTH BUDDY. Renaming a signed artifact is a release-process choice, not a second brand.

## Inspect an exact release artifact

```sh
python3 desktop/release_preflight.py '/absolute/path/AutoCue Studio.app'
python3 desktop/release_preflight.py '/absolute/path/AutoCue Studio.app' --distribution
```

The command prints JSON to stdout, returns 0 only when that profile's checks pass, does not sign, contact a notarization service, upload or publish. It records a content fingerprint so an acceptance record can identify its exact app. Development passing is expected; distribution failing is expected for this unsigned, development-identifier, uncleared-rights package. Signature tooling and Gatekeeper checks need a real macOS host, not mocks.

Inventory generation currently explicitly records `commercial_rights_cleared: false`. Do not flip that field to bypass the gate. Any future signing pipeline must regenerate nested-binary inventory hashes **after** signing those binaries and **before** sealing the main app. Finish the artifact before recording clean-machine and compatibility evidence; any subsequent byte changes invalidate the content fingerprint.

Apple's external distribution workflow requires the appropriate [Developer ID](https://developer.apple.com/developer-id/) and [notarization preparation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution/). Credentials, bundle identity, signing/notarization submission and public publication have not been invented or performed here.

## Gates to the first commercial macOS release

1. Production bundle identifier and owner-controlled Developer ID signing, hardened-runtime validation, notarization/stapling and Gatekeeper acceptance of the exact downloadable artifact.
2. Exact shipped-code/content rights review, complete shipped-binary inventory and applicable source/distribution obligations. In particular, the existing Mutagen GPL declaration is not removed by putting it inside a native app. No model rights are assumed.
3. Independent clean-account/clean-machine install, first launch, permissions, offline operation, upgrade, backup/restore, interruption recovery, uninstall and rollback on a declared supported OS/hardware matrix.
4. Complete target DJ-software import/path/playback validation using authorized fixtures. The local [Serato acceptance matrix](SERATO-ACCEPTANCE.md) now records a crate appearing after restart and matching ordered memberships/source hashes. Visible row order and playback of all exported tracks remain pending; cue-loop semantics remain separately gated.
5. Independent musical/set-quality acceptance against declared criteria. Synthetic feasibility and regression passes are not listening acceptance or algorithm promotion.
6. Owner-approved terms, privacy policy, pricing, support/refund commitments and distribution channel. If paid licensing/subscriptions are selected, design offline entitlement, expiration/grace, revocation and recovery behavior before implementing billing. No fake entitlement or payment-success path is added.
7. A tested update/rollback process and vulnerability/dependency response ownership. Current setup has no automatic updater; do not advertise one.

Provider connections, Windows, model downloads and live DJ automation are separate scope; they need not prevent validating a useful local macOS core. Continue with controlled local acceptance rather than broadening claims.

## Verification evidence

See the [dated verification report](COMMERCIAL-VERIFICATION-2026-11-16.md) produced for this continuation. Earlier roadmap and native-milestone reports are historical checkpoints, not proof that every later dirty-worktree change passed their old runs. Nothing is committed, pushed or published by this setup.

Native Serato delivery and its exact acceptance boundaries are recorded in [Serato export verification](SERATO-EXPORT-VERIFICATION-2026-11-16.md) and [the export follow-up](SERATO-EXPORT-FOLLOWUP.md). Consult these newer checkpoints before relying on the earlier commercial baseline.
