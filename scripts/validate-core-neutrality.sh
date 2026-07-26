#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

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
echo "Validated core target neutrality"
echo "Intentional Jenkins gate probe: core neutrality failure" >&2
exit 98
