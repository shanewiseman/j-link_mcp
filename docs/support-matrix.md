# Capability and support matrix

| Area | Certified baseline | Behavior outside baseline |
|---|---|---|
| Host | Linux x86-64, cgroup v2 | Reported unavailable; no false claim |
| Container | Docker Engine/Compose | Snap overlay documented; no privileged mode |
| MCP SDK | Python SDK 1.27.2, `<2` | v2 requires controlled migration |
| SEGGER pack | J-Link 9.62 x86-64 | Upgrade requires parser, headless, GUI/OCR regression |
| Probe | EDU Mini V2, serial-selected | Other J-Links discovered; limits reported dynamically |
| Target | Arduino GIGA R1, STM32H747XI M7/M4 | Generic profiles can be added through adapter contract |
| Interface | SWD up to detected/model limit | JTAG only when target/profile/probe support it |
| Flash inputs | ELF, HEX, explicit-address BIN | Other address-bearing formats via validated raw tool |
| Debug | Commander and managed GDB/MI | Unsupported commands return validator/dependency reason |
| RTT | Managed GDB channel 0 with ELF-derived control block; Logger for channels 1-15 | Unavailable without GDB Server/Logger or `_SEGGER_RTT` |
| SWO/ITM | EDU Mini maximum 4 MHz | Physical wire is unknown until capture |
| ETM trace | Not supported by EDU Mini | Explicitly unavailable |
| Target power | Not supported by EDU Mini | GIGA requires separate USB power |
| Serial | USB CDC correlated by Arduino serial | Fails closed on ambiguity or inaccessible node |
| Protocol bridge | Versioned M7 firmware; SPI, I²C, UART, classic CAN, USB host, Wi-Fi station, BLE central, GPIO | Physical protocol capability remains unknown until its wired companion HIL passes |
| Bridge wire | COBS/CRC-32/TLV; 4-KiB frames, 64-KiB assembly, 64,000-byte application payload | Unknown, duplicate, malformed, stale, or unsupported inputs fail closed |
| CAN | Two classic CAN controllers with external transceivers and termination | CAN FD is not advertised |
| Wireless | Wi-Fi client sockets or BLE central, one radio mode at a time | No raw 802.11, AP/server, persistent bonding, or BLE peripheral role |
| GUI | Xvfb, AT-SPI, xdotool, OCR, OpenCV | Optional noVNC diagnostic profile |
| Ozone/SystemView | Optional read-only mounts | Precise missing/unlicensed reason |
| J-Link SDK | Adapter contract only | Disabled until separately licensed package is supplied |

The runtime capability manifest is authoritative. It reports the actual probe
model, firmware, licenses, installed executables/versions, selected target and
core, interfaces/speeds, workflows, trace paths, and a reason/dependency list
for every unavailable feature.

The EDU Mini certification assumes qualifying non-profit educational use.
Commercial use requires an appropriately licensed probe and SEGGER software.
