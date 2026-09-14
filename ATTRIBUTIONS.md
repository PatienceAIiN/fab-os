# Attributions

Fab OS is built from free software. Patience AI owns only what it created (branding, artwork, packaging,
Fab OS applications, the agent, documentation). Everything below keeps its own copyright and licence; the
texts are in `THIRD_PARTY_LICENSES/` (index: `THIRD_PARTY_LICENSES/README.md`) and, on an installed system, in
`/usr/share/doc/<package>/copyright`.

| Component | Source | Licence | Copyright | Modified by Fab OS? | Attribution / redistribution duty |
|-----------|--------|---------|-----------|---------------------|-----------------------------------|
| Ubuntu 26.04 LTS base system, Linux kernel | ubuntu.com | GPL-2.0, LGPL, others per package | Canonical Ltd. and upstream authors | No (unmodified packages) | offer corresponding source (legal/SOURCE-OFFER.md); do not use the Ubuntu trademark for the product |
| KDE Plasma 6, KWin, KDE Frameworks 6, System Settings, Discover, Dolphin, Konsole, Kate, Gwenview, Okular, Ark, Spectacle, KCalc, KWallet, KInfoCenter, SDDM theme components | kde.org | GPL-2.0-or-later / LGPL-2.1-or-later | KDE e.V. and contributors | Yes: display strings only, length-preserving (legal/PATCHED-BINARIES.md); launcher names overridden via desktop-entry overrides | keep copyright notices (kept); offer source (kept); document modifications (done) |
| SDDM | github.com/sddm | GPL-2.0-or-later | SDDM contributors | No; Fab OS ships its own greeter theme | keep notices |
| Plymouth | freedesktop.org | GPL-2.0-or-later | Red Hat and contributors | No; Fab OS theme only | keep notices |
| Calamares | calamares.io | GPL-3.0-or-later | Calamares contributors | No; Fab OS branding/config only | keep notices |
| casper (live boot) | Ubuntu | GPL-2.0-or-later | Canonical Ltd. | No | keep notices |
| Firefox | Mozilla | MPL-2.0 (THIRD_PARTY_LICENSES/MPL-2.0.txt) | Mozilla Foundation | No: Mozilla's own unmodified `.deb` from packages.mozilla.org, signing key pinned by fingerprint, apt pin 1000; never renamed by Fab OS catalogs or desktop-entry overrides | Firefox name/logo are Mozilla trademarks; the application keeps its own branding; the FabOS launcher tile uses a generic globe glyph, not the Firefox logo |
| PackageKit (apt backend) | freedesktop.org | GPL-2.0-or-later | PackageKit authors | Yes: the origin display label, length-preserving byte patch (legal/PATCHED-BINARIES.md) | keep notices (kept); offer source (kept); document modification (done) |
| GRUB 2 (signed), shim-signed, casper (ISO profile) | GNU / Red Hat / Ubuntu | GPL-3.0-or-later (GRUB), BSD-2-Clause (shim), GPL-2.0-or-later (casper) | respective authors | No | keep notices; offer source |
| llama.cpp | github.com/ggml-org/llama.cpp | MIT (THIRD_PARTY_LICENSES/MIT.txt) | 2023-2026 The ggml authors | No | keep notice |
| systemd-zram-generator (swap on zram, docs/LOW-RAM.md) | github.com/systemd/zram-generator | MIT (THIRD_PARTY_LICENSES/MIT.txt) | zram-generator contributors | No; Fab OS ships its own `/etc/systemd/zram-generator.conf` | keep notice |
| Inter typeface | rsms.me/inter | SIL OFL 1.1 (also offered under Apache-2.0 upstream) | 2016-2023 The Inter Project Authors | No | keep OFL notice; do not sell the font alone |
| JetBrains Mono | jetbrains.com/mono | SIL OFL 1.1 | 2020 JetBrains s.r.o. | No | as above |
| Noto Sans | Google | SIL OFL 1.1 | Google | No | as above |
| Google Material Symbols (icon glyphs) | github.com/google/material-design-icons | Apache-2.0 | Google LLC | Yes: glyphs placed on Fab OS colour tiles | keep Apache notice (THIRD_PARTY_LICENSES/Apache-2.0.txt) |
| Breeze icon theme (fallback icons), Breeze style, Breeze colour schemes (basis of Fab Dark/Light) | kde.org | LGPL-3.0-or-later | KDE e.V. | Colour schemes derived (renamed, recoloured) | keep notices; derived schemes stay LGPL-3.0-or-later |
| Python 3, PyQt6 | python.org, Riverbank | PSF-2.0 / GPL-3.0 | respective authors | No | Fab OS apps using PyQt6 are Apache-2.0 source distributed alongside; GPL applies to the combined binary distribution of PyQt6 |
| anthropic Python SDK | Anthropic | MIT (THIRD_PARTY_LICENSES/MIT.txt) | 2023 Anthropic, PBC | No | keep notice |
| ai-native-os agent stack (aios, aiosd) | Patience AI | Apache-2.0 | Patience AI | Fab OS packaging | — |

Fab OS-created material (Apache-2.0; artwork additionally CC0-1.0): brand assets (identity icon, avatar, lockups),
Plymouth/SDDM/KSplash themes, FabOS Plasma theme, FabOS icon tiles, wallpapers, 3D logo, packaging, build scripts,
fabos-agent, Command Center, Updates, Feedback, Welcome, website, documentation. Copyright © 2026 Patience AI where
so marked. The Plasma wallpaper package (`/usr/share/wallpapers/FabOS`, Name "Fab OS", Author "Patience AI") declares
**Apache-2.0** as its licence; see legal/ARTWORK.md.
