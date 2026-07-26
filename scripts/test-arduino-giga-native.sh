#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

mkdir -p artifacts
export COVERAGE_FILE=artifacts/.coverage-arduino-giga

.venv/bin/python -m pytest \
  extensions/arduino_giga/tests \
  --ignore=extensions/arduino_giga/tests/hil \
  -m "not hardware and not hil and not gui and not destructive" \
  --junitxml=artifacts/junit-arduino-giga.xml \
  --cov=jlink_mcp_arduino_giga \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:artifacts/coverage-arduino-giga.xml \
  --cov-fail-under=80

echo "Intentional Jenkins gate probe: Arduino GIGA test failure" >&2
exit 94
