#!/bin/sh
set -eu
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python is unavailable. Use Python 3.10+ and the repository virtual environment." >&2
  exit 1
fi
"$PY" -m web.pilot_preflight
if [ "$#" -eq 0 ]; then
  set -- --setup
fi
exec "$PY" -m web.read_only_prep "$@"
