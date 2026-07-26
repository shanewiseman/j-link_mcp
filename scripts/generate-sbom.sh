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

output_dir=${1:-sbom}
mkdir -p "$output_dir"
mkdir -p "$repository_dir/.ci-cache"
inventory_dir=$(mktemp -d \
  "$repository_dir/.ci-cache/jlink-mcp-core-sbom.XXXXXX")
trap 'rm -r -- "$inventory_dir"' EXIT HUP INT TERM
UV_PROJECT_ENVIRONMENT="$inventory_dir/venv" "$uv_command" sync \
  --frozen --offline --package jlink-mcp --extra gui --extra sbom --no-editable
"$inventory_dir/venv/bin/cyclonedx-py" environment \
  --pyproject pyproject.toml \
  --mc-type application \
  --spec-version 1.6 \
  --output-reproducible \
  --output-format JSON \
  --output-file "$output_dir/jlink-mcp.cdx.json" \
  --validate \
  "$inventory_dir/venv/bin/python"
"$inventory_dir/venv/bin/pip-licenses" \
  --python "$inventory_dir/venv/bin/python" \
  --format markdown \
  --with-urls \
  --order name \
  --output-file "$output_dir/python-licenses.md"
echo "Generated the core Python SBOM and license inventory"
