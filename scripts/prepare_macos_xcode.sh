#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="${AUTOCUE_MAC_BUILD_ROOT:-$HOME/autocue-mac-build}"
BUILD_ENV="$BUILD_ROOT/build-env"

if [[ ! -x "$BUILD_ENV/bin/python" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    BASE_PYTHON="$ROOT/.venv/bin/python"
  elif command -v python3.10 >/dev/null 2>&1; then
    BASE_PYTHON="$(command -v python3.10)"
  else
    echo "Python 3.10+ is required. Install it or create the project .venv first." >&2
    exit 1
  fi
  "$BASE_PYTHON" -c 'import sys; sys.version_info >= (3, 10) or sys.exit("Python 3.10+ is required.")'
  "$BASE_PYTHON" -m venv "$BUILD_ENV"
fi

"$BUILD_ENV/bin/python" -c 'import platform,sys; sys.version_info >= (3, 10) or sys.exit("Python 3.10+ is required."); platform.machine() in ("arm64", "aarch64") or sys.exit("Native Apple Silicon Python is required.")'
PIP_DISABLE_PIP_VERSION_CHECK=1 "$BUILD_ENV/bin/python" -m pip install \
  -r "$ROOT/desktop/requirements-build.txt"
PIP_DISABLE_PIP_VERSION_CHECK=1 "$BUILD_ENV/bin/python" -m pip install \
  --no-build-isolation "$ROOT"

echo "Xcode build environment ready: $BUILD_ENV/bin/python"
