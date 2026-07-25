#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest --cov=jlink_mcp --cov-report=term-missing
docker compose --env-file .env.hardware config --quiet
scripts/generate-sbom.sh

if find . -path ./.git -prune -o -path ./.venv -prune -o -path ./state -prune \
  -o -type f \( -name 'JLinkExe' -o -name 'JLinkGDBServerCLExe' -o -name '*.so' \) \
  -print | grep -q .; then
  echo "Unexpected binary/proprietary-looking file in publishable tree" >&2
  exit 1
fi
