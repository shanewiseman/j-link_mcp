# Third-party license inventory

Repository-authored source is PolyForm Noncommercial 1.0.0. Third-party
components are not relicensed and retain their own terms.

The authoritative machine-readable inventory is
[`sbom/jlink-mcp.cdx.json`](sbom/jlink-mcp.cdx.json). The generated Python
package table is [`sbom/python-licenses.md`](sbom/python-licenses.md). Regenerate
both with `scripts/generate-sbom.sh` after dependency changes.

The protocol-bridge Arduino library inventory is
[`sbom/arduino-libraries.cdx.json`](sbom/arduino-libraries.cdx.json):
`Arduino_USBHostMbed5@0.3.1` (Apache-2.0), `ArduinoBLE@2.1.0`
(LGPL-2.1-only), and its explicitly pinned `Arduino_SpiNINA@0.0.2` dependency
(MPL-2.0). Their upstream license files are installed by Arduino CLI in the
container; they are not copied into this repository.

Runtime Python dependencies include the MCP Python SDK (MIT), HTTPX (BSD),
Pydantic and pydantic-settings (MIT), pyudev (LGPL-2.1+), pyserial (BSD),
pyelftools (public domain), pygdbmi (MIT), psutil (BSD-3-Clause), Uvicorn
(BSD-3-Clause), OpenCV Python (Apache-2.0), Pillow (MIT-CMU), and their locked
transitive dependencies. Test/SBOM tools are separately listed in the
generated table.

The Debian base image supplies Xvfb, AT-SPI, D-Bus, Openbox, xdotool,
Tesseract, ImageMagick/scrot, noVNC/websockify, libusb, tini, and supporting
libraries under their Debian/upstream licenses. Arduino CLI, the pinned
`arduino:mbed_giga@4.6.0` platform, GNU Arm toolchain, OpenOCD, dfu-util,
imgtool, SVD files, and bootloader assets retain upstream licenses.

SEGGER software is deliberately absent from the repository, image, SBOM, and
inventory because it is not distributed by this project. It is a separately
licensed user-supplied read-only runtime mount. See
[`docs/licensing.md`](docs/licensing.md).

Package metadata is informative and can be imperfect. Distributors must review
the license texts shipped by each upstream package and the Debian image.
