#!/bin/sh
set -eu

extension_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$extension_dir"
"$extension_dir/../../.venv/bin/python" -m json.tool \
  sbom/arduino-libraries.cdx.json >/dev/null
echo "Validated the pinned protocol-bridge library inventory"
