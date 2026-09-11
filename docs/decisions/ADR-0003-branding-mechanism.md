# ADR-0003: Branding via dpkg-divert, os-release ID=fabric, single brand.conf

## Problem
Identity files (`/usr/lib/os-release`, `/etc/lsb-release`, `/etc/issue`,
`/etc/legal`, MOTD, dpkg origin) are owned by Ubuntu's `base-files`, which is
Essential and updated by Canonical. Overwriting them breaks on the next update;
forking base-files means rebuilding an Essential package forever (what Mint
does).

## Selected
`fabric-branding` ships its rendered files under `/usr/lib/fabric/` and in
postinst uses `dpkg-divert --rename` on each Ubuntu file, then symlinks ours
into place. Ubuntu updates land on the diverted path and never clobber us;
`prerm` reverses it cleanly. All values render from `brand/brand.conf`, so a
rename is one line.

os-release: `ID=fabric`, `ID_LIKE="ubuntu debian"`, `VERSION_CODENAME=loom`,
`UBUNTU_CODENAME=resolute` (keeps PPAs and distro-info working), `LOGO=fabric-os`
(picked up by KDE About). `/etc/upstream-release/lsb-release` carries Ubuntu's
values for tools that follow the Mint convention.

## Tradeoffs
A handful of third-party scripts test `ID=ubuntu` literally and will need
`ID_LIKE` awareness; this is the same situation as Mint/Pop!_OS and is well
understood.
