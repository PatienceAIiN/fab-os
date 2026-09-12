# Licensing

## Ownership boundary

| Material | Rights holder | Licence |
|----------|---------------|---------|
| Fab OS brand (name, mark, wordmark), artwork, themes, wallpapers, icon tiles, 3D logo | Patience AI | Apache-2.0 OR CC0-1.0 (artwork); trademark use governed by legal/TRADEMARKS.md |
| Fab OS packaging, build recipe, scripts, tests, documentation | Patience AI | Apache-2.0 |
| Fab OS applications: fabos-agent (daemon, CLI, Command Center, KRunner runner), fabos-updates, fabos-feedback, fabos-welcome, fabos-firstboot | Patience AI | Apache-2.0 |
| Derived colour schemes (Fab Dark / Fab Light, from Breeze) | KDE e.V. (original) + Patience AI (changes) | LGPL-3.0-or-later |
| Length-preserving string patches applied to upstream binaries | upstream authors (binaries) | unchanged upstream licences; the patch tool is Apache-2.0 |
| Everything else on the image | upstream authors | per package (`/usr/share/doc/*/copyright`) |

Patience AI does **not** claim ownership of Ubuntu, KDE, Mozilla or any other upstream code. Copyright notices,
licence files and NOTICE files are preserved on every image; the container base's documentation exclusion is
removed at build so copyright files are always present.

## Obligations Fab OS meets

- **Source offer** for GPL/LGPL packages: legal/SOURCE-OFFER.md, generated manifest per image (scripts/source-offer.sh).
- **Modification disclosure**: legal/PATCHED-BINARIES.md lists every patched file and how to restore it.
- **Trademarks**: Ubuntu (Canonical), KDE/Plasma (KDE e.V.), Firefox (Mozilla) are named only for identification;
  the product identity is Fab OS by Patience AI. Naming clearance status: legal/TRADEMARK-SEARCH.md.
- **Fonts**: OFL fonts are redistributed unmodified with their licence text (THIRD_PARTY_LICENSES/SIL-OFL-1.1.txt).
- **Material Symbols**: Apache-2.0 notice retained (THIRD_PARTY_LICENSES/Apache-2.0.txt).

## Not yet cleared

- Final product name (see legal/TRADEMARK-SEARCH.md).
- HTTPS for the update server (signatures already protect integrity).
