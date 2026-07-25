# Licensing and distribution boundary

Repository-authored source and build definitions are available under the
PolyForm Noncommercial License 1.0.0 (`PolyForm-Noncommercial-1.0.0`); see
`LICENSE`. It permits copying,
modification, and distribution for noncommercial purposes. It does not license
sale, resale, commercial productization, or use in a revenue-generating
product, service, workflow, or business model. This is a source-available
license, not an OSI-approved open-source license.

Python and system dependencies retain their own licenses; the CycloneDX SBOM
and generated Python inventory are under `sbom/`, with a summary in
`THIRD_PARTY_LICENSES.md`. The repository's license does not replace or narrow
rights granted directly by third-party licensors.

SEGGER J-Link Software, firmware, manuals, Ozone, SystemView, and J-Link SDK are
not part of this project and are not redistributed. Users install and license
them separately. Compose mounts an architecture-matched J-Link 9.62 directory
read-only at `/opt/segger/JLink`; settings, logs, screenshots, and generated
command files go to separate writable state. `.dockerignore` excludes local
SEGGER directories and state from the image context.

The J-Link EDU Mini is licensed by SEGGER for qualifying non-profit
educational use. The source license does not authorize commercial use of that
probe or of SEGGER software. Commercial users must choose suitable licensed
hardware and software.

Arduino CLI, the Arduino Mbed GIGA core, GNU Arm tools, OpenOCD, DFU tools,
imgtool, SVD files, and Arduino bootloader assets are fetched into the local
container build from their upstream distribution. They are used as toolchain
and validation inputs and retain upstream terms. Review the SBOM and upstream
notices before distributing a built image.

Direct J-Link SDK integration is intentionally unavailable. Supplying SDK
headers/binaries later requires a separate valid license, read-only external
mount, and an updated third-party review; they must never be committed.
