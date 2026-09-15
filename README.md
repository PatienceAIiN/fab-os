<div align="center">

# Fab OS™

**An agentic desktop operating system by [Patience AI](https://patienceai.in).**
Ubuntu underneath, so everything Linux already works. A built-in agent on top, so the computer does the work.

[Download](https://fabos.patienceai.in) · [Website](https://fabos.patienceai.in) · [Report a bug](https://github.com/PatienceAIiN/fab-os/issues) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

Ubuntu 26.04 LTS base · KDE Plasma 6 (Wayland) · Inter type · Material-expressive look · Apache-2.0

</div>

---

## What Fab OS is

Fab OS™ is a complete desktop OS built on Ubuntu 26.04 LTS. It looks and feels like its own product — its own boot splash, greeter, theme, icons, and app names — but every Ubuntu command, package, `.deb`, Flatpak, and Docker workflow keeps working exactly as it does on Ubuntu. Ubuntu security updates still flow from Ubuntu; Fab OS features and branding updates flow from Patience AI's own signed repository.

The difference is the **home screen**. Instead of hunting through menus, you type what you want into one bar — *"open the editor, write the release notes, and email them to the team"* — and the built-in agent does it, at the level of the real operating system, with your permission.

> **Status: public pre-release (v1.0).** Solid to try and build; expect rough edges and please report them.

## The agent, in one screen

The desktop's centrepiece is the **"Ask me to do anything…"** bar. Type (or tap the mic and speak) and press **Do it**: the answer arrives **right there** — a panel unfolds flush beneath the bar, on the home screen itself, with your request, a live feed of every action as the agent takes it (the app it opens with its own icon, the text it types appearing letter by letter, files, commands, mail), inline Allow/Deny cards when a step needs your permission, inline questions, and the result as formatted text. The panel is part of the desktop, never a floating window: the apps the agent opens sit above it, and it is back the moment they are minimised or closed. Only the bar and its panel belong to the applet — the empty desktop around them stays the desktop's (a right-click there opens the desktop's own menu). Stop, retry, edit the prompt, copy the result, minimise to a one-line status pill, or send a follow-up into the same conversation — no window opens unless you click "Open in Fab AI Controls". If the mic cannot record, the bar tells you why (no microphone, no speech engine) instead of doing nothing. Behind it runs a local agent daemon (`fabos-agentd`, bound to `127.0.0.1`, bearer-token authenticated) that can actually operate the machine:

| It can | Tools |
|---|---|
| Run commands, as you or (with approval) as root | `run_shell` |
| Read, write, and list files | `read_file` · `write_file` · `list_dir` |
| Open and drive apps, type into them | `open_app` · `type_text` · `list_apps` |
| Send and check mail | `send_email` · `check_email` |
| Fetch the web, watch for things to happen | `web_fetch` · `schedule_watch` |
| Ask you a question, notify you | `ask_user` · `notify_user` |

**It shows its work.** Ask it to *write* something — a hi note, a mail, a document — and it does not quietly drop a file somewhere: it opens Fab Editor (LibreOffice Writer for documents) first, types the text so you watch it appear, saves the file, and only then sends the mail or runs the code, narrating each step. Pure file and system operations still happen directly, without a window.

**You stay in control.** Every action is scored by a deterministic risk classifier (LOW → CRITICAL) and gated by a permission mode:

- **Ask** — approve each risky step.
- **Auto** — the agent proceeds on its own; it pauses for CRITICAL actions (root, disks, wiping a home or system directory, credentials, piping downloads to a shell), while other HIGH actions such as package installs or sending mail run without a prompt but are logged. Choose Ask if you want to approve those too.
- **Bypass** — full autonomy, for when you trust the task.

A single **System-Wide AI** switch turns the whole thing off. Nothing leaves your machine except the requests you give the agent, sent only to the provider you configure yourself: **the built-in local model** (inside the ISO, fully offline, served by llama.cpp, no account) or **Anthropic (Claude), Google Gemini, OpenAI, DeepSeek** with your own key. No telemetry, no accounts, no crash uploads.

One provider is active at a time. In **Fab AI Controls › Settings › AI provider** you pick it from a single dropdown, paste its key, and press **Check connection**: the daemon makes a real, lightweight authenticated call to the provider (its model list), tells you *Connected · model · latency* with an animated check mark, or *Key rejected* / *Cannot reach provider* with a shake — and refuses to save a key whose check failed (unless you untick the requirement). `fabos check` does the same from the terminal.

**Mail is your own account.** In **Fab AI Controls › Settings › Mail** you pick your provider — Gmail first, then Outlook / Hotmail, Yahoo, Zoho, iCloud, or any IMAP/SMTP server — type your address and press **Sign in**. On builds where the distributor has registered a Google OAuth client (`/etc/fabos/google-oauth.env`; how in [ADR-0014](docs/decisions/ADR-0014-user-mail-accounts.md)) Gmail signs in through the browser and the agent then talks to Google over XOAUTH2; otherwise the dialog reveals an **app password** field with a three-step hint for your provider and a **Check connection** button that really signs in to the SMTP and IMAP servers (15 s each) and tells *wrong password — needs an app password* (a 535, or a server such as Yahoo that simply closes the connection at sign-in) apart from *cannot reach*. Outlook.com no longer accepts any password for IMAP, so an Outlook account is **sending-only**: the check says so, Save is still enabled, the agent can send but not read that inbox. Nothing is saved until that check passes; the password or refresh token goes into systemd-creds like every other secret and is never logged. Server names and ports come from presets checked against the providers' own documentation (Gmail 587 STARTTLS / 993 · Outlook 587 STARTTLS / 993 · Yahoo 465 SSL / 993 · Zoho 465 SSL / 993 · iCloud 587 / 993) and can be overridden under *Advanced*. From the terminal: `fabos settings mail.provider gmail`, `fabos settings mail.address you@gmail.com`, `fabos set-key mail`, `fabos mail-check` (and `fabos mail-signin` where Google sign-in is available). Fab Feedback's relay is a separate channel and never carries your mail.

Everything the agent does is recorded and shown as a chat in **Fab AI Controls**: your requests as pills on the right, the agent's answers as plain text on the left with copy / good / bad / speak / edit / retry underneath, and every tool step in a **live action timeline** — the app's icon for "Opening Fab Files", a keyboard for typed text (revealed as it is typed), a terminal for commands, file, globe, mail, bell, question and eye icons for the rest — with a spinner while a step runs, a green check or amber cross when it finishes, and the agent's one-line narration in Indian English underneath ("Opening Fab Files for you now." → "Done, Fab Files is open."). Follow up in the same chat and the agent keeps the context (the earlier requests, outcomes and the files it touched); hover a message to edit, retry, or copy it; stop a running task with one click; speak your request with the microphone in the composer (needs `fabos-voice`); and every risky step, delete, or mode change asks you first in a rounded confirmation dialog (approval requests show exactly what would run behind "Show details" — opened for you when the risk is high). Catastrophic commands — wiping the home directory or a system directory, writing to a disk device, fork bombs, force-pushes, touching `~/.ssh` or the agent's own secrets — are always **CRITICAL** and ask first even in Auto mode.

The same history opens from the home-screen bar in **Fab AI Controls** (the bar's "Open in Fab AI Controls" button, `fabos-command-center --task ID`, jumps straight to the current chat): your requests on the right, the agent's answers on the left, and every tool step folded into a small "Worked: N actions" chip you can expand. Follow up in the same chat and the agent keeps the context; hover a message to edit, retry, or copy it; stop a running task with one click; and every risky step, delete, or mode change asks you first in a rounded confirmation dialog (approval requests show exactly what would run behind "Show details" — opened for you when the risk is high).

## Works offline out of the box

Fab OS ships a small language model **inside the ISO** — Qwen2.5 1.5B Instruct (Apache-2.0, GGUF Q4_K_M, 1.1 GB), served on your own machine by llama.cpp. No account, no API key, nothing to download: pick **Local model** in Fab AI Controls → Settings and the agent runs entirely offline, tool calls included (verified in the image: "Create a file named hello.txt containing hi" comes back as a `write_file` call).

- **RAM: it needs a 4 GB machine.** While loaded, `llama-server` peaks at about **1.45 GB** resident with the low-memory setting Fab OS uses below 6 GB of RAM (2.06 GB with the faster default above that) — measured in the Fab OS image with the shipped settings. With 3 GB or less the local endpoint is not offered at all; use a cloud provider there (`fabos-local-model status` tells you which case you are in).
- **Loaded on demand.** `fabos-llama.socket` listens on `127.0.0.1:8080` in every session, but nothing runs until the first request; then the model loads (1–3 s when the file is already in the disk cache; longer the first time after boot while 1.1 GB is read from disk) and answers. After **10 idle minutes** it is unloaded again, so it costs no RAM while you are not using it.
- **Small model, honest expectations.** 1.5 billion parameters is good at short, concrete tasks and tool calls and weak at long reasoning and facts. For hard tasks connect a cloud provider — it is one dropdown away, and everything else stays the same.

## Talk to Fab

Say **"Hey Fab"**, wait for the short chime, then say what you want — *"open my downloads folder"*, *"write a note that says call Amma at six"* — and Fab OS does it, telling you what it is doing as it goes: *"Sure, doing it now."*, *"Opening Fab Files for you now."*, *"This needs your permission: run the command apt update as administrator. Shall I go ahead?"* Answer with a short **yes / haan** or **no / nahi**; only an answer-shaped reply counts (a longer sentence, a doubt or overheard talk leaves the decision to the approval card on screen, and anything run as administrator needs a clear "yes"). The spotter reports the phrase at the end of what you said, so if you ran on — *"Hey Fab open my downloads"* — Fab OS uses those words after the chime instead of making you repeat them. While a task is running, *"Hey Fab"* then *"stop"* cancels it, and any other request starts a new task. Or press the microphone in Fab AI Controls (`fabos-voice listen-once`) to talk without the wake word.

| Piece | Offline — no key, nothing to download | With an OpenAI or Gemini key in Fab AI Controls |
|---|---|---|
| Wake word "Hey Fab" | PocketSphinx keyphrase spotting on the live microphone, always on-device | same — the wake word never uses the cloud |
| Speech-to-text | whisper.cpp with the `tiny.en` model shipped in the ISO (`/usr/share/fabos/voice`; about a second for a short sentence on a 4-thread laptop CPU, skipped when less than 600 MB of RAM is free) | the provider's speech model through the agent (better with Indian accents and names) |
| Spoken replies | eSpeak NG — a plain, synthetic British-English voice | a natural Indian-English voice through the agent |
| Narration of every step, approvals and questions by voice, the final reply | yes | yes |

Honest limits: the Ubuntu archive has no offline Indian-English voice, so the human-like voice needs a cloud key; offline you get eSpeak NG. The `tiny.en` model is small — it hears clear English well and mangles some names; a cloud key helps there. The wake-word spotter is tuned to be eager (it must never miss you), so it occasionally fires on look-alikes such as *"a fabulous day"*; whisper.cpp then double-checks the last six seconds offline and quietly drops a false wake. Every model ships inside the ISO — nothing is downloaded on your machine.

Turn it off with `fabos-voice wake off` (setting `voice.enabled`; the Voice tab of **Fab AI Controls** has the same switch, plus **Voice check** — `fabos-voice doctor` in a small box — and **Test voice**; when the composer microphone fails, the toast says why in fabos-voice's own words: muted or silent microphone, no audio session, missing speech engine). `fabos settings voice.offline_only true` keeps every recording on the machine even when a cloud key exists. Command line: `fabos-voice listen-once` (prints what you said), `fabos-voice say "text"`, `fabos-voice status`. Settings (`fabos settings KEY VALUE`, all read live by the listener): `voice.enabled`, `voice.wake_word` (words must be in the shipped dictionary), `voice.speak_replies`, `voice.speak_full`, `voice.offline_only`, `voice.verify_wake`, `voice.kws_threshold` (PocketSphinx sensitivity, default `1e-50`). Privacy details: [legal/PRIVACY.md](legal/PRIVACY.md).

**If voice does not work**, run `fabos-voice doctor`. It checks every stage for real and prints one `OK`/`FAIL` line each with the fix: the audio session (PipeWire), the default microphone (with its mute state and level), a one-second capture, whisper.cpp with the shipped model (a 0.5 s round trip), PocketSphinx and the wake phrase in its dictionary, the default speaker, and text-to-speech (a line is rendered with eSpeak NG and played, naming the player and the sink), then the optional pieces — the agent, the `fabos-voiced` listener, and the `voice.*` settings. It exits 0 only when every required stage passes (`--quiet` renders the sample without playing it, `--json` is for scripts). `fabos-voice say --test` speaks a two-sentence sample and prints which backend and sink it used. `fabos-voice status` carries `mic_reason`, `stt_reason` and `tts_reason` — empty when a piece works, otherwise the sentence Fab AI Controls shows next to the greyed-out control (for example "The microphone is muted. Unmute it in the volume applet and try again.").

How the microphone path behaves: `listen-once` records through PipeWire (`pw-record`), naming the default source explicitly when the automatic choice delivers nothing, then falls back to `parec` and plain ALSA `arecord`; it never runs longer than the timeout plus three seconds. Exit codes are the voice contract: `0` with the text on stdout, `3` when nothing was heard — including a microphone that is muted or silent, recognised after one second of an all-zero stream and named on stderr — and `4` when there is no backend at all (no audio session, no microphone, no speech-to-text). Spoken replies are queued, never overlapped: the listener waits for whatever Fab AI Controls' Speak button is saying (and the other way round), each sentence of a task is spoken once — the narration when a step starts, its done-line once when it finishes, an approval question once, the final reply once, and nothing repeated within thirty seconds — and the wake spotter is fed no audio while anything speaks, so it cannot hear itself. The offline voice is eSpeak NG at 150 words a minute, amplitude 175 of 200, a short gap between words (`-v en-gb-x-rp -s 150 -p 45 -a 175 -g 6`), rendered to a file and played with `pw-play` (then `paplay`, `aplay`) on the default sink.

## What's in the box

- **Apps you already know:** Firefox (Mozilla's own unmodified build), LibreOffice, VLC, plus the Fab suite — Fab Files, Fab Terminal, Fab Editor, Fab Software, Fab Photos, Fab Documents, Fab Calculator, Fab Screenshot, Fab Monitor, Fab System Info, Weather.
- **Fab AI Controls** — chat with the agent: searchable history grouped by day, follow-ups with context, the live action timeline, approvals, the System-Wide AI switch, voice, and compact settings (four tabs — permission mode and System-Wide AI · AI provider with a real connection check · Voice with a Voice check and Test voice · Mail with Sign in — everything else under *Advanced*). Launch it with Meta+Space (`fabos-command-center`; `--task ID` opens a specific conversation).
- **Talk to Fab** — the "Hey Fab" wake word, offline speech-to-text and spoken narration (`fabos-voice`).
- **Fab Updates** — one place for updates, with Standard and Beta channels.
- **Fab Feedback** — send a bug or idea straight to the team.
- **Welcome to Fab OS** — a first-run wizard for appearance, privacy, connecting an AI provider and using your own mail account.

## Design

| Piece | What |
|---|---|
| Desktop | KDE Plasma 6 on Wayland; Fab OS look-and-feel in dark and light, following the system colour scheme everywhere |
| Top bar & dock | One **quick settings** group at the top-right corner — network glyph with the live download / upload rate, Bluetooth, volume, battery **with its percentage**, and a bell — opens a pane that slides down from the bar: Wi-Fi and Bluetooth tiles, volume and brightness sliders, battery with power profiles, network details, notification history and Do Not Disturb; each tile's chevron opens the full standard applet. The **dock** magnifies icons under the pointer like macOS (hovered 1.6x, neighbours 1.3x / 1.1x; one switch shared with the bar, three strengths on the dock's page; the dock's width never changes while you hover) with a bounce on launch. One **Bar size** setting (Small / Medium / Large) scales the indicators, their text and the clock together |
| Type | Inter for UI, JetBrains Mono for code |
| Icons | Google Material Symbols on Fab OS tiles for system apps; third-party apps keep their own icons |
| Motion | Rounded, animated surfaces; Overview and edge-tiling for multitasking; multi-monitor extend/duplicate |
| Windows | Drag a window to a side edge for a half, to a corner for a quarter, to the top to maximise. Hold **Shift** while dragging to drop it into a tile layout (**Meta+T** edits the layouts, **Meta+Arrows** quick-tile from the keyboard). After a window snaps to one half, **Snap Assist** shows the other open windows so you can pick one for the remaining half ([ADR-0013](docs/decisions/ADR-0013-window-snapping.md)). Every window has **four rounded corners (radius 14)** in light and dark, cut by the compositor — the KDE-Rounded-Corners KWin effect, built from source into the image ([ADR-0019](docs/decisions/ADR-0019-rounded-corners-effect.md)); maximised, full-screen and snapped windows stay square |
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

**Smooth on 2 GB.** Fab OS keeps its own components quiet while you are not using them: the ask bar makes one small
request (a single `curl` for status and task together) every 2 s while it shows a working task, every 8 s while you are
around, and one a minute once it has been idle for 30 s (so work started from Fab AI Controls or the CLI still shows in
the bar); the quick settings read the kernel's counters every 5 s with a two-process script and ask NetworkManager,
Bluetooth and the audio server only every 30 s, when the link changes, or while their pane is open;
the dock animates only under the pointer; Fab AI Controls talks to the agent on a worker thread, so a slow reply can
never freeze the window. Measured in the image, that takes the desktop's idle process creation from about 21 to about 1
per second ([`docs/LOW-RAM.md`](docs/LOW-RAM.md), "Idle budget"). Every Plasma and KWin animation runs at half its stock
length (`AnimationDurationFactor=0.5`), tearing is off, blur is off on machines under 3.5 GB, and the voice listener runs
at nice 15 with idle I/O. `tests/perf-vm.sh` re-measures all of it in the booted 2 GB virtual machine: CPU of the
components over 20 s (< 8 % of one core), task creations per second (< 1), the Alt+Tab latency through KWin's own
handler (median < 150 ms) and a repaint check of Fab AI Controls while a task streams.

---

## Install Fab OS

1. **Download the ISO** (about 4 GB — the offline AI model and voice are inside) from [fabos.patienceai.in](https://fabos.patienceai.in).
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
python3 tests/voice-test.py      # voice: VAD, phrases, CLI contract, daemon follow-loop; wake word + whisper when the engines are present
```

The build is designed to be **cache-friendly and honest**: `build-rootfs.sh` refuses to export a stale image if a build step fails, and never re-downloads the desktop layer unless you change it.

---

## How updates work

Fab OS keeps two update streams, both automatic and both signed:

- **Ubuntu** security and package updates come from the Ubuntu archive, unchanged.
- **Fab OS** feature, AI, and branding updates come from Patience AI's signed repository at `https://fabos.patienceai.in/apt` (suites `loom` for Standard, `loom-beta` for Beta).
- **Firefox** updates come from Mozilla's own signed repository (`https://packages.mozilla.org/apt`, configured in `/etc/apt/sources.list.d/mozilla.sources` with Mozilla's keyring and pinned above the Ubuntu archive, whose own `firefox` package is only a snap shim), through the same `apt` path — Fab OS ships Mozilla's official build unmodified and never patches it. The one Fab OS addition is Mozilla's documented enterprise-policy file (`/usr/lib/firefox/distribution/policies.json`): it makes the first launch quiet (no Terms of Use / Privacy Notice screen — Mozilla's `SkipTermsOfUse` policy, under which Patience AI accepts the Firefox Terms of Use on behalf of Fab OS users, see `legal/OPEN-SOURCE-RELEASE-CHECKLIST.md` C6; no welcome tour, no telemetry or studies, no default-browser prompt, no stock bookmarks, no sponsored tiles, home page `fabos.patienceai.in`) and locks nothing. A system installed from the 1.0-3 / 1.0-4 images (which shipped a different browser) gets Firefox on its next update: `fabos-browser-migrate.service` installs it from Mozilla's source, removes the old browser (its data stays in your home folder; Firefox can import it) and repoints your defaults.

`apt`, `flatpak`, and everything else you know work normally.

---

## Security defaults

- **AppArmor** on, with Ubuntu's profiles unchanged.
- **Secure Boot** works: Ubuntu's signed kernel and shim, unmodified.
- **Firewall on** from the first boot: ufw denies incoming and allows outgoing connections, with no extra rules; the shipped image has no SSH server. `sudo ufw status` shows it, `sudo ufw allow <port>` opens a port.
- **Full-disk encryption** (LUKS2) is pre-selected in the installer.
- **Signed updates only**: Ubuntu's archive keys and the Fab OS Archive key. Fab OS never downloads anything on first use: models, voices, fonts and icons are shipped inside the ISO release.
- **No telemetry**, no analytics, no accounts ([legal/PRIVACY.md](legal/PRIVACY.md)).
- **The agent is gated by permissions**: it runs as you, stays off until you add a provider key, asks before risky steps, and reaches root only through a single-use, policy-checked path ([SECURITY.md](SECURITY.md)).

---

## Legal & licensing

Fab OS is free and open source. **Own code is Apache-2.0** ([LICENSE](LICENSE), [NOTICE](NOTICE)). Upstream components keep their own licences, preserved and documented:

- **[LICENSING.md](LICENSING.md)** — how the pieces fit together.
- **[ATTRIBUTIONS.md](ATTRIBUTIONS.md)** and **[THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES/)** — every third-party component and its licence text.
- **[legal/](legal/)** — trademark notes, the Ubuntu-derivative compliance record, the GPL source offer, the privacy statement, the list of length-preserving string patches, and the open-source release checklist.

Trademark note: **Ubuntu** is a trademark of Canonical Ltd.; **KDE** and **Plasma** are trademarks of KDE e.V.; **Firefox** is a trademark of the Mozilla Foundation; Fab OS ships Mozilla's own unmodified build. Fab OS is an independent project and is **not endorsed by** any of them. All Ubuntu/Canonical and KDE trademarks and logos are removed from the product surface; the underlying free software and its copyright notices are unchanged.

No telemetry, no analytics, no accounts. See [legal/PRIVACY.md](legal/PRIVACY.md) and [SECURITY.md](SECURITY.md).

---

## Repository layout

```
brand/       identity: brand.conf + generators for icons, wallpapers, splash, themes
image/       the OS recipe (Containerfile) and per-profile overlays (vm, iso)
packages/    the Fab OS .deb sources (agent, voice, desktop, branding, updates, feedback, welcome, ai)
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
