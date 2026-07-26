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
distribution_dir=$(mktemp -d /tmp/jlink-mcp-distributions.XXXXXX)
trap 'rm -r -- "$distribution_dir"' EXIT HUP INT TERM

"$uv_command" build --offline --wheel --all-packages \
  --out-dir "$distribution_dir/wheels"
core_wheel=$(find "$distribution_dir/wheels" -name 'jlink_mcp-*.whl' -print -quit)
giga_wheel=$(find "$distribution_dir/wheels" -name 'jlink_mcp_arduino_giga-*.whl' -print -quit)
bridge_wheel=$(find "$distribution_dir/wheels" -name 'jlink_mcp_giga_protocol_bridge-*.whl' -print -quit)
test -n "$core_wheel" && test -n "$giga_wheel" && test -n "$bridge_wheel"

CORE_WHEEL="$core_wheel" GIGA_WHEEL="$giga_wheel" BRIDGE_WHEEL="$bridge_wheel" \
  .venv/bin/python - <<'PY'
import os
import zipfile

with zipfile.ZipFile(os.environ["CORE_WHEEL"]) as archive:
    core = "\n".join(archive.namelist()).lower()
assert not any(word in core for word in ("arduino", "giga", "stm32h747", "protocol_bridge"))
with zipfile.ZipFile(os.environ["GIGA_WHEEL"]) as archive:
    giga = archive.namelist()
assert any(name.endswith("/firmware/giga_hil/m7/m7.ino") for name in giga)
with zipfile.ZipFile(os.environ["BRIDGE_WHEEL"]) as archive:
    bridge = archive.namelist()
assert any(name.endswith("/firmware/protocol_bridge/release/protocol_bridge_m7.hex") for name in bridge)
PY

for package in core giga bridge; do
  mkdir "$distribution_dir/$package"
done
"$uv_command" pip install --python .venv/bin/python --no-deps \
  --target "$distribution_dir/core" \
  "$core_wheel"
"$uv_command" pip install --python .venv/bin/python --no-deps \
  --target "$distribution_dir/giga" \
  "$core_wheel" "$giga_wheel"
"$uv_command" pip install --python .venv/bin/python --no-deps \
  --target "$distribution_dir/bridge" \
  "$core_wheel" "$giga_wheel" "$bridge_wheel"

DIST_ROOT="$distribution_dir" .venv/bin/python - <<'PY'
import os
from importlib.metadata import distributions
from pathlib import Path

root = Path(os.environ["DIST_ROOT"])
expected = {
    "core": [],
    "giga": ["arduino_giga"],
    "bridge": ["arduino_giga", "giga_protocol_bridge"],
}
for name, wanted in expected.items():
    points = sorted(
        point.name
        for distribution in distributions(path=[str(root / name)])
        for point in distribution.entry_points
        if point.group == "jlink_mcp.extensions"
    )
    assert points == wanted, (name, points)
PY

echo "Validated three independent wheels, contents, installs, and entry points"
