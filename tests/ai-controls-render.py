#!/usr/bin/env python3
"""Offscreen render + behaviour check for Fab AI Controls (packages/fabos-agent/.../command_center.py).

Starts fabos-agentd with the scripted provider in a temp dir, seeds one chat with a follow-up and a running task plus an
older chat, then constructs the window under a Fab Dark and a Fab Light QPalette, renders each to a QPixmap and saves
  <out>/ai-controls-dark.png  <out>/ai-controls-light.png  (+ ai-controls-{dialog,settings,approval,empty,edit}-{dark,light}.png,
  provider-{dark,light}.png = the AI-provider tab after a successful connection check)
and, on the live window, checks: a runtime colour-scheme switch restyles every surface; sidebar rows are reconciled in
place (widgets keep their identity when another chat appears, moves or disappears); the live action timeline spins for
the running step and shows checks + narration for finished ones; the provider tab has one dropdown / key / model,
the connection check (through a patched api) enables Save on success and blocks it after a rejected key; the approval
dialog hides the raw command behind "Show details" (ui.show_raw off, low risk) and Deny reaches the daemon; an approval
resolved elsewhere closes the dialog WITHOUT posting a decision; editing the chat's root message in place threads the
new version into the same chat; --task ID opens on that chat; Save in Settings with the daemon offline keeps the dialog open;
the GUI thread stays responsive while a daemon reply takes 2 s (every call runs on the window's worker thread);
generated images: a generate_image step whose output names a REAL PNG (written by tests/askbar-qml-harness/mkpng.py, no
python3-pil in the image) renders ONE image card (also when the final text names the same file), a step naming a missing
file renders none; the card opens the 80 % ImageViewer whose controls exist and degrade by the binaries found, Copy image
puts the picture on the clipboard, Save as copies through the (patched) file dialog and falls back to ~/Pictures, Open /
wallpaper launch their binaries, Regenerate posts the follow-up with the chat root as parent_id and closes the viewer
(ai-controls-image-{card,viewer}.png); the composer's Send is disabled on an empty / whitespace box and Enter posts nothing;
the cloud hint chip shows only for the local provider, Choose opens Settings › AI provider, dismissal lasts the session.
Exit code 0 only if every step ran without an exception. Needs PyQt6 — run it inside the image:
  podman run --rm -v $PWD:/work:Z -e QT_QPA_PLATFORM=offscreen localhost/fabos:vm python3 /work/tests/ai-controls-render.py /work/build

Quick passes (no chat seeding; the output directory stays the first argument):
  ... /work/tests/ai-controls-render.py /work/build --settings --welcome
  --settings  the compact Settings dialog in dark + light: every tab rendered (settings-{general,provider,voice,mail}-*.png,
              settings-general-advanced-*.png, settings-mail-{ok,fail,outlook}-*.png, settings-voice-*.png), per-tab heights against the
              560 px budget, the Mail Sign in -> app-password path -> Check connection (patched api) -> Save gating, the Voice
              check box with a fake fabos-voice (with and without `doctor` / `say --test`), and the composer mic toast reasons
              (exit 3 / exit 4 / missing binary) — the mic never fails silently; the General tab's Start-up row ("Ask for the
              disk password when the computer starts"): disabled with the reason on this unencrypted daemon, and against a patched
              api (encrypted): OFF opens the plain-words confirmation with the passphrase field + eye toggle
              (settings-startup-dialog-*.png) and posts the passphrase, ON posts nothing else, Cancel posts nothing, a refused
              passphrase puts the switch back with the reason, an unencrypted disk disables the row
  --welcome   the welcome wizard's Mail page (welcome-mail-*.png) and its "Use your own mail" button
"""
import json, os, shutil, sqlite3, subprocess, sys, tempfile, time, traceback, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "askbar-qml-harness"))
import mkpng                                    # the standard-library PNG writer shared with the ask bar harness
AGENT_DIR = os.path.join(ROOT, "packages/fabos-agent/usr/lib/fabos/agent")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build")
PORT = "18791"

SCHEMES = {   # from packages/fabos-desktop/DEBIAN/postinst (Fab Dark / Fab Light on Breeze structure)
    "dark": dict(window="#0F1420", base="#171D2B", alt="#1F2737", button="#1F2737", text="#FCFCFC", mid="#3D4658", highlight="#3B6EF5", hitext="#FFFFFF", placeholder="#8A93A4"),
    "light": dict(window="#F5F7FD", base="#FFFFFF", alt="#EAEEF8", button="#EAEEF8", text="#232629", mid="#B8BEC8", highlight="#3B6EF5", hitext="#FFFFFF", placeholder="#7A828F"),
}


def make_palette(QPalette, QColor, s):
    p = QPalette()
    R = QPalette.ColorRole
    for role, key in ((R.Window, "window"), (R.Base, "base"), (R.AlternateBase, "alt"), (R.Button, "button"), (R.Text, "text"), (R.WindowText, "text"),
                      (R.ButtonText, "text"), (R.ToolTipBase, "alt"), (R.ToolTipText, "text"), (R.Mid, "mid"), (R.Dark, "mid"), (R.Light, "alt"),
                      (R.Highlight, "highlight"), (R.HighlightedText, "hitext"), (R.PlaceholderText, "placeholder"), (R.Link, "highlight")):
        p.setColor(role, QColor(s[key]))
    return p


FAKE_VOICE = r'''#!/usr/bin/env python3
# fake fabos-voice for the offscreen render: %(variant)s
import json, sys
args = sys.argv[1:]
open(%(log)r, "a").write(" ".join(args) + "\n")
verbose = args[:1] == ["-v"]
if verbose:
    args = args[1:]
cmd = args[0] if args else ""
if cmd == "status":
    st = {"wake": False, "listening": False, "stt": "whisper.cpp", "tts": "espeak-ng", "mic": True, "mic_allowed": %(mic_allowed)r, "service": True,
          "mic_reason": "", "stt_reason": "", "tts_reason": ""}
    if verbose:
        st.update({"source": "alsa_input.pci-0000_00_1f.3.analog-stereo", "source_description": "Built-in Audio Analog Stereo"})
    print(json.dumps(st)); sys.exit(0)
if cmd == "doctor":
    if %(old)r:
        sys.stderr.write("usage: fabos-voice {listen-once,say,status,wake,chime}\nfabos-voice: error: argument cmd: invalid choice: 'doctor'\n"); sys.exit(2)
    print("microphone: alsa_input.pci-0000_00_1f.3.analog-stereo (PipeWire) — level ok")
    print("audio session: PipeWire 1.4 running for this login")
    print("speech-to-text: whisper.cpp tiny.en (/usr/share/fabos/voice/ggml-tiny.en.bin)")
    print("text-to-speech: espeak-ng"); print("wake word: pocketsphinx, 'hey fab' in dictionary"); sys.exit(0)
if cmd == "say":
    if "--test" in args and %(old)r:
        sys.stderr.write("fabos-voice say: error: unrecognized arguments: --test\n"); sys.exit(2)
    sys.exit(0)
if cmd == "listen-once":
    if "--progress" in args:      # 1.0-8: the live state file the composer polls
        p = args[args.index("--progress") + 1]
        open(p, "w").write(json.dumps({"state": "error", "reason": %(listen_err)r, "code": %(listen_code)d, "level": 0, "peak": 0, "speech": False}))
    sys.stderr.write(%(listen_err)r + "\n"); sys.exit(%(listen_code)d)
sys.exit(0)
'''


def write_fake_voice(path, log, variant="new", old=False, listen_err="No microphone found on this computer.", listen_code=3, mic_allowed=True):
    with open(path, "w") as f:
        f.write(FAKE_VOICE % {"variant": variant, "log": log, "old": old, "listen_err": listen_err, "listen_code": listen_code, "mic_allowed": mic_allowed})
    os.chmod(path, 0o755)


def quick_passes():
    """--settings / --welcome: the compact Settings dialog (every tab, dark + light, height budget 560 px, the Mail sign-in /
    check / Save gating, the Voice check box and the mic toast reasons) and the welcome wizard's Mail page, offscreen.
    Fast (no chat seeding); prints 'settings-height dark=N light=N'. Exit 0 only if every assertion held."""
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fabos-settings-render-")
    env = dict(os.environ, XDG_RUNTIME_DIR=tmp, FABOS_AGENT_DATA=os.path.join(tmp, "data"), XDG_CONFIG_HOME=os.path.join(tmp, "cfg"),
               FABOS_AGENT_PROVIDER="fake", FABOS_AGENT_PORT=PORT, HOME=os.path.join(tmp, "home"), PATH="/usr/bin:/bin",
               FABOS_GOOGLE_OAUTH_ENV=os.path.join(tmp, "no-google-oauth.env"))
    os.makedirs(env["HOME"])
    proc = subprocess.Popen([sys.executable, os.path.join(AGENT_DIR, "fabos_agentd.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    rc = 1
    heights = {}
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/health" % PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("daemon did not start")
        os.environ["XDG_RUNTIME_DIR"] = tmp
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, AGENT_DIR)
        import command_center as cc
        from PyQt6.QtWidgets import QApplication, QDialog
        from PyQt6.QtGui import QPalette, QColor, QFont
        from PyQt6.QtCore import QEvent
        app = QApplication(sys.argv)
        app.setFont(QFont("Inter", 10))

        def spin(n=10):
            for _ in range(n):
                app.processEvents()

        def settle(ms=400):
            deadline = time.time() + ms / 1000.0
            while time.time() < deadline:
                app.processEvents()
                time.sleep(0.01)

        def wait_for(pred, what, timeout=8):
            deadline = time.time() + timeout
            while time.time() < deadline:
                app.processEvents()
                if pred():
                    return
                time.sleep(0.02)
            raise AssertionError("timed out waiting for " + what)
        vlog = os.path.join(tmp, "voice.log")
        fake_new, fake_old = os.path.join(tmp, "fabos-voice"), os.path.join(tmp, "fabos-voice-old")
        write_fake_voice(fake_new, vlog); write_fake_voice(fake_old, vlog, variant="old CLI without doctor / --test", old=True)
        fake_nostt = os.path.join(tmp, "fabos-voice-nostt"); write_fake_voice(fake_nostt, vlog, listen_err="Speech recognition is not available: no offline model and no cloud provider key.", listen_code=4)
        real_api = cc.api
        # pure mapping first: the toast text for every failure class
        vf = cc.voice_failure_text
        assert vf(3, "Sorry, I did not catch that. Say it once more?\n") == "Sorry, I did not catch that. Say it once more?"
        assert vf(3, "").startswith("Microphone is muted or silent"), vf(3, "")
        assert vf(4, "") == "Speech engine missing — run fabos-voice doctor" and vf(4, "No microphone found on this computer.") == "No microphone found — plug one in or check Fab Settings › Sound"
        assert vf(4, "Not enough free memory for offline speech recognition right now (it needs about 210 MB).") == "Speech recognition needs 210 MB free — close some apps"
        assert vf(4, "Microphone is off in Settings. Allow it in Fab AI Controls › Settings › Voice (“Allow Fab OS to use the microphone”).").startswith(cc.MIC_OFF_TIP)
        assert cc.voice_phase_text("starting") == "Starting the microphone…" and cc.voice_phase_text("recording") == "Listening… speak now" and cc.voice_phase_text("recording", True) == "Listening…"
        assert vf(127, "").startswith("Speech engine missing") and "not installed" in vf(None, "")
        assert vf(4, "pw-record failed: Connection refused").startswith("No audio session — ")
        if "--settings" in sys.argv or "--welcome" not in sys.argv:
            for name, scheme in SCHEMES.items():
                app.setPalette(make_palette(QPalette, QColor, scheme))
                w = cc.AIControls()
                w.resize(1280, 800)
                w.show()
                spin()
                # the window's own `fabos-voice status` probe (started in Voice.__init__) overwrites voice.status when it
                # finishes: let it finish first, or the fixture below is replaced a moment later and the Voice tab's
                # buttons follow the image's real voice status (a race that made this pass flaky)
                wait_for(lambda: w.voice._status_proc is None, "initial voice probe")
                w.voice.bin = fake_new
                w.voice.status = {"stt": "whisper.cpp", "tts": "espeak-ng", "mic": True, "wake": False, "listening": False}
                w.update_voice_buttons()
                settings = cc.api("GET", "/settings")
                assert settings["mail_provider_order"][0] == "gmail" and not settings["mail_oauth"]["google"], settings.get("mail_oauth")
                sd = cc.SettingsDialog(w, settings, w.voice)
                sd.show()
                spin()
                sd.refit()
                spin()

                def fits(what):
                    """No clipped rows: the dialog is at least as tall as its layout needs at its real width, and within the 560 px budget."""
                    lay = sd.layout(); need = lay.totalHeightForWidth(sd.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
                    assert sd.height() >= need, "%s: dialog %d px tall but its content needs %d (clipped rows)" % (what, sd.height(), need)
                    assert sd.height() <= 560, "%s: dialog %d px tall, budget 560" % (what, sd.height())
                    return sd.height()
                per_tab = {}
                for i in range(sd.tabs.count()):        # the dialog fits the CURRENT tab; every tab must stay inside the 560 px budget
                    sd.tabs.setCurrentIndex(i); spin(); sd.refit(); spin()
                    per_tab[sd.tabs.tabText(i)] = sd.height()
                sd.tabs.setCurrentIndex(0); spin(); sd.refit(); spin()
                heights[name] = max(per_tab.values())
                print("[%s] settings dialog %dx%d; per tab %s (budget 560)" % (name, sd.width(), sd.height(), per_tab))
                assert heights[name] <= 560, "settings dialog is %d px tall on its tallest tab, budget 560: %s" % (heights[name], per_tab)
                # --- compact first level: General = mode + System-Wide AI; everything else collapsed under Advanced
                assert sd.tabs.count() == 4 and [sd.tabs.tabText(i) for i in range(4)] == ["General", "AI provider", "Voice", "Mail"]
                assert sd.mode.isVisible() and sd.ai_switch.isVisible() and sd.ai_switch.isChecked()
                for adv in (sd.general_adv, sd.provider_adv, sd.voice_adv, sd.mail_adv):
                    assert not adv.is_open() and not adv.content.isVisible(), "Advanced must start collapsed"
                assert not sd.persona.isVisible() and not sd.show_raw.isVisible() and not sd.max_turns.isVisible() and not sd.result_limit.isVisible()
                sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-general-%s.png" % name))
                sd.general_adv.set_open(True); spin(); sd.refit(); spin()
                assert sd.persona.isVisible() and sd.show_raw.isVisible() and sd.max_turns.isVisible(), "Advanced did not reveal the general extras"
                sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-general-advanced-%s.png" % name))
                sd.general_adv.set_open(False); spin()
                # --- Start-up row ("Ask for the disk password when the computer starts"). Its state comes from GET /system/disk-unlock;
                # this daemon runs in a container (no installed helper, or an unencrypted root): the switch is disabled and the note says why
                wait_for(lambda: sd.boot_worker is None, "disk-unlock status")
                assert sd.boot_prompt.isVisible() and not sd.boot_prompt.isEnabled(), sd.boot_note.text()
                assert sd.boot_note.text().startswith(cc.SettingsDialog.BOOT_PROMPT_ON) and ("unavailable" in sd.boot_note.text() or "not encrypted" in sd.boot_note.text()), sd.boot_note.text()
                # an encrypted computer (patched api): the switch is on and live; OFF opens the plain-words confirmation with the passphrase
                # field + eye toggle and posts {prompt_at_boot: false, passphrase}; ON posts {prompt_at_boot: true} and asks nothing;
                # Cancel posts nothing; a refused passphrase puts the switch back and explains; an unencrypted disk disables the row
                from PyQt6.QtCore import QTimer
                du_state = {"encrypted": True, "device": "luks-abc", "prompt_at_boot": True, "consistent": True, "keyfile_present": False, "available": True, "risk": "CRITICAL"}
                du_calls = []

                def du_api(method, path, body=None, timeout=5):
                    if path != "/system/disk-unlock":
                        return real_api(method, path, body)
                    if method == "GET":
                        return dict(du_state)
                    du_calls.append(dict(body))
                    if body.get("prompt_at_boot") is False and body.get("passphrase") != "fabos-test":
                        return {"ok": False, "prompt_at_boot": True, "error": "the passphrase was not accepted for luks-abc", "risk": "CRITICAL", "exit_code": 3}
                    du_state["prompt_at_boot"] = body["prompt_at_boot"]
                    return {"ok": True, "prompt_at_boot": body["prompt_at_boot"], "risk": "CRITICAL", "exit_code": 0}
                cc.api = du_api
                seen = {}
                poll = QTimer(); poll.setInterval(30)

                def drive():
                    for d in app.topLevelWidgets():
                        if isinstance(d, cc.DiskUnlockDialog) and d.isVisible():
                            seen.setdefault("dialogs", 0); seen["dialogs"] += 1
                            seen["title"], seen["text"] = d.title.text(), d.message.text()
                            seen["confirm_disabled_at_open"] = not d.confirm_btn.isEnabled()
                            seen["echo"] = d.pw.echoMode(); d.eye.click(); seen["echo_eye"] = d.pw.echoMode(); d.eye.click(); seen["echo_back"] = d.pw.echoMode()
                            act = seen.get("act", "confirm")
                            if act == "cancel":
                                d.cancel_btn.click()
                            else:
                                d.pw.setText(seen.get("typed", "fabos-test")); seen["confirm_enabled_after_typing"] = d.confirm_btn.isEnabled()
                                if "shot" not in seen:
                                    spin(); pm = d.grab(); assert pm.save(os.path.join(OUT, "settings-startup-dialog-%s.png" % name)); seen["shot"] = (pm.width(), pm.height())
                                d.confirm_btn.click()
                        elif isinstance(d, cc.RoundedDialog) and d.isVisible() and d is not sd and not isinstance(d, cc.SettingsDialog) and d.title.text().startswith("Couldn't change"):
                            seen["info"] = d.message.text(); d.confirm_btn.click()
                try:
                    sd3 = cc.SettingsDialog(w, settings, w.voice); sd3.show(); spin()
                    wait_for(lambda: sd3.boot_worker is None, "disk-unlock status (encrypted)")
                    assert sd3.boot_prompt.isEnabled() and sd3.boot_prompt.isChecked() and sd3.boot_note.text() == cc.SettingsDialog.BOOT_PROMPT_ON, sd3.boot_note.text()
                    sd3.refit(); spin(); fits("general tab with the Start-up row")
                    poll.timeout.connect(drive); poll.start()
                    sd3.boot_prompt.click()                     # -> toggled(False) -> the modal confirmation; drive() fills and confirms it
                    wait_for(lambda: sd3.boot_worker is None and du_calls, "disk-unlock off")
                    assert seen.get("title") == "Stop asking for the disk password?" and seen.get("text") == cc.DiskUnlockDialog.TEXT, seen
                    for must in ("Your files stay encrypted on the drive", "anyone who starts this computer can use it without a password", "unencrypted /boot partition", "only where the computer itself is secure"):
                        assert must in seen["text"].replace("⁠", ""), must           # the word joiner only keeps "/boot" on one line
                    assert seen["confirm_disabled_at_open"] and seen["confirm_enabled_after_typing"], seen
                    assert (seen["echo"], seen["echo_eye"], seen["echo_back"]) == (cc.QLineEdit.EchoMode.Password, cc.QLineEdit.EchoMode.Normal, cc.QLineEdit.EchoMode.Password), seen
                    assert du_calls[-1] == {"prompt_at_boot": False, "passphrase": "fabos-test"}, du_calls
                    assert not sd3.boot_prompt.isChecked() and sd3.boot_prompt.isEnabled() and sd3.boot_note.text().startswith(cc.SettingsDialog.BOOT_PROMPT_OFF) and sd3.boot_note.text().endswith("done."), sd3.boot_note.text()
                    print("[%s] Start-up confirmation dialog rendered %dx%d (settings-startup-dialog-%s.png)" % (name, seen["shot"][0], seen["shot"][1], name))
                    n = len(du_calls)
                    sd3.boot_prompt.click()                     # back ON: no dialog, one POST
                    wait_for(lambda: sd3.boot_worker is None and len(du_calls) > n, "disk-unlock on")
                    assert du_calls[-1] == {"prompt_at_boot": True} and sd3.boot_prompt.isChecked() and "asks for the disk password again" in sd3.boot_note.text(), (du_calls, sd3.boot_note.text())
                    assert seen["dialogs"] == 1, seen["dialogs"]
                    seen["act"] = "cancel"; n = len(du_calls)
                    sd3.boot_prompt.click(); settle(200)
                    assert len(du_calls) == n and sd3.boot_prompt.isChecked() and seen["dialogs"] == 2, "Cancel must post nothing and leave the switch on"
                    seen["act"] = "confirm"; seen["typed"] = "wrong-one"; n = len(du_calls)
                    sd3.boot_prompt.click()
                    wait_for(lambda: sd3.boot_worker is None and len(du_calls) > n and "info" in seen, "disk-unlock refused")
                    assert sd3.boot_prompt.isChecked() and "not accepted" in sd3.boot_note.text() and "not changed" in sd3.boot_note.text(), sd3.boot_note.text()
                    assert "not accepted" in seen["info"] and "still asks for the disk password" in seen["info"], seen["info"]
                    sd3.reject(); spin()
                    du_state["encrypted"] = False; du_state["prompt_at_boot"] = False
                    sd4 = cc.SettingsDialog(w, settings, w.voice); sd4.show(); spin()
                    wait_for(lambda: sd4.boot_worker is None, "disk-unlock status (unencrypted)")
                    assert not sd4.boot_prompt.isEnabled() and not sd4.boot_prompt.isChecked() and sd4.boot_note.text() == cc.SettingsDialog.BOOT_NOT_ENCRYPTED, sd4.boot_note.text()
                    sd4.reject(); spin()
                finally:
                    poll.stop(); cc.api = real_api
                # --- AI provider: dropdown, key, Check connection on the first level; model / endpoint / requirement under Advanced
                sd.tabs.setCurrentIndex(1); spin()
                assert sd.provider.isVisible() and sd.key.isVisible() and sd.check_btn.isVisible() and sd.help.isVisible()
                assert not sd.model.isVisible() and not sd.base_url.isVisible() and not sd.require_check.isVisible()
                assert [sd.provider.itemData(i) for i in range(sd.provider.count())] == ["claude", "gemini", "openai", "deepseek", "local", "ollama"]
                sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-provider-%s.png" % name))
                sd.provider.setCurrentIndex(4); spin()
                assert sd.provider_adv.is_open() and sd.base_url.isVisible() and sd.base_url.text() == "http://127.0.0.1:8080/v1", "Local must open Advanced with the endpoint"
                sd.provider.setCurrentIndex(0); spin(); sd.provider_adv.set_open(False); spin()
                assert not sd.base_url.isVisible() and sd.model.text() == "claude-opus-5"
                # --- Voice: Hey Fab on/off, speak replies, Voice check + Test voice; wake word text etc. under Advanced
                sd.tabs.setCurrentIndex(2); spin()
                assert sd.voice_enabled.isVisible() and sd.speak_replies.isVisible() and sd.doctor_btn.isVisible() and sd.test_voice_btn.isVisible()
                # --- the microphone permission row (1.0-8): switch + indicator with the PipeWire device name, rendered ON and OFF
                assert sd.mic_allowed.isVisible() and sd.mic_indicator.isVisible()
                assert not sd.mic_allowed.isChecked(), "a fresh daemon database has the permission OFF"
                assert sd.mic_indicator.text().startswith("○") and "off — nothing records" in sd.mic_indicator.text(), sd.mic_indicator.text()
                assert "Built-in Audio Analog Stereo" in sd.mic_indicator.text(), sd.mic_indicator.text()
                sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-voice-micoff-%s.png" % name))
                sd.mic_allowed.setChecked(True); spin()
                assert sd.mic_indicator.text().startswith("●") and sd.mic_indicator.text().endswith("— allowed"), sd.mic_indicator.text()
                assert not sd.wake_word.isVisible() and not sd.offline_only.isVisible() and not sd.cloud_voice.isVisible() and not sd.doctor_box.isVisible()
                assert sd.doctor_btn.isEnabled() and sd.test_voice_btn.isEnabled(), (sd.doctor_btn.isEnabled(), sd.test_voice_btn.isEnabled())
                sd.doctor_btn.click()
                assert not sd.doctor_btn.isEnabled() and sd.doctor_box.isVisible()
                wait_for(lambda: sd.doctor_proc is None and sd.doctor_btn.isEnabled(), "fabos-voice doctor")
                assert "PipeWire" in sd.doctor_box.text() and "whisper.cpp" in sd.doctor_box.text() and sd.doctor_box.objectName() == "raw", sd.doctor_box.text()
                sd.test_voice_btn.click()
                wait_for(lambda: w.voice.say_proc is None, "say --test")
                settle(150)
                sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-voice-%s.png" % name))
                # an older fabos-voice without doctor / --test degrades to status + plain say, and says so
                w.voice.bin = fake_old
                sd2 = cc.SettingsDialog(w, settings, w.voice); sd2.show(); spin(); sd2.tabs.setCurrentIndex(2); spin()
                sd2.doctor_btn.click(); wait_for(lambda: sd2.doctor_proc is None and sd2.doctor_btn.isEnabled(), "doctor fallback", 10)
                assert sd2.doctor_box.text().startswith("(no 'doctor' in this fabos-voice; status only)") and '"stt": "whisper.cpp"' in sd2.doctor_box.text(), sd2.doctor_box.text()
                open(vlog, "w").close()
                sd2.test_voice_btn.click(); wait_for(lambda: w.voice.say_proc is None and not w.voice._test_fallback, "say --test fallback", 10)
                settle(200)
                calls = open(vlog).read().splitlines()
                assert calls[0] == "say --test" and len(calls) >= 2 and calls[1].startswith("say Namaste"), calls
                sd2.reject(); w.voice.bin = fake_new
                # --- Mail: provider (Gmail first), address, Sign in; without Google OAuth the app-password path appears with a 3-step hint
                sd.tabs.setCurrentIndex(3); spin()
                assert [sd.mail_provider.itemData(i) for i in range(sd.mail_provider.count())] == ["gmail", "outlook", "yahoo", "zoho", "icloud", "other"]
                assert sd.mail_provider.itemText(0) == "Gmail" and sd.mail_provider.currentData() == "gmail"
                assert sd.mail_address.isVisible() and sd.mail_signin_btn.isVisible() and sd.mail_signin_btn.text() == "Sign in"
                assert not sd.mail_pw.isVisible() and not sd.mail_hint.isVisible() and not sd.mail_check_btn.isVisible(), "password path must be hidden until Sign in"
                assert not sd.m["mail.smtp_host"].isVisible() and sd.m["mail.smtp_host"].text() == "smtp.gmail.com" and sd.m["mail.smtp_port"].text() == "587"
                assert sd.confirm_btn.isEnabled(), "Save allowed when nothing changed"
                sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-mail-%s.png" % name))
                sd.mail_signin_btn.click(); spin(); sd.refit(); spin()
                assert sd.mail_pw.isVisible() and sd.mail_hint.isVisible() and sd.mail_check_btn.isVisible() and not sd.mail_signin_btn.isVisible()
                mail_states = {"gmail app-password path": fits("Mail: app-password path")}
                top = lambda wd: wd.mapTo(sd, wd.rect().topLeft()).y()
                order = [top(x) for x in (sd.mail_address, sd.mail_pw, sd.mail_hint, sd.mail_check_btn)]
                assert order == sorted(order) and len(set(order)) == 4, "rows must read Address, App password, hint, Check connection (y = %r)" % order
                assert sd.mail_result.text() == "No Google sign-in on this build — use an app password instead." and "GOOGLE_OAUTH_CLIENT_ID" in sd.mail_result.toolTip(), (sd.mail_result.text(), sd.mail_result.toolTip())
                hint = sd.mail_hint.text()
                assert "1. " in hint and "2. " in hint and "3. " in hint and "App passwords" in hint and "2-Step Verification" in hint, hint
                sd.mail_address.setText("me@gmail.com"); sd.mail_address.textEdited.emit("me@gmail.com")
                sd.mail_pw.setText("abcd efgh ijkl mnop"); sd.mail_pw.textEdited.emit("abcd efgh ijkl mnop"); spin()
                assert not sd.confirm_btn.isEnabled() and "mail" in sd.save_note.text().lower(), (sd.confirm_btn.isEnabled(), sd.save_note.text())
                mail_calls = []

                def fake_api(method, path, body=None, timeout=5):
                    if path == "/mail/test":
                        mail_calls.append(body)
                        if body.get("provider") == "outlook":
                            return {"ok": True, "detail": "Signed in (sending only)", "smtp": {"ok": True, "detail": "signed in at smtp-mail.outlook.com:587"},
                                    "imap": {"ok": False, "skipped": True, "login_disabled": True, "detail": "Outlook / Hotmail has switched off password sign-in for IMAP (LOGINDISABLED) — sending with the app password works, reading the inbox does not"},
                                    "latency_ms": 388, "provider": "outlook", "address": body["address"], "auth": "password"}
                        if body.get("password") == "abcd efgh ijkl mnop":
                            return {"ok": True, "detail": "Signed in", "smtp": {"ok": True, "detail": "signed in at smtp.gmail.com:587"}, "imap": {"ok": True, "detail": "signed in at imap.gmail.com:993"},
                                    "latency_ms": 412, "provider": body["provider"], "address": body["address"], "auth": "password"}
                        return {"ok": False, "detail": "wrong password — Gmail needs an app password, not your account password", "smtp": {"ok": False, "detail": "wrong password — Gmail needs an app password, not your account password", "code": 535},
                                "imap": {"ok": False, "detail": "wrong password — Gmail needs an app password, not your account password"}, "latency_ms": 640, "provider": body["provider"], "address": body["address"], "auth": "password"}
                    return real_api(method, path, body)
                cc.api = fake_api
                try:
                    sd.mail_check_btn.click()
                    assert not sd.mail_check_btn.isEnabled() and sd.mail_mark.state == "busy"
                    wait_for(lambda: sd.mail_worker is None, "mail check")
                    assert mail_calls and mail_calls[0]["provider"] == "gmail" and mail_calls[0]["address"] == "me@gmail.com" and mail_calls[0]["password"] == "abcd efgh ijkl mnop"
                    assert mail_calls[0]["mail.smtp_host"] == "smtp.gmail.com" and mail_calls[0]["mail.imap_host"] == "imap.gmail.com", mail_calls[0]
                    assert sd.mail_mark.state == "ok" and sd.mail_result.text() == "Signed in · SMTP ✓ · IMAP ✓ · 412 ms", (sd.mail_mark.state, sd.mail_result.text())
                    assert sd.confirm_btn.isEnabled() and not sd.save_note.isVisible(), "Save must be enabled after a successful mail check"
                    spin(); assert not sd.mail_hint.isVisible(), "the app-password hint is noise once the check passed"
                    mail_states["gmail ok"] = fits("Mail: gmail ok")
                    settle(600)
                    sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-mail-ok-%s.png" % name))
                    # a different password invalidates the check; a rejected one shakes the field, says why, blocks Save
                    sd.mail_pw.setText("my-normal-password"); sd.mail_pw.textEdited.emit("my-normal-password")
                    assert not sd.confirm_btn.isEnabled() and sd.mail_mark.state == "idle"
                    sd.mail_check_btn.click(); wait_for(lambda: sd.mail_worker is None, "mail check 2")
                    assert sd.mail_mark.state == "fail" and sd.mail_result.text().startswith("Wrong password — Gmail needs an app password"), sd.mail_result.text()
                    assert getattr(sd.mail_pw, "_shake_anim", None) is not None, "no shake on the password field"
                    spin(); assert sd.mail_hint.isVisible(), "the hint comes back after a failed check"
                    mail_states["gmail wrong password"] = fits("Mail: gmail wrong password")
                    assert not sd.confirm_btn.isEnabled() and sd.save_note.isVisible() and "failed" in sd.save_note.text()
                    settle(600)
                    sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-mail-fail-%s.png" % name))
                    # presets follow the dropdown (Advanced fields auto-filled), "other" clears them
                    sd.mail_provider.setCurrentIndex(2); spin()
                    assert (sd.m["mail.smtp_host"].text(), sd.m["mail.smtp_port"].text(), sd.mail_security.currentData(), sd.m["mail.imap_host"].text()) == ("smtp.mail.yahoo.com", "465", "ssl", "imap.mail.yahoo.com")
                    assert sd.mail_pw_label.text() == "App password" and "Yahoo" in sd.mail_hint.text() and sd.mail_mark.state == "idle"
                    sd.mail_provider.setCurrentIndex(5); spin()
                    assert sd.m["mail.smtp_host"].text() == "" and sd.mail_pw_label.text() == "Password" and sd.mail_provider.currentData() == "other"
                    # Outlook: the preset note warns that IMAP password sign-in is off; a sending-only check still enables Save and says why
                    sd.mail_provider.setCurrentIndex(1); spin()
                    assert "cannot check its inbox" in sd.mail_hint.text() and sd.m["mail.imap_host"].placeholderText() == "none = sending only", sd.mail_hint.text()
                    sd.mail_address.setText("me@outlook.com"); sd.mail_address.textEdited.emit("me@outlook.com")
                    sd.mail_pw.setText("outlook-app-pw"); sd.mail_pw.textEdited.emit("outlook-app-pw")
                    assert not sd.confirm_btn.isEnabled()
                    spin(); sd.refit(); spin(); mail_states["outlook before the check (hint + note)"] = fits("Mail: outlook hint + note")
                    sd.mail_check_btn.click(); wait_for(lambda: sd.mail_worker is None, "mail check outlook")
                    assert sd.mail_mark.state == "ok" and sd.mail_result.text().startswith("Signed in · SMTP ✓ · IMAP off · 388 ms\n") and "switched off password sign-in" in sd.mail_result.text(), sd.mail_result.text()
                    assert sd.confirm_btn.isEnabled(), "Save must be enabled for a sending-only account"
                    settle(600); sd.refit(); spin()
                    assert not sd.mail_hint.isVisible()
                    mail_states["outlook sending-only result"] = fits("Mail: outlook sending-only")
                    print("[%s] Mail tab states (px, budget 560, none clipped): %s" % (name, mail_states))
                    heights[name] = max([heights[name]] + list(mail_states.values()))
                    sp = sd.grab(); assert sp.save(os.path.join(OUT, "settings-mail-outlook-%s.png" % name))
                    sd.mail_address.setText("me@gmail.com"); sd.mail_address.textEdited.emit("me@gmail.com")
                    sd.mail_provider.setCurrentIndex(0); spin()
                    # Save after a good check: PUT carries the account, no Brevo keys, servers equal to the preset are stored as "auto" (empty)
                    sd.mail_pw.setText("abcd efgh ijkl mnop"); sd.mail_pw.textEdited.emit("abcd efgh ijkl mnop")
                    sd.mail_check_btn.click(); wait_for(lambda: sd.mail_worker is None, "mail check 3")
                    assert sd.confirm_btn.isEnabled()
                    puts, posts = [], []

                    def save_api(method, path, body=None, timeout=5):
                        if method == "PUT":
                            puts.append(body); return {"ok": True}
                        if method == "POST":
                            posts.append((path, body)); return {"ok": True, "storage": "systemd-creds"}
                        return real_api(method, path, body)
                    cc.api = save_api
                    sd.save()
                    assert not sd.confirm_btn.isEnabled() and sd.confirm_btn.text() == "Saving…" and not sd.cancel_btn.isEnabled(), "Save must disable both buttons while its worker runs"
                    wait_for(lambda: sd.save_worker is None, "save worker"); spin()
                    assert sd.result() == QDialog.DialogCode.Accepted, "Save did not accept after a good check"
                    body = puts[0]
                    assert body["mail.provider"] == "gmail" and body["mail.address"] == "me@gmail.com" and body["ai.enabled"] == "true" and body["mode"] == "auto", body
                    assert body["mail.smtp_host"] == "" and body["mail.smtp_security"] == "" and body["mail.imap_port"] == "", "preset-equal servers must be stored as auto"
                    assert "mail.transport" not in body and "mail.from" not in body and "mail.user" not in body, sorted(body)
                    assert ("/secrets", {"name": "mail_password", "value": "abcd efgh ijkl mnop"}) in posts and not any(p[1].get("name") == "mail_api_key" for p in posts), posts
                finally:
                    cc.api = real_api
                # --- the microphone permission OFF: the composer mic is live but dimmed (struck glyph, MIC_OFF_TIP) and a click opens
                # Settings › Voice instead of recording (permissions only enable)
                w.voice.bin = fake_new; w.voice.status = {"stt": "whisper.cpp", "tts": "espeak-ng", "mic": True, "mic_allowed": False, "service": True}
                w.update_voice_buttons()
                assert w.mic_btn.isEnabled() and w.mic_btn.blocked and w.mic_btn.toolTip() == cc.MIC_OFF_TIP and w.mic_btn.glyph == "mic-off", (w.mic_btn.isEnabled(), w.mic_btn.toolTip(), w.mic_btn.glyph)
                opened = []
                real_open = w.open_settings; w.open_settings = lambda tab=None: opened.append(tab)
                try:
                    w.toggle_listen(); spin(3)
                finally:
                    w.open_settings = real_open
                assert opened == ["voice"] and w.voice.listen_proc is None and w.toast.text().startswith(cc.MIC_OFF_TIP), (opened, w.toast.text())
                sp = w.grab(); assert sp.save(os.path.join(OUT, "ai-controls-mic-off-%s.png" % name))
                # --- the composer mic never fails silently: fabos-voice's stderr reason lands in the toast
                w.voice.bin = fake_new; w.voice.status = {"stt": "whisper.cpp", "tts": "espeak-ng", "mic": True}
                w.update_voice_buttons(); assert w.mic_btn.isEnabled() and not w.mic_btn.blocked and w.mic_btn.glyph == "mic"
                w.toggle_listen(); wait_for(lambda: w.voice.listen_proc is None, "listen-once exit 3")
                assert w.toast.isVisible() and w.toast.text() == "No microphone found — plug one in or check Fab Settings › Sound", w.toast.text()
                assert w.voice._progress_last and w.voice._progress_last.get("state") == "error", "the composer polls the progress file of the listen run"
                w.voice.bin = fake_nostt; w.voice.status = {"stt": "whisper.cpp", "tts": "espeak-ng", "mic": True}
                w.toggle_listen(); wait_for(lambda: w.voice.listen_proc is None, "listen-once exit 4")
                assert w.toast.text().startswith("Speech recognition is not available") and "Voice check" in w.toast.text(), w.toast.text()
                assert w.voice.status["stt"] == "none" and not w.mic_btn.isEnabled(), "exit 4 must mark speech-to-text unavailable"
                w.voice.bin = os.path.join(tmp, "no-such-fabos-voice"); w.voice.status = {"stt": "whisper.cpp", "tts": "espeak-ng", "mic": True}
                w.update_voice_buttons(); w.toggle_listen(); wait_for(lambda: w.voice.listen_proc is None, "missing binary")
                assert w.toast.text().startswith("Speech engine missing"), w.toast.text()
                w.voice.bin = fake_new
                w.close(); w.deleteLater(); app.sendPostedEvents(None, QEvent.Type.DeferredDelete); spin(5)
            print("settings-height dark=%d light=%d" % (heights["dark"], heights["light"]))
        if "--welcome" in sys.argv or "--settings" not in sys.argv:
            welcome_dir = os.path.join(ROOT, "packages/fabos-welcome/usr/lib/fabos/welcome")
            sys.path.insert(0, welcome_dir)
            import fabos_welcome as fw
            for name, scheme in SCHEMES.items():
                app.setPalette(make_palette(QPalette, QColor, scheme))
                wz = fw.build_wizard(app)
                wz.show(); spin()
                titles = []
                for _ in range(6):
                    titles.append(wz.currentPage().findChildren(cc.QLabel)[0].text())
                    if titles[-1] == "Your mail, your account":
                        break
                    wz.next(); spin()
                assert titles[-1] == "Your mail, your account", titles
                assert titles[-2] == "The Fab OS agent", "the Mail page must follow the AI page: %r" % titles
                btn = [b for b in wz.currentPage().findChildren(cc.QPushButton) if b.objectName() == "mailSetup"]
                assert btn and btn[0].text().startswith("Use your own mail (Gmail, Outlook, Yahoo, Zoho, iCloud)"), [b.text() for b in wz.currentPage().findChildren(cc.QPushButton)]
                texts = " ".join(l.text() for l in wz.currentPage().findChildren(cc.QLabel))
                assert "Nothing passes through Patience AI" in texts and "Fab AI Controls › Settings › Mail" in texts, texts
                spin(); px = wz.grab(); assert px.save(os.path.join(OUT, "welcome-mail-%s.png" % name))
                print("[%s] welcome mail page %dx%d" % (name, px.width(), px.height()))
                wz.next(); spin(); assert wz.currentPage().findChildren(cc.QLabel)[0].text() == "You're ready"
                wz.close(); wz.deleteLater(); app.sendPostedEvents(None, QEvent.Type.DeferredDelete); spin(5)
        rc = 0
    except Exception:
        traceback.print_exc()
        try:
            print("--- daemon output ---"); proc.terminate(); print(proc.communicate(timeout=5)[0][-3000:])
        except Exception:
            pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except Exception:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return rc


def main():
    if "--settings" in sys.argv or "--welcome" in sys.argv:
        sys.exit(quick_passes())
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fabos-ai-controls-render-")
    env = dict(os.environ, XDG_RUNTIME_DIR=tmp, FABOS_AGENT_DATA=os.path.join(tmp, "data"), XDG_CONFIG_HOME=os.path.join(tmp, "cfg"),
               FABOS_AGENT_PROVIDER="fake", FABOS_AGENT_PORT=PORT, HOME=os.path.join(tmp, "home"), PATH="/usr/bin:/bin")
    os.makedirs(env["HOME"])
    proc = subprocess.Popen([sys.executable, os.path.join(AGENT_DIR, "fabos_agentd.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    rc = 1
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/health" % PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("daemon did not start")
        os.environ["XDG_RUNTIME_DIR"] = tmp                 # command_center.api() reads token/port from here
        os.environ["HOME"] = env["HOME"]                    # so ~/Pictures/Fab OS/… in the seeded final text is the test's home
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, AGENT_DIR)
        import command_center as cc
        from PyQt6.QtWidgets import QApplication, QDialog
        from PyQt6.QtGui import QPalette, QColor, QFont, QGuiApplication
        from PyQt6.QtCore import QPoint, QTimer, QEvent

        def wait_until(app, pred, what, timeout=8):
            deadline = time.time() + timeout
            while time.time() < deadline:
                app.processEvents()
                if pred():
                    return
                time.sleep(0.02)
            raise AssertionError("timed out waiting for " + what)

        def wait(tid, states=("done", "failed", "cancelled", "waiting_approval", "waiting_user"), timeout=40):
            for _ in range(timeout * 5):
                t = cc.api("GET", "/tasks/%d" % tid)
                if t.get("status") in states:
                    return t
                time.sleep(0.2)
            raise RuntimeError("task %d stuck" % tid)

        def spin(app, n=10):
            for _ in range(n):
                app.processEvents()

        def sync(win, timeout_ms=8000):
            """Every daemon call of the window runs on its worker thread: wait until all of them are back (they chain:
            a created task refreshes the list, which refreshes the chat). Slow rounds are printed; a timeout names the
            jobs still queued (their coalescing keys) so a re-fetch loop or a stuck daemon call can be told apart."""
            t0 = time.time()
            ok = win.flush_api(timeout_ms)
            if time.time() - t0 > 2:
                print("    (sync took %.1f s; inflight now %d)" % (time.time() - t0, win.api_q.inflight))
            assert ok, "daemon calls still in flight after %d ms: inflight=%d queued keys=%r" % (timeout_ms, win.api_q.inflight, list(win.api_q._pending))

        def close_window(app, win):
            """Tear a window down the way the event loop would (deferred delete), so no zombie widgets survive into the
            next scheme; a leftover window with running timers is exactly what used to abort the light pass."""
            win.close()
            win.deleteLater()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            spin(app, 5)

        # --- seed: a chat with a follow-up and a running task; a second, older chat
        T0 = time.time()
        root = cc.api("POST", "/tasks", {"request": "show me the system", "mode": "bypass"})["id"]
        wait(root)
        fu = cc.api("POST", "/tasks", {"request": "and now open the editor and write hello from Fab OS", "parent_id": root, "mode": "bypass"})["id"]
        wait(fu)
        run = cc.api("POST", "/tasks", {"request": "long sleep please", "parent_id": root, "mode": "bypass"})["id"]
        other = cc.api("POST", "/tasks", {"request": "open editor and write a note for the light theme", "mode": "bypass"})["id"]
        wait(other)
        db = sqlite3.connect(os.path.join(env["FABOS_AGENT_DATA"], "agent.db"))
        db.execute("UPDATE tasks SET created=created-3*86400, updated=updated-3*86400 WHERE id=?", (other,))
        # a Markdown answer (list, inline code, fenced code block) so the render exercises the Markdown bubble
        md = "Here is what I found:\n\n- Kernel **Linux 6.x**, up for 3 days\n- Note saved to `~/Documents/fabos-note.txt`\n\n```bash\nuname -a; date\n```\n\nRun `fabos status` any time for a summary."
        db.execute("INSERT INTO steps(task_id,ts,kind,name,input,output,risk,decision) VALUES(?,?,?,?,?,?,?,?)", (fu, time.time(), "assistant", "fake", "", md, "", ""))
        # a generated image: a REAL PNG (mkpng, standard library only) named by a finished generate_image step AND by the final
        # text (one card, not two); a second step naming a file that was never written (no card)
        img_dir = os.path.join(env["HOME"], "Pictures", "Fab OS")
        os.makedirs(img_dir, exist_ok=True)
        img_path = mkpng.write_png(os.path.join(img_dir, "fab-test-image.png"))
        img_prompt = "a red kite over a green hill at sunrise"
        db.execute("INSERT INTO steps(task_id,ts,kind,name,input,output,risk,decision) VALUES(?,?,?,?,?,?,?,?)",
                   (fu, time.time(), "tool_call", "generate_image", json.dumps({"prompt": img_prompt}),
                    json.dumps({"path": img_path, "width": 640, "height": 400, "provider": "fake", "prompt": img_prompt}), "LOW", "auto-approved"))
        db.execute("INSERT INTO steps(task_id,ts,kind,name,input,output,risk,decision) VALUES(?,?,?,?,?,?,?,?)",
                   (fu, time.time(), "tool_call", "generate_image", json.dumps({"prompt": "a second one"}),
                    json.dumps({"path": os.path.join(img_dir, "never-written.png"), "width": 640, "height": 400, "provider": "fake", "prompt": "a second one"}), "LOW", "auto-approved"))
        db.execute("INSERT INTO steps(task_id,ts,kind,name,input,output,risk,decision) VALUES(?,?,?,?,?,?,?,?)",
                   (fu, time.time(), "final", "", "", "Your kite is saved at ~/Pictures/Fab OS/fab-test-image.png.", "", ""))
        db.commit()
        db.close()
        for _ in range(25):        # let the sleeping task reach 'running'
            if cc.api("GET", "/tasks/%d" % run).get("status") == "running":
                break
            time.sleep(0.2)

        print("seeded at +%.1fs; run status: %s" % (time.time() - T0, cc.api("GET", "/tasks/%d" % run).get("status")))
        app = QApplication(sys.argv)
        app.setFont(QFont("Inter", 10))
        print("QApplication ready at +%.1fs" % (time.time() - T0))
        results = {}
        real_api = cc.api
        for name, scheme in SCHEMES.items():
            app.setPalette(make_palette(QPalette, QColor, scheme))
            fits = {"n": 0}
            orig_fit = cc.MarkdownView.heightForWidth

            def counted_fit(self_, *a, _o=orig_fit):
                fits["n"] += 1
                return _o(self_, *a)
            cc.MarkdownView.heightForWidth = counted_fit
            t1 = time.time()
            if os.environ.get("FABOS_RENDER_PROFILE"):
                import cProfile, pstats
                pr = cProfile.Profile()
                pr.enable()
                w = cc.AIControls()
                pr.disable()
                pstats.Stats(pr).sort_stats("cumulative").print_stats(18)
            else:
                w = cc.AIControls()
            w.resize(1280, 800)
            w.show()
            spin(app)
            print("[%s] window constructed in %.1fs" % (name, time.time() - t1))
            t1 = time.time()
            w.refresh_list()
            sync(w)
            w.select_conversation(root)
            sync(w)
            print("[%s] conversation selected in %.1fs (fits=%d)" % (name, time.time() - t1, fits["n"]))
            t1 = time.time()
            deadline = time.time() + 1.2
            while time.time() < deadline:                 # typing indicator ticks, fade-ins, layout settle
                app.processEvents()
                time.sleep(0.02)
            print("[%s] settle loop %.1fs (fits=%d)" % (name, time.time() - t1, fits["n"]))
            cc.MarkdownView.heightForWidth = orig_fit
            print("[%s] tasks:" % name, [(t["id"], t["status"], t.get("parent_id")) for t in w.tasks], "chat root:", w.current_root, "composer glyph:", w.send_btn.glyph)
            # --- the GUI thread never waits on the daemon: with a /status reply that takes 2 s (slept on the worker thread), a
            # 100 ms QTimer on the GUI thread must keep ticking (>= 15 times in 2.3 s), refresh_list() must return at once and
            # the window must still repaint (grab) meanwhile. With the calls on the GUI thread this loop saw ~3 ticks.
            slow = {"n": 0}

            def slow_api(method, path, body=None, *a, _o=real_api, **k):
                if path == "/status":
                    slow["n"] += 1
                    time.sleep(2.0)
                return _o(method, path, body, *a, **k)
            ticks = {"n": 0}
            tick = QTimer()
            tick.setInterval(100)
            tick.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
            cc.api = slow_api
            grabs = 0
            try:
                tick.start()
                t_call = time.time()
                w.refresh_list()
                call_ms = (time.time() - t_call) * 1000
                deadline = time.time() + 2.3
                while time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                    if grabs < 2 and ticks["n"] >= 5 * (grabs + 1):
                        assert not w.grab().isNull(), "window did not repaint while the daemon reply was pending"
                        grabs += 1
                tick.stop()
                sync(w)
            finally:
                tick.stop()
                cc.api = real_api
            assert slow["n"] >= 1, "the slow /status was never requested"
            assert call_ms < 200, "refresh_list() blocked the GUI thread for %.0f ms" % call_ms
            assert ticks["n"] >= 15, "GUI thread starved during a 2 s daemon reply: only %d timer ticks in 2.3 s" % ticks["n"]
            assert grabs == 2, "window repaint checks did not run (%d)" % grabs
            print("[%s] GUI thread responsive during a 2 s daemon reply: %d ticks of a 100 ms timer in 2.3 s, refresh_list() returned in %.1f ms, %d repaints" % (name, ticks["n"], call_ms, grabs))
            assert w.stack.currentWidget() is w.view, "conversation view not shown"
            assert len(w.view.turns) == 3, "expected 3 turns, got %d" % len(w.view.turns)
            assert w.send_btn.glyph == "stop", "send button should be STOP while the last task runs (glyph=%s)" % w.send_btn.glyph
            assert w.view.turns[run].typing.isVisible(), "typing indicator should be visible for the running task"
            assert w.view.turns[fu].chip.isVisible() and w.view.turns[fu].chip.button.text().startswith("Worked:"), "worked chip missing"
            assert "Ran a command" in "".join(l.text() for l in w.view.turns[root].chip.panel.findChildren(cc.QLabel)), "friendly label missing"
            assert not any("uname" in l.text() for l in w.view.turns[root].chip.panel.findChildren(cc.QLabel)), "raw command leaked with ui.show_raw off"
            # live action feed: the running task's timeline is open, its step shows a spinner + present-tense label + narration;
            # the finished task's rows carry a check mark and the "done" narration; the header chip names the provider
            live = w.view.turns[run].chip
            assert live.button.text().startswith("Working:") and live.panel.isVisible() and live.button.isChecked(), (live.button.text(), live.panel.isVisible())
            live_rows = [live.rows[s] for s in live.order]
            assert live_rows and live_rows[-1].state == "busy" and live_rows[-1].mark.spinner.isActive(), "running step should spin"
            assert live_rows[-1].title.text() == "Running a command", live_rows[-1].title.text()
            assert live_rows[-1].narration.isVisible() and live_rows[-1].narration.text() == "Running a command for you.", live_rows[-1].narration.text()
            done_rows = [w.view.turns[fu].chip.rows[s] for s in w.view.turns[fu].chip.order]
            assert done_rows and all(r.state == "ok" for r in done_rows), [r.state for r in done_rows]
            assert any(r.title.text().startswith("Opened Fab Editor") for r in done_rows), [r.title.text() for r in done_rows]
            assert any(r.narration.text() == "Done, Fab Editor is open." for r in done_rows), [r.narration.text() for r in done_rows]
            assert not any(r.raw.isVisible() for r in done_rows), "raw output shown with ui.show_raw off"
            assert w.provider_chip.text() == "Test provider · ready", w.provider_chip.text()
            # --- generated images: ONE card for the real file (its step and the final text both name it), none for the missing file
            assert any(r.title.text() == "Created an image" for r in done_rows), [r.title.text() for r in done_rows]
            turn_fu = w.view.turns[fu]
            assert list(turn_fu.image_cards) == [img_path], list(turn_fu.image_cards)
            card = turn_fu.image_cards[img_path]
            assert card.isVisible() and card.caption.text() == img_prompt and card.meta.text() == "Test provider · 640 × 400", (card.caption.text(), card.meta.text())
            pm = card.thumb.pixmap()
            assert pm is not None and not pm.isNull() and pm.height() <= 320 and pm.width() <= card.width(), (pm.width(), pm.height(), card.width())
            spin(app)
            assert card.width() == pm.width() + cc.ImageCard.PAD_X, "the card hugs its picture, border included (card %d, thumbnail %d)" % (card.width(), pm.width())
            assert card.thumb.width() == pm.width() and card.caption.x() == card.thumb.x() and card.meta.x() == card.thumb.x(), \
                "caption and provider line sit under the picture's left edge (thumb %d/%d wide at x %d, caption x %d, meta x %d)" % (card.thumb.width(), pm.width(), card.thumb.x(), card.caption.x(), card.meta.x())
            assert abs(pm.width() / pm.height() - 1.6) < 0.02, "the 640x400 file keeps its aspect ratio (%dx%d)" % (pm.width(), pm.height())
            assert cc.image_paths_in_text("see ~/Pictures/Fab OS/a b.png, file:///home/u/Pictures/Fab%20OS/c.jpg and /tmp/x.png") == ["~/Pictures/Fab OS/a b.png", "/home/u/Pictures/Fab OS/c.jpg"]
            assert cc.image_from_step({"kind": "tool_call", "name": "generate_image", "decision": "auto-approved", "input": "{}", "output": json.dumps({"path": "/p/k.png", "provider": "x"})}) == {"path": "/p/k.png", "prompt": "", "provider": "x", "width": 0, "height": 0}
            assert cc.image_from_step({"kind": "tool_call", "name": "generate_image", "decision": "auto-approved", "input": "{}", "output": ""}) is None
            cpx = card.grab()
            assert cpx.save(os.path.join(OUT, "ai-controls-image-card%s.png" % ("" if name == "dark" else "-light")))
            results["image-card-" + name] = (cpx.width(), cpx.height())
            # the enlarge viewer: 80 % of the screen, every control present; binary-dependent ones follow what the machine has
            viewer = turn_fu.open_image(card)
            spin(app)
            sg = w.screen().availableGeometry()
            assert viewer.isVisible() and viewer.width() == int(sg.width() * 0.8) and viewer.height() == int(sg.height() * 0.8), (viewer.width(), viewer.height(), sg)
            b = viewer.buttons
            assert set(b) == {"save", "copy", "open", "wallpaper", "regenerate", "close"}, sorted(b)
            assert b["copy"].isEnabled() and b["save"].isEnabled() and b["regenerate"].isEnabled() and b["close"].isEnabled()
            assert b["open"].isEnabled() == bool(shutil.which("gwenview") or shutil.which("xdg-open")), ("open", b["open"].isEnabled())
            assert b["wallpaper"].isEnabled() == bool(shutil.which("plasma-apply-wallpaperimage")), ("wallpaper", b["wallpaper"].isEnabled())
            if not b["wallpaper"].isEnabled():
                assert "plasma-apply-wallpaperimage" in b["wallpaper"].toolTip(), b["wallpaper"].toolTip()
            if not b["open"].isEnabled():
                assert "gwenview" in b["open"].toolTip(), b["open"].toolTip()
            b["copy"].click()
            spin(app)
            ci = QGuiApplication.clipboard().image()
            assert not ci.isNull() and (ci.width(), ci.height()) == (640, 400), "Copy image did not put the picture on the clipboard"
            assert viewer.toast.text() == "Image copied", viewer.toast.text()
            dest = os.path.join(tmp, "saved copy.png")
            orig_dlg = cc.QFileDialog.getSaveFileName
            cc.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (dest, "Images"))
            try:
                b["save"].click()
            finally:
                cc.QFileDialog.getSaveFileName = orig_dlg
            assert os.path.isfile(dest) and open(dest, "rb").read() == open(img_path, "rb").read(), "Save as did not copy the file"
            assert viewer.toast.text() == "Saved to " + dest, viewer.toast.text()
            os.remove(dest)

            def no_dialog(*a, **k):
                raise RuntimeError("no file dialog here")
            cc.QFileDialog.getSaveFileName = staticmethod(no_dialog)
            try:
                b["save"].click()
            finally:
                cc.QFileDialog.getSaveFileName = orig_dlg
            fallback = os.path.join(env["HOME"], "Pictures", "fab-test-image.png")
            assert os.path.isfile(fallback) and viewer.toast.text() == "Saved to " + fallback, ("fallback copy in ~/Pictures", viewer.toast.text())
            os.remove(fallback)
            launched = []

            class FakeProc:
                def poll(self):
                    return None
            orig_popen = cc.subprocess.Popen
            cc.subprocess.Popen = lambda argv, *a, **k: (launched.append(list(argv)), FakeProc())[1]
            try:
                if b["open"].isEnabled():
                    b["open"].click()
            finally:
                cc.subprocess.Popen = orig_popen
            if b["open"].isEnabled():
                assert launched and launched[0][-1] == img_path and os.path.basename(launched[0][0]) in ("gwenview", "xdg-open"), launched
                assert viewer.toast.text().startswith("Opened in"), viewer.toast.text()
            if b["wallpaper"].isEnabled():       # for real: without a plasmashell the tool fails and the viewer says so (graceful, never silent)
                b["wallpaper"].click()
                assert viewer.toast.text() == "Setting the wallpaper…", viewer.toast.text()
                wait_until(app, lambda: viewer.toast.text() != "Setting the wallpaper…", "plasma-apply-wallpaperimage to finish", 20)
                assert viewer.toast.text() in ("Wallpaper set", "Could not set the wallpaper"), viewer.toast.text()
                print("[%s] wallpaper outcome in the container: %s" % (name, viewer.toast.text()))
            viewer.toast.setText("")
            vpx = viewer.grab()
            assert vpx.save(os.path.join(OUT, "ai-controls-image-viewer%s.png" % ("" if name == "dark" else "-light")))
            results["image-viewer-" + name] = (vpx.width(), vpx.height())
            regen_posts = []
            # a second click on the card while its viewer is open only raises the same viewer — and must not wire Regenerate
            # a second time (that once posted TWO follow-ups from ONE click)
            assert turn_fu.open_image(card) is viewer and turn_fu.open_image(card) is viewer, "an open viewer is reused, not re-created"
            regen_emits = []
            turn_fu.regenerate_requested.connect(lambda tid: regen_emits.append(tid))

            def regen_api(method, path, body=None, *a, _o=real_api, **k):
                if method == "POST" and path == "/tasks":
                    regen_posts.append(body)
                    return {"id": 99999, "status": "queued"}          # not a real task: the fake provider must not act on it
                return _o(method, path, body, *a, **k)
            cc.api = regen_api
            try:
                b["regenerate"].click()
                spin(app)
                sync(w)
            finally:
                cc.api = real_api
            assert regen_emits == [fu], "ONE Regenerate click after three card clicks must emit once: %r" % regen_emits
            assert regen_posts == [{"request": cc.REGENERATE_REQUEST, "parent_id": root}], regen_posts
            assert not viewer.isVisible(), "Regenerate must close the viewer"
            assert card.viewer is None, "the card forgot its closed viewer"
            w.refresh_list()
            sync(w)
            # the image ships fabos-voice since ISO 1.0 rev 2, so the "no voice" state is forced here rather than assumed
            w.voice.bin, w.voice.status = None, {"stt": "none", "tts": "none", "mic": False, "wake": False, "listening": False}
            w.update_voice_buttons()
            assert not w.mic_btn.isEnabled() and w.mic_btn.toolTip() == cc.VOICE_UNAVAILABLE, "mic must be disabled without fabos-voice"
            # item 10 while a task runs: a voice transcript is kept as the follow-up in the composer — never a "Stop this
            # task?" prompt, never sent while the agent works (fake an available fabos-voice for this check only)
            saved_voice = (w.voice.bin, w.voice.status)
            w.voice.bin, w.voice.status = "/bin/true", {"stt": "whisper.cpp", "tts": "none", "mic": True, "wake": False, "listening": False}
            w.update_voice_buttons()
            assert w.mic_btn.isEnabled() and w.mic_btn.toolTip() == "Speak your request", (w.mic_btn.isEnabled(), w.mic_btn.toolTip())
            dialogs, posts = [], []
            orig_confirm = cc.RoundedDialog.confirm
            cc.RoundedDialog.confirm = staticmethod(lambda *a, **k: (dialogs.append(a[1:3]), False)[1])

            def spy_api(method, path, body=None, *a, _o=real_api, **k):
                if method == "POST":
                    posts.append((path, body))
                return _o(method, path, body, *a, **k)
            cc.api = spy_api
            try:
                w.on_transcript("open fab files please")
                spin(app)
                sync(w)
            finally:
                cc.RoundedDialog.confirm = orig_confirm
                cc.api = real_api
            assert not dialogs, "a transcript while the agent works must not open a confirmation: %r" % dialogs
            assert not posts, "a transcript while the agent works must not be sent: %r" % posts
            assert w.ask.text() == "open fab files please", w.ask.text()
            assert w.send_btn.glyph == "stop" and cc.api("GET", "/tasks/%d" % run).get("status") == "running", "the running task must survive a transcript"
            assert w.toast.isVisible() and "still working" in w.toast.text(), (w.toast.isVisible(), w.toast.text())
            w.ask.clear()
            w.voice.bin, w.voice.status = saved_voice
            w.update_voice_buttons()
            assert not w.mic_btn.isEnabled() and w.mic_btn.toolTip() == cc.VOICE_UNAVAILABLE
            assert w.mode_btn.text() == "Auto", w.mode_btn.text()
            assert w.sidebar.width() == 282 and w.sidebar.new_btn.height() == 36
            assert w.sidebar.rows[root].height() == 48, w.sidebar.rows[root].height()
            assert w.sidebar.list.count() >= 4, "sidebar should have 2 groups + 2 chats (got %d rows)" % w.sidebar.list.count()
            # the assistant action row: copy · good · bad · speak · edit · retry
            first_assistant = [b for b in w.view.turns[fu].step_widgets.values() if b.role == "assistant"][0]
            assert [b.glyph for b in first_assistant.action_buttons] == ["copy", "thumb-up", "thumb-down", "speaker", "edit", "retry"]

            def headers():
                return [w.sidebar.list.itemWidget(w.sidebar.list.item(i)).text() for i in range(w.sidebar.list.count())
                        if isinstance(w.sidebar.list.itemWidget(w.sidebar.list.item(i)), cc.QLabel)]
            assert headers() == ["Today", "Earlier"], headers()
            # show the hover action icons on one user bubble and one agent bubble so they appear in the render
            w.view.turns[run].user._hover(True)
            last_assistant = [b for b in w.view.turns[fu].step_widgets.values() if b.role == "assistant"][-1]
            last_assistant._hover(True)
            app.processEvents()
            assert all(b.isVisible() for b in last_assistant.action_buttons), "assistant hover actions not visible"
            px = w.grab()
            path = os.path.join(OUT, "ai-controls-%s.png" % name)
            assert not px.isNull() and px.save(path), "grab/save failed for " + name
            results[name] = (px.width(), px.height())

            # --- live colour-scheme switch: the application palette changes while the window is open -> every
            # stylesheet surface follows (sample the sidebar card's empty area, which is the palette's Base colour)
            other_name = "light" if name == "dark" else "dark"
            probe = w.sidebar.list.mapTo(w, QPoint(w.sidebar.list.width() // 2, w.sidebar.list.height() - 12))
            key0 = w._style_key
            l0 = QColor(px.toImage().pixel(probe)).lightness()
            app.setPalette(make_palette(QPalette, QColor, SCHEMES[other_name]))
            spin(app, 30)
            assert w._style_key != key0, "stylesheet was not rebuilt after a live palette switch"
            l1 = QColor(w.grab().toImage().pixel(probe)).lightness()
            assert (l0 < 128) == (name == "dark") and (l1 < 128) == (other_name == "dark"), "surface did not follow the live switch: lightness %d -> %d" % (l0, l1)
            app.setPalette(make_palette(QPalette, QColor, scheme))
            spin(app, 30)
            assert w._style_key == key0, "stylesheet did not return to the original scheme"
            l2 = QColor(w.grab().toImage().pixel(probe)).lightness()
            assert (l2 < 128) == (name == "dark"), "surface did not follow the switch back (lightness %d)" % l2
            print("[%s] live scheme switch: lightness %d -> %d -> %d" % (name, l0, l1, l2))

            # the rounded confirmation dialog, same palette
            d = cc.RoundedDialog(w, "Delete this chat?", "“show me the system” and its 3 turns — including every recorded step — are removed from the history. Running tasks are stopped.", "Delete")
            d.show()
            spin(app)
            dp = d.grab()
            assert dp.save(os.path.join(OUT, "ai-controls-dialog-%s.png" % name))
            results[name + "-dialog"] = (dp.width(), dp.height())
            d.close()
            # the Markdown bubble rendered a code block in monospace on a tint
            mdv = [b.md for b in w.view.turns[fu].step_widgets.values() if b.role == "assistant" and "uname -a" in b.md.markdown()]
            assert mdv, "markdown bubble missing"
            blk = mdv[0].document().begin()
            mono_blocks = 0
            while blk.isValid():
                if blk.blockFormat().nonBreakableLines() and "Mono" in "".join(blk.charFormat().fontFamilies() or []):
                    mono_blocks += 1
                blk = blk.next()
            assert mono_blocks >= 1, "code block not styled monospace"
            # the Settings dialog constructs and renders, with the raw-responses checkbox following the daemon setting
            sd = cc.SettingsDialog(w, cc.api("GET", "/settings"), w.voice)
            sd.show()
            spin(app)
            assert not sd.show_raw.isChecked()
            sp = sd.grab()
            assert sp.save(os.path.join(OUT, "ai-controls-settings-%s.png" % name))
            results[name + "-settings"] = (sp.width(), sp.height())
            # --- AI provider tab: ONE dropdown (5 providers), one key field, model, endpoint only for Local, Check connection
            sd.tabs.setCurrentIndex(1)
            spin(app)
            assert [sd.provider.itemData(i) for i in range(sd.provider.count())] == ["claude", "gemini", "openai", "deepseek", "local", "ollama"]
            assert sd.provider.itemText(3) == "DeepSeek" and sd.provider.itemText(0) == "Anthropic (Claude)"
            assert sd.provider.itemText(5) == "Ollama (on this computer)"
            sd.provider.setCurrentIndex(5); assert sd.current_pid == "ollama" and not sd.key.placeholderText().startswith("Paste") and sd.base_url.isVisibleTo(sd) and sd.check_btn.text() == "Check Ollama"
            sd.provider.setCurrentIndex(0)
            assert not sd.base_url.isVisible(), "endpoint field must be hidden for cloud providers"
            assert sd.model.text() == "claude-opus-5" and sd.key.echoMode() == cc.QLineEdit.EchoMode.Password
            assert sd.confirm_btn.isEnabled(), "Save must be allowed when no new key was typed"
            sd.provider.setCurrentIndex(4)
            spin(app)
            assert sd.base_url.isVisible() and sd.base_url.text() == "http://127.0.0.1:8080/v1" and sd.model.text() == "local"
            sd.provider.setCurrentIndex(3)
            spin(app)
            assert sd.model.text() == "deepseek-chat" and not sd.base_url.isVisible()
            # a typed key blocks Save until a successful check; the check runs off the GUI thread through cc.api
            sd.key.setText("sk-typed"); sd.key.textEdited.emit("sk-typed")
            spin(app)
            assert not sd.confirm_btn.isEnabled(), "Save must be disabled until the typed key was checked"
            calls = []

            def fake_api(method, path, body=None, timeout=5):
                if path == "/providers/test":
                    calls.append(body)
                    if body.get("api_key") == "sk-typed":
                        return {"ok": True, "latency_ms": 312, "detail": "Connected", "models_sample": ["deepseek-chat"], "provider": body["provider"], "model": "deepseek-chat"}
                    return {"ok": False, "detail": "key rejected", "http": 401, "latency_ms": 40, "provider": body["provider"], "model": "deepseek-chat"}
                return real_api(method, path, body)
            cc.api = fake_api
            try:
                sd.check_btn.click()
                assert not sd.check_btn.isEnabled() and sd.mark.state == "busy", "button must be disabled and the mark spinning while checking"
                deadline = time.time() + 5
                while sd.worker is not None and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.02)
                assert sd.worker is None, "provider check did not finish"
                assert calls and calls[0]["provider"] == "deepseek" and calls[0]["api_key"] == "sk-typed", calls
                assert sd.mark.state == "ok" and sd.check_result.text() == "Connected · deepseek-chat · 312 ms", (sd.mark.state, sd.check_result.text())
                assert sd.confirm_btn.isEnabled(), "Save must be enabled after a successful check"
                for _ in range(30):                  # let the check-mark animation draw
                    app.processEvents()
                    time.sleep(0.02)
                pp = sd.grab()
                assert pp.save(os.path.join(OUT, "provider-%s.png" % name))
                results["provider-" + name] = (pp.width(), pp.height())
                # a different key invalidates the check; a rejected key shakes the field, shows "Key rejected" and blocks Save
                sd.key.setText("sk-bad"); sd.key.textEdited.emit("sk-bad")
                assert not sd.confirm_btn.isEnabled() and sd.mark.state == "idle"
                sd.check_btn.click()
                deadline = time.time() + 5
                while sd.worker is not None and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.02)
                assert sd.mark.state == "fail" and sd.check_result.text() == "Key rejected", (sd.mark.state, sd.check_result.text())
                assert not sd.confirm_btn.isEnabled() and "failed" in sd.confirm_btn.toolTip()
                # the reason is written inline next to the buttons, not only in the tooltip
                spin(app)
                assert sd.save_note.isVisible() and sd.save_note.text() == sd.confirm_btn.toolTip(), (sd.save_note.isVisible(), sd.save_note.text())
                assert sd.save_note.x() < sd.cancel_btn.x() and sd.save_note.width() > 100, (sd.save_note.geometry(), sd.cancel_btn.geometry())
                pb = sd.grab()
                assert pb.save(os.path.join(OUT, "provider-blocked-%s.png" % name))
                results["provider-blocked-" + name] = (pb.width(), pb.height())
                assert getattr(sd.key, "_shake_anim", None) is not None, "no shake animation on the key field"
                sd.require_check.setChecked(False)
                assert sd.confirm_btn.isEnabled() and not sd.save_note.isVisible(), "unticking the requirement must allow Save and hide the note"
                sd.require_check.setChecked(True)
                assert not sd.confirm_btn.isEnabled() and sd.save_note.isVisible()
            finally:
                cc.api = real_api
            assert sd.tabs.tabText(2) == "Voice" and sd.wake_word.text() == "hey fab" and sd.speak_replies.isChecked() and not sd.offline_only.isChecked()
            assert not sd.test_voice_btn.isEnabled() and sd.test_voice_btn.toolTip() == cc.VOICE_UNAVAILABLE   # no fabos-voice in the image
            sd.reject()

            # --- Save with the daemon unreachable: the dialog stays open and says so (nothing was saved)
            sd = cc.SettingsDialog(w, cc.api("GET", "/settings"))
            sd.show()
            spin(app)
            seen_info = []
            poll = QTimer()
            poll.setInterval(30)
            ticks = {"n": 0}

            def close_info():
                ticks["n"] += 1
                for dlg_ in app.topLevelWidgets():
                    if isinstance(dlg_, cc.RoundedDialog) and dlg_ is not sd and dlg_.isVisible() and not isinstance(dlg_, cc.SettingsDialog):
                        seen_info.append(dlg_.title.text())
                        dlg_.confirm_btn.click()
                if seen_info or ticks["n"] > 200:
                    poll.stop()
            poll.timeout.connect(close_info)
            poll.start()

            def offline_api(method, path, body=None):
                raise cc.AgentOffline("connection refused (test)")
            cc.api = offline_api
            try:
                sd.save()
                deadline = time.time() + 8          # Save runs on a worker (ApiJobWorker): wait for its result on the GUI thread
                while sd.save_worker is not None and time.time() < deadline:
                    app.processEvents(); time.sleep(0.02)
                assert sd.save_worker is None, "offline save never came back"
            finally:
                cc.api = real_api
                poll.stop()
            assert seen_info == ["Agent service offline"], seen_info
            assert sd.isVisible() and sd.result() != QDialog.DialogCode.Accepted, "Settings closed although nothing was saved"
            sd.reject()
            spin(app)

            # --- an approval request surfaces as a rounded Allow / Deny dialog; the raw command is behind "Show details"
            # (ui.show_raw off, low risk); Deny reaches the daemon. Sidebar rows keep their identity while the new chat
            # appears and disappears (in-place reconcile, no clear-and-rebuild).
            row_root, row_other = w.sidebar.rows[root], w.sidebar.rows[other]
            ask = cc.api("POST", "/tasks", {"request": "show me the system", "mode": "ask"})["id"]
            wait(ask, ("waiting_approval",))
            w.refresh_list()
            sync(w)
            assert w.sidebar.rows[root] is row_root and w.sidebar.rows[other] is row_other, "sidebar rows were rebuilt when a chat was added"
            assert w.sidebar.list.item(1).data(cc.Qt.ItemDataRole.UserRole) == ask, "new chat should be the first row under TODAY"
            assert w.approval_dialogs, "approval dialog did not open"
            dlg = list(w.approval_dialogs.values())[0]
            high = (dlg.approval.get("risk") or "MEDIUM") in ("HIGH", "CRITICAL")
            if high:
                assert dlg.raw.isVisible() and dlg.details_btn.isChecked(), "HIGH/CRITICAL approval must show what runs"
            else:
                assert not dlg.raw.isVisible(), "raw command shown in the approval dialog with ui.show_raw off (risk %s)" % dlg.approval.get("risk")
                assert dlg.details_btn.text() == "Show details"
                dlg.details_btn.setChecked(True)
                spin(app)
                assert dlg.raw.isVisible() and "uname" in dlg.raw.text() and dlg.details_btn.text() == "Hide details"
            ap = dlg.grab()
            assert ap.save(os.path.join(OUT, "ai-controls-approval-%s.png" % name))
            results[name + "-approval"] = (ap.width(), ap.height())
            dlg.cancel_btn.click()            # Deny
            spin(app)
            sync(w)
            assert cc.api("GET", "/approvals/pending") == [], "deny did not reach the daemon"
            assert not w.approval_dialogs
            wait(ask, ("done", "failed"))
            cc.api("DELETE", "/tasks/%d" % ask)
            w.refresh_list()
            sync(w)
            assert w.sidebar.rows[root] is row_root and w.sidebar.rows[other] is row_other, "sidebar rows were rebuilt when a chat was removed"
            assert headers() == ["Today", "Earlier"] and w.sidebar.list.count() == 4, (headers(), w.sidebar.list.count())

            # --- an approval resolved elsewhere (notification / CLI) closes the dialog WITHOUT posting a decision
            ask2 = cc.api("POST", "/tasks", {"request": "show me the system", "mode": "ask"})["id"]
            wait(ask2, ("waiting_approval",))
            w.refresh_list()
            sync(w)
            assert w.approval_dialogs, "second approval dialog did not open"
            aid = list(w.approval_dialogs)[0]
            assert cc.api("POST", "/approvals/%d" % aid, {"decision": "approved"}).get("ok"), "external approve failed"
            posts = []

            def spy_api(method, path, body=None):
                if method == "POST":
                    posts.append((path, body))
                return real_api(method, path, body)
            cc.api = spy_api
            try:
                w.refresh_list()
                sync(w)
            finally:
                cc.api = real_api
            assert not w.approval_dialogs, "stale approval dialog still open"
            assert not any(p.startswith("/approvals/") for p, _ in posts), "closing a stale approval dialog posted a decision: %r" % posts
            wait(ask2, ("done", "failed"))
            cc.api("DELETE", "/tasks/%d" % ask2)
            w.refresh_list()
            sync(w)

            # --- a chat that gets activity moves to the top: the moved row is re-created, the others keep their identity
            fu2 = cc.api("POST", "/tasks", {"request": "and the kernel version", "parent_id": other, "mode": "bypass"})["id"]
            w.refresh_list()
            sync(w)
            assert headers() == ["Today"], headers()
            assert w.sidebar.list.item(1).data(cc.Qt.ItemDataRole.UserRole) == other and w.sidebar.list.item(2).data(cc.Qt.ItemDataRole.UserRole) == root
            assert w.sidebar.rows[root] is row_root, "the unmoved row lost its widget"
            assert w.sidebar.list.currentRow() == 2, "selection did not follow the current chat (row %d)" % w.sidebar.list.currentRow()
            wait(fu2)
            cc.api("DELETE", "/tasks/%d" % fu2)
            w.refresh_list()
            sync(w)
            assert headers() == ["Today", "Earlier"] and w.sidebar.list.item(1).data(cc.Qt.ItemDataRole.UserRole) == root

            # --- editing the ROOT message threads the new version into the same chat (no look-alike duplicate chat)
            n_convs = len(w.convs)
            # the edit-message state (design brief): the request pill turns into a card with Cancel / Send pills
            w.start_edit(root)
            turn = w.view.turns[root]
            spin(app)
            assert turn.editing and turn.edit_card.isVisible() and not turn.user.isVisible(), "edit card did not replace the request pill"
            assert turn.edit_card.text() == "show me the system", turn.edit_card.text()
            turn.edit_card.cancel_btn.click()
            spin(app)
            assert not turn.editing and turn.user.isVisible() and w.editing is None, "Cancel did not restore the request pill"
            w.start_edit(root)
            turn.edit_card.editor.setText("show me the system please")
            ep = turn.edit_card.grab()
            assert ep.save(os.path.join(OUT, "ai-controls-edit-%s.png" % name))
            turn.edit_card.send_btn.click()
            spin(app)
            sync(w)
            assert w.current_root == root and len(w.convs) == n_convs, "editing the root spawned a new chat (convs=%r)" % sorted(w.convs)
            new_ids = [t["id"] for t in w.convs[root]["tasks"] if t["id"] not in (root, fu, run)]
            assert len(new_ids) == 1 and w.by_id[new_ids[0]]["parent_id"] == root, [(t["id"], t.get("parent_id")) for t in w.convs[root]["tasks"]]
            assert w.by_id[root]["title"].startswith(cc.SUPERSEDED), w.by_id[root]["title"]
            assert w.editing is None and w.ask.text() == ""
            row = w.sidebar.rows[root]
            assert row._full_title == "show me the system" and "edited" in row.meta, (row._full_title, row.meta)
            assert w.view.turns[root].user.frame.graphicsEffect() is not None, "superseded turn not dimmed"
            assert user_text_ok(cc, w.details.get(new_ids[0]) or cc.api("GET", "/tasks/%d" % new_ids[0]))
            # put the fixture back for the next scheme
            cc.api("DELETE", "/tasks/%d" % new_ids[0])
            cc.api("PATCH", "/tasks/%d" % root, {"title": "show me the system"})
            w.refresh_list()
            sync(w)

            # enlarge toggle hides the sidebar and back
            w.enlarge_btn.setChecked(True)
            app.processEvents()
            assert not w.sidebar.isVisible()
            w.enlarge_btn.setChecked(False)
            app.processEvents()
            assert w.sidebar.isVisible()
            # empty state renders (Fab AI mark, three columns of cards) and a "Try asking" card prefills the composer
            w.new_chat()
            spin(app)
            assert w.stack.currentIndex() == 0
            ep = w.grab()
            assert ep.save(os.path.join(OUT, "ai-controls-empty-%s.png" % name))
            results[name + "-empty"] = (ep.width(), ep.height())
            cards = [c for c in w.stack.widget(0).findChildren(cc.QPushButton) if c.objectName() == "emptyCard"]
            assert len(cards) == 3
            cards[0].click()
            assert w.ask.text() == cards[0].text()
            w.ask.clear()
            # --- the composer's Send follows the text: disabled on an empty / whitespace box (Enter posts nothing), live with text
            w.list_timer.stop()                                       # no poll may rewrite status / composer state during these checks
            sync(w)
            spin(app)
            assert w.send_btn.glyph == "send" and not w.send_btn.isEnabled() and w.send_btn.toolTip() == "Type a request first", (w.send_btn.glyph, w.send_btn.isEnabled(), w.send_btn.toolTip())
            w.ask.setText("   ")
            spin(app)
            assert not w.send_btn.isEnabled(), "whitespace must not enable Send"
            enter_posts = []

            def enter_api(method, path, body=None, *a, _o=real_api, **k):
                if method == "POST":
                    enter_posts.append((path, body))
                return _o(method, path, body, *a, **k)
            cc.api = enter_api
            try:
                w.submit(source="enter")
                spin(app)
                sync(w)
            finally:
                cc.api = real_api
            assert not enter_posts, "Enter on a whitespace box must post nothing: %r" % enter_posts
            w.ask.setText("draw a poster of a kite")
            spin(app)
            assert w.send_btn.isEnabled() and w.send_btn.toolTip() == "Send", (w.send_btn.isEnabled(), w.send_btn.toolTip())
            w.ask.clear()
            spin(app)
            assert not w.send_btn.isEnabled()
            # --- cloud hint chip: only for the built-in (local) provider; Choose -> Settings › AI provider; dismissal lasts the session
            assert not w.cloud_hint.isVisible(), "no chip with the test provider"
            w.status["provider"] = "local"
            w.update_header()
            spin(app)
            assert w.cloud_hint.isVisible() and w.cloud_hint_label.text() == "Using the built-in model. For the best results use a cloud model", w.cloud_hint_label.text()
            w.toast.timer.stop(); w.toast.hide()                       # the "still working" toast from the transcript check must not sit over the chip
            spin(app)
            assert not w.toast.isVisible()
            hp = w.grab()
            assert hp.save(os.path.join(OUT, "ai-controls-cloud-hint-%s.png" % name))
            results[name + "-cloud-hint"] = (hp.width(), hp.height())
            opened = []
            w.open_settings = lambda tab=None: opened.append(tab)      # instance attribute shadows the method for this check
            try:
                w.cloud_hint_choose.click()
            finally:
                del w.open_settings
            assert opened == ["provider"], opened
            w.cloud_hint_close.click()
            spin(app)
            assert not w.cloud_hint.isVisible(), "dismiss must hide the chip"
            w.update_header()
            spin(app)
            assert not w.cloud_hint.isVisible(), "the dismissal must last the session"
            w._cloud_hint_dismissed = False
            w.status["provider"] = "fake"
            w.update_header()
            spin(app)
            assert not w.cloud_hint.isVisible(), "never with a cloud / non-local provider"
            w.list_timer.start()
            close_window(app, w)
            del w
            # --task ID opens the app on that task's conversation (the home-screen bar uses it)
            w2 = cc.AIControls(task_id=fu)
            w2.show()
            spin(app, 20)
            sync(w2)
            assert w2.current_root == root and w2.stack.currentWidget() is w2.view, ("--task did not open the chat", w2.current_root)
            close_window(app, w2)
            del w2
        cc.api("POST", "/tasks/%d/cancel" % run)
        for k, (wd, ht) in results.items():
            print("%s %dx%d" % (k, wd, ht))
        rc = 0
    except Exception:
        traceback.print_exc()
        try:
            print("--- daemon output ---")
            proc.terminate()
            print(proc.communicate(timeout=5)[0][-3000:])
        except Exception:
            pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except Exception:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(rc)


def user_text_ok(cc, task):
    """The edited request carries the chat context (minus the superseded turn) and ends with the user's words."""
    req = task.get("request") or ""
    assert cc.user_text(req) == "show me the system please", req
    assert cc.FOLLOWUP_MARK in req, req
    ctx = req.split(cc.FOLLOWUP_MARK)[0]
    assert "asked: and now open the editor" in ctx, ctx                    # the live follow-up is in the context
    assert "asked: show me the system" not in ctx, ctx                     # the superseded root is not
    return True


if __name__ == "__main__":
    main()
