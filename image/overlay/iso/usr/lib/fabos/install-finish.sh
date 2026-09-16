#!/bin/sh
# Fab OS installer: post-copy clean-up in the target. Run by Calamares' shellprocess module inside the target chroot
# (image/overlay/iso/etc/calamares/modules/shellprocess.conf). It lives in a file because Calamares expands $name and
# ${name} inside every shellprocess command line itself (KMacroExpander: ROOT, USER) and aborts the whole installation
# with "Missing variables" when a shell variable such as $f appears there — which is exactly what failed the round-6
# automated install (ADR-0021). Every step is guarded and only logs; this script always exits 0. Nothing needs the network.
# tests/calamares-jobs-test.sh replays it offline inside the image.
export LC_ALL=C.UTF-8

# live-session artefacts (casper, the passwordless live user's sudo rule and autologin, the desktop installer icon)
rm -f /etc/skel/Desktop/fabos-install.desktop /etc/casper.conf /etc/sddm.conf.d/20-autologin-live.conf /etc/sudoers.d/fabos-live
rm -f /home/*/Desktop/fabos-install.desktop
echo "fabos: live-session files removed"

# the live user (never present on the installed system; the users module has already created the real account)
if id fabos >/dev/null 2>&1; then
  if userdel -r fabos 2>/tmp/userdel.err; then echo "fabos: live user removed"; else echo "fabos: userdel exit $? - $(cat /tmp/userdel.err)"; fi
  rm -f /tmp/userdel.err
else
  echo "fabos: no live user in target"
fi

# units that only make sense on the live medium / in the QEMU harness
systemctl disable serial-getty@ttyS0.service fabos-live-selftest.service >/dev/null 2>&1
rm -f /var/lib/fabos/firstboot-done
echo "fabos: live-only units disabled, first-boot marker cleared"

# first boot, update check, feedback relay, automatic security updates (all shipped in the image; enable = symlinks only)
if systemctl enable fabos-firstboot.service fabos-update-check.timer fabos-feedback.socket unattended-upgrades.service >/dev/null 2>&1; then
  echo "fabos: units enabled"
else
  echo "fabos: systemctl enable exit $? (services-systemd already enabled them)"
fi

# evidence in the log: the Fab OS apt source + keyring and the --global user units survived the copy
for f in /etc/apt/sources.list.d/fabos.sources /usr/share/keyrings/fabos-archive-keyring.gpg /etc/apt/apt.conf.d/52fabos-unattended \
         /etc/systemd/user/default.target.wants/fabos-agent.service /etc/systemd/user/default.target.wants/fabos-voiced.service \
         /etc/systemd/user/sockets.target.wants/fabos-llama.socket; do
  if [ -e "$f" ]; then echo "fabos: ok $f"; else echo "fabos: MISSING $f"; fi
done

# install stamp for support ("when was this system installed")
mkdir -p /etc/fabos && date -u +%Y-%m-%dT%H:%M:%SZ > /etc/fabos/installed-at && echo "fabos: stamp written"
exit 0
