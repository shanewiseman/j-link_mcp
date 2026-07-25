# Security model

The service is designed for a local trusted operator, not exposure to an
untrusted network. It binds to loopback, requires a bearer token, runs non-root,
drops Linux capabilities, uses `no-new-privileges`, and keeps the root
filesystem read-only apart from explicit workspace/state/tmpfs mounts.

Target-changing operations require a registered profile, stable selection,
live positive identity, and an exclusive probe lease. Raw Commander, GDB, and
SEGGER application inputs use finite validators, reject shell/host-code escape
facilities, bound counts and sizes, and confine file operands to workspace or
state. Audit entries include failures and form a verifiable hash chain.

SEGGER software is proprietary, user-supplied, and mounted read-only. It is not
copied into source, wheels, images, SBOMs, or reports.

Extensions are a trust boundary: an installed extension is inert until
allowlisted, but once activated it executes as trusted in-process Python with
the service's authority. The allowlist does not sandbox it. Review extension
code and dependencies, pin versions, restrict configuration to mode `0600`,
and enable only known IDs. Process isolation is deferred beyond API version 1.

Physical safety remains operator-owned. A profile proves digital identity, not
wiring correctness, voltage-domain compatibility, current limits, radio policy,
or the presence of external transceivers. Follow extension-specific contracts
before connecting or driving hardware.
