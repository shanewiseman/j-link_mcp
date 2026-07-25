# Example prompts

Core-only examples:

- “Call dependency doctor and capabilities, then summarize what this J-Link can
  do without changing the target.”
- “Using target profile `example_target`, stable probe serial `…`, and board
  serial `…`, connect and report voltage, DP ID, core, and CPUID.”
- “Back up this profile's application range, hash it, and retain the operation
  and artifact IDs. Do not flash anything.”
- “Inspect this ELF and explain its loadable segments and MCP fixture symbols.”
- “Verify the audit chain and generate a validation report.”

Prompts for first-party hardware bundles are maintained in their extension
documentation. Name the extension and require doctor/capabilities/preflight
before changing hardware.
