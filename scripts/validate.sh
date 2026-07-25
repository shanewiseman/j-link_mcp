#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

.venv/bin/python -m compileall -q src tests \
  extensions/arduino_giga/src extensions/arduino_giga/tests \
  extensions/giga_protocol_bridge/src extensions/giga_protocol_bridge/tests
.venv/bin/python -m pytest tests --cov=jlink_mcp --cov-report=term-missing
.venv/bin/python -m pytest extensions/arduino_giga/tests
.venv/bin/python -m pytest extensions/giga_protocol_bridge/tests
docker compose --env-file .env.hardware config --quiet
docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.giga.yaml config --quiet
scripts/generate-sbom.sh
extensions/arduino_giga/scripts/generate-sbom.sh
extensions/giga_protocol_bridge/scripts/generate-sbom.sh
scripts/validate-distributions.sh

if grep -Eiq 'arduino|giga|stm32h747|protocol.?bridge|fqbn' \
  Dockerfile compose.yaml sbom/jlink-mcp.cdx.json sbom/python-licenses.md; then
  echo "Hardware-specific implementation leaked into a core distribution surface" >&2
  exit 1
fi
if find src/jlink_mcp -type f -name '*.py' -print0 \
  | xargs -0 grep -Eiq 'arduino|giga|stm32h747|protocol.?bridge|fqbn'; then
  echo "Hardware-specific implementation leaked into the core package" >&2
  exit 1
fi

if find . -path ./.git -prune -o -path ./.venv -prune -o -path ./state -prune \
  -o -type f \( -name 'JLinkExe' -o -name 'JLinkGDBServerCLExe' -o -name '*.so' \) \
  -print | grep -q .; then
  echo "Unexpected binary/proprietary-looking file in publishable tree" >&2
  exit 1
fi
