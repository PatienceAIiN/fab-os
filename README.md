<div align="center">

# Fab OS

**An agentic desktop operating system by [Patience AI](https://patienceai.in).**
Ubuntu underneath, so everything Linux already works. A built-in agent on top, so the computer does the work.

[Download](https://fabos.patienceai.in) · [Website](https://fabos.patienceai.in) · [Report a bug](https://github.com/PatienceAIiN/fab-os/issues) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

Ubuntu 26.04 LTS base · KDE Plasma 6 (Wayland) · Inter type · Material-expressive look · Apache-2.0

</div>

---

## What Fab OS is

Fab OS is a complete desktop OS built on Ubuntu 26.04 LTS. It looks and feels like its own product — its own boot splash, greeter, theme, icons, and app names — but every Ubuntu command, package, `.deb`, Flatpak, and Docker workflow keeps working exactly as it does on Ubuntu. Ubuntu security updates still flow from Ubuntu; Fab OS features and branding updates flow from Patience AI's own signed repository.

The difference is the **home screen**. Instead of hunting through menus, you type what you want into one bar — *"open the editor, write the release notes, and email them to the team"* — and the built-in agent does it, at the level of the real operating system, with your permission.

> **Status: public pre-release (v1.0).** Solid to try and build; expect rough edges and please report them.

## The agent, in one screen

The desktop's centrepiece is the **"Ask me to do anything…"** bar. Behind it runs a local agent daemon (`fabos-agentd`, bound to `127.0.0.1`, bearer-token authenticated) that can actually operate the machine:

| It can | Tools |
|---|---|
| Run commands, as you or (with approval) as root | `run_shell` |
| Read, write, and list files | `read_file` · `write_file` · `list_dir` |
| Open and drive apps, type into them | `open_app` · `type_text` · `list_apps` |
| Send and check mail | `send_email` · `check_email` |
| Fetch the web, watch for things to happen | `web_fetch` · `schedule_watch` |
| Ask you a question, notify you | `ask_user` · `notify_user` |

**You stay in control.** Every action is scored by a deterministic risk classifier (LOW → CRITICAL) and gated by a permission mode:

- **Ask** — approve each risky step.
- **Auto** — the agent proceeds, pausing only for high-risk actions.
- **Bypass** — full autonomy, for when you trust the task.

A single **System-Wide AI** switch turns the whole thing off. Nothing leaves your machine except the requests you give the agent, sent only to the provider you configure yourself: **the built-in local model** (inside the ISO, offline, served by llama.cpp) or **Claude, OpenAI, Google Gemini** with your own key. No telemetry, no accounts, no crash uploads.

Everything the agent does is recorded and shown as a chat in **Fab AI Controls**: your requests on the right, the agent's answers on the left, and every tool step folded into a small "Worked: N actions" chip you can expand. Follow up in the same chat and the agent keeps the context; hover a message to edit, retry, or copy it; stop a running task with one click; and every risky step, delete, or mode change asks you first in a rounded confirmation dialog (approval requests show exactly what would run behind "Show details" — opened for you when the risk is high).

## Works offline out of the box

Fab OS ships a small language model **inside the ISO** — Qwen2.5 1.5B Instruct (Apache-2.0, GGUF Q4_K_M, 1.1 GB), served on your own machine by llama.cpp. No account, no API key, nothing to download: pick **Local model** in Fab AI Controls → Settings and the agent runs entirely offline, tool calls included (verified in the image: "Create a file named hello.txt containing hi" comes back as a `write_file` call).

- **RAM: it needs a 4 GB machine.** While loaded, `llama-server` peaks at about **1.45 GB** resident with the low-memory setting Fab OS uses below 6 GB of RAM (2.06 GB with the faster default above that) — measured in the Fab OS image with the shipped settings. With 3 GB or less the local endpoint is not offered at all; use a cloud provider there (`fabos-local-model status` tells you which case you are in).
- **Loaded on demand.** `fabos-llama.socket` listens on `127.0.0.1:8080` in every session, but nothing runs until the first request; then the model loads (1–3 s when the file is already in the disk cache; longer the first time after boot while 1.1 GB is read from disk) and answers. After **10 idle minutes** it is unloaded again, so it costs no RAM while you are not using it.
- **Small model, honest expectations.** 1.5 billion parameters is good at short, concrete tasks and tool calls and weak at long reasoning and facts. For hard tasks connect a cloud provider — it is one dropdown away, and everything else stays the same.

## What's in the box

- **Apps you already know:** Firefox (Mozilla's own build), LibreOffice, VLC, plus the Fab suite — Fab Files, Fab Terminal, Fab Editor, Fab Software, Fab Photos, Fab Documents, Fab Calculator, Fab Screenshot, Fab Monitor, Fab System Info, Weather.
- **Fab AI Controls** — chat with the agent: searchable history grouped by day, follow-ups with context, approvals, the System-Wide AI switch, and settings. Launch it with Meta+Space (`fabos-command-center`).
- **Fab Updates** — one place for updates, with Standard and Beta channels.
- **Fab Feedback** — send a bug or idea straight to the team.
- **Welcome to Fab OS** — a first-run wizard for appearance, privacy, and connecting an AI provider.

## Design

| Piece | What |
|---|---|
| Desktop | KDE Plasma 6 on Wayland; Fab OS look-and-feel in dark and light, following the system colour scheme everywhere |
| Type | Inter for UI, JetBrains Mono for code |
| Icons | Google Material Symbols on Fab OS tiles for system apps; third-party apps keep their own icons |
| Motion | Rounded, animated surfaces; Overview and edge-tiling for multitasking; multi-monitor extend/duplicate |
| Identity | Original Fab OS mark, wallpapers, and boot splash, rendered from source at every resolution up to 4K |

Design docs live in [`docs/design/`](docs/design/). Decisions are recorded as ADRs in [`docs/decisions/`](docs/decisions/).

---

## Minimum requirements

| | Minimum | Notes |
|---|---|---|
| Memory | **2 GB RAM** | Fab OS ships compressed swap in RAM (zram, half of RAM) and keeps background services on demand, so 2 GB runs the full desktop with every effect on. 4 GB recommended for large documents, many browser tabs and the built-in offline AI model (see above). |
| Disk | **20 GB** | The installed system is about 7 GB; the rest is for updates, Flatpaks and your files. |
| Processor / firmware | **64-bit (x86-64), UEFI** | Secure Boot works (Ubuntu's signed kernel and shim). Legacy BIOS is not supported. |
| Graphics | any GPU with a Mesa or vendor driver | Wayland-only desktop. |

What is tuned for small machines, and how to change it back, is in [`docs/LOW-RAM.md`](docs/LOW-RAM.md).

---

## Install Fab OS

1. **Download the ISO** (about 3.8 GB — the offline AI model is inside) from [fabos.patienceai.in](https://fabos.patienceai.in).
2. **Write it to a USB stick** (8 GB or larger) with [Balena Etcher](https://etcher.balena.io/) — free, and the same click-and-go steps on Windows, macOS, and Linux.
3. **Restart** and pick the USB stick from your computer's boot menu (usually F12, F2, or Esc at power-on).
4. **Try it live** — Fab OS runs from the stick without touching your disk. When you're ready, open **Install Fab OS** on the desktop.
5. **Verify the download** (optional): `sha256sum -c fabos-1.0-desktop-amd64.iso.sha256`.

After first login, open **Fab AI Controls → Settings** and pick **Local model** to use the built-in offline model (no account needed; 4 GB RAM), or connect a cloud provider with your own key. The agent does nothing until you choose.

---

## Build it yourself

Fab OS builds with rootless `podman` — no root, no host contamination. The build is a multi-stage container image exported to a bootable disk.

**Prerequisites** (Ubuntu/Fedora host): `podman`, `qemu-system-x86_64`, `ovmf`, `python3` with Pillow, and about 20 GB free disk.

```bash
git clone https://github.com/PatienceAIiN/fab-os.git
cd fab-os

# 1. Build the root filesystem (VM profile) and export it to ext4
MIRROR=http://archive.ubuntu.com/ubuntu scripts/build-rootfs.sh vm

# 2. Assemble a bootable GPT disk (UEFI, systemd-boot)
scripts/make-disk.sh vm

# 3. Boot it in QEMU/KVM (add --headless for no window, --heads 2 for dual display)
scripts/boot-vm.sh
```

Set `MIRROR` to a fast Ubuntu mirror near you; it affects downloads only — the shipped OS always points at the official archive.

### Build the installer ISO

```bash
scripts/build-rootfs.sh iso      # casper live rootfs + Calamares installer
scripts/build-iso.sh             # -> build/fabos-1.0-desktop-amd64.iso
tests/iso-boot-test.sh           # headless live-boot smoke test
```

### Test it

```bash
tests/branding-check.sh vm       # 60+ static checks: identity, no Canonical/KDE names, legal files present
tests/ui-tour.sh                 # boots headless, drives the UI, captures screenshots to build/screenshots/
python3 tests/agent-test.py      # agent unit tests (offline, FakeProvider)
```

The build is designed to be **cache-friendly and honest**: `build-rootfs.sh` refuses to export a stale image if a build step fails, and never re-downloads the desktop layer unless you change it.

---

## How updates work

Fab OS keeps two update streams, both automatic and both signed:

- **Ubuntu** security and package updates come from the Ubuntu archive, unchanged.
- **Fab OS** feature, AI, and branding updates come from Patience AI's signed repository at `https://fabos.patienceai.in/apt` (suites `loom` for Standard, `loom-beta` for Beta).

`apt`, `flatpak`, and everything else you know work normally.

---

## Legal & licensing

Fab OS is free and open source. **Own code is Apache-2.0** ([LICENSE](LICENSE), [NOTICE](NOTICE)). Upstream components keep their own licences, preserved and documented:

- **[LICENSING.md](LICENSING.md)** — how the pieces fit together.
- **[ATTRIBUTIONS.md](ATTRIBUTIONS.md)** and **[THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES/)** — every third-party component and its licence text.
- **[legal/](legal/)** — trademark notes, the Ubuntu-derivative compliance record, the GPL source offer, the privacy statement, the list of length-preserving string patches, and the open-source release checklist.

Trademark note: **Ubuntu** is a trademark of Canonical Ltd.; **KDE** and **Plasma** are trademarks of KDE e.V.; **Firefox** is a trademark of the Mozilla Foundation (Fab OS ships Mozilla's own unmodified build). Fab OS is an independent project and is **not endorsed by** any of them. All Ubuntu/Canonical and KDE trademarks and logos are removed from the product surface; the underlying free software and its copyright notices are unchanged.

No telemetry, no analytics, no accounts. See [legal/PRIVACY.md](legal/PRIVACY.md) and [SECURITY.md](SECURITY.md).

---

## Repository layout

```
brand/       identity: brand.conf + generators for icons, wallpapers, splash, themes
image/       the OS recipe (Containerfile) and per-profile overlays (vm, iso)
packages/    the Fab OS .deb sources (agent, desktop, branding, updates, feedback, welcome, ai)
scripts/     build, disk, QEMU, ISO, apt-publish, and release tooling
tests/       branding, UI-tour, agent, and boot tests
docs/        design docs and architecture decision records (ADRs)
legal/       licensing, trademark, privacy, and compliance records
website/     fabos.patienceai.in
```

## Contributing

Issues and pull requests are welcome — start with [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Found a security issue? See [SECURITY.md](SECURITY.md).

<div align="center">

**Fab OS** · by Patience AI · [fabos.patienceai.in](https://fabos.patienceai.in)

</div>
