# Support matrix

## Core

| Area | Support |
|---|---|
| Host | Linux x86-64, cgroup v2, Docker Compose v2 |
| Runtime | Python 3.12; Streamable HTTP and stdio proxy |
| Probe | SEGGER J-Link discovered by stable serial; actual model capabilities are reported dynamically |
| Target | Any target supplied by an enabled API-v1 target-profile extension |
| Interface | Profile-defined; SWD is the default |
| Atomic operations | Connect/state, memory/registers, break/watch, flash/verify, GDB, RTT/SWO, serial, applications, GUI |
| Generic workflows | Flash, explicit-address BIN, backup, comparison, bounded region comparison, restore, validation report |
| SDK | Reported unavailable unless a separately licensed integration is supplied |

Core-only has no board detector or target profile by design. Probe discovery,
dependency reporting, audit, and non-target operations remain available.

## First-party optional extensions

| Extension | Certified baseline | Documentation |
|---|---|---|
| `arduino_giga` | Arduino GIGA R1 WiFi, STM32H747 M7/M4, Arduino platform 4.6.0 | [README](../extensions/arduino_giga/README.md) |
| `giga_protocol_bridge` | GIGA bundle plus its pinned bridge libraries and checked release | [README](../extensions/giga_protocol_bridge/README.md) |

Unavailable capabilities include a precise dependency or physical-fixture
reason. Optional GUI products, probe licenses, SWO wiring, and protocol peers
are never inferred merely because software is installed.
