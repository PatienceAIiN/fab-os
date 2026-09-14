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
      # the identity icon: bare accent mark on transparency (no tile) — vector first, then every hicolor size up to 512
      for s in 16 22 24 32 48 64 128 256 512; do install -Dm644 "$SRC/assets/icons/fabos-$s.png" "$dst/usr/share/icons/hicolor/${s}x${s}/apps/fabos.png"; done
      install -Dm644 "$SRC/assets/icons/fabos.svg"          "$dst/usr/share/icons/hicolor/scalable/apps/fabos.svg"
      install -Dm644 "$SRC/assets/icons/fabos-symbolic.svg" "$dst/usr/share/icons/hicolor/scalable/apps/fabos-symbolic.svg"
      install -Dm644 "$SRC/assets/pixmaps/fabos-logo.png" "$dst/usr/share/pixmaps/fabos-logo.png"
      install -Dm644 "$SRC/assets/pixmaps/fabos-logo-dark.png" "$dst/usr/share/pixmaps/fabos-logo-dark.png"
      install -Dm644 "$SRC/assets/pixmaps/fabos.png"      "$dst/usr/share/pixmaps/fabos.png"        # 1024 px mark: About page, installer
      install -Dm644 "$SRC/assets/pixmaps/fabos-face.png" "$dst/usr/share/pixmaps/fabos-face.png"   # 512 px mark with a thin halo: default avatar
      # boot splash: 36 frames at 256 px + the 2x wordmark; fabos.script scales both to the screen (12 % / 11 % of its height)
      cp "$SRC"/assets/plymouth/spinner-*.png "$SRC/assets/plymouth/wordmark.png" "$dst/usr/share/plymouth/themes/fabos/"
      python3 - "$dst/usr/share/plymouth/themes/fabos" <<'PY'
import sys; from PIL import Image
d=sys.argv[1]; Image.new("RGBA",(8,8),(42,49,59,255)).save(d+"/bar-bg.png"); Image.new("RGBA",(8,8),(110,155,255,255)).save(d+"/bar-fg.png")
PY
      ;;
    fabos-desktop)
      LNF=in.patienceai.fabos.desktop
      # Wallpaper package: Plasma picks the file whose name best matches the screen (1280x800 … 3840x2160) and crops it
      # (PreserveAspectCrop). images/ = light set (default), images_dark/ = dark set (used with a dark colour scheme).
      for f in "$SRC"/assets/wallpapers/light-*.png; do b=$(basename "$f" .png); install -Dm644 "$f" "$dst/usr/share/wallpapers/FabOS/contents/images/${b#light-}.png"; done
      for f in "$SRC"/assets/wallpapers/dark-*.png;  do b=$(basename "$f" .png); install -Dm644 "$f" "$dst/usr/share/wallpapers/FabOS/contents/images_dark/${b#dark-}.png"; done
      install -Dm644 "$SRC/assets/pixmaps/fabos-face.png" "$dst/etc/skel/.face.icon"                  # default avatar: the mark, transparent, thin halo
      install -Dm644 "$SRC/assets/wallpapers/screenshot.png" "$dst/usr/share/wallpapers/FabOS/contents/screenshot.png"
      # Greeter + session splash: 3840x2160 background (QML crops it), vector mark (QML rasterises it at 2x the shown size)
      install -Dm644 "$SRC/assets/sddm/background.png" "$dst/usr/share/sddm/themes/fabos/background.png"
      install -Dm644 "$SRC/assets/icons/fabos.svg"     "$dst/usr/share/sddm/themes/fabos/mark.svg"
      install -Dm644 "$SRC/assets/sddm/background.png" "$dst/usr/share/plasma/look-and-feel/$LNF/contents/splash/images/background.png"
      install -Dm644 "$SRC/assets/icons/fabos.svg"     "$dst/usr/share/plasma/look-and-feel/$LNF/contents/splash/images/mark.svg"
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
    fabos-voice)
      # no generated assets (the whisper tiny.en model is fetched sha256-pinned in image/Containerfile); gate the build on a compile check
      python3 -m py_compile "$dst"/usr/lib/fabos/voice/*.py "$dst/usr/bin/fabos-voice" && find "$dst" -name __pycache__ -type d -prune -exec rm -rf {} +
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
  [ -f "$dst/etc/sudoers.d/fabos-agent" ] && chmod 0440 "$dst/etc/sudoers.d/fabos-agent"
  # tray-defaults is a shell script without an extension (autostart Exec=/usr/lib/fabos/tray-defaults): it needs the exec bit too
  find "$dst/usr/lib/fabos" -type f \( -name "*.sh" -o -name "*.py" -o -name "rebrand-*" -o -name "tray-defaults" \) -exec chmod 755 {} + 2>/dev/null || true
  ver=$(sed -n 's/^Version: //p' "$dst/DEBIAN/control"); arch=$(sed -n 's/^Architecture: //p' "$dst/DEBIAN/control")
  dpkg-deb --root-owner-group -Zxz --build "$dst" "$OUT/${name}_${ver}_${arch}.deb"
done
ls -la "$OUT"
