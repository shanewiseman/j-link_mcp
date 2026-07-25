#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

mkdir -p sbom
.venv/bin/cyclonedx-py environment \
  --pyproject pyproject.toml \
  --mc-type application \
  --spec-version 1.6 \
  --output-reproducible \
  --output-format JSON \
  --output-file sbom/jlink-mcp.cdx.json \
  --validate \
  .venv/bin/python
.venv/bin/pip-licenses \
  --python .venv/bin/python \
  --format markdown \
  --with-urls \
  --order name \
  --output-file sbom/python-licenses.md
.venv/bin/python -m json.tool sbom/arduino-libraries.cdx.json >/dev/null
echo "Generated Python SBOM/licenses and validated sbom/arduino-libraries.cdx.json"
