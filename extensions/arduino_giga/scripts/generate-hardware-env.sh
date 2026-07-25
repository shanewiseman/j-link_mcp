#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
giga_tty=""
for candidate in /dev/serial/by-id/* /dev/ttyACM*; do
  [ -e "$candidate" ] || continue
  if udevadm info --query=property --name="$candidate" 2>/dev/null \
      | grep -q '^ID_VENDOR_ID=2341$'; then
    giga_tty=$(readlink -f "$candidate")
    break
  fi
done

if [ -z "$giga_tty" ]; then
  echo "No Arduino VID 2341 serial device was found." >&2
  exit 1
fi

output="$repository_dir/.env.hardware"
{
  echo "HOST_UID=$(id -u)"
  echo "HOST_GID=$(id -g)"
  echo "PLUGDEV_GID=$(getent group plugdev | cut -d: -f3)"
  echo "DIALOUT_GID=$(getent group dialout | cut -d: -f3)"
  echo "SEGGER_ROOT=${SEGGER_ROOT:-/opt/SEGGER/JLink_V962}"
  echo "TEST_TARGET_DISPOSABLE=${TEST_TARGET_DISPOSABLE:-false}"
} >"$output"
chmod 600 "$output"
echo "$output"
