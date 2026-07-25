#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_rule="$repository_dir/config/59-jlink-mcp.rules"
destination=/etc/udev/rules.d/59-jlink-mcp.rules
legacy_destination=/etc/udev/rules.d/99-jlink-mcp.rules

sudo -v
sudo install -o root -g root -m 0644 "$source_rule" "$destination"
if [ -e "$legacy_destination" ] || [ -L "$legacy_destination" ]; then
  sudo rm -f -- "$legacy_destination"
fi
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --subsystem-match=tty
sudo udevadm settle

failed=0
verify_node() {
  node=$1
  expected_group=$2
  mode=$(stat -c %a "$node")
  group=$(stat -c %G "$node")
  if [ "$mode" != 660 ] || [ "$group" != "$expected_group" ]; then
    echo "FAIL $node is $mode:$group; expected 660:$expected_group" >&2
    failed=1
  else
    echo "PASS $node is $mode:$group"
  fi
}

for node in /dev/bus/usb/*/*; do
  [ -c "$node" ] || continue
  vendor=$(udevadm info --query=property --name="$node" 2>/dev/null \
    | sed -n 's/^ID_VENDOR_ID=//p' | head -n 1)
  case "$vendor" in
    1366|2341) verify_node "$node" plugdev ;;
  esac
done

for node in /dev/ttyACM* /dev/ttyUSB*; do
  [ -c "$node" ] || continue
  case "$node" in
    /dev/ttyUSB*) verify_node "$node" dialout ;;
    /dev/ttyACM*)
      vendor=$(udevadm info --query=property --name="$node" 2>/dev/null \
        | sed -n 's/^ID_VENDOR_ID=//p' | head -n 1)
      if [ "$vendor" = 2341 ]; then
        verify_node "$node" dialout
      fi
      ;;
  esac
done

if [ "$failed" -ne 0 ]; then
  echo "The restrictive udev policy did not take effect. Reconnect the affected devices and rerun this installer." >&2
  exit 1
fi

echo "Installed and verified $destination."
