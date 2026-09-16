#!/bin/bash
# Container half of tests/ota-stage-repo.sh — runs inside localhost/fabos:pkgs (dpkg-dev, python3, gpg).
#   ota-stage-repo-inside.sh --src /work --debs /debs --out /out [--bump SUFFIX] [--suite loom]
# Takes the built .debs, replaces the maintainer-side files of fabos-updates / fabos-desktop / fabos-branding with the
# working tree's (rendered from brand/brand.conf like packages/build-debs.sh does), optionally appends SUFFIX to every
# version so an installed system sees an upgrade, and writes a repository with the same layout publish-apt.sh deploys.
set -euo pipefail
SRC=/work; DEBS=/debs; OUT=/out; BUMP=""; SUITE=""
while [ $# -gt 0 ]; do case $1 in
  --src) SRC=$2; shift;; --debs) DEBS=$2; shift;; --out) OUT=$2; shift;; --bump) BUMP=$2; shift;; --suite) SUITE=$2; shift;;
  *) echo "unknown arg $1" >&2; exit 2;; esac; shift; done
# shellcheck disable=SC1091
. "$SRC/brand/brand.conf"
SUITE=${SUITE:-$DISTRO_CODENAME}
DISTRO_ID_TITLE="$(echo "${DISTRO_ID:0:1}" | tr a-z A-Z)${DISTRO_ID:1}"; DISTRO_CODENAME_TITLE="$(echo "${DISTRO_CODENAME:0:1}" | tr a-z A-Z)${DISTRO_CODENAME:1}"
export DISTRO_ID_TITLE DISTRO_CODENAME_TITLE
vars=$(grep -oE '^[A-Z_]+=' "$SRC/brand/brand.conf" | tr -d '=' ; echo DISTRO_ID_TITLE; echo DISTRO_CODENAME_TITLE)
render() { local f=$1; for v in $vars; do val="${!v}"; val="${val//\\/\\\\}"; val="${val//&/\\&}"; val="${val//|/\\|}"; sed -i "s|@$v@|$val|g" "$f"; done; }
render_tree() { while IFS= read -r -d '' f; do grep -qI '@[A-Z_]*@' "$f" 2>/dev/null && render "$f"; done < <(find "$1" -type f -print0); }
W=$(mktemp -d); mkdir -p "$OUT/pool/$SUITE" "$OUT/dists/$SUITE/main/binary-amd64" "$OUT/dists/$SUITE/main/binary-all"
rm -f "$OUT/pool/$SUITE"/*.deb
for deb in "$DEBS"/*.deb; do
  name=$(dpkg-deb -f "$deb" Package); ver=$(dpkg-deb -f "$deb" Version); arch=$(dpkg-deb -f "$deb" Architecture)
  d=$W/$name; rm -rf "$d"; dpkg-deb -R "$deb" "$d"
  case $name in
    fabos-updates)   # the whole package from the working tree (small, no generated assets)
      rm -rf "$d"; cp -a "$SRC/packages/fabos-updates" "$d"; find "$d" -name __pycache__ -type d -prune -exec rm -rf {} +
      render_tree "$d"; sed -i "s/^Version: .*/Version: $ver/" "$d/DEBIAN/control"
      chmod 755 "$d"/usr/bin/* "$d"/usr/lib/fabos/updates/*.sh "$d"/usr/lib/fabos/updates/*.py ;;
    fabos-desktop)   # only the maintainer-side files this round changes (the 33 MB asset tree stays as built)
      cp "$SRC/packages/fabos-desktop/DEBIAN/postinst" "$d/DEBIAN/postinst"; cp "$SRC/packages/fabos-desktop/DEBIAN/triggers" "$d/DEBIAN/triggers"
      install -Dm755 "$SRC/packages/fabos-desktop/usr/lib/fabos/desktop-postupgrade.sh" "$d/usr/lib/fabos/desktop-postupgrade.sh"
      render "$d/DEBIAN/postinst"; render "$d/usr/lib/fabos/desktop-postupgrade.sh" ;;
    fabos-branding)
      cp "$SRC/packages/fabos-branding/DEBIAN/triggers" "$d/DEBIAN/triggers" ;;
  esac
  newver=$ver$BUMP; sed -i "s/^Version: .*/Version: $newver/" "$d/DEBIAN/control"
  ( cd "$d" && find . -path ./DEBIAN -prune -o -type f -print0 | xargs -0 md5sum | sed 's| \./| |' > DEBIAN/md5sums )
  chmod 755 "$d"/DEBIAN/post* "$d"/DEBIAN/pre* 2>/dev/null || true; chmod 644 "$d"/DEBIAN/control "$d"/DEBIAN/md5sums "$d"/DEBIAN/triggers "$d"/DEBIAN/conffiles 2>/dev/null || true
  dpkg-deb --root-owner-group -Zxz -b "$d" "$OUT/pool/$SUITE/${name}_${newver}_${arch}.deb" >/dev/null
  echo "staged $name $newver"
done
cd "$OUT"
dpkg-scanpackages --arch amd64 "pool/$SUITE" > "dists/$SUITE/main/binary-amd64/Packages" 2>/dev/null
dpkg-scanpackages --arch all   "pool/$SUITE" > "dists/$SUITE/main/binary-all/Packages" 2>/dev/null
for a in amd64 all; do gzip -9kf "dists/$SUITE/main/binary-$a/Packages"
  printf "Archive: %s\nComponent: main\nOrigin: %s\nLabel: %s\nArchitecture: %s\n" "$SUITE" "$VENDOR_NAME" "$DISTRO_NAME" "$a" > "dists/$SUITE/main/binary-$a/Release"; done
python3 - "$SUITE" "$VENDOR_NAME" "$DISTRO_NAME" <<'PY'
import hashlib, os, sys, time
suite, origin, label = sys.argv[1:4]; base = "dists/%s" % suite
files = sorted(os.path.join(dp, f)[len(base) + 1:] for dp, _, fs in os.walk(base) for f in fs if not f.startswith(("Release", "InRelease")))
out = ["Origin: %s" % origin, "Label: %s" % label, "Suite: %s" % suite, "Codename: %s" % suite, "Date: %s" % time.strftime("%a, %d %b %Y %H:%M:%S UTC", time.gmtime()),
       "Architectures: amd64 all", "Components: main", "Description: %s package updates by %s (local OTA test stage)" % (label, origin)]
for name, h in (("MD5Sum", hashlib.md5), ("SHA256", hashlib.sha256)):
    out.append(name + ":")
    for f in files:
        data = open(os.path.join(base, f), "rb").read(); out.append(" %s %16d %s" % (h(data).hexdigest(), len(data), f))
open(os.path.join(base, "Release"), "w").write("\n".join(out) + "\n")
PY
# signing happens on the host (tests/ota-stage-repo.sh --sign-test-key): this image has gpg but no gpg-agent, which key generation needs
chmod -R a+rX "$OUT" 2>/dev/null || true
echo "repo: $(ls pool/$SUITE | wc -l) packages, suite $SUITE"; grep -E '^(Package|Version):' "dists/$SUITE/main/binary-amd64/Packages" | paste - - | sed 's/Package: //;s/\tVersion:/ /'
