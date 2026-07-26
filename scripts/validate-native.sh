#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

scripts/validate-native-static.sh
scripts/test-core-native.sh
scripts/test-arduino-giga-native.sh
scripts/test-protocol-bridge-native.sh
scripts/validate-sboms.sh
scripts/validate-distributions.sh
scripts/validate-core-neutrality.sh
scripts/validate-proprietary-artifacts.sh
echo "Completed hardware-free native validation"
