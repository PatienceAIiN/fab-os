# How do I stop the disk password prompt at start-up?

Fab OS can encrypt the whole disk when it is installed. An encrypted computer asks for the **disk passphrase**
every time it starts, before anything else appears. This page explains the switch that turns that prompt off
(and on again), what it does, why you might still see the prompt afterwards, and how to check and fix it.

## Where the switch is

**Fab AI Controls → Settings → General → Start-up:** the row *"Ask for the disk password when the computer starts"*.

- **On** (the default): the computer asks for the disk passphrase at start-up.
- **Off**: the computer starts without asking.

Under the switch the row shows the **real** state — *"Start-up asks for the disk password: yes / no — checked just
now"* — which comes from looking at the actual start-up files, not from the position of the switch. If the two
disagree, an amber notice says so and offers **Fix now**. **Details** lists every check that was made.

In a terminal the same thing is:

```
fabos disk-unlock status            the switch and the real start-up state
fabos disk-unlock diagnose          every check with its reason (add --admin for the complete check; you are asked for your password)
fabos disk-unlock off               stop asking (you are asked for the disk passphrase; it is never shown)
fabos disk-unlock on                ask again
fabos disk-unlock repair            make the start-up files match the switch again (the same as Fix now)
```

## What turning it off does — and the trade-off

Your files stay encrypted on the drive. To start without asking, Fab OS creates a random unlock key, registers it
with the encrypted disk, and stores a copy inside the start-up files on the small **unencrypted** `/boot` partition.
At start-up that copy unlocks the disk.

**The trade-off:** anyone who can start this computer can use it without a password — and anyone who takes the drive
out can read the key from `/boot` and unlock your files. Turn the prompt off only where the computer itself is
secure (a desk at home, a locked office). Your login-screen password still applies, but it protects the session,
not the drive.

Turning the switch back **on** removes the key from the start-up files first, proves it is gone, and only then
removes it from the disk — in that order, so the computer always keeps starting.

Every change is written to `/var/log/fabos/disk-unlock.log` (administrators only) and recorded as a CRITICAL entry
in Fab AI Controls' activity log. The system asks for **your login password** once (the standard permission dialog)
because the change is made as administrator.

## The switch exists from update 1.0-7

If you do not see the Start-up row in Settings → General, your Fab OS is older than 1.0-7.

**Check your version:** open **Fab Updates** — it shows whether an update is waiting and what it contains. The exact
installed version is printed by this terminal command: `dpkg-query -W -f='${Version}\n' fabos-agent` (for example
`1.0-7`; anything from `1.0-7` up has the switch).

**Get the update now:** Fab Updates → **Install**, or in a terminal:

```
sudo apt update && sudo apt full-upgrade
```

Fab OS also installs its updates automatically in the background; a notice tells you when one has been applied.

## I turned it off, but the computer still asks. Why?

Open Settings → General → Start-up and read the line under the switch. The usual reasons, and what to do:

1. **The change never completed.** Turning the prompt off needs your login password in the system dialog. If that
   dialog was cancelled or timed out, or the disk passphrase you typed was not accepted, nothing was changed — the
   row now says so in red ("Last attempt … did not complete"). Turn the switch off again and enter your login
   password when the dialog appears.
2. **The start-up files were rebuilt without the key.** A kernel update or a package upgrade rebuilds the start-up
   files; if their configuration had lost the key pattern, the new files ask again (or, worse, cannot start). The
   row shows the amber notice *"The switch is off but the start-up files still ask for the password"*. Press
   **Fix now**.
3. **Another encrypted partition asks.** A separate encrypted swap or data partition that has no key of its own
   asks for *its* password at start-up — it looks exactly like the disk prompt. **Details** names it. The Start-up
   switch only manages the disk the system lives on; give that partition a key (in `/etc/crypttab`) or remove it.
4. **The start menu points at another start-up file.** If GRUB's default entry boots a start-up file that Fab OS does
   not maintain (for example one left behind by a removed kernel), changes never reach it. **Details** shows it;
   **Fix now** refreshes the menu (`update-grub`).
5. **You are looking at the login screen.** After the disk is unlocked, the login screen asks for your **user
   password**. That is a different password and a different screen (it shows your name and picture); the Start-up
   switch does not change it. To sign in automatically instead, use System Settings → Users → your account →
   *Automatic login* (which also lowers your security).

### What "Fix now" checks and does

**Fix now** first looks at the real state and then re-applies the switch's setting from the start:

- the system disk is encrypted and listed in `/etc/crypttab`; the entry's key column and options
  (`initramfs`) are what the setting expects;
- the unlock key file exists (`/etc/fabos/luks-unlock.key`, readable by the administrator only), has its own key slot
  on the disk, and actually opens the disk;
- `/boot` is mounted, so the rebuilt files land where the firmware boots from;
- `KEYFILE_PATTERN` (in `/etc/cryptsetup-initramfs/conf-hook`) points at the key and `UMASK=0077` keeps the files
  administrator-only;
- **every** installed kernel's start-up file carries the key (or none does, when the switch is on), and what the
  file's own configuration says about the disk;
- the start-up file GRUB's default entry boots is one of those files, and the EFI boot chain loads the menu from this
  disk;
- the unlock method inside the file is the one Fab OS configures (cryptsetup-initramfs);
- other encrypted devices in `/etc/crypttab` and whether they would prompt.

It then keeps the unlock key if it still opens the disk (otherwise it stores a new one), rebuilds every start-up
file, proves the key is inside and opens the disk, refreshes the GRUB menu when needed, and checks again. Nothing is
removed from the disk before the rebuilt files are proven, and if anything fails half-way it rolls back, so the
computer keeps starting either way. You are asked for the **disk passphrase** (it must open the disk before anything
is changed) and for your **login password** (the permission dialog).

## How to turn it back on

Settings → General → Start-up → switch **on**. Nothing else is asked. The next start asks for the disk passphrase
again. (Terminal: `fabos disk-unlock on`.)

## Two different passwords

| Prompt | When | What it protects | Changed by |
|---|---|---|---|
| **Disk passphrase** — a plain text field on the Fab OS start screen, before anything else | right after power-on | the encrypted drive | this switch |
| **Login password** — the login screen with your name and picture | after the disk is unlocked | your session | System Settings → Users |

## For administrators

- `fabos disk-unlock diagnose --admin` prints every check as administrator (the start-up files on `/boot` are
  readable by the administrator only, so the ordinary check reports some items as *unknown* unless a recent
  administrator run left a record in `/var/lib/fabos/disk-unlock-check.json`).
- The helper behind the switch is `/usr/lib/fabos/agent/disk_unlock.sh {status|diagnose|off|on|repair}`; it runs as
  root through polkit (`pkexec`), never with `sudo` from the desktop, and never puts the passphrase on a command line.
- Layout: EFI partition + unencrypted `/boot` (ext4) + LUKS2 root, as the Fab OS installer creates it.
