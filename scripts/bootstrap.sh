#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

mkdir -p state/segger
chmod 700 state state/segger

if [ ! -s .token ]; then
  if [ -x .venv/bin/jlink-mcp ]; then
    .venv/bin/jlink-mcp token --output .token
  else
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))' >.token
    chmod 600 .token
  fi
fi

scripts/generate-hardware-env.sh
echo "Core bootstrap complete. Start with: docker compose --env-file .env.hardware up --build -d"
