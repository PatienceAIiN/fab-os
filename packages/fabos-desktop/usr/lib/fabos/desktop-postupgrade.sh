#!/bin/sh
# @DISTRO_NAME@ desktop: what a running system needs right after fabos-desktop was installed or upgraded (called by
# DEBIAN/postinst configure with the previous version as $1; empty on first install). Idempotent, never fails.
#
# It deliberately does NOT restart plasmashell, KWin or SDDM: an update installed in the background (unattended-upgrades)
# must never take the user's session away. Advice ("log out and back in to finish") comes from fabos-updates: this
# package declares `activate-noawait fabos-postupgrade` (DEBIAN/triggers), so dpkg runs fabos-updates' record step at the
# end of the apt run, which pokes every session's notifier and feeds the banner in Fab Updates (docs/UPDATES.md).
set +e
prev=${1:-}
# 1. Greeter: sddm-greeter-qt6 keeps compiled QML (.qmlc) under the sddm user's cache. Qt checks source timestamps, but a
#    theme file replaced by dpkg keeps the archive's mtime, which can be OLDER than the cached copy — drop the cache so the
#    next login screen is built from the shipped theme files.
for d in /var/lib/sddm/.cache/sddm-greeter-qt6/qmlcache /var/lib/sddm/.cache/sddm-greeter/qmlcache; do
  [ -d "$d" ] && rm -rf "$d" 2>/dev/null
done
# 2. Icon theme index (the FabOS icon theme is a directory tree; the cache is optional but makes the first paint faster)
if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -f /usr/share/icons/FabOS/index.theme ]; then
  gtk-update-icon-cache -q -f -t /usr/share/icons/FabOS 2>/dev/null
fi
# 3. Desktop entries (dpkg file triggers of desktop-file-utils do this too; harmless when repeated)
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q 2>/dev/null
# 4. Only on a live upgrade (not the image build, not a fresh install): make sure the follow-up trigger is pending even if
#    fabos-updates is older than 1.0-7 and does not know it yet (then dpkg-trigger says "no such trigger" and we move on).
if [ -n "$prev" ] && [ -d /run/systemd/system ]; then
  dpkg-trigger --no-await fabos-postupgrade 2>/dev/null
fi
exit 0
