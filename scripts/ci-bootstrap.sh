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

if [ ! -f uv.lock ]; then
  echo "uv.lock is required for CI bootstrap" >&2
  exit 1
fi

exec "$uv_command" sync \
  --frozen \
  --all-packages \
  --all-extras
