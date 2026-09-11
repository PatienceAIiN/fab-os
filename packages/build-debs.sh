#!/usr/bin/env bash
# Render @VARS@ from brand.conf into package trees, drop in generated assets + binaries, build .debs.
# Usage: build-debs.sh <src-root> <out-dir>    (src-root has brand/, packages/, assets/, bin/)
set -euo pipefail
SRC=$1; OUT=$2; mkdir -p "$OUT"; WORK=$(mktemp -d)
# shellcheck disable=SC1090
. "$SRC/brand/brand.conf"
DISTRO_ID_TITLE="$(echo "${DISTRO_ID:0:1}" | tr a-z A-Z)${DISTRO_ID:1}"
DISTRO_CODENAME_TITLE="$(echo "${DISTRO_CODENAME:0:1}" | tr a-z A-Z)${DISTRO_CODENAME:1}"
export DISTRO_ID_TITLE DISTRO_CODENAME_TITLE
vars=$(grep -oE '^[A-Z_]+=' "$SRC/brand/brand.conf" | tr -d '=' ; echo DISTRO_ID_TITLE; echo DISTRO_CODENAME_TITLE)
render() { # in-place substitution of @VAR@ in text files
  local f=$1; for v in $vars; do
    val="${!v}"; val="${val//\\/\\\\}"; val="${val//&/\\&}"; val="${val//|/\\|}"
    sed -i "s|@$v@|$val|g" "$f"; done; }
for pkgdir in "$SRC"/packages/*/; do
  name=$(basename "$pkgdir"); dst="$WORK/$name"; cp -a "$pkgdir" "$dst"
  # --- inject generated assets ---
  case $name in
    fabos-branding)
      for s in 16 22 24 32 48 64 128 256 512; do install -Dm644 "$SRC/assets/icons/fabos-$s.png" "$dst/usr/share/icons/hicolor/${s}x${s}/apps/fabos.png"; done
      install -Dm644 "$SRC/assets/pixmaps/fabos-logo.png" "$dst/usr/share/pixmaps/fabos-logo.png"
      install -Dm644 "$SRC/assets/pixmaps/fabos-logo-dark.png" "$dst/usr/share/pixmaps/fabos-logo-dark.png"
      install -Dm644 "$SRC/assets/pixmaps/fabos.png" "$dst/usr/share/pixmaps/fabos.png"
      cp "$SRC"/assets/plymouth/spinner-*.png "$SRC/assets/plymouth/wordmark.png" "$SRC/assets/plymouth/mark.png" "$dst/usr/share/plymouth/themes/fabos/"
      python3 - "$dst/usr/share/plymouth/themes/fabos" <<'PY'
import sys; from PIL import Image
d=sys.argv[1]; Image.new("RGBA",(8,8),(42,49,59,255)).save(d+"/bar-bg.png"); Image.new("RGBA",(8,8),(110,155,255,255)).save(d+"/bar-fg.png")
PY
      ;;
    fabos-desktop)
      LNF=in.patienceai.fabos.desktop
      for f in dark-1920x1080 dark-2560x1440 dark-3840x2160 light-1920x1080 light-2560x1440 light-3840x2160; do
        [ -f "$SRC/assets/wallpapers/$f.png" ] && install -Dm644 "$SRC/assets/wallpapers/$f.png" "$dst/usr/share/wallpapers/FabOS/contents/images/${f#*-}.png"; done
      # Plasma picks images by resolution name; dark is the default set, light under images_dark? keep light variants alongside
      for f in light-1920x1080 light-2560x1440 light-3840x2160; do
        [ -f "$SRC/assets/wallpapers/$f.png" ] && install -Dm644 "$SRC/assets/wallpapers/$f.png" "$dst/usr/share/wallpapers/FabOS/contents/images_light/${f#*-}.png"; done
      install -Dm644 "$SRC/assets/wallpapers/screenshot.png" "$dst/usr/share/wallpapers/FabOS/contents/screenshot.png"
      install -Dm644 "$SRC/assets/sddm/background.png" "$dst/usr/share/sddm/themes/fabos/background.png"
      install -Dm644 "$SRC/assets/sddm/wordmark.png"   "$dst/usr/share/sddm/themes/fabos/wordmark.png"
      install -Dm644 "$SRC/assets/plymouth/mark.png"    "$dst/usr/share/sddm/themes/fabos/mark.png"
      install -Dm644 "$SRC/assets/sddm/background.png" "$dst/usr/share/plasma/look-and-feel/$LNF/contents/splash/images/background.png"
      install -Dm644 "$SRC/assets/plymouth/mark.png"    "$dst/usr/share/plasma/look-and-feel/$LNF/contents/splash/images/mark.png"
      install -Dm644 "$SRC/assets/plymouth/wordmark.png" "$dst/usr/share/plasma/look-and-feel/$LNF/contents/splash/images/wordmark.png"
      install -Dm644 "$SRC/assets/wallpapers/screenshot.png" "$dst/usr/share/plasma/look-and-feel/$LNF/contents/previews/preview.png"
      install -Dm644 "$SRC/assets/wallpapers/screenshot.png" "$dst/usr/share/plasma/look-and-feel/$LNF/contents/previews/splash.png"
      install -Dm644 "$SRC/assets/3d/fabos-mark.glb" "$dst/usr/share/fabos/3d/fabos-mark.glb"
      install -Dm644 "$SRC/assets/3d/fabos-mark.obj" "$dst/usr/share/fabos/3d/fabos-mark.obj"
      mkdir -p "$dst/usr/share/icons/FabOS" && cp -a "$SRC"/assets/icon-theme/. "$dst/usr/share/icons/FabOS/"
      mkdir -p "$dst/usr/share/plasma/desktoptheme/FabOS" && cp -a "$SRC"/assets/plasma-theme/. "$dst/usr/share/plasma/desktoptheme/FabOS/"
      ;;
    fabos-ai)
      install -Dm755 "$SRC/bin/aios"  "$dst/usr/bin/aios"
      install -Dm755 "$SRC/bin/aiosd" "$dst/usr/bin/aiosd"
      ;;
  esac
  # --- render templates in all text files ---
  while IFS= read -r -d '' f; do
    if grep -qI '@[A-Z_]*@' "$f" 2>/dev/null; then render "$f"; fi
  done < <(find "$dst" -type f -print0)
  # md5sums + permissions
  ( cd "$dst" && find . -path ./DEBIAN -prune -o -type f -print0 | xargs -0 md5sum | sed 's| \./| |' > DEBIAN/md5sums )
  find "$dst" -type d -exec chmod 755 {} +
  find "$dst" -path "$dst/DEBIAN" -prune -o -type f -exec chmod 644 {} +
  chmod 755 "$dst"/DEBIAN/post* "$dst"/DEBIAN/pre* 2>/dev/null || true
  chmod 755 "$dst"/usr/bin/* "$dst"/usr/lib/fabos/motd/* 2>/dev/null || true
  find "$dst/usr/lib/fabos" -type f \( -name "*.sh" -o -name "*.py" -o -name "rebrand-*" \) -exec chmod 755 {} + 2>/dev/null || true
  ver=$(sed -n 's/^Version: //p' "$dst/DEBIAN/control"); arch=$(sed -n 's/^Architecture: //p' "$dst/DEBIAN/control")
  dpkg-deb --root-owner-group -Zxz --build "$dst" "$OUT/${name}_${ver}_${arch}.deb"
done
ls -la "$OUT"
