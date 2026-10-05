#!/bin/bash
set -e
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

exec "$PY" tools/reload.py "$@"
