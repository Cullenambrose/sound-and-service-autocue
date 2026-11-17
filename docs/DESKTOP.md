# macOS desktop development build

This is an unsigned/ad-hoc local test build of the existing Studio alpha, not a
signed or notarized distribution. Gatekeeper may block it on another Mac.
Signing, notarization, distribution, updates, and the other Studio release gates
remain external work; this is not commercial readiness.

The app runs the same packaged Studio HTML/JS and a frozen Python backend: no
Python installation is needed on the target machine. The AppKit/WKWebView shell
starts its own loopback backend, waits for its readiness, and terminates that
specific child on quit. A parent monitor also stops it after an abnormal shell
exit. A native progress view remains visible until the workspace loads; startup,
backend and WebKit failures show recovery guidance before quitting. The app
menu provides About, hide and quit controls, and the Edit and Window menus
provide standard keyboard actions. The shell holds a lock for its canonical app-data directory, preventing
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

## Open and build in Xcode

The shared `desktop/AutoCueStudio.xcodeproj` project and `AutoCueStudio` scheme
build the same AppKit/WKWebView shell for **arm64 only**, with a macOS 12.0
minimum. Before the first Xcode build, prepare the external Python build
environment:

```sh
bash scripts/prepare_macos_xcode.sh
open desktop/AutoCueStudio.xcodeproj
```

In Xcode, select the `AutoCueStudio` scheme and **My Mac**, then use **Product
> Build**. The build phase refreshes the installed app package from this
checkout, freezes the Python backend, copies the current Studio assets (including
the plan-view module), and writes the dependency notices and release inventory
into the built app. It defaults to
`$HOME/autocue-mac-build/build-env/bin/python`; set the Xcode build setting
`AUTOCUE_BUILD_PYTHON` only if you prepared the environment elsewhere. The
build phase has user-script sandboxing disabled because PyInstaller must read
the Python environment and project package.

The project preserves the current development bundle identifier and data path.
Its signing configuration is for local development only; no Developer ID,
team, hardened-runtime distribution, notarization or update feed is configured.
The project currently has no branded app icon. Those release choices/assets
must be supplied before publication. Keep the existing standalone DMG script
for its disposable packaging smoke-test workflow.

The build now includes `Contents/Resources/ThirdPartyNotices/` and
`release-inventory.json`: copied installed dependency/runtime license files,
their hashes and exact native-binary hashes. Missing or unsafe notice paths
stop the build. It also emits `AutoCueStudio.spdx.json` in SPDX 2.3 format,
with a Python dependency closure derived from installed distribution metadata.
For Xcode builds, native files under `Contents/MacOS` are excluded from the
inventory hashes because Xcode signs them after the inventory is generated.
That closure can include build-environment packages not embedded in the app and
does not identify native components inside wheels. It is not a complete
shipped-binary SBOM or commercial legal clearance.

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
removal/replay/undo, backup/restore, SPDX metadata/hash and model-tag persistence
across backend restart, then terminates its backend. To test the actual shell,
open the app, choose a folder, exercise the existing Studio controls and quit
normally. Keep test data and artifacts private: Studio data and exports can
contain local paths.

The latest disposable isolated build produced the unsigned app and DMG; the
packaged-backend test passed and verified the bundled SPDX 2.3 document/hash.
The temporary build, generated media and test data were removed. This does not
cover signing, notarization, clean-machine installation or release rights.
