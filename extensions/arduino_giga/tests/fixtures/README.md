# Synthetic ELF fixture

`synthetic_fixture.elf` is a host-only test asset. It contains no SEGGER,
Arduino, target, or proprietary payload. Keeping the generated ELF beside its
source makes the artifact parser tests deterministic in the lean Jenkins image,
which intentionally has no native compiler or linker.

Regenerate it from this directory on an x86-64 Linux host:

```sh
cc -c -fno-asynchronous-unwind-tables -fno-stack-protector \
  synthetic_fixture.c -o /tmp/jlink-mcp-synthetic-fixture.o
ld --build-id=none -T synthetic_fixture.ld \
  /tmp/jlink-mcp-synthetic-fixture.o -o synthetic_fixture.elf
```

The checked-in fixture SHA-256 is
`6db9890bacd52ab4bdbd8f24ef7f4487421395e5b039eabedaa9bcc4d9bd8112`.
