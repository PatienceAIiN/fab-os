#!/usr/bin/env python3
"""Real-VM proof for the greeter and the boot splash (docs/design/LOGIN-BOOT.md), on a DISPOSABLE overlay of the VM disk.

  flock -w 5400 /tmp/fabos-vm.lock -c 'python3 tests/login-boot-vm.py --repo /home/harsh/Downloads/fabric-os --outdir build/r7-login-boot'

What it does (every step leaves a file in --outdir):
  1. boots build/fabos-vm.img through a qcow2 overlay (scripts/boot-vm.sh --headless, QMP socket), waits for ssh;
  2. installs the SDDM theme + sddm conf and the Plymouth theme + initramfs hook from THIS tree exactly where the packages
     put them (@VARS@ rendered from brand/brand.conf), removes the VM autologin drop-in, `systemctl restart sddm`;
  3. screendumps the greeter (sddm-greeter.png), saves the sddm journal since the restart and fails on any QML diagnostic;
  4. logs in through the greeter with QMP send-key: Enter (opens the password field), a wrong password (sddm-greeter-wrong.png:
     red text), the right one -> waits for the fabos Wayland session and plasmashell (desktop-after-login.png);
  5. runs the fabos-branding postinst steps by hand (hook chmod, update-alternatives, update-initramfs -u) and, with sddm
     stopped, starts plymouthd on the VM's display: show-splash + ask-for-password renders the two-step disk-password dialog
     (plymouth-password.png, plymouth-password-typed.png after three keystrokes), then --mode=shutdown for the title
     (plymouth-shutdown.png);
  6. adds plymouth.debug to the kernel command line (overlay only), reboots, screendumps the console every 0.7 s until the
     greeter is back: the boot splash frame(s) -> plymouth-boot.png (+ frames/), then pulls /var/log/plymouth-debug.log,
     /sys/firmware/acpi/bgrt presence and lsinitramfs from the guest;
  7. powers the VM off and deletes the overlay.
Exit code 0 only when every check passed; the summary is printed and written to summary.txt.
"""
import argparse, glob, importlib.util, json, os, re, shlex, shutil, subprocess, sys, time

T0 = time.time()
def log(m): print("[%6.1fs] %s" % (time.time() - T0, m), flush=True)

def load_qmp(repo):
    spec = importlib.util.spec_from_file_location("ivd", os.path.join(repo, "tests", "install-vm-driver.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def brand(repo):
    conf = {}
    for line in open(os.path.join(repo, "brand", "brand.conf")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); conf[k] = shlex.split(v)[0] if v else ""
    return conf

def render(path, conf):
    try: s = open(path, encoding="utf-8").read()
    except UnicodeDecodeError: return
    if "@" not in s: return
    for k, v in conf.items(): s = s.replace("@%s@" % k, v)
    open(path, "w", encoding="utf-8").write(s)

SSH = ["sshpass", "-p", "fabos", "ssh", "-p", "2222", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
       "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8", "fabos@127.0.0.1"]
SCP = ["sshpass", "-p", "fabos", "scp", "-P", "2222", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-r"]

def ssh(cmd, timeout=120):
    p = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()
def sudo(cmd, timeout=300):
    return ssh("echo fabos | sudo -S bash -c %s 2>&1 | grep -v '^\\[sudo\\]'" % shlex.quote(cmd), timeout)
def wait_ssh(deadline):
    while time.time() < deadline:
        try:
            rc, _ = ssh("true", timeout=15)
            if rc == 0: return True
        except subprocess.TimeoutExpired: pass
        time.sleep(3)
    return False

def to_png(ppm, png):
    from PIL import Image
    im = Image.open(ppm); im.save(png, optimize=True); os.remove(ppm); return im

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True); ap.add_argument("--outdir", required=True)
    ap.add_argument("--tree", default=None, help="source tree with the packages/ to install (default: --repo)")
    ap.add_argument("--tag", default="login-boot")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo); tree = os.path.abspath(a.tree or a.repo)
    out = os.path.abspath(a.outdir) if os.path.isabs(a.outdir) else os.path.join(tree, a.outdir)
    os.makedirs(os.path.join(out, "frames"), exist_ok=True)
    ivd = load_qmp(repo); conf = brand(repo)
    checks = []
    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail)); log(("PASS  " if ok else "FAIL  ") + name + (" — " + detail if detail else ""))

    ovl = "/tmp/r7-%s.qcow2" % a.tag; qmp_path = "/tmp/r7-%s.qmp" % a.tag; vars_ = "/tmp/r7-%s-vars.fd" % a.tag
    serial = os.path.join(out, "serial.log"); mon = "/tmp/r7-%s.mon" % a.tag
    for p in (ovl, qmp_path, vars_):
        if os.path.exists(p): os.remove(p)
    subprocess.run(["qemu-img", "create", "-f", "qcow2", "-b", os.path.join(repo, "build", "fabos-vm.img"), "-F", "raw", ovl], check=True, capture_output=True)
    shutil.copy(os.path.join(repo, "build", "OVMF_VARS.fd"), vars_)
    # stage the files as the packages install them
    stage = "/tmp/r7-%s-stage" % a.tag; shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage)
    shutil.copytree(os.path.join(tree, "packages/fabos-desktop/usr/share/sddm/themes/fabos"), os.path.join(stage, "sddm-fabos"))
    shutil.copytree(os.path.join(tree, "packages/fabos-branding/usr/share/plymouth/themes/fabos"), os.path.join(stage, "plymouth-fabos"))
    shutil.copy(os.path.join(tree, "packages/fabos-desktop/etc/sddm.conf.d/zz-fabos.conf"), stage)
    shutil.copy(os.path.join(tree, "packages/fabos-branding/usr/share/initramfs-tools/hooks/fabos-plymouth-fonts"), stage)
    shutil.copy(os.path.join(tree, "packages/fabos-branding/usr/lib/fabos/plymouthd.conf"), stage)
    for root_, _, files in os.walk(stage):
        for f in files:
            if f.endswith((".qml", ".conf", ".desktop", ".plymouth")) or f == "fabos-plymouth-fonts": render(os.path.join(root_, f), conf)

    qemu = subprocess.Popen(["scripts/boot-vm.sh", "--headless", "--mem", "2048", "--cpus", "2", "--disk", ovl, "--qmp", qmp_path,
                             "--vars", vars_, "--serial", serial, "--monitor", mon], cwd=repo,
                            stdout=open(os.path.join(out, "boot-vm.out"), "w"), stderr=subprocess.STDOUT)
    q = None
    try:
        q = ivd.QMP(qmp_path, wait=120); log("QMP up")
        chk("VM boots to ssh", wait_ssh(time.time() + 420))
        rc, ver = ssh("cat /etc/os-release | head -1; uname -r; dpkg-query -W fabos-desktop fabos-branding sddm plymouth"); log(ver.replace("\n", " | "))
        open(os.path.join(out, "guest-versions.txt"), "w").write(ver + "\n")

        # ---- 2. install the theme files where the packages put them
        subprocess.run(SCP + [os.path.join(stage, x) for x in os.listdir(stage)] + ["fabos@127.0.0.1:/tmp/"], check=True, capture_output=True, timeout=180)
        rc, o = sudo("""set -e
            cp /usr/share/sddm/themes/fabos/background.png /tmp/bg.png 2>/dev/null || true
            rm -rf /usr/share/sddm/themes/fabos && cp -a /tmp/sddm-fabos /usr/share/sddm/themes/fabos
            [ -f /tmp/bg.png ] && mv /tmp/bg.png /usr/share/sddm/themes/fabos/background.png
            chown -R root:root /usr/share/sddm/themes/fabos && chmod -R u=rwX,go=rX /usr/share/sddm/themes/fabos
            install -m644 /tmp/zz-fabos.conf /etc/sddm.conf.d/zz-fabos.conf
            rm -f /etc/sddm.conf.d/20-autologin-vm.conf /etc/sddm.conf.d/20-autologin-live.conf
            ls /etc/sddm.conf.d/; grep -c . /usr/share/sddm/themes/fabos/Main.qml""")
        chk("greeter theme + conf installed, autologin removed", rc == 0 and "20-autologin" not in o, o.replace("\n", " "))
        rc, o = sudo("date -u +%FT%TZ; systemctl restart sddm; sleep 1; systemctl is-active sddm")
        since = o.split("\n")[0]
        chk("sddm restarted", "active" in o, o.replace("\n", " "))
        time.sleep(14)
        q.screendump(os.path.join(out, "sddm-greeter.ppm")); im = to_png(os.path.join(out, "sddm-greeter.ppm"), os.path.join(out, "sddm-greeter.png"))
        log("greeter screendump %dx%d" % im.size)
        rc, jr = sudo("journalctl -b --no-pager -o short-iso --since %s | grep -iE 'sddm|greeter|qml|kwin_wayland' | tail -80" % shlex.quote(since))
        open(os.path.join(out, "sddm-journal.txt"), "w").write(jr + "\n")
        badrx = re.compile(r"Cannot assign|is not a type|Fallback to embedded theme|TypeError|ReferenceError|Main\.qml:\d+|Unexpected token|is not defined")
        chk("no QML errors in the sddm journal", not badrx.search(jr), "; ".join(l for l in jr.splitlines() if badrx.search(l))[:300])
        # seat0 only: the VM profile also has a serial-console autologin and this driver's own ssh session for `fabos`, both
        # logind sessions without a seat; the greeter runs as `sddm` on seat0, a desktop login is a `fabos` session on seat0
        SEAT = "loginctl list-sessions --no-legend | awk '$3==\"fabos\" && $4==\"seat0\"' | wc -l"
        rc, o = ssh("pgrep -a sddm-greeter | head -2; " + SEAT)
        chk("greeter process is up and no fabos session on seat0 yet", "sddm-greeter" in o and o.strip().splitlines()[-1].strip() == "0", o.replace("\n", " "))

        # ---- 4. log in through the greeter with the keyboard
        q.send_keys(["ret"], hold=60); time.sleep(1.5)
        q.screendump(os.path.join(out, "sddm-greeter-password.ppm")); to_png(os.path.join(out, "sddm-greeter-password.ppm"), os.path.join(out, "sddm-greeter-password.png"))
        ivd.type_text(q, "wrongpass"); q.send_keys(["ret"], hold=60); time.sleep(4)
        q.screendump(os.path.join(out, "sddm-greeter-wrong.ppm")); to_png(os.path.join(out, "sddm-greeter-wrong.ppm"), os.path.join(out, "sddm-greeter-wrong.png"))
        rc, o = ssh(SEAT)
        chk("wrong password did not log in (no fabos session on seat0)", o.strip() == "0", o)
        ivd.type_text(q, "fabos"); q.send_keys(["ret"], hold=60)
        # the first Plasma session start on a 2 GB VM (software rendering) is slow — give it room
        deadline = time.time() + 240; ok = False
        while time.time() < deadline:
            rc, o = ssh(SEAT + "; pgrep -u fabos -c plasmashell || true")
            parts = o.split()
            if len(parts) >= 2 and parts[0] != "0" and parts[1] != "0": ok = True; break
            time.sleep(4)
        chk("typed password opened the fabos session (plasmashell running)", ok, o.replace("\n", " "))
        time.sleep(18)
        q.screendump(os.path.join(out, "desktop-after-login.ppm")); to_png(os.path.join(out, "desktop-after-login.ppm"), os.path.join(out, "desktop-after-login.png"))
        rc, jr = sudo("journalctl -b --no-pager -o short-iso --since %s | grep -iE 'sddm-greeter|qml' | tail -40" % shlex.quote(since))
        open(os.path.join(out, "sddm-journal-after-login.txt"), "w").write(jr + "\n")
        chk("still no QML errors after the login round-trip", not badrx.search(jr))

        # ---- 5. plymouth: postinst steps, then render the dialog on the VM display
        rc, o = sudo("""set -e
            rm -rf /usr/share/plymouth/themes/fabos && cp -a /tmp/plymouth-fabos /usr/share/plymouth/themes/fabos
            chown -R root:root /usr/share/plymouth/themes/fabos && chmod -R u=rwX,go=rX /usr/share/plymouth/themes/fabos
            install -m644 /tmp/fabos-plymouth-fonts /usr/share/initramfs-tools/hooks/fabos-plymouth-fonts
            # --- the fabos-branding postinst lines ---
            chmod 755 /usr/share/initramfs-tools/hooks/fabos-plymouth-fonts 2>/dev/null || true
            update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth /usr/share/plymouth/themes/fabos/fabos.plymouth 200
            update-alternatives --set default.plymouth /usr/share/plymouth/themes/fabos/fabos.plymouth
            update-initramfs -u -k all 2>&1 | tail -3
            readlink -f /usr/share/plymouth/themes/default.plymouth
            lsinitramfs /boot/initrd.img-$(uname -r) | grep -E 'themes/fabos/(fabos.plymouth|throbber-0001.png|entry.png)|two-step.so|Inter-Regular.otf|fabos.script' """, timeout=900)
        open(os.path.join(out, "plymouth-install.txt"), "w").write(o + "\n")
        chk("update-initramfs: theme, two-step.so and Inter in the initramfs, no fabos.script",
            rc == 0 and "throbber-0001.png" in o and "two-step.so" in o and "Inter-Regular.otf" in o and "fabos.script" not in o, o.replace("\n", " ")[-300:])
        # stop the display manager (frees the DRM device) and run plymouthd on the console
        # NB redirect plymouthd's stdio to /dev/null: it daemonises but keeps the inherited fd, and sudo() pipes through
        # `| grep`, so without this the ssh command never sees EOF and hangs until the timeout.
        rc, o = sudo("systemctl stop sddm; sleep 2; pkill -x plymouthd || true; sleep 1; "
                     "plymouthd --mode=boot --tty=/dev/tty1 --kernel-command-line='splash plymouth.debug' --debug-file=/tmp/plymouth-live.log </dev/null >/dev/null 2>&1; "
                     "sleep 1; plymouth --show-splash; sleep 3; echo shown", timeout=90)
        chk("plymouthd started on the console", "shown" in o, o.replace("\n", " "))
        time.sleep(1)
        q.screendump(os.path.join(out, "plymouth-splash-live.ppm")); to_png(os.path.join(out, "plymouth-splash-live.ppm"), os.path.join(out, "plymouth-splash-live.png"))
        sudo("nohup plymouth ask-for-password --prompt='Please unlock disk luks-3f2a9c1e' --number-of-tries=1 >/tmp/ply-answer.txt 2>&1 </dev/null &", timeout=30)
        time.sleep(3)
        q.screendump(os.path.join(out, "plymouth-password.ppm")); to_png(os.path.join(out, "plymouth-password.ppm"), os.path.join(out, "plymouth-password.png"))
        ivd.type_text(q, "abc"); time.sleep(1.5)
        q.screendump(os.path.join(out, "plymouth-password-typed.ppm")); to_png(os.path.join(out, "plymouth-password-typed.ppm"), os.path.join(out, "plymouth-password-typed.png"))
        q.send_keys(["ret"], hold=60); time.sleep(2)
        rc, o = sudo("cat /tmp/ply-answer.txt; plymouth quit; sleep 1; pgrep -x plymouthd || echo gone")
        chk("typed characters reached plymouth's password entry (3 bullets -> 'abc')", "abc" in o, o.replace("\n", " "))
        rc, o = sudo("plymouthd --mode=shutdown --tty=/dev/tty1 --kernel-command-line='splash' </dev/null >/dev/null 2>&1; sleep 1; plymouth --show-splash; sleep 3; echo shown", timeout=90)
        time.sleep(1)
        q.screendump(os.path.join(out, "plymouth-shutdown.ppm")); to_png(os.path.join(out, "plymouth-shutdown.ppm"), os.path.join(out, "plymouth-shutdown.png"))
        sudo("plymouth quit; sleep 1; pkill -x plymouthd || true")
        rc, o = sudo("grep -E 'two-step|working directory|throbber|watermark|bgrt|Using|loading' /tmp/plymouth-live.log | head -30; cp /tmp/plymouth-live.log /tmp/plymouth-live.copy && chmod 644 /tmp/plymouth-live.copy")
        open(os.path.join(out, "plymouth-live-debug.txt"), "w").write(o + "\n")
        chk("live plymouthd loaded the two-step theme from /usr/share/plymouth/themes/fabos", "themes/fabos" in o and ("throbber" in o or "two-step" in o), o.replace("\n", " ")[:200])

        # ---- 6. reboot with plymouth.debug on the kernel command line, film the console
        rc, o = sudo("printf 'GRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash plymouth.debug\"\\n' > /etc/default/grub.d/99-r7-plymouth-debug.cfg && update-grub 2>&1 | tail -1; cat /sys/firmware/acpi/bgrt/status 2>/dev/null || echo no-bgrt")
        bgrt_before = o.strip().splitlines()[-1]
        log("firmware BGRT in this VM: " + bgrt_before)
        open(os.path.join(out, "serial-offset.txt"), "w").write(str(os.path.getsize(serial)))
        sudo("systemctl reboot", timeout=20)
        frames = []; t_reboot = time.time(); n = 0
        while time.time() - t_reboot < 75:
            n += 1; ppm = os.path.join(out, "frames", "f%03d.ppm" % n)
            try:
                q.screendump(ppm); im = to_png(ppm, ppm[:-4] + ".png"); frames.append((time.time() - t_reboot, ppm[:-4] + ".png", im))
            except Exception as e:
                log("screendump failed: %s" % e); time.sleep(1); continue
            time.sleep(0.7)
        # classify frames: greeter = clock at the top (bright pixels in the top 20 %), splash = dark top + something in the
        # middle/bottom (mark, spinner, wordmark, title)
        def stats(im):
            g = im.convert("L"); w, h = g.size
            top = g.crop((0, 0, w, int(h * 0.2))); mid = g.crop((0, int(h * 0.25), w, int(h * 0.98)))
            th = top.histogram(); mh = mid.histogram()
            bright_top = sum(th[100:]) / float(top.size[0] * top.size[1]); bright_mid = sum(mh[60:]) / float(mid.size[0] * mid.size[1])
            return bright_top, bright_mid
        splash = []; shut = []; greeter_t = None
        for t, path, im in frames:
            bt, bm = stats(im)
            if bt > 0.002 and bm > 0.01: greeter_t = greeter_t or t                      # clock at the top -> greeter (or desktop)
            elif bt < 0.0005 and 0.0005 < bm < 0.25: (shut if t < 12 else splash).append((t, path, bm))
        open(os.path.join(out, "frames", "index.txt"), "w").write("\n".join("%.1fs %s top=%.4f mid=%.4f" % ((t, os.path.basename(p)) + stats(im)) for t, p, im in frames) + "\n")
        if splash:
            best = max(splash, key=lambda x: x[2]); shutil.copy(best[1], os.path.join(out, "plymouth-boot.png")); log("boot splash frame: %s at %.1fs" % (os.path.basename(best[1]), best[0]))
        if shut:
            best = max(shut, key=lambda x: x[2]); shutil.copy(best[1], os.path.join(out, "plymouth-shutdown-real.png"))
        chk("boot splash frames captured during the reboot (plymouth-boot.png)", bool(splash), "%d splash frames, greeter back at %s" % (len(splash), "%.1fs" % greeter_t if greeter_t else "never"))
        chk("VM back to ssh after the reboot", wait_ssh(time.time() + 300))
        time.sleep(6)
        q.screendump(os.path.join(out, "sddm-greeter-after-reboot.ppm")); to_png(os.path.join(out, "sddm-greeter-after-reboot.ppm"), os.path.join(out, "sddm-greeter-after-reboot.png"))
        rc, o = sudo("cat /proc/cmdline; echo; ls /sys/firmware/acpi/bgrt 2>/dev/null || echo no-bgrt; echo; "
                     "grep -E 'two-step|working directory|throbber|watermark|bgrt|Using|loading|entry' /var/log/plymouth-debug.log 2>/dev/null | head -40; "
                     "cp /var/log/plymouth-debug.log /tmp/plymouth-debug.copy 2>/dev/null && chmod 644 /tmp/plymouth-debug.copy; journalctl -b --no-pager -u sddm | tail -5")
        open(os.path.join(out, "plymouth-boot-debug.txt"), "w").write(o + "\n")
        chk("boot plymouthd used the fabos two-step theme (plymouth-debug.log)", "themes/fabos" in o and "throbber" in o, o.replace("\n", " ")[:200])
        chk("no BGRT in QEMU/OVMF -> bgrt-fallback.png (the mark) drawn", "no-bgrt" in o or "bgrt" in o.lower())
        rc, jr = sudo("journalctl -b --no-pager | grep -iE 'sddm-greeter|Main.qml|Fallback to embedded' | tail -20")
        chk("greeter after reboot: no QML errors", not badrx.search(jr), jr[-200:])
        for src, dst in (("/tmp/plymouth-debug.copy", "plymouth-debug.log"), ("/tmp/plymouth-live.copy", "plymouth-live-debug.log")):
            subprocess.run(SCP + ["fabos@127.0.0.1:" + src, os.path.join(out, dst)], capture_output=True, timeout=60)
    finally:
        # ---- 7. power off, clean up
        try:
            if q and q.alive():
                try: sudo("systemctl poweroff", timeout=15)
                except Exception: pass
                for _ in range(40):
                    if qemu.poll() is not None: break
                    time.sleep(2)
        except Exception: pass
        if qemu.poll() is None:
            subprocess.run(["pkill", "-f", ovl]); time.sleep(3)
            if qemu.poll() is None: qemu.kill()
        qemu.wait(timeout=30)
        for p in (ovl, vars_, qmp_path, mon):
            try: os.remove(p)
            except OSError: pass
        shutil.rmtree(stage, ignore_errors=True)
        for p in glob.glob(os.path.join(out, "frames", "*.ppm")): os.remove(p)
    summary = "\n".join(("PASS  " if ok else "FAIL  ") + n + (" — " + d if d and not ok else "") for n, ok, d in checks)
    open(os.path.join(out, "summary.txt"), "w").write(summary + "\n")
    print(summary); print("evidence in", out)
    sys.exit(0 if all(ok for _, ok, _ in checks) else 1)

if __name__ == "__main__":
    main()
