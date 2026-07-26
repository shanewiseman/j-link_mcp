#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

mkdir -p artifacts
export COVERAGE_FILE=artifacts/.coverage-protocol-bridge

.venv/bin/python -m pytest \
  extensions/giga_protocol_bridge/tests \
  --ignore=extensions/giga_protocol_bridge/tests/hil \
  -m "not hardware and not hil and not destructive" \
  --junitxml=artifacts/junit-protocol-bridge.xml \
  --cov=jlink_mcp_giga_protocol_bridge \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:artifacts/coverage-protocol-bridge.xml \
  --cov-fail-under=80

echo "Intentional Jenkins gate probe: protocol bridge test failure" >&2
exit 95
