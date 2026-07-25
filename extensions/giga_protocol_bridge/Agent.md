# GIGA protocol-bridge extension agent contract

Read the root and `extensions/arduino_giga/Agent.md` contracts first. This
extension owns bridge wire/models/resources/backend, firmware and checked
release, deployment and MCP tools, pinned libraries, profiles, docs, and HIL.

Preserve bridge tool names and request schemas. Canonicalize and bound all
base64 payloads, reject resource conflicts, protect reserved GPIO, serialize
exchanges, and never infer that a physical peer exists. Wi-Fi/BLE secrets come
only from a mode-`0600` profile file and must not enter logs, audit requests, or
artifacts.

Bridge deployment is destructive. It requires the Arduino GIGA extension,
positive M7 identity, an exclusive probe lease, a full authorized flash backup,
the checked release checksum, a successful serial handshake, and unconditional
original-firmware restoration for validation runs. Physical SPI/I2C/UART/CAN/
USB/Wi-Fi/BLE/GPIO acceptance runs only for explicitly described fixtures; list
unavailable fixture reasons instead of claiming coverage.

Release changes must rebuild deterministically with the pinned platform and
libraries, update HEX/manifest/SHA256SUMS together, verify the wheel contains
them, and record commands and hashes in the detailed commit message.
