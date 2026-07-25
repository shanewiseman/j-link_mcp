# Security model

## Trust boundaries

The MCP client and configured workspace are trusted to request target changes.
The network, raw tool inputs, artifact paths, USB enumeration, debugger output,
and GUI state are untrusted. SEGGER programs are trusted only as locally
installed licensed binaries mounted read-only.

The service intentionally exposes destructive target operations without
per-command confirmation after one-time setup. Safety comes from loopback
binding, bearer authentication, stable physical selection, live positive
target identity, exclusive leases, path confinement, fixed executable
allowlists, and immutable audit evidence—not from interactive prompts.

## Container controls

- Runs as the host UID/GID, never root.
- `cap_drop: [ALL]`, read-only root filesystem, no privileged mode.
- USB/ACM/USB-serial cgroup classes only: majors 189, 166, and 188.
- `/dev/bus/usb` is writable for libusb; host `/dev` and USB sysfs are
  read-only discovery views.
- Workspace and state are the only writable bind mounts; `/tmp` and `/run` are
  size-limited `noexec,nosuid,nodev` tmpfs mounts.
- SEGGER, Ozone, and SystemView (when configured) are read-only mounts.
- The service listens at `127.0.0.1:8000`; health is public but all MCP traffic
  requires a constant-time bearer-token comparison.
- Compose normally enables `no-new-privileges`. `compose.snap.yaml` removes
  only that option because the Canonical snap runtime rejects initial exec;
  all other controls remain. Prefer standard Docker Engine where possible.

## Raw-input policy

No backend uses `shell=True`. Commander command names must be syntactically
valid and cannot contain `;&|` backticks, substitutions, redirection, control
characters, or unconstrained paths. GDB accepts a strict MI/CLI allowlist,
supports debugger expressions, and rejects shell, Python, Guile, source,
pipe, command-definition, environment, sysroot, and executable-loading
escapes. `-interpreter-exec` is limited to quoted `monitor` commands.

Application names come from the installed SEGGER tool allowlist. Each path
operand—also `--option=/path` forms—must resolve beneath workspace or state.
Symlink resolution is strict for existing paths, preventing traversal.

## Target-changing policy

Before Commander, GDB, GUI, or destructive SEGGER application use, the service
checks the selected probe serial, SW-DP ID, core CPUID, and VTref >= 1.0 V.
Ambiguous selection and stale/unverified board identities fail closed. The
primary GIGA suite never tests irreversible readout protection, option-byte
provisioning, or mass erase; those paths are covered with mocks only.

## Secrets and evidence

`.token`, `.env.hardware`, local state, and SEGGER runtime copies are ignored
by Git and excluded from the Docker build context. Token files are mode 0600.
Audits deliberately record commands and hashes but callers must not place
secrets in firmware filenames or raw debug commands. The audit hash chain
detects mutation; it is not a substitute for signing or off-host retention.

Report suspected vulnerabilities privately to the repository owner. Rotate
the token, stop the container, preserve `state/`, and verify the audit chain
before restarting after an incident.
