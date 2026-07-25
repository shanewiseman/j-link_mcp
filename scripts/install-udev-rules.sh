#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_rule="$repository_dir/config/59-jlink-mcp.rules"
destination=/etc/udev/rules.d/59-jlink-mcp.rules

sudo -v
sudo install -o root -g root -m 0644 "$source_rule" "$destination"
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
sudo udevadm settle

failed=0
for node in /dev/bus/usb/*/*; do
  [ -c "$node" ] || continue
  vendor=$(udevadm info --query=property --name="$node" 2>/dev/null \
    | sed -n 's/^ID_VENDOR_ID=//p' | head -n 1)
  [ "$vendor" = 1366 ] || continue
  mode=$(stat -c %a "$node")
  group=$(stat -c %G "$node")
  if [ "$mode" != 660 ] || [ "$group" != plugdev ]; then
    echo "FAIL $node is $mode:$group; expected 660:plugdev" >&2
    failed=1
  else
    echo "PASS $node is $mode:$group"
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "The restrictive J-Link udev policy did not take effect." >&2
  exit 1
fi
echo "Installed and verified $destination."
