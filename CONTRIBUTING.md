# Contributing

- Identity strings come from `brand/brand.conf`; never hard-code the OS name.
- Every heavy command (podman build, QEMU, cargo) runs under `tools/rg`.
- Add or update an ADR in `docs/decisions/` for architectural changes.
- Packages must install and remove cleanly on stock Ubuntu 26.04 too.
- No Canonical artwork or trademarks, ever. Run `tests/branding-check.sh`.
- Be kind; the code of conduct is the Contributor Covenant 2.1.
