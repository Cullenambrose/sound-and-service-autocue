#!/bin/bash
# Reproducible native verification; all app/test data stays under temporary roots.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(dirname "$project_dir")"
results_parent="${BOOTH_BUDDY_RESULTS_PARENT:-/tmp}"
results_dir="$(mktemp -d "$results_parent/booth-buddy-verify.XXXXXX")"
printf 'BOOTH BUDDY verification artifacts: %s\n' "$results_dir"
finish() {
  status=$?
  if [[ -d "$results_dir/UI.xcresult" ]]; then
    xcrun xcresulttool export attachments --path "$results_dir/UI.xcresult" \
      --output-path "$results_dir/screenshots" > "$results_dir/screenshots.log" 2>&1 \
      || printf 'Screenshot export failed; see %s/screenshots.log\n' "$results_dir"
  fi
  printf 'Verification exit: %s; artifacts: %s\n' "$status" "$results_dir"
  exit "$status"
}
trap finish EXIT
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$project_dir" -p 'test_native_*.py' > "$results_dir/native-model-tests.log" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$project_dir" -p 'test_release_preflight.py' > "$results_dir/preflight-tests.log" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$project_dir" -p 'test_serato_acceptance.py' > "$results_dir/serato-acceptance-tests.log" 2>&1
node --test "$repo_root"/web/test_*.cjs > "$results_dir/node.log" 2>&1
build_python="${AUTOCUE_BUILD_PYTHON:-$HOME/autocue-mac-build/build-env/bin/python}"
(cd "$repo_root" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine "$build_python" -m unittest \
  web.test_studio web.test_studio_product web.test_library_operations web.test_studio_backup \
  engine.setlist.test_planning engine.setlist.test_transitions desktop.test_release_inventory) > "$results_dir/backend.log" 2>&1
xcodebuild -project "$project_dir/AutoCueStudio.xcodeproj" -scheme AutoCueStudio \
  -destination 'platform=macOS' -derivedDataPath "$results_dir/DerivedData" \
  build-for-testing analyze > "$results_dir/native-build.log" 2>&1
xcodebuild -project "$project_dir/AutoCueStudio.xcodeproj" -scheme AutoCueStudio \
  -destination 'platform=macOS' -derivedDataPath "$results_dir/DerivedData" \
  -resultBundlePath "$results_dir/UI.xcresult" -parallel-testing-enabled NO test-without-building > "$results_dir/native.log" 2>&1
xcodebuild -project "$project_dir/AutoCueStudio.xcodeproj" -scheme AutoCueStudio \
  -configuration Release -destination 'platform=macOS' \
  -derivedDataPath "$results_dir/ReleaseData" build > "$results_dir/release.log" 2>&1
python3 "$project_dir/release_preflight.py" "$results_dir/ReleaseData/Build/Products/Release/AutoCue Studio.app" > "$results_dir/release-preflight.json"
printf 'Native/Serato verifier and preflight tests, Node/backend regressions, native UI tests, analysis, Release build and development artifact preflight passed.\n'
