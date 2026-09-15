# Third-party licence texts

Verbatim licence texts for the third-party components that Fab OS redistributes or derives from. Which
component is under which licence is listed in `../ATTRIBUTIONS.md`; on an installed system every package's
own notice is in `/usr/share/doc/<package>/copyright` (never excluded from Fab OS images).

| File | Licence | Used by (examples) |
|------|---------|--------------------|
| `Apache-2.0.txt` | Apache License 2.0 | Google Material Symbols glyphs (FabOS icon theme); Inter (dual OFL-1.1 / Apache-2.0); also Fab OS's own code |
| `GPL-2.0.txt` | GNU GPL v2 | Linux kernel; KDE applications and Plasma components licensed GPL-2.0-or-later; SDDM; Plymouth; casper; PackageKit |
| `GPL-3.0.txt` | GNU GPL v3 | Calamares (GPL-3.0-or-later); PyQt6; GRUB; eSpeak NG (GPL-3.0-or-later, offline voice of fabos-voice) |
| `LGPL-2.0.txt` | GNU Library GPL v2 | bubblewrap (LGPL-2.0-or-later, the agent's shell sandbox); polkit / pkexec (LGPL-2.0-or-later, the agent's root path) |
| `LGPL-2.1.txt` | GNU LGPL v2.1 | KDE Frameworks, KWallet and other LGPL-2.1-or-later libraries; systemd |
| `LGPL-3.0.txt` | GNU LGPL v3 | KDE Breeze icon theme, Breeze style and colour schemes (basis of Fab Dark / Fab Light, which stay LGPL-3.0-or-later) |
| `MPL-2.0.txt` | Mozilla Public License 2.0 | Firefox (Mozilla's unmodified .deb from packages.mozilla.org; ADR-0018 — the same text covered the browser of the 1.0-3/1.0-4 images, ADR-0016) |
| `MIT.txt` | MIT / Expat | llama.cpp; anthropic Python SDK; whisper.cpp and the Whisper `tiny.en` ggml model shipped in `/usr/share/fabos/voice` |
| `Qwen2.5-LICENSE.txt` | Apache License 2.0 with the upstream copyright notice "Copyright 2024 Alibaba Cloud" | Qwen2.5-1.5B-Instruct GGUF model weights, the built-in offline model (`/usr/share/fabos/models/`); verbatim `LICENSE` of huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF |
| `BSD-2-Clause.txt` | BSD 2-Clause "Simplified" | PocketSphinx and the `pocketsphinx-en-us` acoustic model, dictionary and language model (Carnegie Mellon University) |
| `SIL-OFL-1.1.txt` | SIL Open Font License 1.1 | Inter, JetBrains Mono, Noto Sans |
| `CC0-1.0.txt` | CC0 1.0 Universal | Fab OS artwork may additionally be used under CC0 (see `../legal/ARTWORK.md`) |

Texts were taken from `/usr/share/common-licenses/` of the Ubuntu 26.04 base (Debian `base-files`) except
`MIT.txt` (Expat wording as used in the Debian copyright files of llama.cpp and python3-anthropic) and
`SIL-OFL-1.1.txt` (OFL 1.1 text as published by SIL International) and `BSD-2-Clause.txt` (the licence block of the
Debian copyright file of pocketsphinx 5.0.4, i.e. the SPDX BSD-2-Clause text with CMU's copyright line).
