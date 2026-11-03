#!/bin/sh
set -eu
cd "$(dirname "$0")"
PY=python3
[ -d .venv/bin ] && PATH="$PWD/.venv/bin:$PATH"
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" web/server.py
