# Trademark search — "Fabric OS" (2026-09-11) and "Fab OS" (2026-09-12)

> **Historical note.** The first section below records the search for the project's ORIGINAL working name,
> **"Fabric OS"**, which is Brocade's registered mark. That finding is what caused the rename to **"Fab OS"**.
> The second section ("Fab OS" / "FabOS") is the search for the current name. Do not read the Brocade finding as
> applying to the name "Fab OS" — it concerns "Fabric OS" only.

## Finding for "Fabric OS": DIRECT CONFLICT — do not ship publicly under that name without legal advice

"Fabric OS" (and "Secure Fabric OS") is a **registered trademark of Brocade
Communications Systems, Inc., now part of Broadcom**, in the United States and
other countries. It is the name of the operating system / firmware for Brocade
Fibre Channel SAN switches and directors. The product is actively maintained
(Fabric OS 10.0.1 was released 25 June 2026) and documented at
techdocs.broadcom.com under "fabric-os".

Both products are *operating system software* (Nice class 9), so this is the
same class, same goods, near-identical mark. That is the textbook fact pattern
for a likelihood-of-confusion claim, regardless of whether Patience AI's use is
non-commercial or open source.

Secondary conflicts to be aware of: "Microsoft Fabric" (analytics platform,
Microsoft), "Fabric" (Meta AI hardware project name), "Hyperledger Fabric".
These are different goods and weaker conflicts, but they add to the noise.

Sources:
- https://en.wikipedia.org/wiki/Fabric_OS
- https://www.broadcom.com/products/fibre-channel-networking/software/fabric-operating-system
- https://techdocs.broadcom.com/us/en/fibre-channel-networking/fabric-os/fabric-os-software-licensing/10-0-x.html

## What this repository does about it

1. The product name lives in exactly one place: `brand/brand.conf`. Every
   package, splash screen, theme, os-release field, GRUB title and document is
   rendered from it. **Renaming the OS is a one-line change plus a rebuild.**
2. Internal identifiers (`DISTRO_ID=fabric`, package prefix `fabric-`) are
   descriptive words, not the registered mark, and are low risk.
3. Until a final name is cleared, treat "Fab OS" as an **internal codename**
   for private testing only. Do not publish ISOs, websites or social accounts
   under it.

## Recommended action before any public release

- Pick a name with no live software registration. Candidates that keep the
  weaving metaphor: **Loom OS**, **Weave OS**, **Patience OS**, **Warp OS**,
  **Tessel OS**. Search each in: USPTO TESS (US), EUIPO eSearch (EU),
  IP India public search (Class 9), WIPO Global Brand Database, plus a plain
  web search for existing Linux distributions and GitHub organisations.
- File an Indian Class 9 trademark application for the chosen name under
  Patience AI. It is inexpensive and creates priority.
- Register the domain and GitHub organisation at the same time.

This file is a research note, not legal advice. Have a trademark attorney
confirm the chosen name before launch.

## Update 2026-09-11 — renamed to "Fab OS"

The product name was changed from "Fabric OS" to **"Fab OS"** (brand/brand.conf). Search result for the new name:

- **"FabOS"** is the name of a German government-funded (BMWi) Industrie 4.0 research project — an "open, distributed,
  real-time capable and secure operating system for production" — run by Fraunhofer IPA, KIT and ~22 partners with the
  Eclipse Foundation (fab-os.org, github.com/FabOS-AI, docs.fab-os.org). It is a factory/production platform, not a
  desktop OS, and we found no registered word mark in the sources checked, but the name is in active public use for
  "an operating system" in the EU.
- No other software product named "Fab OS" was found in the sources checked. Brocade's "Fabric OS" mark is a different
  word and the conflict analysis above no longer applies directly, though similarity remains arguable.

Assessment: **materially lower risk than "Fabric OS", not zero.** Before public release: run the formal searches listed
above for "Fab OS" / "FabOS" (IP India Class 9, EUIPO, USPTO, WIPO), consider "Fab OS by Patience AI" as the
consistent public form, and file an Indian Class 9 application.

Sources: https://www.eclipse.org/research/projects/fab_os/ · https://www.fab-os.org/en/ · https://github.com/FabOS-AI/ · https://docs.fab-os.org/
