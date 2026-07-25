#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output="$repository_dir/.env.hardware"
{
  echo "HOST_UID=$(id -u)"
  echo "HOST_GID=$(id -g)"
  echo "PLUGDEV_GID=$(getent group plugdev | cut -d: -f3)"
  echo "SEGGER_ROOT=${SEGGER_ROOT:-/opt/SEGGER/JLink_V962}"
} >"$output"
chmod 600 "$output"
echo "$output"
