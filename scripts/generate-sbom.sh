#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

mkdir -p sbom
inventory_dir=$(mktemp -d /tmp/jlink-mcp-core-sbom.XXXXXX)
trap 'rm -r -- "$inventory_dir"' EXIT HUP INT TERM
UV_PROJECT_ENVIRONMENT="$inventory_dir/venv" .venv/bin/uv sync \
  --frozen --package jlink-mcp --extra test --extra gui --no-editable
"$inventory_dir/venv/bin/cyclonedx-py" environment \
  --pyproject pyproject.toml \
  --mc-type application \
  --spec-version 1.6 \
  --output-reproducible \
  --output-format JSON \
  --output-file sbom/jlink-mcp.cdx.json \
  --validate \
  "$inventory_dir/venv/bin/python"
"$inventory_dir/venv/bin/pip-licenses" \
  --python "$inventory_dir/venv/bin/python" \
  --format markdown \
  --with-urls \
  --order name \
  --output-file sbom/python-licenses.md
echo "Generated the core Python SBOM and license inventory"
