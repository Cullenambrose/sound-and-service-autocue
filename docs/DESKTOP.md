# macOS desktop development build

This is an unsigned/ad-hoc local test build of the existing Studio alpha, not a
signed or notarized distribution. Gatekeeper may block it on another Mac.
Signing, notarization, distribution, updates, and the other Studio release gates
remain external work; this is not commercial readiness.

The app runs the same packaged Studio HTML/JS and a frozen Python backend: no
Python installation is needed on the target machine. The AppKit/WKWebView shell
starts its own loopback backend, waits for its readiness, and terminates that
specific child on quit. A parent monitor also stops it after an abnormal shell
exit. The shell holds a lock for its canonical app-data directory, preventing
two desktop instances from sharing it. This lock does **not** prevent a
separately launched `python -m web.studio` from using the same data; do not run
one against that directory concurrently.

The native **Choose music folder…** button supplies a folder path to the
existing root field. Nothing is scanned until the existing scan button is
pressed for that selection; previously enabled folder monitors remain active.
External HTTP(S) links open in the system browser; other external
navigation is blocked. The native bridge only requests a folder selection.
Web exports use the native save dialog.

Default data is `~/Library/Application Support/AutoCue Studio` (resolved to a
canonical path). For isolated development testing only, set the absolute
`AUTOCUE_DESKTOP_DATA` environment variable when directly launching the app
executable. The lock and backend use the resolved directory. The regular
Studio data-safety and local-alpha limitations still apply.

## Build on Apple Silicon macOS

Requires Xcode/Swift, `hdiutil`, a Python 3.10+ build interpreter and network
access to install build dependencies. These commands create an isolated build
environment outside the project's `.venv`; replace the example paths with your
own dedicated output directory and Python interpreter:

```sh
BUILD="$HOME/autocue-mac-build"
mkdir -p "$BUILD"
python3 -m venv "$BUILD/build-env"
"$BUILD/build-env/bin/python" -m pip install -r desktop/requirements-build.txt .
"$BUILD/build-env/bin/python" scripts/build_macos_app.py --output "$BUILD"
```

The script refuses to overwrite an existing app, DMG, or its named PyInstaller
work directories. It produces `AutoCue Studio.app` and `AutoCue Studio.dmg`.
It bundles the Studio package installed in the build environment, so rerun the
`pip install` command after changing Studio and build into a fresh output directory.
The build is Apple Silicon only; Intel and universal builds are not covered.
PyInstaller embeds Python and the worker-only dependencies, not optional
beat/stem/word model stacks. No signing, notarization, account access or
publishing is performed.

The build now includes `Contents/Resources/ThirdPartyNotices/` and
`release-inventory.json`: copied installed dependency/runtime license files,
their hashes and exact native-binary hashes. Missing or unsafe notice paths
stop the build. This is an evidence inventory, not a complete transitive SPDX
SBOM or commercial legal clearance.

**Mutagen declares GPL-2.0-or-later.** Commercial use is not automatically
forbidden, but its applicable distribution/source obligations and the intended
product license must be resolved before publication. Do not assume the package
is suitable for a proprietary release. The PyInstaller bootloader exception,
CPython, scientific/native vendor notices, optional model stacks and privacy/
content terms also need exact-build review. No model weights are bundled.

Studio's private database backup button uses the same native save/download
path as playlist ZIPs. The frozen backend also accepts `--backup` and `--restore`
as documented in [Studio recovery](STUDIO.md#private-database-backup-and-non-destructive-restore).
Restoration targets a new directory only and exits without starting a server.
For isolated shell testing against that directory, use the existing
`AUTOCUE_DESKTOP_DATA` override; there is no in-place GUI restore workflow.

Run the packaged backend fixture outside the checkout in a fresh directory:

```sh
"$BUILD/build-env/bin/python" desktop/test_packaging.py \
  "$BUILD/AutoCue Studio.app" "$BUILD/verification"
```

The test creates generated demo audio and a database under `verification`; it
checks packaged assets, session, search, phase planning and transition evidence,
inbox, batch creation, existing-crate edit/replay/undo, reviewed history
removal/replay/undo, backup/restore and model-tag persistence across backend restart, then
terminates its backend. To test the actual shell, open the app, choose a
folder, exercise the existing Studio controls and quit normally. Keep test
data and artifacts private: Studio data and exports can contain local paths.
