# Fab OS changelog

Every Fab OS release, newest first, in plain language: what you see and what you get. Two kinds of release exist.
An **image** is a disc image you write to a USB stick and install from. An **over-the-air update** reaches installed
systems on its own through Fab Updates, with nothing to reinstall. Version numbers read 1.0-N: 1.0 is the Fab OS
version and N counts the package revision. Every published revision also has a GitHub release.

This file is the single source of truth. The changelog page on the website, the updates feed and the GitHub release
notes are all generated from it and never edited by hand.

<!-- Maintainers: the editing rules and the per-release checklist are in docs/RELEASING.md.
     Every release starts with a machine header on its own line, in this form (angle brackets omitted here):
         !-- release: rev=N tag=v1.0.(N-1) kind=image|ota|withheld date=YYYY-MM-DD [time=HH:MM] [file=NAME.iso] [status=draft] --
     followed by "## 1.0-N — <date in words> (<kind in words>)", an optional short paragraph, 4-10 short bullets
     ("- "), and an optional paragraph starting "Known limits:". Drafts (status=draft) never reach the website, the
     feed or GitHub. Render with scripts/changelog-render.py; gate with tests/changelog-check.sh.
     Style, enforced by the gate: plain words only. No file paths, identifiers, component or tool names, test names or
     counts, hosts, addresses, secrets, and no third-party product name used as ours. Say "login screen", "start-up
     screen", "installer", "updates", "the assistant" rather than the names of the parts underneath. -->

<!-- release: rev=8 tag=v1.0.7 kind=ota date=unreleased status=draft -->
## 1.0-8 — not yet released (over-the-air, draft)

TODO — draft, not published. The maintainer finalises these bullets once the round's work has landed and been
verified, then sets the date, removes the draft mark and re-renders. Until then nothing below reaches the website,
the feed or GitHub.

- Performance and battery modes: choose Balanced, Performance or Battery saver from quick settings; the desktop and the assistant follow the choice.
- Microphone: voice input works on machines where the microphone was never picked up, and Fab OS asks for microphone permission before the first use.
- Several conversations at once: start a new chat with the agent while another task is still running; each keeps its own history.
- Research and Computer-use switches in Fab AI Controls: decide whether the agent may look things up on the web and whether it may drive apps on your behalf.
- Scheduler: ask the agent to do something later or on a schedule, then see, pause or cancel what is planned.
- Branding sweep: the last stray names and words replaced across the desktop; fingerprint readers and cameras are recognised on more laptops.

Known limits: to be written from the round's verification.

<!-- release: rev=7 tag=v1.0.6 kind=ota date=2026-09-16 time=18:16 -->
## 1.0-7 — 16 September 2026 (over-the-air)

The first update delivered entirely through Fab Updates, with nothing to reinstall. It answers the first reports from
Fab OS running on a real laptop.

- Dock: clicking an app that is already open brings it to the front (or minimises it) instead of opening a second copy; the running marker stays while a window is minimised or on another desktop; middle-click opens a new window on purpose.
- Login screen: a new look with the clock and date, your user tile, a password field with a show/hide eye, "Not listed?", a session chooser and a power menu; a wrong password shakes the field and says so. This also replaces the red error screen some laptops showed instead of the login screen.
- Start-up: the maker's logo stays on screen while Fab OS starts (the Fab OS mark where the computer shows none), with a spinner and the Fab OS wordmark; on an encrypted disk the password box is now a real box with bullets, a label and a Caps Lock warning; shutting down shows "Shutting down safely…".
- Leave screen: shutting down, restarting or logging out first lists your open windows and flags the ones that look unsaved; the countdown pauses while work looks unsaved, and Cancel is the default.
- Start-up setting: Fab AI Controls › Settings › General gains "Ask for the disk password when the computer starts". Turning it off explains in plain words what it means before it acts, and it can be turned back on at any time.
- Updates: after an update Fab Updates tells you exactly one thing — nothing to do, log out and back in, or restart — and one desktop notification per version carries an "Open Fab Updates" button that works.

Known limits: the new dock, login screen and leave screen appear after the next log-in. The disk-password box cannot show what you type (the login screen has the eye, the start-up screen cannot). With the start-up password turned off your files stay encrypted on the drive, but the unlock key is kept on the computer itself, so anyone who starts it can use it without a password — the setting says so before it acts, and it is meant only for a computer that is itself kept secure. The new start-up screen was verified in a virtual machine; whether a laptop hands its maker's logo over depends on its firmware.

<!-- release: rev=6 tag=v1.0.5 kind=image date=2026-09-16 time=04:15 file=fabos-1.0-desktop-amd64.iso -->
## 1.0-6 — 16 September 2026 (image v1.0.5)

The disc image whose installer was proven by an automated installation before it was published. It replaces 1.0-4,
whose installer failed on a real device.

- Installer: the "Package Manager error" at the end of an install is fixed; installing with and without full-disk encryption was completed end to end in a virtual machine, and the installed system started on its own, before this image went out.
- Firefox is back as the browser (Mozilla's own build) and replaces Brave; it opens quietly, without first-run prompts.
- Quick settings: a wider, animated pane with tiles you can rearrange; connection speed is always shown.
- Dock: even spacing, clear running and active indicators, and a gentle magnify on hover. Windows have rounded corners and a single soft shadow everywhere.
- Ask bar: "Do it" stays off until you type; a hint suggests a cloud model for the best answers; pictures appear inline — ask for an image, logo or wallpaper, then tap it to enlarge, save, copy, open, set as wallpaper or regenerate.
- Image generation through a cloud provider with your own key, or through a local image server.
- Ollama as a provider: pick any model you have pulled, in Fab AI Controls. The built-in offline model and Ollama can now carry out multi-step tasks and fetch web pages.

Known limits: the built-in offline model varies from run to run on multi-step tasks — use a cloud provider or a larger Ollama model for reliable results. Offline speech-to-text needs about 600 MB of free memory. Google sign-in for mail works for listed testers only while the consent screen is in testing. A global menu bar was added to the top panel without being asked for; it can be removed.

<!-- release: rev=5 tag=v1.0.4 kind=withheld date=2026-09-15 -->
## 1.0-5 — 15 September 2026 (update only; image withheld)

No disc image was published for this revision: one was built and held back after the installer of the previous image
failed on a real device. Installed systems did receive 1.0-5 through Fab Updates on 15 September, until 1.0-6 replaced
it the next day. What it carried — Firefox returning in place of Brave, rounded corners on every window, a stronger
step-by-step mode for the built-in offline model, and a second pass on the top bar and dock — is listed under 1.0-6.
There is no GitHub release for this revision.

<!-- release: rev=4 tag=v1.0.3 kind=image date=2026-09-15 -->
## 1.0-4 — 15 September 2026 (image v1.0.3)

- Smoother on small machines: the quick settings pane opens faster, animations are shorter, the background blur is off on computers with less than 3.5 GB of memory, and Fab AI Controls no longer pauses while saving settings.
- Administrator actions by the agent ask for your password in the normal system dialog instead of relying on a passwordless rule.
- Shell steps run by the agent are sandboxed away from your keys and credentials, and cannot see provider keys.
- Administrators can set limits for the whole product in one policy file; the agent's activity log is tamper-evident and can be verified.
- The system is hardened (kernel settings, and the built-in model runs confined), and a list of every component is generated for audit.
- The image's checksum list is signed with the Fab OS Archive key, so it can be verified before writing a USB stick.

Known limits: the installer of this image failed on a real device with "Package Manager error" — install 1.0-6 or later instead. In a live session (before installing), administrator actions prompt for a password the live user does not have.

<!-- release: rev=3 tag=v1.0.2 kind=image date=2026-09-15 -->
## 1.0-3 — 15 September 2026 (image v1.0.2)

- Ask bar: the answer panel now lives on the home screen right under the bar (no floating window), shows what the agent is doing as it happens, and remembers the conversation between sessions.
- Voice: the microphone is found on more machines and the bar says why when it is not, the assistant never repeats a sentence, the offline voice is louder and clearer, and a voice check-up tells you what is wrong when something is.
- New quick settings pane in the top bar (Wi-Fi, Bluetooth, volume, brightness, battery, Do Not Disturb, night light, screenshot) and a new dock that magnifies on hover.
- Mail through your own account (Gmail, Outlook, Yahoo, Zoho or iCloud with an app password): the agent can read and send from it, and asks you to sign in when it is not set up.
- Windows have rounded top corners and a softer shadow.
- The password wallet is gone: nothing asks you to create or unlock a wallet.
- Brave Browser replaced Firefox in this image (Firefox returned in 1.0-6).

Known limits: the built-in offline model handles one step at a time; use a cloud provider for multi-step tasks.

<!-- release: rev=2 tag=v1.0.1 kind=image date=2026-09-14 time=17:20 -->
## 1.0-2 — 14 September 2026 (image v1.0.1)

Published a few hours after 1.0-1, with these changes on top of it:

- "Hey Fab": say it to talk to the agent; offline speech-to-text and a spoken Indian-English voice narrate what it does.
- A built-in offline model ships with the image, so a computer with no account and no network can still ask for one thing at a time (needs about 4 GB of memory).
- Fab AI Controls (previously Fab Command Center): one provider selector with a real connection check, DeepSeek added, a live timeline of the agent's actions, and persona and narration settings.
- Ask bar: answers appear inline with a live feed of actions, an animated Fab mark and voice input.
- The Fab OS look, completed: its own window frame, monochrome status icons and an 11 pt interface.
- Window snapping with Snap Assist; the firewall is on by default.
- Low-memory defaults for 2 GB machines; full-disk encryption is pre-selected in the installer.
- By voice, every permission question is asked once, and the assistant never reads raw command text aloud unless you ask it to.

Known limits: the built-in offline model is a single-step assistant; it is not available on machines with less than about 3 GB of memory, and says so.

<!-- release: rev=1 tag=v1.0.0 kind=image date=2026-09-14 -->
## 1.0-1 — 14 September 2026 (image v1.0.0)

The first Fab OS 1.0 build made public, replaced later the same day by 1.0-2.

- A calm KDE Plasma desktop on Ubuntu 26.04 with the Fab OS look: its own wallpapers, boot and login screens, the Inter typeface, and Fab names for the system apps.
- The Fab OS agent, built in: ask it to do things on the desktop from the ask bar; it shows a plan and asks before risky steps. Sign in with your own cloud provider key (Anthropic Claude, Google Gemini and others) or run a local model.
- Three permission modes — ask, auto and bypass — and a System-Wide AI switch that pauses assistance everywhere.
- Fab Command Center (renamed Fab AI Controls in 1.0-2), Fab Updates, Fab Feedback, Fab Terminal, Fab Files, Fab Editor, Fab Software, Fab Photos and Fab Documents, with Firefox, LibreOffice, VLC and a weather app.
- Signed update channel: installed systems get Fab OS updates from Patience AI and Ubuntu updates from Ubuntu.
- Live session first, then a guided installer from the desktop.
