# ADR-0015: No wallet — KWallet disabled, KWallet Manager not shipped

**Status:** accepted (2026-09-15) · supersedes the "Fab Wallet" part of ADR-0008 (the rebrand rules stay; the wallet itself is off)

## Context
ISO 1.0 rev 2 greeted users with a "Fab Wallet" prompt and listed a wallet application in the launcher. The owner wants no
wallet prompts and no wallet application unless something in the product needs one. Nothing does: the Fab OS agent keeps
its provider keys and mail password in `systemd-creds` (`fabos_agentd.py` `set_secret`/`get_secret`), the browser has its
own password store, and NetworkManager can hold Wi-Fi secrets itself.

## Facts verified (round-3 image `localhost/fabos:vm`, 2026-09-15)
- Installed wallet stack (`dpkg -l`): `kwalletmanager 4:25.12.3`, `kwallet6 6.24.0` (kwalletd6, ksecretd),
  `libpam-kwallet5 4:6.6.4`, `libpam-kwallet-common`, `libkf6wallet6`; `/etc/xdg/kwalletrc` created a default wallet
  `FabWallet` with an `[Auto Allow]` list.
- Who pulls what (`apt-cache show`, `apt-cache rdepends --installed`):
  - `kwalletmanager` is only **Recommended**: by `ksshaskpass` and `libqt6keychain1` (both installed) and by our own
    `fabos-desktop-meta`. Nothing Depends on it. Because the image installs Recommends, dropping it from the package list is
    not enough — it needs an apt pin (`Pin-Priority: -1` in `/etc/apt/preferences.d/00-fabos-blocklist`).
  - `kwallet6` (the daemon) is Recommended by `kio6`, `plasma-workspace`, `plasma-nm`, `okular`; `libpam-kwallet5` by
    `plasma-desktop` (Recommends) and `sddm` (Suggests). Both stay installed; with the wallet disabled they are inert.
- Login hook: `/etc/pam.d/sddm` carried `-auth optional pam_kwallet5.so` and `-session optional pam_kwallet5.so auto_start`,
  which starts `kwalletd6` at every graphical login with the login password.
- Configuration keys the shipped binaries actually read (printable-string scan): `kwalletd6` → `Enabled`, `Close When Idle`,
  `Idle Timeout`, `Launch Manager`; `libKF6Wallet.so.6` → `Enabled`, `Default Wallet`, `Local Wallet`, `Use One Wallet`.
  `First Use` and `Prompt on Open` occur in neither binary (case-insensitive), so they are not shipped.
- plasma-nm: the kded module `networkmanagement.so` (class `SecretAgent`, "Provides secrets to the NetworkManager daemon")
  links `libKF6Wallet.so.6` and references `KWallet::Wallet::isEnabled()`; `libplasmanm_editor.so` references it too and
  carries the connection editor's password-storage choices "Store password for this user only (encrypted)" and
  "Store password for all users (not encrypted)". `Wallet::isEnabled()` reads exactly `[Wallet] Enabled` from `kwalletrc`.
  Upstream plasma-nm therefore does not open a wallet when it is disabled and defaults new passwords to system storage:
  NetworkManager keeps the PSK itself (`psk-flags=0`) in `/etc/NetworkManager/system-connections/<name>.nmconnection`,
  root-only (0600). This is the documented, acceptable outcome; the live behaviour in a VM is listed under "not verified".
- Browsers: Brave (ADR-0016) is Chromium-based; on KDE it asks `org.kde.kwalletd6` whether the wallet is enabled and, when
  it is not, falls back to Chromium's basic store for its saved passwords (an obfuscated store, not wallet-protected).
- SSH: `ksshaskpass` works without a wallet (it simply asks each time).
- The agent: `systemd-creds --user encrypt/decrypt` in `~/.config/fabos-agent/secrets/`, never KWallet.

## Decision
1. **No wallet application.** `kwalletmanager` leaves the apps layer of `image/Containerfile`, is pinned to `-1` in
   `00-fabos-blocklist` (so `ksshaskpass`/`libqt6keychain1` cannot pull it back) and leaves `fabos-desktop-meta`'s
   Recommends. Its menu entry ("Fab Wallet") disappears with it; `/etc/xdg/autostart/pam_kwallet_init.desktop` stays
   (it belongs to `libpam-kwallet-common` and exits at once without a PAM-provided socket). The AppStream merge component
   that renamed `org.kde.kwalletmanager5` to "Fab Wallet" is dropped from `fabos-names.xml` (Fab Software would otherwise
   list an installable "Fab Wallet" whose install the pin then refuses), the icon-theme mapping for `kwalletmanager` stays
   (harmless if a user installs it), and the VM self-test's `FAB_WALLET` marker becomes `NO_WALLET=<manager>:<Enabled>:kwalletd6=<count>`.
2. **Wallet disabled system-wide.** `/etc/xdg/kwalletrc` is now `[Wallet] Enabled=false`, `Close When Idle=false`,
   `Launch Manager=false` — only keys the binaries read; the `FabWallet` default wallet and `[Auto Allow]` list are gone.
3. **No wallet daemon at login.** The image build deletes both `pam_kwallet5.so` lines from `/etc/pam.d/sddm` (they are
   `optional`, so authentication is unchanged) and asserts the result.
4. `kwallet6`, `libkf6wallet6` and `libpam-kwallet5` remain installed (Recommends of Plasma packages; removing them would
   make apt want to remove or fight with the desktop). Every client that asks gets "wallet disabled" and falls back.
5. The rebrand rules `KDE Wallet → Fab Wallet` in `rebrand-catalogs`/`rebrand-binaries` stay: the strings still exist inside
   `libKF6Wallet`/`kwalletd6` error dialogs, so the name could still surface on a system where a user re-enables the wallet.
6. `tests/branding-check.sh` asserts: kwalletmanager absent and pinned, `Enabled=false` with no `Auto Allow`, no wallet
   desktop file or override, `pam_kwallet` gone from `/etc/pam.d/sddm`. (The three earlier checks that asserted the Fab
   Wallet KCM/override/Firefox were rewritten in place, since their old expectation is now the defect — the file is otherwise
   append-only; `docs/QA.md` "Changes after this record" lists the three by name and the new count.)

## Consequences
- No wallet prompt at first login or when joining Wi-Fi. Wi-Fi/VPN secrets live in NetworkManager's root-only files,
  readable by any administrator of the machine (the single-user desktop case Fab OS targets); `legal/PRIVACY.md` says so.
- Saved browser passwords are protected by the browser's own store only. A user who wants KWallet back sets `Enabled=true`
  in `~/.config/kwalletrc` (or `/etc/xdg/kwalletrc`) and installs `kwalletmanager`; nothing else in Fab OS depends on the
  choice.
- The removal is visible only in a rebuilt image (`localhost/fabos:vm` from round 3 still carries the packages).
