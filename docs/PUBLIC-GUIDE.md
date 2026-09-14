# Fab OS — public guide

Fab OS is a Linux desktop by Patience AI, based on Ubuntu and KDE Plasma. It
is intended to make useful AI assistance available without making the desktop
harder to understand.

## Product basics

- Familiar Linux desktop foundations.
- Optional, local-first assistance.
- Clear user choice before important work.
- Visible network and privacy expectations.
- Open-source notices and third-party license records with each release.
- Window snapping: drag a window to a side edge for a half, to a corner for a quarter, to the top to maximise;
  hold Shift while dragging to drop it into a tile layout (Meta+T edits layouts, Meta+Arrows quick-tile). After one
  window snaps to a half, Snap Assist shows the other windows so one can be picked for the remaining half.

This guide describes the public product experience. It does not document
private implementation, internal services, unpublished business rules, or
operational credentials.

## Current release status

Fab OS is under development. The public image is not published yet. Before an
image is released, the project must complete real-device validation, recovery
testing, release checksums, software-bill-of-materials records, license review,
security review, and final public notices.

Do not install an unreleased image on a primary computer.

## System requirements

2 GB of RAM (Fab OS uses compressed swap in RAM and starts background services on demand; 4 GB is recommended),
20 GB of disk, a 64-bit processor and UEFI firmware. `docs/LOW-RAM.md` lists the low-memory defaults and how to
change them; file-content search is off until you enable it in Fab Settings → Search.

## Device evaluation

Use a spare device or replaceable test disk. Record the exact model, firmware,
CPU, memory, graphics, storage, network, audio, sleep, update, shutdown, and
recovery results. A device is not supported merely because it boots once.

## Privacy and security

The public principles are minimal collection, user control, limited access,
clear network indicators, and recovery where supported. The final release
notice must identify the actual services, data practices, retention periods,
support contact, and applicable legal basis.

Shipped defaults: the firewall is on (ufw — incoming connections denied, outgoing allowed, no SSH server in the
image), AppArmor is on, full-disk encryption is pre-selected in the installer, updates are signed (Ubuntu's keys and
the Fab OS Archive key), Fab OS never downloads anything on first use (models, voices, fonts and icons are shipped
inside the ISO release), there is no telemetry, and the built-in agent stays off until a provider key is added and asks
before risky steps.

## Licensing

Patience AI code and artwork carry their stated licenses. Ubuntu, Linux, KDE
Plasma, firmware, fonts, and other third-party components keep their own
licenses and notices. Read `LICENSE`, `NOTICE`, `legal/THIRD-PARTY.md`, and
the relevant upstream terms before redistribution.

## Contact

Patience AI: [info@patienceai.in](mailto:info@patienceai.in)
