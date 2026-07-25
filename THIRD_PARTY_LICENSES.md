# Core third-party license inventory

Repository-authored source is PolyForm Noncommercial 1.0.0. Third-party
components are not relicensed and retain their own terms.

The core machine-readable inventory is
[`sbom/jlink-mcp.cdx.json`](sbom/jlink-mcp.cdx.json); the generated package
table is [`sbom/python-licenses.md`](sbom/python-licenses.md). Regenerate both
with `scripts/generate-sbom.sh` after core dependency changes.

Core runtime dependencies include the MCP Python SDK, HTTPX, Pydantic,
pydantic-settings, pyudev, pyserial, pyelftools, pygdbmi, psutil, Uvicorn, and
their locked transitive dependencies. Optional GUI/test dependencies are
listed in the generated inventory. Debian image components retain their
Debian/upstream licenses.

Extension inventories are maintained separately:

- [`extensions/arduino_giga/THIRD_PARTY_LICENSES.md`](extensions/arduino_giga/THIRD_PARTY_LICENSES.md)
- [`extensions/giga_protocol_bridge/THIRD_PARTY_LICENSES.md`](extensions/giga_protocol_bridge/THIRD_PARTY_LICENSES.md)

SEGGER software is absent from the repository, image, SBOM, and inventory. It
is a separately licensed, user-supplied, read-only runtime mount. Package
metadata is informative and may be imperfect; distributors must review the
license texts shipped by each upstream package.
