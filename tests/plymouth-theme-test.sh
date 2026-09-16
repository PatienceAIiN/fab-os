#!/usr/bin/env bash
# Plymouth boot-splash theme check (two-step plugin), static on the tree + inside the built image:
#   1. static: fabos.plymouth parses as a key file, ModuleName=two-step, ImageDir is the install path, every image the plugin
#      reads is shipped (throbber-0001.png, watermark.png, bgrt-fallback.png, entry.png, bullet.png, lock.png, capslock.png),
#      every [two-step] / mode-section key is one plymouth 24.004's two-step plugin actually reads, the shutdown / reboot
#      titles are there, no fabos.script is left behind, the initramfs font hook parses (sh -n) and is executable in git,
#      fabos-branding Depends names the plugin's owner (plymouth-theme-spinner) and fonts-inter, the postinst runs
#      update-alternatives + update-initramfs and chmods the hook, PNG headers are sane (the bullet fits the entry), no
#      user-facing forbidden wording;
#   2. image: the theme is copied in with @VARS@ rendered, `update-alternatives --set` makes default.plymouth resolve to it,
#      two-step.so exists and `dpkg -S` owns it by the package in Depends, `plymouthd --debug` (no display, so the plugin is
#      never loaded — a dry run of the daemon's own config parse) reports the theme file, and the font hook, run against a
#      fake DESTDIR, copies Inter and refreshes the fontconfig cache. With FULL=1 it also runs `update-initramfs -u` inside
#      the throwaway container and `lsinitramfs` must list the theme images, two-step.so, label-pango.so and Inter.
#   A real boot with the theme (the only place the plugin renders) is the VM proof in docs/design/LOGIN-BOOT.md.
# Usage: tests/plymouth-theme-test.sh      Env: IMAGE=localhost/fabos:vm  FULL=0
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
IMAGE=${IMAGE:-localhost/fabos:vm}; FULL=${FULL:-0}
PKG=packages/fabos-branding; T=$PKG/usr/share/plymouth/themes/fabos; HOOK=$PKG/usr/share/initramfs-tools/hooks/fabos-plymouth-fonts
OUT=build/plymouth-theme-test; mkdir -p "$OUT"
fail=0
chk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }

echo "== 1. static checks on $T"
python3 - "$T" > "$OUT/static.log" 2>&1 <<'PY'
import configparser, os, struct, sys
t = sys.argv[1]; bad = []
cp = configparser.RawConfigParser(strict=True); cp.optionxform = str
cp.read(os.path.join(t, "fabos.plymouth"), encoding="utf-8")
def need(cond, msg):
    if not cond: bad.append(msg)
need(cp.get("Plymouth Theme", "ModuleName", fallback="") == "two-step", "ModuleName must be two-step")
need(cp.get("two-step", "ImageDir", fallback="") == "/usr/share/plymouth/themes/fabos", "ImageDir must be the install path")
TWO_STEP = {"Font", "TitleFont", "ImageDir", "HorizontalAlignment", "VerticalAlignment", "ProgressBarHorizontalAlignment",
            "ProgressBarVerticalAlignment", "WatermarkHorizontalAlignment", "WatermarkVerticalAlignment",
            "SecureBootHorizontalAlignment", "SecureBootVerticalAlignment", "DialogHorizontalAlignment", "DialogVerticalAlignment",
            "TitleHorizontalAlignment", "TitleVerticalAlignment", "Transition", "TransitionDuration", "BackgroundStartColor",
            "BackgroundEndColor", "ProgressBarBackgroundColor", "ProgressBarForegroundColor", "ProgressBarWidth", "ProgressBarHeight",
            "ScaleBackgroundImage", "DialogClearsFirmwareBackground", "MessageBelowAnimation", "ProgressFunction",
            "ShowAnimationPercent", "MonospaceFont", "ConsoleLogTextColor"}
MODE = {"SuppressMessages", "ProgressBarShowPercentComplete", "UseProgressBar", "UseFirmwareBackground", "UseAnimation",
        "UseEndAnimation", "Title", "SubTitle"}
MODES = {"boot-up", "shutdown", "reboot", "updates", "system-upgrade", "firmware-upgrade", "system-reset"}
for k in cp.options("two-step"):
    need(k in TWO_STEP, "unknown [two-step] key %s" % k)
for sec in cp.sections():
    if sec in ("Plymouth Theme", "two-step"): continue
    need(sec in MODES, "unknown section [%s]" % sec)
    for k in cp.options(sec): need(k in MODE, "unknown [%s] key %s" % (sec, k))
for sec, key, val in (("boot-up", "UseFirmwareBackground", "true"), ("two-step", "DialogClearsFirmwareBackground", "false")):
    need(cp.get(sec, key, fallback="") == val, "[%s] %s must be %s" % (sec, key, val))
need(cp.get("shutdown", "Title", fallback="").startswith("Shutting down"), "[shutdown] Title missing")
need(cp.get("reboot", "Title", fallback="").startswith("Restarting"), "[reboot] Title missing")
for a in ("DialogVerticalAlignment", "VerticalAlignment", "WatermarkVerticalAlignment", "TitleVerticalAlignment"):
    v = float(cp.get("two-step", a)); need(0 <= v <= 1, "%s out of range" % a)
need(float(cp.get("two-step", "TitleVerticalAlignment")) < float(cp.get("two-step", "VerticalAlignment")), "title must sit above the spinner")
def png(name):
    p = os.path.join(t, name)
    if not os.path.exists(p): bad.append("missing " + name); return (0, 0)
    b = open(p, "rb").read(24)
    if b[:8] != b"\x89PNG\r\n\x1a\n": bad.append(name + " is not a PNG"); return (0, 0)
    return struct.unpack(">II", b[16:24])
th = sorted(f for f in os.listdir(t) if f.startswith("throbber-") and f.endswith(".png"))
need(len(th) >= 24, "need at least 24 throbber frames (30 fps), have %d" % len(th))
need(th == ["throbber-%04d.png" % (i + 1) for i in range(len(th))], "throbber frames must be numbered throbber-0001.png …")
tw, thh = png(th[0]) if th else (0, 0); need(tw == thh and 48 <= tw <= 160, "throbber frame size %dx%d" % (tw, thh))
ew, eh = png("entry.png"); bw, bh = png("bullet.png"); lw, lh = png("lock.png"); cw, ch = png("capslock.png")
ww, wh = png("watermark.png"); fw, fh = png("bgrt-fallback.png")
need(0 < bh <= eh and 0 < bw <= ew / 4, "bullet %dx%d must fit the entry %dx%d" % (bw, bh, ew, eh))
need(lh <= eh, "lock taller than the entry"); need(ww > 60 and wh > 30, "watermark too small"); need(fw >= 96, "bgrt fallback too small")
need(not os.path.exists(os.path.join(t, "fabos.script")), "fabos.script must be gone (two-step only)")
for extra in ("box.png", "background.png", "background-tile.png"):
    need(not os.path.exists(os.path.join(t, extra)), extra + " would change the dialog / background layout")
print("throbber frames: %d (%dx%d); entry %dx%d bullet %dx%d lock %dx%d capslock %dx%d watermark %dx%d fallback %dx%d"
      % (len(th), tw, thh, ew, eh, bw, bh, lw, lh, cw, ch, ww, wh, fw, fh))
for b in bad: print("BAD", b)
sys.exit(1 if bad else 0)
PY
r=$?; cat "$OUT/static.log" | sed 's/^/      /'
chk "fabos.plymouth valid for the two-step plugin (keys, files, layout)" "[ $r = 0 ]"
chk "initramfs font hook parses and is executable"  "sh -n '$HOOK' && test -x '$HOOK' && grep -q '^PREREQ=\"plymouth\"' '$HOOK'"
chk "Depends: plymouth-theme-spinner + fonts-inter"  "grep -E '^Depends:' $PKG/DEBIAN/control | grep -q plymouth-theme-spinner && grep -E '^Depends:' $PKG/DEBIAN/control | grep -q fonts-inter"
chk "postinst: alternatives + initramfs + hook chmod" "grep -q 'update-alternatives --set default.plymouth /usr/share/plymouth/themes/fabos/fabos.plymouth' $PKG/DEBIAN/postinst && grep -q 'update-initramfs -u' $PKG/DEBIAN/postinst && grep -q 'chmod 755 /usr/share/initramfs-tools/hooks/fabos-plymouth-fonts' $PKG/DEBIAN/postinst"
chk "plymouthd.conf selects the theme"                "grep -q '^Theme=fabos' $PKG/usr/lib/fabos/plymouthd.conf"
chk "no forbidden wording"                            "! grep -rniE 'chatgpt|openai|gpt|snowui|sora|dall|download' '$T' $HOOK --include=*.plymouth --include=fabos-plymouth-fonts"

echo "== 2. inside $IMAGE"
cat > "$OUT/inimage.sh" <<'EOF'
set -u; fail=0
chk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
. /work/brand/brand.conf
T=/usr/share/plymouth/themes/fabos; rm -rf $T; cp -a /work/packages/fabos-branding/usr/share/plymouth/themes/fabos $T
sed -i -e "s|@DISTRO_NAME@|$DISTRO_NAME|g" -e "s|@VENDOR_NAME@|$VENDOR_NAME|g" -e "s|@HOME_URL@|$HOME_URL|g" $T/fabos.plymouth
install -m755 /work/packages/fabos-branding/usr/share/initramfs-tools/hooks/fabos-plymouth-fonts /usr/share/initramfs-tools/hooks/fabos-plymouth-fonts
update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth $T/fabos.plymouth 200 >/dev/null 2>&1
update-alternatives --set default.plymouth $T/fabos.plymouth >/dev/null 2>&1
chk "default.plymouth -> fabos"     "[ \$(readlink -f /usr/share/plymouth/themes/default.plymouth) = $T/fabos.plymouth ]"
P=\$(plymouth --get-splash-plugin-path)
chk "two-step.so present"           "test -f \${P}two-step.so && test -f \${P}label-pango.so"
owner=\$(dpkg -S \${P}two-step.so 2>/dev/null | cut -d: -f1)
chk "two-step.so owner (\$owner) is in Depends" "grep -E '^Depends:' /work/packages/fabos-branding/DEBIAN/control | grep -qw \"\$owner\""
chk "Inter installed for the labels"  "test -f /usr/share/fonts/opentype/inter/Inter-Regular.otf"
rm -f /r7out/plymouthd-dry.log
timeout 10 plymouthd --debug --debug-file=/r7out/plymouthd-dry.log --no-daemon --tty=/dev/console --kernel-command-line=splash --mode=boot --pid-file=/tmp/ply.pid >/dev/null 2>&1 &
sleep 2; timeout 4 plymouth --show-splash >/dev/null 2>&1; sleep 1; timeout 3 plymouth quit >/dev/null 2>&1; sleep 1
chk "plymouthd dry run resolves the theme file" "grep -q \"System configured theme file is '/usr/share/plymouth/themes//fabos/fabos.plymouth'\" /r7out/plymouthd-dry.log"
chk "plymouthd dry run: no key-file parse errors" "! grep -iE 'ply-key-file.*(error|malformed|unexpected)' /r7out/plymouthd-dry.log | grep -q ."
export DESTDIR=/tmp/ird; rm -rf \$DESTDIR; mkdir -p \$DESTDIR/etc/fonts
/usr/share/initramfs-tools/hooks/fabos-plymouth-fonts; hk=\$?
chk "font hook exit 0 and copies Inter" "[ \$hk = 0 ] && test -f \$DESTDIR/usr/share/fonts/opentype/inter/Inter-Regular.otf && test -f \$DESTDIR/usr/share/fonts/opentype/inter/Inter-Light.otf && ls \$DESTDIR/var/cache/fontconfig/*.cache-* >/dev/null 2>&1"
chk "font hook is a no-op without the plymouth fontconfig dir" "rm -rf \$DESTDIR && mkdir -p \$DESTDIR && /usr/share/initramfs-tools/hooks/fabos-plymouth-fonts && ! test -d \$DESTDIR/usr/share/fonts"
if [ "\${FULL:-0}" = 1 ]; then
  echo "-- FULL: update-initramfs -u (throwaway container)"
  update-initramfs -u -k all > /r7out/update-initramfs.log 2>&1; echo "update-initramfs exit=\$?" >> /r7out/update-initramfs.log
  chk "update-initramfs ran"         "grep -q 'exit=0' /r7out/update-initramfs.log"
  lsinitramfs /boot/initrd.img-* > /r7out/lsinitramfs.txt 2>/dev/null
  for f in usr/share/plymouth/themes/fabos/fabos.plymouth usr/share/plymouth/themes/fabos/throbber-0001.png usr/share/plymouth/themes/fabos/entry.png usr/share/plymouth/themes/fabos/watermark.png usr/share/plymouth/themes/fabos/bgrt-fallback.png usr/share/fonts/opentype/inter/Inter-Regular.otf usr/share/fonts/opentype/inter/Inter-Light.otf; do
    chk "initramfs has \$f" "grep -qx \"\$f\" /r7out/lsinitramfs.txt"
  done
  chk "initramfs has two-step.so + label-pango.so" "grep -q 'plymouth/two-step.so' /r7out/lsinitramfs.txt && grep -q 'plymouth/label-pango.so' /r7out/lsinitramfs.txt"
  chk "initramfs has no fabos.script" "! grep -q 'fabos/fabos.script' /r7out/lsinitramfs.txt"
fi
exit \$fail
EOF
# (the heredoc is quoted; the \$ escapes above are literal for the inner shell)
sed -i 's/\\\$/$/g' "$OUT/inimage.sh"
podman run --rm --network none -v "$ROOT:/work:Z" -v "$ROOT/$OUT:/r7out:Z" -e FULL="$FULL" "$IMAGE" bash /r7out/inimage.sh 2>&1 | tee "$OUT/inimage.log" | grep -E '^(PASS|FAIL|--)' ; r=${PIPESTATUS[0]}
[ "$r" = 0 ] || fail=1
exit $fail
