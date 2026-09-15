#!/bin/bash
# Fab OS image build step (ADR-0019): compile the KDE-Rounded-Corners KWin effect (GPL-3.0) against the KWin that is
# installed in THIS rootfs, package it as the Debian package `fabos-rounded-corners`, install it, then remove the build
# tools again so the image only grows by the effect itself. Run by image/Containerfile through a bind mount; not shipped.
#
# Why here and not in the pkgs stage: a KWin effect has no ABI stability across KWin minors (KWIN_EFFECT_API_VERSION,
# KWIN_PLUGIN_VERSION_NUM in the effect's sources), so it must be built against exactly the kwin-dev of the desktop layer.
# The release tarball URL + sha256 below are the corresponding source (legal/SOURCE-OFFER.md); the same URL and hash are
# written into the package's copyright file.
#
# The clean-up purges EXACTLY the packages this script installed (the dpkg list before vs after), never a name list:
# a dry run in the finished image (2026-09-15) showed that `apt-get purge qt6-base-dev-tools ...` takes a package that
# Plasma already depended on and cascades through plasma-workspace and fabos-desktop, and a following `autoremove`
# then removed 215 packages. No `autoremove` here for the same reason. Pre-existing packages that apt upgrades while
# resolving the dev dependencies (e.g. perl-base) stay upgraded and are LISTED in the build log (version list before vs
# after). The downloaded .deb archives are deleted by comparing the /var/cache/apt/archives listing before vs after — not
# with `find -newer`, because apt gives every downloaded .deb the server's Last-Modified mtime (older than any marker), so
# a marker-based find matched nothing and the 138 MB of archives stayed in the image (measured 2026-09-15: net +133 MB).
# The build FAILS on a hash mismatch, a compile error, a missing plugin file, a dangling library, a build tool left
# behind, or if any package that existed before this step is gone afterwards.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
URL=https://github.com/matinlotfali/KDE-Rounded-Corners/archive/refs/tags/v0.10.0.tar.gz
SHA=f3f03d96e17ae4b7dcee6347a01c75de6f90ed19e070e98ae8bf2dd71ae276db
SRCDIR=KDE-Rounded-Corners-0.10.0
VER=0.10.0-0fabos1
# The list is the upstream Kubuntu 26.04 CI list (.github/workflows/kubuntu2604.yml) minus kwin-x11-dev, plus dpkg-dev
# for dpkg-shlibdeps. kwin-dev pulls libkf6i18n-dev, libepoxy-dev, libxcb1-dev and the rest itself.
DEV="cmake g++ gettext extra-cmake-modules qt6-base-private-dev qt6-base-dev-tools libkf6configwidgets-dev libkf6kcmutils-dev kwin-dev libdrm-dev dpkg-dev"
J=$(nproc); [ "$J" -gt 4 ] && J=4
ARCHIVES=/var/cache/apt/archives
used() { du -sxm /usr /var /etc 2>/dev/null | awk '{s+=$1} END{print s}'; }
pkglist() { dpkg-query -W -f '${Package}\t${Status}\n' | awk -F'\t' '$2 ~ /installed$/ {print $1}' | LC_ALL=C sort; }
pkgvers() { dpkg-query -W -f '${Package} ${Version} ${Status}\n' | awk '$NF == "installed" {print $1, $2}' | LC_ALL=C sort; }

D0=$(used); pkglist > /tmp/rc-pkgs.before; pkgvers > /tmp/rc-vers.before; ls -1 "$ARCHIVES" | LC_ALL=C sort > /tmp/rc-archives.before
echo "rounded-corners: before  ${D0} MB, $(wc -l < /tmp/rc-pkgs.before) packages, $(grep -c '\.deb$' /tmp/rc-archives.before || true) cached archives from earlier layers"
apt-get update
apt-get install -y --no-install-recommends $DEV
pkglist > /tmp/rc-pkgs.dev
NEW=$(LC_ALL=C comm -13 /tmp/rc-pkgs.before /tmp/rc-pkgs.dev | tr '\n' ' ')
D1=$(used); echo "rounded-corners: with build tools ${D1} MB (+$((D1-D0)) MB), $(echo $NEW | wc -w) packages added for the build"
dpkg-query -W -f '${Package} ${Version}\n' kwin-dev libkwin6 cmake g++ extra-cmake-modules qt6-base-dev | sed 's/^/rounded-corners: /'

# ---- fetch (pinned) + build
rm -rf /tmp/rc && mkdir -p /tmp/rc && cd /tmp/rc
curl -fL -sS --retry 3 --retry-delay 10 --retry-connrefused -o src.tar.gz "$URL"
echo "$SHA  src.tar.gz" | sha256sum -c -
tar xzf src.tar.gz && test -f "$SRCDIR/CMakeLists.txt"
cmake -S "$SRCDIR" -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr | grep -E "Found KWin|KWinEffect API|Configuring done|Generating done"
# the compiler's exit status is checked directly (a pipe into grep would hide it); the log is summarised afterwards
cmake --build build -j"$J" > /tmp/rc/build.log 2>&1 || { echo "ERROR: compile failed (cmake --build exit $?); last lines:"; tail -40 /tmp/rc/build.log; exit 1; }
echo "rounded-corners: compiled $(grep -c 'Building CXX object' /tmp/rc/build.log || true) translation units, $(grep -c -E '(warning|error):' /tmp/rc/build.log || true) compiler warnings/errors: $(grep -o 'Built target [a-z0-9_]*' /tmp/rc/build.log | tr '\n' ',' | sed 's/,$//')"
rm -rf /tmp/pkgroot && DESTDIR=/tmp/pkgroot cmake --install build > /tmp/rc/install.log
SO=$(find /tmp/pkgroot -name kwin4_effect_shapecorners.so); KCM=$(find /tmp/pkgroot -name kwin_shapecorners_config.so)
test -n "$SO" && test -n "$KCM" && test -f /tmp/pkgroot/usr/share/kwin/shaders/shapecorners.frag
SO_PATH=${SO#/tmp/pkgroot}; KCM_PATH=${KCM#/tmp/pkgroot}
echo "rounded-corners: built $SO_PATH ($(stat -c %s "$SO") bytes) + $KCM_PATH"

# ---- Debian package (dpkg-shlibdeps needs a debian/control in the cwd; the temp dir is deleted below)
ARCH=$(dpkg --print-architecture)
rm -rf /tmp/shl && mkdir -p /tmp/shl/debian && cd /tmp/shl
printf 'Source: fabos-rounded-corners\nMaintainer: Patience AI <info@patienceai.in>\n\nPackage: fabos-rounded-corners\nArchitecture: any\nDescription: x\n' > debian/control
DEPS=$(dpkg-shlibdeps -O -e "$SO" -e "$KCM" 2>/dev/null | sed 's/^shlibs:Depends=//'); test -n "$DEPS"
KWINV=$(dpkg-query -W -f '${Version}' libkwin6)
KWINMM=$(echo "$KWINV" | sed -E 's/^([0-9]+:)?([0-9]+\.[0-9]+).*/\1\2/')
KWINNEXT=$(echo "$KWINMM" | awk -F: '{e=(NF>1)?$1":":""; v=$NF; split(v,a,"."); printf "%s%d.%d", e, a[1], a[2]+1}')
echo "rounded-corners: libkwin6 $KWINV -> Depends libkwin6 (>= $KWINMM), libkwin6 (<< $KWINNEXT); shlibs: $DEPS"
cd /tmp/pkgroot && mkdir -p DEBIAN usr/share/doc/fabos-rounded-corners
cp "/tmp/rc/$SRCDIR/LICENSE" usr/share/doc/fabos-rounded-corners/LICENSE
cp "/tmp/rc/$SRCDIR/README.md" usr/share/doc/fabos-rounded-corners/README.upstream.md
# Licence facts read from the v0.10.0 tree (2026-09-15): LICENSE = GPL version 3; the only per-file notices are the headers
# of src/Effect.cpp and src/Effect.h ("Copyright 2015 Robert Metsäranta ... either version 2 of the License, or (at your
# option) any later version"); no other file carries a copyright line; src/metadata.json names the authors Rob and Matin
# Lotfaliei. GPL-2.0-or-later code may be distributed under GPL-3.0, which is what the whole is shipped under here.
{
  printf 'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n'
  printf 'Upstream-Name: KDE-Rounded-Corners\nUpstream-Contact: Matin Lotfaliei (https://github.com/matinlotfali)\n'
  printf 'Source: https://github.com/matinlotfali/KDE-Rounded-Corners\n'
  printf 'Comment: Built by the Fab OS image build (image/rounded-corners-build.sh) from the unmodified v0.10.0 release\n tarball %s\n (sha256 %s). That tarball is the complete corresponding source; see legal/SOURCE-OFFER.md in the\n Fab OS repository (https://github.com/PatienceAIiN/fab-os).\n The upstream LICENSE file is the GNU GPL version 3. The two source headers that carry a notice (src/Effect.cpp,\n src/Effect.h) are GPL-2.0-or-later; the whole is distributed under GPL-3.0, as that licence permits.\n\n' "$URL" "$SHA"
  printf 'Files: *\nCopyright: the KDE-Rounded-Corners authors (Rob, Matin Lotfaliei; src/metadata.json)\nLicense: GPL-3.0\n\n'
  printf 'Files: src/Effect.cpp src/Effect.h\nCopyright: 2015 Robert Metsäranta\nLicense: GPL-2.0-or-later\n\n'
  printf 'Files: debian/*\nCopyright: 2026 Patience AI\nLicense: GPL-3.0\n\n'
  printf 'License: GPL-3.0\n On Debian systems the complete text of the GNU General Public License version 3 is in\n /usr/share/common-licenses/GPL-3; the upstream copy is shipped as LICENSE next to this file.\n\n'
  printf 'License: GPL-2.0-or-later\n This program is free software; you can redistribute it and/or modify it under the terms of the GNU General\n Public License as published by the Free Software Foundation; either version 2 of the License, or (at your\n option) any later version. On Debian systems the complete text of the GNU General Public License version 2 is\n in /usr/share/common-licenses/GPL-2.\n'
} > usr/share/doc/fabos-rounded-corners/copyright
printf 'fabos-rounded-corners (%s) resolute; urgency=medium\n\n  * KDE-Rounded-Corners v0.10.0 built from source for Fab OS (ADR-0019):\n    real rounded corners on every window, radius 14 (/etc/xdg/kwinrc [Round-Corners]).\n\n -- Patience AI <info@patienceai.in>  Tue, 15 Sep 2026 12:00:00 +0000\n' "$VER" | gzip -9n > usr/share/doc/fabos-rounded-corners/changelog.gz
SIZE=$(du -sk --exclude=DEBIAN . | cut -f1)
cat > DEBIAN/control <<CTRL
Package: fabos-rounded-corners
Version: $VER
Section: kde
Priority: optional
Architecture: $ARCH
Maintainer: Patience AI <info@patienceai.in>
Installed-Size: $SIZE
Depends: $DEPS, libkwin6 (>= $KWINMM), libkwin6 (<< $KWINNEXT), kwin-wayland
Homepage: https://github.com/matinlotfali/KDE-Rounded-Corners
Description: rounded window corners for KWin (KDE-Rounded-Corners effect)
 The KDE-Rounded-Corners KWin effect (upstream id kwin4_effect_shapecorners,
 GPL-3.0) built from the unmodified v0.10.0 release for Fab OS: it rounds all
 four corners of every application window in the compositor, with an outline
 and a shadow, and ships its System Settings module. Fab OS configures it in
 /etc/xdg/kwinrc ([Round-Corners], radius 14). Source: the release tarball named
 in /usr/share/doc/fabos-rounded-corners/copyright.
CTRL
find . -type f ! -path './DEBIAN/*' -printf '%P\n' | LC_ALL=C sort | xargs md5sum > DEBIAN/md5sums
cd / && dpkg-deb --build --root-owner-group /tmp/pkgroot "/tmp/fabos-rounded-corners_${VER}_${ARCH}.deb"
echo "rounded-corners: $(ls -l /tmp/fabos-rounded-corners_*.deb | awk '{print $5, $9}')"
apt-get install -y "/tmp/fabos-rounded-corners_${VER}_${ARCH}.deb"
[ -z "${RC_OUT:-}" ] || cp /tmp/fabos-rounded-corners_*.deb "$RC_OUT/"   # test harness only: keep the .deb

# ---- remove exactly what the build added (never a name list, never autoremove: see the header)
apt-get purge -y $NEW
# apt keeps every downloaded .deb in /var/cache/apt/archives (the rootfs stage removes Ubuntu's docker-clean hook), so the
# build-tool archives would stay in the image. Only the archives THIS step fetched are deleted: the directory listing
# before the install vs now (see the header for why `find -newer` cannot do it). A blanket `apt-get clean` would also
# wipe the archives the earlier layers left behind, which is a separate decision (see the notes of ADR-0019).
ls -1 "$ARCHIVES" | LC_ALL=C sort > /tmp/rc-archives.after
LC_ALL=C comm -13 /tmp/rc-archives.before /tmp/rc-archives.after | grep '\.deb$' > /tmp/rc-archives.fetched || true
FETCHED_KB=0; while IFS= read -r f; do [ -n "$f" ] || continue; FETCHED_KB=$((FETCHED_KB + $(du -k "$ARCHIVES/$f" | cut -f1))); rm -f "$ARCHIVES/$f"; done < /tmp/rc-archives.fetched
echo "rounded-corners: deleted $(wc -l < /tmp/rc-archives.fetched) archives this step fetched ($((FETCHED_KB/1024)) MB); $(ls "$ARCHIVES"/*.deb 2>/dev/null | wc -l) archives from earlier layers left as they were"
[ "$(LC_ALL=C comm -13 /tmp/rc-archives.before <(ls -1 "$ARCHIVES" | LC_ALL=C sort) | grep -c '\.deb$' || true)" = 0 ] || { echo "ERROR: fetched archives still present in $ARCHIVES"; exit 1; }
rm -rf /var/lib/apt/lists/* /tmp/rc /tmp/pkgroot /tmp/shl /tmp/fabos-rounded-corners_*.deb
pkglist > /tmp/rc-pkgs.after; pkgvers > /tmp/rc-vers.after
gone=$(LC_ALL=C comm -23 /tmp/rc-pkgs.before /tmp/rc-pkgs.after | tr '\n' ' ')
[ -z "$gone" ] || { echo "ERROR: packages that existed before this step are gone: $gone"; exit 1; }
added=$(LC_ALL=C comm -13 /tmp/rc-pkgs.before /tmp/rc-pkgs.after | tr '\n' ' ')
[ "$added" = "fabos-rounded-corners " ] || { echo "ERROR: unexpected packages left behind: $added"; exit 1; }
# pre-existing packages whose version changed (apt upgraded them as dependencies of the dev packages); logged, not undone
upgraded=$(LC_ALL=C join /tmp/rc-vers.before /tmp/rc-vers.after | awk '$2 != $3 {printf "%s %s -> %s; ", $1, $2, $3}')
UPGRADED_N=$(LC_ALL=C join /tmp/rc-vers.before /tmp/rc-vers.after | awk '$2 != $3' | wc -l)
echo "rounded-corners: $UPGRADED_N pre-existing package(s) upgraded by the dev install and left upgraded: ${upgraded:-none}"
for p in kwin-dev cmake g++ extra-cmake-modules qt6-base-private-dev libkf6kcmutils-dev dpkg-dev; do
  ! dpkg -s "$p" >/dev/null 2>&1 || { echo "ERROR: build tool $p still installed"; exit 1; }
done
test -f "$SO_PATH" && test -f "$KCM_PATH" && test -f /usr/share/kwin/shaders/shapecorners.frag && test -f /usr/share/kwin/shaders/shapecorners_core.frag
dpkg -s fabos-rounded-corners | grep -q '^Status: install ok installed'
[ "$(ldd "$SO_PATH" | grep -c 'not found')" = 0 ] || { echo "ERROR: $SO_PATH has unresolved libraries"; ldd "$SO_PATH" | grep 'not found'; exit 1; }
grep -q "$SHA" /usr/share/doc/fabos-rounded-corners/copyright && grep -q '^License: GPL-3.0$' /usr/share/doc/fabos-rounded-corners/copyright
D2=$(used); echo "rounded-corners: after purge ${D2} MB => net $((D2-D0)) MB for the effect, $((D1-D2)) MB of build tools removed; only fabos-rounded-corners added, nothing removed, $UPGRADED_N pre-existing upgraded (listed above)"
rm -f /tmp/rc-pkgs.before /tmp/rc-pkgs.dev /tmp/rc-pkgs.after /tmp/rc-vers.before /tmp/rc-vers.after /tmp/rc-archives.before /tmp/rc-archives.after /tmp/rc-archives.fetched
