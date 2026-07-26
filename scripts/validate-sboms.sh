#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

output_dir=artifacts/sbom
mkdir -p "$output_dir"
scripts/generate-sbom.sh "$output_dir"
extensions/arduino_giga/scripts/generate-sbom.sh
extensions/giga_protocol_bridge/scripts/generate-sbom.sh
.venv/bin/python -m json.tool \
  extensions/arduino_giga/sbom/arduino-platform.cdx.json >/dev/null
.venv/bin/python -m json.tool \
  extensions/giga_protocol_bridge/sbom/arduino-libraries.cdx.json >/dev/null
cp extensions/arduino_giga/sbom/arduino-platform.cdx.json \
  "$output_dir/arduino-platform.cdx.json"
cp extensions/giga_protocol_bridge/sbom/arduino-libraries.cdx.json \
  "$output_dir/arduino-libraries.cdx.json"
echo "Validated and staged core and extension CycloneDX inventories"
echo "Intentional Jenkins gate probe: repository SBOM failure" >&2
exit 96
