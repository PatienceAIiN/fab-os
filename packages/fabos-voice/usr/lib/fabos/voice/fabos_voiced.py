#!/usr/bin/env python3
"""fabos-voiced — the always-on "Hey Fab" listener (per-user systemd service, fabos-voiced.service).

Loop:  settings (GET /settings, refreshed every 30 s)  ->  is voice.enabled and a microphone present?
       -> pw-record | pocketsphinx -keyphrase "hey fab" live -   (the only always-on consumer, ~35 MB, offline)
       -> on the wake phrase: chime, "Listening…" notification, record + transcribe like `fabos-voice listen-once`,
          POST /tasks to the agent, then follow the task: speak every tool step's narration, ask for approvals
          by voice (yes/no), relay questions, speak the final reply or the error. Never listens while speaking.
       -> while a task is followed the spotter keeps running in the background (PipeWire/Pulse only): "Hey Fab" then
          "stop" cancels the task, any other request starts a new task (the old one carries on in Fab AI Controls),
          silence resumes the narration.
First start after login: one notification telling the user about "Hey Fab" (only when a microphone exists).

Debug/test: `fabos_voiced.py --handle "open the files app"` runs the post-wake path once (no audio, no spotter)
and exits; with FABOS_VOICE_FAKE_SPEAK / FABOS_VOICE_FAKE_LISTEN set this is fully offline.
"""
import argparse
import collections
import contextlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import phrases as P  # noqa: E402
import voicelib as V  # noqa: E402

APP = "Fab OS"
SETTINGS_REFRESH_S = 30
POLL_S = 1.5
APPROVAL_LISTEN_S = 6
ANSWER_LISTEN_S = 10
WAKE_START_S = 5           # after the chime, give up when nobody has started speaking within this many seconds
FOLLOW_MAX_S = 30 * 60
MIC_RECHECK_S = 30
AGENT_RECHECK_S = 5
SPOTTER_BACKOFF_S = 3      # first restart delay after the spotter dies; doubles up to SPOTTER_BACKOFF_MAX_S
SPOTTER_BACKOFF_MAX_S = 60
SPOTTER_HEALTHY_S = 30     # a spotter that lived this long resets the backoff
SPEECH_GRACE_S = 1.0       # detections this soon after our own voice stopped are ours (pocketsphinx reports at utterance end)
RING_SECONDS = 6           # audio kept in memory (192 KB) for the second look at the wake clip and for the words of a run-on
                           # request: pocketsphinx reports the phrase at the END of the utterance, so the clip must hold "hey fab"
                           # plus up to ~4.5 s of request (measured in the Ubuntu 26.04 container: a 5 s "hey fab, write a note
                           # that says…" fell off a 5 s ring and was rejected, 6 s kept it); never written to disk unless verifying
FIRST_RUN_MARKER = os.path.join(V.STATE_DIR, "first-run-done")
DICT = "/usr/share/pocketsphinx/model/en-us/cmudict-en-us.dict"
TERMINAL = ("done", "failed", "cancelled")


def log(*a):
    V.log("voiced:", *a)


class Voiced:
    def __init__(self):
        self.agent = V.Agent()
        self.settings = self.agent.settings()
        self.settings_ts = time.time()
        self.notify_id = None
        self.rec = None
        self.spot = None
        self.spot_err = None
        self.pump = None
        self.ring = collections.deque(maxlen=RING_SECONDS * 1000 // V.CHUNK_MS)
        self.stop = False
        self._warned = set()
        self._wake_cache = {}      # raw setting -> validated phrase (cmudict is 3 MB; look each phrase up once)
        self.spot_failures = 0
        self.spot_started_at = 0.0
        self.last_spoke_end = 0.0
        self.spotting_allowed = False   # only the real main loop (run) may open the microphone; --handle never does
        self.interrupt = None           # wake clip caught by the background spotter while a task is followed
        self.bg = None

    # ------------------------------------------------------------------ settings
    def refresh_settings(self, force=False):
        if force or time.time() - self.settings_ts > SETTINGS_REFRESH_S:
            self.settings = self.agent.settings()
            self.settings_ts = time.time()
        return self.settings

    def flag(self, key):
        return V.truthy(self.settings.get(key, V.DEFAULT_SETTINGS.get(key, "false")))

    def enabled(self):
        return self.flag("voice.enabled")

    def wake_word(self):
        """The configured phrase, lower-cased, letters only, every word known to the acoustic dictionary."""
        raw = re.sub(r"[^a-z' ]", " ", str(self.settings.get("voice.wake_word") or "hey fab").lower())
        if raw in self._wake_cache:
            return self._wake_cache[raw]
        words = raw.split()
        result = "hey fab"
        if words:
            try:
                known = set()
                with open(DICT, errors="replace") as f:
                    for line in f:
                        w = line.split(" ", 1)[0]
                        if w in words:
                            known.add(w)
                if known >= set(words):
                    result = " ".join(words)
                else:
                    self.warn_once("wake word '%s' has words missing from the dictionary; using 'hey fab'" % raw.strip())
            except OSError:
                pass
        self._wake_cache[raw] = result
        return result

    def warn_once(self, msg):
        if msg not in self._warned:
            self._warned.add(msg)
            log(msg)

    # ------------------------------------------------------------------ output
    def notify(self, title, body, timeout_ms=5000, urgency="normal"):
        cmd = ["notify-send", "-a", APP, "-i", "fabos", "-u", urgency, "-t", str(timeout_ms), "-p"]
        if self.notify_id:
            cmd += ["-r", str(self.notify_id)]
        cmd += [title, body]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
            if out.isdigit():
                self.notify_id = int(out)
        except Exception:
            pass

    def speak(self, text):
        text = " ".join((text or "").split())
        if not text:
            return
        V.write_state(speaking=True)
        try:
            V.speak(text, self.agent, self.settings)
        except V.NoBackend as e:
            self.warn_once("no text-to-speech (%s); showing replies as notifications instead" % e)
            self.notify(APP, text, 8000)
        except Exception as e:  # never let a speech hiccup kill the listener
            log("speak failed:", e)
        finally:
            V.write_state(speaking=False)
            self.last_spoke_end = time.time()

    def own_voice(self):
        """True while we (or another Fab UI) speak, and for a moment after: the spotter must not wake on itself."""
        return V.lock_active("speaking") or time.time() - self.last_spoke_end < SPEECH_GRACE_S

    def listen(self, timeout, chime=False, start_timeout=None):
        """One utterance as text, or None (nothing heard / no backend — logged, never raised)."""
        try:
            text, backend = V.listen_once(timeout, self.agent, self.settings, chime=chime, start_timeout=start_timeout,
                                          on_state=lambda listening: V.write_state(listening=listening))
            log("heard (%s): %s" % (backend, text))
            return text
        except V.NothingHeard:
            return None
        except V.NoBackend as e:
            log("cannot listen:", e)
            self.notify(APP, str(e), 6000)
            return None
        except Exception as e:
            log("listen failed:", e)
            return None

    # ------------------------------------------------------------------ first run
    def first_run(self):
        try:
            os.makedirs(V.STATE_DIR, exist_ok=True)
            if os.path.exists(FIRST_RUN_MARKER) or not V.mic_present():
                return
            self.notify(P.FIRST_RUN_TITLE, P.FIRST_RUN_BODY, 12000)
            with open(FIRST_RUN_MARKER, "w") as f:
                f.write(time.strftime("%Y-%m-%dT%H:%M:%S\n"))
        except OSError as e:
            log("first-run marker:", e)

    # ------------------------------------------------------------------ the spotter pipeline
    def start_spotter(self):
        cmd = V.capture_command()
        if cmd is None:
            self.warn_once("no recorder (pw-record / parec / arecord) installed")
            return False
        wake = self.wake_word()
        threshold = str(self.settings.get("voice.kws_threshold") or V.DEFAULT_SETTINGS["voice.kws_threshold"])
        try:
            self.spot_err = open(os.path.join(V.ensure_run_dir(), "spotter.err"), "w+b")
            self.rec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.spot = subprocess.Popen(V.spotter_command(wake, threshold), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.spot_err)
            self.ring.clear()
            # recorder -> (ring buffer of the last RING_SECONDS) -> pocketsphinx; the ring gives whisper a second look at the wake clip
            self.pump = threading.Thread(target=self._pump, args=(self.rec, self.spot), daemon=True, name="audio-pump")
            self.pump.start()
        except OSError as e:
            log("cannot start the spotter:", e)
            self.stop_spotter()
            return False
        self.active_wake = wake
        self.active_threshold = threshold
        self.spot_started_at = time.time()
        V.write_state(wake=True, listening=False, mic=True, wake_word=wake)
        log("listening for '%s' (threshold %s) via %s" % (wake, threshold, cmd[0]))
        return True

    def _pump(self, rec, spot):
        try:
            while True:
                chunk = rec.stdout.read(V.CHUNK_BYTES)
                if not chunk:
                    break
                self.ring.append(chunk)
                spot.stdin.write(chunk)
                spot.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                spot.stdin.close()

    def stop_spotter(self):
        for p in (self.spot, self.rec):
            if p is not None:
                with contextlib.suppress(Exception):
                    p.kill()
                with contextlib.suppress(Exception):
                    p.wait(2)
                for f in (p.stdin, p.stdout, p.stderr):
                    if f is not None:
                        with contextlib.suppress(Exception):
                            f.close()
        self.spot = self.rec = None
        with contextlib.suppress(Exception):
            self.spot_err.close()
        V.write_state(wake=False)

    def spotter_died(self):
        """Log the death (in full once per distinct error, then quietly) and return how long to wait before the next
        try: 3 s, doubling to 60 s while it keeps dying at once (e.g. an ALSA node without PipeWire), reset after a
        healthy run."""
        err = b""
        with contextlib.suppress(Exception):
            self.spot_err.seek(0)
            err = self.spot_err.read()[-400:]
        if time.time() - self.spot_started_at > SPOTTER_HEALTHY_S:
            self.spot_failures = 0
        self.spot_failures += 1
        msg = "spotter stopped (pocketsphinx rc=%s, recorder rc=%s) %s" % (self.spot.returncode, self.rec.returncode, err.decode(errors="replace").strip())
        delay = min(SPOTTER_BACKOFF_S * 2 ** (self.spot_failures - 1), SPOTTER_BACKOFF_MAX_S)
        if self.spot_failures == 1:
            log(msg)
        else:
            self.warn_once(msg + " -- keeps happening; retrying with a backoff of up to %d s, further repeats are not logged" % SPOTTER_BACKOFF_MAX_S)
        self.stop_spotter()
        return delay

    def spot_loop(self):
        """Read spotter output until the wake phrase, a process death, or a settings change that needs a restart."""
        while not self.stop:
            if self.spot.poll() is not None or self.rec.poll() is not None:
                self._sleep(self.spotter_died())
                return
            ready, _, _ = select.select([self.spot.stdout], [], [], 1.0)
            if time.time() - self.settings_ts > SETTINGS_REFRESH_S:
                self.refresh_settings(force=True)
                if not self.enabled() or self.wake_word() != self.active_wake or \
                        str(self.settings.get("voice.kws_threshold") or V.DEFAULT_SETTINGS["voice.kws_threshold"]) != self.active_threshold:
                    log("settings changed; restarting the listener")
                    self.stop_spotter()
                    return
            if not ready:
                continue
            line = self.spot.stdout.readline()
            if not line:
                continue
            if self.own_voice():
                continue             # our own voice (or another Fab UI's) is playing: ignore
            if V.parse_spot_line(line.decode(errors="replace"), self.active_wake):
                log("wake phrase heard")
                clip = b"".join(self.ring)
                self.spot_failures = 0
                self.stop_spotter()  # release the microphone; nothing is spotted while we talk
                try:
                    self.on_wake(clip)
                except Exception as e:
                    log("wake handling failed:", e)
                return

    # ------------------------------------------------------------------ after "Hey Fab"
    def on_wake(self, clip=b""):
        """Chime at once, record the request, and meanwhile let whisper.cpp hear the wake clip again: the spotter is
        deliberately eager (1e-50 also fires on "a fabulous day"), so a rejected clip drops what followed, silently.
        pocketsphinx reports the phrase only at the end of the utterance, so someone who ran on ("Hey Fab open my
        downloads") has already spoken: when nothing follows the chime, the words after the wake phrase in that same
        clip transcript become the request."""
        V.play_chime()
        self.notify(APP, P.LISTENING, 8000)
        self.refresh_settings(force=True)
        heard = [None, ""]
        checker = None
        if self.flag("voice.verify_wake") and clip:
            checker = threading.Thread(target=lambda: heard.__setitem__(slice(0, 2), V.hear_wake_clip(clip)), daemon=True, name="wake-verify")
            checker.start()
        text = self.listen(V.DEFAULT_TIMEOUT_S, start_timeout=WAKE_START_S)
        if checker is not None:
            checker.join(30)
        if heard[0] is False:
            self.notify(APP, P.WAKE_UNSURE, 6000)
            return
        if not text:
            tail = V.request_after_wake(heard[1])
            if tail:
                log("no speech after the chime; using the words that followed the wake phrase: %r" % tail)
                text = tail
        if not text:
            self.speak(P.NOT_HEARD)
            self.notify(APP, P.LISTENING_AGAIN, 8000)
            text = self.listen(V.DEFAULT_TIMEOUT_S, start_timeout=WAKE_START_S)
        if not text:
            self.speak(P.NOT_HEARD_FINAL)
            return
        self.handle_text(text)

    def handle_text(self, text):
        self.notify(APP, "“%s”" % text, 6000)
        self.refresh_settings(force=True)
        if not self.flag("ai.enabled"):
            self.speak(P.AI_OFF)
            return
        try:
            r = self.agent.post("/tasks", {"request": text, "mode": self.settings.get("mode") or "auto", "title": text[:60]})
        except V.NoBackend:
            self.speak(P.AGENT_DOWN)
            return
        if not isinstance(r, dict) or "id" not in r:
            self.speak(P.ERROR.format(reason=P.error_reason((r or {}).get("error") if isinstance(r, dict) else str(r))))
            return
        if self.flag("voice.speak_replies"):
            self.speak(P.STARTED)
        self.follow(int(r["id"]))

    # ------------------------------------------------------------------ "Hey Fab" while a task runs
    def bg_spotter_start(self):
        """Keep spotting the wake phrase in a background thread while a task is followed, so the user can cut in with
        "Hey Fab" ("stop", or a new request). Only with PipeWire/Pulse recorders — ALSA capture is exclusive and the
        approval/question recordings need the microphone too — and only from the real main loop."""
        if not (self.spotting_allowed and self.enabled() and V.spotter_ready() and V.mic_present() and V.capture_shared()):
            return False
        self.interrupt = None
        if not self.start_spotter():
            return False
        self.bg = threading.Thread(target=self._bg_read, args=(self.spot,), daemon=True, name="wake-while-busy")
        self.bg.start()
        return True

    def _bg_read(self, spot):
        try:
            for line in iter(spot.stdout.readline, b""):
                if self.stop or self.interrupt is not None:
                    break
                if self.own_voice() or V.lock_active("listening"):
                    continue
                if not V.parse_spot_line(line.decode(errors="replace"), self.active_wake):
                    continue
                clip = b"".join(self.ring)
                if self.flag("voice.verify_wake") and V.verify_wake_clip(clip) is False:
                    log("wake phrase while busy rejected on the second look")
                    continue
                log("wake phrase heard while following a task")
                self.interrupt = clip
                break
        except (OSError, ValueError):
            pass

    def bg_spotter_stop(self):
        self.stop_spotter()
        self.bg = None

    def follow(self, tid):
        """Follow a task to its end, narrating. "Hey Fab" meanwhile interrupts: "stop" cancels the task, anything else
        becomes a new request (the old task carries on, visible in Fab AI Controls), silence resumes the narration."""
        state = {"seen": {}, "approvals": set(), "questions": set(), "started": time.time()}
        while not self.stop:
            self.interrupt = None        # a wake handled on the previous stretch must not fire again if the spotter cannot restart
            bg = self.bg_spotter_start()
            try:
                outcome = self._follow(tid, state)
            finally:
                if bg:
                    self.bg_spotter_stop()
            if outcome != "interrupted":
                return
            V.play_chime()
            self.notify(APP, P.LISTENING, 8000)
            text = self.listen(V.DEFAULT_TIMEOUT_S, start_timeout=WAKE_START_S)
            if not text:
                self.speak(P.RESUMING)
                continue
            if P.is_stop(text):
                self.cancel(tid)
                return
            self.speak(P.LEFT_RUNNING)
            self.handle_text(text)
            return

    def cancel(self, tid):
        try:
            r = self.agent.post("/tasks/%d/cancel" % tid, {})
        except V.NoBackend:
            self.speak(P.AGENT_DOWN)
            return
        if isinstance(r, dict) and r.get("status") == "cancelled":
            self.speak(P.CANCELLED)
        else:
            self.speak(P.ERROR.format(reason=P.error_reason((r or {}).get("error", "") if isinstance(r, dict) else str(r))))

    def _follow(self, tid, state):
        """One stretch of following: returns "done" (terminal state spoken), "timeout", "error" or "interrupted"."""
        seen, handled_approvals, handled_questions = state["seen"], state["approvals"], state["questions"]
        speak_replies = self.flag("voice.speak_replies")
        while not self.stop:
            if self.interrupt is not None:
                return "interrupted"
            try:
                task = self.agent.get("/tasks/%d" % tid)
            except V.NoBackend:
                self.speak(P.AGENT_DOWN)
                return "error"
            if not isinstance(task, dict) or "status" not in task:
                self.speak(P.ERROR.format(reason=P.error_reason((task or {}).get("error", "task not found"))))
                return "error"
            for s in task.get("steps") or []:
                if s.get("kind") != "tool_call":
                    continue
                sid = s.get("id")
                has_out = bool(s.get("output"))
                if sid not in seen:
                    seen[sid] = has_out
                    if speak_replies:
                        self.speak(s.get("narration") or P.narration(s.get("name"), s.get("input")))
                    if has_out and speak_replies and s.get("narration_done"):
                        self.speak(s["narration_done"])
                elif has_out and not seen[sid]:
                    seen[sid] = True
                    if speak_replies and s.get("narration_done"):
                        self.speak(s["narration_done"])
            st = task["status"]
            if st == "waiting_approval":
                for a in task.get("approvals") or []:
                    if a.get("status") == "pending" and a["id"] not in handled_approvals:
                        handled_approvals.add(a["id"])
                        self.ask_approval(a)
            elif st == "waiting_user":
                for q in task.get("questions") or []:
                    if not q.get("answer") and q["id"] not in handled_questions:
                        handled_questions.add(q["id"])
                        self.ask_question(tid, q)
            elif st in TERMINAL:
                self.finish(task)
                return "done"
            if time.time() - state["started"] > FOLLOW_MAX_S:
                self.speak(P.TIMED_OUT)
                return "timeout"
            self._sleep(POLL_S, until=lambda: self.interrupt is not None)
        return "stopped"

    def ask_approval(self, a):
        """Ask by voice and listen for a short answer. Only an answer-shaped reply counts (phrases.intent); for a
        CRITICAL step or anything run as administrator only a clear yes-word up front approves — everything else
        is left to the approval card on screen."""
        inp = P._as_dict(a.get("input"))
        strict = str(a.get("risk") or "").upper() == "CRITICAL" or (a.get("tool") == "run_shell" and bool(inp.get("as_root")))
        summary = P.approval_summary(a.get("tool"), inp, a.get("reason") or "")
        self.speak(P.PERMISSION.format(summary=summary))
        answer = self.listen(APPROVAL_LISTEN_S, chime=True)
        decision = P.intent(answer, strict=strict)
        if answer:
            log("approval answer %r -> %s%s" % (answer, decision, " (strict)" if strict else ""))
        if decision is None:
            self.speak(P.WAIT_ON_SCREEN)
            return
        try:
            r = self.agent.post("/approvals/%d" % a["id"], {"decision": "approved" if decision == "approve" else "denied"})
        except V.NoBackend:
            self.speak(P.AGENT_DOWN)
            return
        if isinstance(r, dict) and r.get("ok"):
            self.speak(P.APPROVED if decision == "approve" else P.DENIED)
        else:
            self.speak(P.WAIT_ON_SCREEN)

    def ask_question(self, tid, q):
        self.speak(P.QUESTION.format(question=P.shorten(q.get("question") or "", full=True)))
        answer = self.listen(ANSWER_LISTEN_S, chime=True)
        if not answer:
            self.speak(P.ANSWER_NOT_HEARD)
            return
        try:
            r = self.agent.post("/tasks/%d/answer" % tid, {"text": answer})
        except V.NoBackend:
            self.speak(P.AGENT_DOWN)
            return
        self.speak(P.ANSWER_TAKEN if isinstance(r, dict) and r.get("ok") else P.ANSWER_NOT_HEARD)

    def finish(self, task):
        st = task["status"]
        if st == "done":
            if self.flag("voice.speak_replies"):
                self.speak(P.shorten(task.get("result") or "", full=self.flag("voice.speak_full")) or P.DONE_EMPTY)
        elif st == "cancelled":
            self.speak(P.CANCELLED)
        else:
            self.speak(P.ERROR.format(reason=P.error_reason(task.get("error") or task.get("result") or "")))

    # ------------------------------------------------------------------ main loop
    def run(self):
        V.ensure_run_dir()
        os.makedirs(V.STATE_DIR, exist_ok=True)
        V.write_state(wake=False, listening=False, speaking=False, mic=V.mic_present())
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)
        self.first_run()
        self.spotting_allowed = True
        log("started (state dir %s, run dir %s)" % (V.STATE_DIR, V.RUN_DIR))
        while not self.stop:
            self.refresh_settings()
            if not self.enabled():
                V.write_state(wake=False)
                self._sleep(AGENT_RECHECK_S)
                continue
            if not self.settings.get("_agent_up"):
                self.warn_once("fabos-agent is not running; waiting")
                V.write_state(wake=False)
                self._sleep(AGENT_RECHECK_S)
                self.refresh_settings(force=True)
                continue
            if not V.spotter_ready():
                self.warn_once("pocketsphinx or its en-us model is missing; wake word disabled")
                V.write_state(wake=False)
                self._sleep(60)
                continue
            if not V.stt_backends(self.agent, self.settings):
                self.warn_once("no speech-to-text backend (no whisper model, no cloud key); wake word idle, checking every 60 s")
                V.write_state(wake=False)
                self._sleep(60)
                continue
            if not V.mic_present():
                self.warn_once("no microphone; will check again every %d s" % MIC_RECHECK_S)
                V.write_state(wake=False, mic=False)
                self._sleep(MIC_RECHECK_S)
                continue
            self.first_run()
            if not self.start_spotter():
                self._sleep(10)
                continue
            self.spot_loop()
        self.stop_spotter()
        V.write_state(wake=False, listening=False, speaking=False)
        log("stopped")

    def _sleep(self, s, until=None):
        """Sleep s seconds, waking early on stop (signal) or as soon as `until()` is true (a wake while a task is followed)."""
        end = time.time() + s
        while not self.stop and time.time() < end and not (until is not None and until()):
            time.sleep(0.25)

    def _on_signal(self, signum, frame):
        self.stop = True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fabos-voiced", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--handle", metavar="TEXT", help="treat TEXT as the words heard after the wake phrase, follow the task, exit")
    ap.add_argument("--state", action="store_true", help="print the daemon state file and exit")
    a = ap.parse_args(argv)
    if a.state:
        print(json.dumps(V.daemon_state()))
        return 0
    d = Voiced()
    if a.handle:
        V.ensure_run_dir()
        d.handle_text(a.handle)
        return 0
    d.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
