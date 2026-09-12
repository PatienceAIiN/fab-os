# Fab OS — UI/UX audit (Phase 1 of the rebrand & revamp specification)

Date: 2026-09-12. Scope: the shipped Fab OS image (`fabos:vm`, Ubuntu 26.04 LTS base, Plasma 6.6.6 desktop stack).
Method: package inventory of the image, on-screen verification with the QEMU screenshot tour (`tests/ui-tour.sh`),
branding scan (`tests/branding-check.sh`), and a read of every Fab OS package. Status legend:
**REAL** = working, backed by a real system service · **STYLED** = real upstream component carrying Fab OS design ·
**FAB** = Fab OS-authored component · **GAP** = not done, listed honestly.

## 1. Platform inventory (what the OS is made of)

| Layer | Component | Origin | Status |
|-------|-----------|--------|--------|
| Kernel / boot | Linux 7.0 (Ubuntu), systemd-boot (VM) / shim+GRUB (ISO), Plymouth | Ubuntu, unmodified | REAL, Fab OS boot animation (FAB) |
| Session | SDDM greeter (Fab OS theme), Wayland, KWin compositor | KDE, styled | STYLED |
| Shell | plasmashell: top bar, floating dock, floating ask-bar panel, launcher, tray, notifications, quick settings | KDE, styled + Fab widgets | STYLED + FAB (ask bar, layout, theme, icons) |
| Design | FabOS Plasma theme (rounded, scheme-following), FabOS icon theme (Material Symbols tiles, 89 families), Inter/JetBrains Mono, wallpapers | FAB | FAB |
| Apps | Files (Dolphin), Terminal (Konsole), Editor (Kate), Software (Discover), Photos, Documents, Calculator, Archives, Screenshot, System Info, Monitor, Firefox | KDE / Mozilla, renamed in launcher | STYLED |
| Fab OS apps | Command Center, Updates, Feedback, Welcome (first run) | FAB (PyQt6) | FAB |
| Agent | fabos-agentd + CLI + KRunner runner + Dolphin action + audited root path | FAB | FAB, live-model evaluation pending a key |
| Updates | signed apt repo, Standard/Beta channels, unattended-upgrades, daily check | FAB | REAL |
| Installer | Calamares with Fab OS branding, casper live session | KDE-community tool, styled | STYLED (ISO under test) |

## 2. Spec sections vs. state

| Spec § | Requirement | State | Notes |
|-------|-------------|-------|-------|
| 2, 9 | Remove KDE / K branding from the experience | mostly done | launcher names, wallet, About page labels, session name, greeter, splash, avatar, "About Fab" menu. Remaining: in-app About dialogs of upstream apps (attribution, kept on purpose), some in-app strings in less common apps |
| 3, 35, 36 | Preserve attribution, ATTRIBUTIONS.md, LICENSING.md, THIRD_PARTY_LICENSES/ | done this round | see repository root |
| 4 | "Fab OS by Patience AI" on login/lock/about/installer/first-run | done | greeter wordmark, About logo, Calamares branding, Welcome wizard |
| 5–7 | Original visual identity, design system, brand system | partial | tokens + generated assets exist (`brand/`, `fab-ui/design-system/tokens`); components are Plasma/Kirigami/Breeze restyled, not a Fab-authored widget kit |
| 11–14 | Shell, dock, launcher, search | STYLED | Plasma panels/Kickoff/KRunner with Fab theme, icons, layout, agent runner. Original dock geometry (floating, centred, fit-width) |
| 15–17 | Window management, animations, workspaces | REAL (KWin) | effects enabled centrally; square window corners (no rounded-corner effect in Ubuntu 26.04) — GAP |
| 18–19 | Notifications, control center | REAL (Plasma) | styled by the FabOS Plasma theme; every toggle is the real Plasma backend |
| 20 | Settings | REAL (System Settings) | Fab icons, Fab Wallet, About page; title patched to "Fab OS Settings" |
| 21–23 | Files, Terminal, Monitor | REAL upstream | renamed in launcher; window titles patched where length-safe |
| 24 | Login / lock | STYLED | SDDM Fab theme (FAB); lock screen uses Plasma's with Fab wallpaper — custom lock QML is a GAP |
| 25 | First-run | FAB | `fabos-welcome` wizard (appearance, privacy, AI provider, links to language/keyboard/network) |
| 26 | Fab Light / Fab Dark | done this round | generated colour schemes; all Fab surfaces follow the scheme |
| 27 | Accessibility | inherited | Plasma/Qt accessibility (screen reader via Orca+AT-SPI available, keyboard navigation). Fab apps use standard Qt widgets. Contrast audit of Fab colours: pending — GAP |
| 28 | Multi-monitor | REAL (KWin/KScreen) | untested in QEMU — GAP (test on hardware) |
| 29 | Performance | measured: 1.1 GB RAM idle with desktop, boot to desktop ~30 s in QEMU | no profiling of animation latency — GAP |
| 30–32, 48 | No fake states / real CRUD | honoured | Fab apps show real errors (e.g. provider not configured, mail not configured, update check failures) |
| 33 | Security | honoured | keys via systemd-creds, root only via audited executor, no plaintext secrets |
| 37–38 | Boot, shutdown/restart | REAL | Plymouth Fab animation; Plasma logout/shutdown dialogs |
| 45 | Documentation set | done this round | FAB_OS_BRANDING.md, DESIGN_SYSTEM.md, MOTION_GUIDELINES.md, ACCESSIBILITY.md, ATTRIBUTIONS.md, LICENSING.md, UI_COMPONENT_CATALOG.md, UI_REVAMP_CHANGELOG.md |

## 3. Findings fixed during the audit (from on-screen verification)

- Colour scheme mismatch (Fab surfaces hard-coded dark) → all Fab surfaces now read the system scheme.
- Ask bar off-centre and misaligned → own centred floating panel, animated single action button, status only when relevant.
- Wallet still named KDE Wallet in Settings → module and service strings patched (length-preserving, documented).
- Launcher footer overlap, white shadow artefacts, missing ask bar, avatar placeholder → fixed.
- Test task opening Kate on every boot → only under automated test.
- Build script exported a stale image after a failed build → now fails hard.

## 4. Known gaps (honest)

1. Rounded window corners: no KWin effect in Ubuntu 26.04; needs a Fab-authored KWin effect (C++) later.
2. Custom lock screen QML: deferred (a broken lock screen locks users out; Plasma's is used with Fab wallpaper).
3. Fab-authored widget kit / bespoke launcher, dock, control center: the current shell is Plasma restyled. Original
   geometry and identity are present; a from-scratch shell is a later phase.
4. Live-model agent evaluation from easy to hard tasks: requires an API key; only the scripted-provider suite ran.
5. Accessibility contrast audit, multi-monitor and touch tests, animation latency profiling: not yet performed.
6. Upstream in-app "About" dialogs still name their projects (required attribution; left intentionally).
