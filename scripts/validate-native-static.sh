#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

uv_command=uv
if ! command -v uv >/dev/null 2>&1; then
  uv_command=.venv/bin/uv
fi
if [ ! -x "$uv_command" ] && ! command -v "$uv_command" >/dev/null 2>&1; then
  echo "uv is required; install it or provide .venv/bin/uv" >&2
  exit 1
fi

"$uv_command" lock --check --offline
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q \
  src tests \
  extensions/arduino_giga/src extensions/arduino_giga/tests \
  extensions/giga_protocol_bridge/src extensions/giga_protocol_bridge/tests
echo "Validated frozen lock, Ruff policy, formatting, and Python compilation"
