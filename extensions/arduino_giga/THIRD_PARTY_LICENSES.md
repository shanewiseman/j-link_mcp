# Arduino GIGA extension third-party inventory

This extension depends on the separately installed `jlink-mcp` core and the
pinned Arduino CLI 1.5.1 plus `arduino:mbed_giga@4.6.0`. That platform brings
the GNU Arm toolchain, OpenOCD, dfu-util, imgtool, board definitions, SVDs, and
bootloader assets under their upstream licenses.

Machine-readable pins are in `sbom/arduino-platform.cdx.json`. The platform and
tools are downloaded while building the optional overlay; their license files
remain with the installed distribution and are not relicensed here. SEGGER
software remains user-supplied and is not part of this inventory.
