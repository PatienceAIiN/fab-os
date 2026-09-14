#!/usr/bin/env python3
"""Offscreen render check for Fab AI Controls (packages/fabos-agent/.../command_center.py).

Starts fabos-agentd with the scripted provider in a temp dir, seeds one chat with a follow-up and a running task plus an
older chat, then constructs the window under a Fab Dark and a Fab Light QPalette, renders each to a QPixmap and saves
  <out>/ai-controls-dark.png  <out>/ai-controls-light.png  (+ ai-controls-dialog-{dark,light}.png for the confirm dialog)
Exit code 0 only if every step ran without an exception. Needs PyQt6 — run it inside the image:
  podman run --rm -v $PWD:/work:Z -e QT_QPA_PLATFORM=offscreen localhost/fabos:vm python3 /work/tests/ai-controls-render.py /work/build
"""
import os, shutil, sqlite3, subprocess, sys, tempfile, time, traceback, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def main():
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
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, AGENT_DIR)
        import command_center as cc
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QPalette, QColor, QFont

        def wait(tid, states=("done", "failed", "cancelled", "waiting_approval", "waiting_user"), timeout=40):
            for _ in range(timeout * 5):
                t = cc.api("GET", "/tasks/%d" % tid)
                if t.get("status") in states:
                    return t
                time.sleep(0.2)
            raise RuntimeError("task %d stuck" % tid)

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
            for _ in range(10):
                app.processEvents()
            print("[%s] window constructed in %.1fs" % (name, time.time() - t1))
            t1 = time.time()
            w.refresh_list()
            w.select_conversation(root)
            print("[%s] conversation selected in %.1fs (fits=%d)" % (name, time.time() - t1, fits["n"]))
            t1 = time.time()
            deadline = time.time() + 1.2
            while time.time() < deadline:                 # typing indicator ticks, fade-ins, layout settle
                app.processEvents()
                time.sleep(0.02)
            print("[%s] settle loop %.1fs (fits=%d)" % (name, time.time() - t1, fits["n"]))
            cc.MarkdownView.heightForWidth = orig_fit
            print("[%s] tasks:" % name, [(t["id"], t["status"], t.get("parent_id")) for t in w.tasks], "chat root:", w.current_root, "composer glyph:", w.send_btn.glyph)
            assert w.stack.currentWidget() is w.view, "conversation view not shown"
            assert len(w.view.turns) == 3, "expected 3 turns, got %d" % len(w.view.turns)
            assert w.send_btn.glyph == "stop", "send button should be STOP while the last task runs (glyph=%s)" % w.send_btn.glyph
            assert w.view.turns[run].typing.isVisible(), "typing indicator should be visible for the running task"
            assert w.view.turns[fu].chip.isVisible() and w.view.turns[fu].chip.button.text().startswith("Worked:"), "worked chip missing"
            assert "Ran a command" in "".join(l.text() for l in w.view.turns[root].chip.panel.findChildren(cc.QLabel)), "friendly label missing"
            assert not any("uname" in l.text() for l in w.view.turns[root].chip.panel.findChildren(cc.QLabel)), "raw command leaked with ui.show_raw off"
            assert w.sidebar.list.count() >= 4, "sidebar should have 2 groups + 2 chats (got %d rows)" % w.sidebar.list.count()
            headers = [w.sidebar.list.itemWidget(w.sidebar.list.item(i)).text() for i in range(w.sidebar.list.count()) if isinstance(w.sidebar.list.itemWidget(w.sidebar.list.item(i)), cc.QLabel)]
            assert headers == ["TODAY", "EARLIER"], headers
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
            # the rounded confirmation dialog, same palette
            d = cc.RoundedDialog(w, "Delete this chat?", "“show me the system” and its 3 turns — including every recorded step — are removed from the history. Running tasks are stopped.", "Delete")
            d.show()
            for _ in range(10):
                app.processEvents()
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
            sd = cc.SettingsDialog(w, cc.api("GET", "/settings"))
            sd.show()
            for _ in range(10):
                app.processEvents()
            assert not sd.show_raw.isChecked()
            sp = sd.grab()
            assert sp.save(os.path.join(OUT, "ai-controls-settings-%s.png" % name))
            results[name + "-settings"] = (sp.width(), sp.height())
            sd.reject()
            # an approval request surfaces as a rounded Allow / Deny dialog; Deny reaches the daemon
            ask = cc.api("POST", "/tasks", {"request": "show me the system", "mode": "ask"})["id"]
            wait(ask, ("waiting_approval",))
            w.refresh_list()
            for _ in range(10):
                app.processEvents()
            assert w.approval_dialogs, "approval dialog did not open"
            dlg = list(w.approval_dialogs.values())[0]
            ap = dlg.grab()
            assert ap.save(os.path.join(OUT, "ai-controls-approval-%s.png" % name))
            results[name + "-approval"] = (ap.width(), ap.height())
            dlg.reject()
            for _ in range(10):
                app.processEvents()
            assert cc.api("GET", "/approvals/pending") == [], "deny did not reach the daemon"
            wait(ask, ("done", "failed"))
            cc.api("DELETE", "/tasks/%d" % ask)
            # enlarge toggle hides the sidebar and back
            w.enlarge_btn.setChecked(True)
            app.processEvents()
            assert not w.sidebar.isVisible()
            w.enlarge_btn.setChecked(False)
            app.processEvents()
            assert w.sidebar.isVisible()
            w.close()
            del w
            app.processEvents()
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


if __name__ == "__main__":
    main()
