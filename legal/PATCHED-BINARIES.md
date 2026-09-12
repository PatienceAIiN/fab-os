# Modified upstream binaries (string rebranding)

Fab OS hides upstream vendor names from the user interface. Where a name is embedded in a compiled file rather
than a configuration or desktop entry, `/usr/lib/fabos/rebrand-binaries` (package `fabos-desktop`) rewrites the
display string in place with a **byte-length-preserving** replacement (for example `KDE Wallet` → `Fab Wallet`,
`Konsole` → `Console`). Rules and the exact file list are in that script.

Facts and obligations:

- The affected packages are free software (LGPL-2.1+/GPL-2.0+: KWallet, KWalletManager, KInfoCenter, KXmlGui,
  Konsole). Modifying and redistributing them is permitted; the licences are unchanged and the copyright notices
  in `/usr/share/doc/*/copyright` are untouched.
- Every patched file keeps its original next to it as `<file>.fabos-orig`; deleting the patched copy and renaming
  the original restores upstream behaviour. `dpkg --verify` reports these files as modified, which is expected.
- Corresponding source for the unmodified packages is offered under `legal/SOURCE-OFFER.md`; the modification
  itself is this script, distributed in source form in the `fabos-desktop` package and this repository.
- Only display strings are changed. No functional identifiers (D-Bus names, symbols, file paths, config keys)
  are touched: UTF-8 replacements in ELF files require a space in the pattern so mangled symbols cannot match,
  and UTF-16 replacements cannot appear in symbol tables at all.
- Upstream projects (KDE e.V., Konsole authors) are credited in `legal/THIRD-PARTY.md`; Fab OS does not claim
  authorship of these programs.
