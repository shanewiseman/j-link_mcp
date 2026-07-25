# Protocol-bridge third-party inventory

The build requires the Arduino GIGA extension stack plus these pinned Arduino
libraries:

- `Arduino_USBHostMbed5@0.3.1` — Apache-2.0
- `ArduinoBLE@2.1.0` — LGPL-2.1-only
- `Arduino_SpiNINA@0.0.2` — MPL-2.0

The machine-readable inventory is `sbom/arduino-libraries.cdx.json`. Upstream
license files are installed with the libraries in the optional overlay and are
not relicensed by this repository. SEGGER software is user-supplied and absent
from the extension distribution.
