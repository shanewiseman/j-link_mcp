#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

if find . \
  -path ./.git -prune -o \
  -path ./.venv -prune -o \
  -path ./.ci-cache -prune -o \
  -path ./state -prune -o \
  -path ./artifacts -prune -o \
  -type f \( -iname 'JLinkExe*' -o -iname 'JLinkGDBServer*' -o \
  -iname 'libjlinkarm*' -o -iname 'JLinkARM.dll' -o -name '*.so' \) \
  -print | grep -q .; then
  echo "Unexpected binary or proprietary SEGGER artifact in publishable tree" >&2
  exit 1
fi
echo "Validated absence of proprietary SEGGER artifacts"
