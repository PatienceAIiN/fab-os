# ADR-0006: Signed apt repository with Standard/Beta channels, automatic updates on by default

## Selected
- Repository at fabos.patienceai.in/apt, signed with the Fab OS Archive key (ed25519). Suites: `loom`
  (Standard) and `loom-beta` (Beta), separate pools. CI publishes main → beta, tags v* → stable.
- Clients ship `fabos.sources` (Standard) and the keyring. `fabos-updates` switches suites, checks and
  installs (pkexec + polkit), and configures unattended-upgrades for "Patience AI" origins and Ubuntu security.
  A daily timer notifies logged-in users when updates are waiting. Automatic installation is ON by default.
- Ubuntu updates keep flowing from Ubuntu unchanged; Firefox from Mozilla's repository (pinned).

## Tradeoffs
HTTP transport until a certificate is issued for the host (signatures protect integrity; privacy of package
names is not protected). Beta and Standard currently share version numbers; bump DISTRO_VERSION per release.
