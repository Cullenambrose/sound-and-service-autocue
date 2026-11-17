# BOOTH BUDDY — native Serato export verification

## Delivered

The native crate view now offers **Export to Serato…** after a verified export preview. It saves a new `.crate` containing the exact ordered memberships, including Unicode and repeated paths. The existing backend download performs fresh source verification before saving. Backend APIs, planner behavior and data contracts were not changed.

The save uses a private 0600 temporary file and an atomic, non-overwriting link. Existing destination files are refused. Export into a `Subcrates` directory is refused while Serato is running, with checks before and after the backend request. The app does not quit Serato or alter audio, tags, cues, existing crates or Serato's database. The inconclusive OS document-open prototype was removed.

## Files changed in this continuation

- `StudioApp.swift`: native encoder, verified export reuse, save panel, no-overwrite writer and Export to Serato UI.
- `AutoCueStudioUITests/AutoCueStudioUITests.swift`: end-to-end export/preservation test; resize driver now drags the straight window edge instead of the rounded corner.
- `test_native_workflow.py`: production Swift playlist/crate encoding tests, strict parser cross-check.
- `verify_native.sh`: includes Serato acceptance verifier tests.
- `verify_serato_acceptance.py`, `test_serato_acceptance.py`: read-only ordered membership/source hash checker and 12 tests.
- `SERATO-EXPORT.md`, `SERATO-ACCEPTANCE.md`, `SERATO-BASELINE.json`, this report: workflow, bounded acceptance evidence and historical baseline.

The preexisting dirty worktree was preserved. No staging, commits or pushes were performed.

## Exact results

Artifacts are under `/tmp/booth-serato-acceptance/`.

| Check | Result | Evidence |
| --- | --- | --- |
| Recovered Debug build for testing | TEST BUILD SUCCEEDED | `build-recovered.log` |
| Xcode static analysis | ANALYZE SUCCEEDED | `build-recovered.log` |
| Release build | BUILD SUCCEEDED | `release.log` |
| Native macOS UI suite | 25 executed, 0 failures, 661.661 seconds; TEST EXECUTE SUCCEEDED | `ui-final.log`, `UI-final.xcresult` |
| Swift export/path, preflight and Serato checker unit tests | 39 passed, 5.990 seconds | `unit-crate-final.log` |
| Synthetic Node suite | 100 passed, 0 failed | `node.log` |
| Packaged Release backend smoke | PASS, including restart/replay/undo, planning, transitions and backup/restore | `packaged-final.log` |
| Development artifact preflight | passed, all 5 checks | `release-preflight.json` |
| Whitespace checks | `git diff --check`, exit 0 | local check |

The only build warning was skipped AppIntents metadata extraction because the app has no AppIntents dependency. Development artifact SHA-256: `c08896c8c589b29ba6946f0a5ff2e843fa3150538059bd1db1c27b0e8dd19fae`.

The earlier 25-test run had 24 passes and one resize-driver failure. A focused retry could not build when disk space ran out; obsolete task-generated build directories were removed, retaining source, logs, screenshots, Release artifacts and fixtures. A fresh build succeeded. Its first runner initialization timed out enabling automation mode; the subsequent complete serial run passed all 25 tests. No system security settings were changed. The prior 149 backend regression passes remain historical evidence; this continuation additionally ran the current packaged backend smoke, without changing backend source.

## Visual and actual Serato evidence

Reviewed app-only screenshots in `final-screenshots/`:

- `B6AC1090-CFFB-47B6-9CDB-D7167E8C37A7.png`: visible Export to Serato action and readable existing-file refusal, teal branding, wrapped copy and consistent control spacing.
- `1B68CAD7-A3E3-400E-8EE0-CBC4B80FF52F.png`: resized window retains sidebar/navigation and usable scan controls; content continues in the scroll view.
- `7F36B5EF-1ADF-46F1-95AF-19D90AD2CB23.png`: light appearance, dark teal actions and visible keyboard focus ring.

There was no visible overlap or clipped primary action in these reviewed states. The UI suite also exercises native keyboard navigation and accessibility identifiers; this is not a complete VoiceOver audit.

Using the authorized test Serato library, an acceptance crate appeared in Serato after restart. Its three ordered paths and source hashes matched the verified manifest; production Swift bytes were identical to the installed crate. SHA-256: `207dbc048f365b13cb8390505c730d507cf9ef1a6f1cb9a3d38d814cc2544065`. Evidence: `installed-crate-check.json` and `run-usihauqo/native-crate-evidence.json`. An existing-library track was loaded and transport/meters observed, but playback of all three exported tracks and their visible row order were not verified. No listening-quality acceptance is claimed.

Automated fixtures and databases used temporary directories. The only external library addition was the explicitly authorized test acceptance crate. Test-only Serato lifecycle work included forced termination after graceful termination stalled; the product contains no force-quit behavior.

## Remaining release gates

Complete exported-track playback/UI-order acceptance, external-volume and additional Serato/macOS versions, and clean-machine installation/upgrade/rollback checks. Commercial distribution still requires production identity, Developer ID signing/hardened runtime/notarization and Gatekeeper verification, dependency-rights clearance (including the declared Mutagen GPL license and incomplete shipped-binary SBOM), and finalized terms/privacy/support/pricing/licensing/update operations. This is a tested development artifact, not commercial-release clearance.
