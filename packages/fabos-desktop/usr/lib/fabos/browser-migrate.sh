#!/bin/sh
# Fab OS — one-shot browser migration for systems installed from the 1.0-3 / 1.0-4 images, which shipped Brave (ADR-0016)
# before the owner brought Firefox back (ADR-0018). Runs as root from fabos-browser-migrate.service. fabos-desktop's postinst
# flags it (touches $FLAG) and starts it when it finds any of: brave-browser installed, Brave's apt source, `firefox` in
# 00-fabos-blocklist, or no Mozilla Firefox. 00-fabos-blocklist is written by the image build, not by a package, so no
# package upgrade can rewrite it — and a `Package: … firefox` record there pins Mozilla's build to -1 as well (replayed
# 2026-09-15: `Candidate: (none)`), which is why an upgraded 1.0-4 system cannot get Firefox from `Recommends:` alone.
# Idempotent and safe to re-run (also by hand: sudo /usr/lib/fabos/browser-migrate.sh). The flag stays until Firefox is
# really installed, so an offline upgrade is retried at the next boot. A fresh 1.0-5+ image never sets the flag.
#
#   1. wait for any other apt/dpkg run (Fab Updates, fabos-firstboot, a terminal) instead of fighting for the lock
#   2. un-pin firefox: drop the word `firefox` from the Package: line of /etc/apt/preferences.d/00-fabos-blocklist
#   3. require Mozilla's source, pin and keyring (fabos-branding ships them; this job never invents a key)
#   4. apt-get update && apt-get install firefox; accept only `Maintainer: Mozilla` (Ubuntu's own firefox is a snap shim)
#   5. only then remove the old browser: purge brave-browser + brave-keyring, delete its source and defaults file (the
#      1.0-3 / 1.0-4 image wrote both; no package owns them). Every user's own data stays (~/.config/BraveSoftware is
#      not touched — Firefox's import wizard reads it).
#   6. point stale per-user defaults at Firefox: brave-browser.desktop -> firefox.desktop in ~/.config/mimeapps.list and
#      in the Plasma launcher lists (plasma-org.kde.plasma.desktop-appletsrc: the dock pin of an existing user; the
#      look-and-feel layout only shapes new users). The system defaults (/etc/xdg/mimeapps.list) already say Firefox.
#   7. clear the flag, write the done marker, tell the logged-in users (one notification, like fabos-firstboot).
set -u
FLAG=/var/lib/fabos/browser-migrate-pending
DONE=/var/lib/fabos/browser-migrate-done
BLOCK=/etc/apt/preferences.d/00-fabos-blocklist
MOZ_SRC=/etc/apt/sources.list.d/mozilla.sources
MOZ_KEY=/usr/share/keyrings/packages.mozilla.org.gpg
MOZ_PIN=/etc/apt/preferences.d/mozilla
LOG(){ echo "fabos-browser-migrate: $*"; }
export DEBIAN_FRONTEND=noninteractive
APT="apt-get -y -q -o DPkg::Lock::Timeout=600 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
mkdir -p /var/lib/fabos
notify_all(){ for d in /run/user/*; do uid=${d##*/}; u=$(id -nu "$uid" 2>/dev/null) || continue; [ -S "$d/bus" ] || continue
  runuser -u "$u" -- env DBUS_SESSION_BUS_ADDRESS="unix:path=$d/bus" XDG_RUNTIME_DIR="$d" notify-send -a "Fab OS" -i fabos "$1" "$2" 2>/dev/null || true; done; }
firefox_is_mozilla(){ dpkg-query -W -f '${Maintainer}' firefox 2>/dev/null | grep -q '^Mozilla'; }
installed(){ dpkg -l "$1" 2>/dev/null | grep -q '^ii'; }

# 1. never fight another apt/dpkg run for the lock (the postinst starts this job from inside an upgrade); 30 min at most
for i in $(seq 1 360); do fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1 || break; sleep 5; done

# 2. un-pin firefox (round 4 listed it to keep the snap shim out; the origin pin in $MOZ_PIN already outranks the shim,
#    and snapd itself is pinned -1 by fabos-nosnap, so the shim stays uninstallable without this word)
if [ -f "$BLOCK" ] && grep -qw firefox "$BLOCK"; then
  sed -i -E '/^Package:/{s/(^| )firefox( |$)/\2/; s/ +$//; s/^Package:  +/Package: /}' "$BLOCK"
  if grep -qw firefox "$BLOCK"; then LOG "could not remove firefox from $BLOCK — will retry"; exit 0; fi
  LOG "removed firefox from $BLOCK"
fi

# 3. Mozilla's source, pin and keyring come from fabos-branding; without them apt would only see the snap shim
if ! [ -s "$MOZ_SRC" ] || ! [ -s "$MOZ_KEY" ] || ! [ -s "$MOZ_PIN" ]; then
  LOG "Mozilla's apt source, keyring or pin missing (fabos-branding not upgraded yet?) — will retry"; exit 0; fi

# 4. Mozilla's Firefox
if ! firefox_is_mozilla; then
  nm-online -q -t 120 2>/dev/null || true
  if ! $APT update >/dev/null 2>&1; then LOG "apt-get update failed (offline?) — will retry at the next boot"; exit 0; fi
  $APT install firefox || true
  if ! firefox_is_mozilla; then
    LOG "firefox is not Mozilla's build after install (candidate: $(apt-cache policy firefox 2>/dev/null | sed -n 's/^ *Candidate: //p')) — will retry"; exit 0; fi
  LOG "installed firefox $(dpkg-query -W -f '${Version}' firefox) from packages.mozilla.org"
fi
[ -f /usr/share/applications/firefox.desktop ] || { LOG "firefox.desktop missing after install — will retry"; exit 0; }

# 5. the old browser goes, now that Firefox is in place; user data stays
if installed brave-browser || installed brave-keyring; then
  $APT purge brave-browser brave-keyring || LOG "purge had errors"
fi
rm -f /etc/apt/sources.list.d/brave-browser-release.sources /etc/apt/sources.list.d/brave-browser-release.list \
      /etc/default/brave-browser /usr/share/keyrings/brave-browser-archive-keyring.gpg
[ -d /opt/brave.com ] && ! installed brave-browser && rm -rf /opt/brave.com
if installed brave-browser; then LOG "brave-browser still installed — will retry"; exit 0; fi

# 6. stale per-user pins -> Firefox (only the brave-browser.desktop token changes; sed -i keeps owner and mode)
for h in /home/* /root; do
  for f in "$h/.config/mimeapps.list" "$h/.local/share/applications/mimeapps.list" "$h/.config/plasma-org.kde.plasma.desktop-appletsrc"; do
    if [ -f "$f" ] && grep -q 'brave-browser\.desktop' "$f"; then
      sed -i 's/brave-browser\.desktop/firefox.desktop/g' "$f" && LOG "repointed $f at firefox.desktop"; fi
  done
done

# 7. done
rm -f "$FLAG"; date -u +%FT%TZ > "$DONE"
notify_all "Firefox is your browser now" "Fab OS ships Firefox again; the previous browser was removed. Its data is kept in ~/.config/BraveSoftware — Firefox can import it (Settings > Import Browser Data)."
LOG "done"
exit 0
