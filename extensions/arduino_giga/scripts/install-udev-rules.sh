#!/bin/sh
set -eu

extension_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_rule="$extension_dir/config/58-jlink-mcp-arduino-giga.rules"
destination=/etc/udev/rules.d/58-jlink-mcp-arduino-giga.rules

sudo -v
sudo install -o root -g root -m 0644 "$source_rule" "$destination"
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
  [ "$vendor" = 2341 ] && verify_node "$node" plugdev
done
for node in /dev/ttyACM* /dev/ttyUSB*; do
  [ -c "$node" ] || continue
  case "$node" in
    /dev/ttyUSB*) verify_node "$node" dialout ;;
    /dev/ttyACM*)
      vendor=$(udevadm info --query=property --name="$node" 2>/dev/null \
        | sed -n 's/^ID_VENDOR_ID=//p' | head -n 1)
      [ "$vendor" = 2341 ] && verify_node "$node" dialout
      ;;
  esac
done

if [ "$failed" -ne 0 ]; then
  echo "The restrictive GIGA udev policy did not take effect." >&2
  exit 1
fi
echo "Installed and verified $destination."
