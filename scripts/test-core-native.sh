#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

mkdir -p artifacts
export COVERAGE_FILE=artifacts/.coverage-core

exec .venv/bin/python -m pytest \
  tests \
  --ignore=tests/container \
  -m "not hardware and not hil and not container and not gui and not destructive" \
  --junitxml=artifacts/junit-core.xml \
  --cov=jlink_mcp \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:artifacts/coverage-core.xml \
  --cov-fail-under=80
