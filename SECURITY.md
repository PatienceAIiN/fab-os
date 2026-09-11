# Security policy

Report vulnerabilities privately via the SUPPORT_URL in `brand/brand.conf`.
Do not open public issues for security bugs.

Posture: unmodified Ubuntu kernel and shim (Secure Boot works), AppArmor on,
LUKS full-disk encryption offered by the installer, no snap, no telemetry,
AI is local-first and every model output is treated as untrusted data.
The VM test profile has a known password and autologin and must never be
distributed.
