#!/usr/bin/env python3
"""fabos-voiced — the always-on "Hey Fab" listener (per-user systemd service, fabos-voiced.service).

Loop:  settings (GET /settings, refreshed every 30 s)  ->  is voice.enabled and a microphone present?
       -> pw-record | pocketsphinx -keyphrase "hey fab" live -   (the only always-on consumer, ~35 MB, offline)
       -> on the wake phrase: chime, "Listening…" notification, record + transcribe like `fabos-voice listen-once`,
          POST /tasks to the agent, then follow the task: speak every tool step's narration, ask for approvals
          by voice (yes/no), relay questions, speak the final reply or the error. Never listens while speaking.
       -> every sentence at most once: per task a (kind, step id, text) triple is spoken a single time — the narration
          when the step appears, its done-line once when it finishes (never when it equals the narration), the
          approval question once per approval id, the final reply once — and any narration text already spoken in the
          last 30 s is skipped. Speech is queued behind whatever Fab AI Controls is saying (the shared 'speaking' lock,
          waited for up to 20 s) and the microphone feed to the spotter is dropped while anything speaks.
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
REPEAT_WINDOW_S = 30       # the same narration text is not spoken twice within this window
# Voice-activity gate in front of pocketsphinx (docs/PERFORMANCE.md). The recorder keeps running (its stream is what
# the ring buffer and the wake verification need), but the decoder is fed only around speech-like audio: a 100 ms
# chunk whose RMS is GATE_RATIO times the running noise floor (and above GATE_MIN_FLOOR: digital silence is 0). Then
# the GATE_PREROLL_CHUNKS before it go in first, and feeding continues GATE_HANGOVER_CHUNKS past the last loud chunk so
# pocketsphinx's own endpointer sees the trailing silence that closes the utterance and prints its line. In a quiet room
# — or on the silent microphone of the test VM — the decoder therefore receives nothing and costs nothing; measured in
# the image: pocketsphinx decoding a continuous 16 kHz stream is ~1.5 % of a core (60 s of audio = 0.9 s CPU) plus a
# wake-up every 100 ms; gated, it sleeps in read().
GATE_RATIO = V.SPEECH_RATIO          # 3.0, the same ratio the recorder's Segmenter uses to call a chunk speech
GATE_MIN_FLOOR = V.MIN_FLOOR         # 40
GATE_PREROLL_CHUNKS = 3              # 300 ms before the onset
GATE_HANGOVER_CHUNKS = 12            # 1.2 s after the last loud chunk
GATE_FLOOR_RISE = 1.02               # the floor follows a rising room level 2 % per chunk, a falling one at once
# Spotter policy (voicelib.spotter_policy: voice.spotter on | battery-off | off, tightened by the performance mode):
# battery-off releases the microphone after IDLE_ON_BATTERY_S without input while discharging and reopens it when the
# user is back; any policy pauses while the screen is locked (nobody should drive a locked machine by voice, and the
# codec can power down). Lock / power / idle state is re-read every POLICY_POLL_S while listening (2 busctl calls) and
# every POLICY_PAUSED_POLL_S while paused, so the listener is back within ten seconds of the user's return.
POLICY_POLL_S = 30
POLICY_PAUSED_POLL_S = 10
IDLE_ON_BATTERY_S = 300
NO_DEDUPE_KINDS = ("approval", "question", "prompt", "ack")   # things the user must answer, or answers to what they said
SPOKEN_LOG_MAX = 256 * 1024
FIRST_RUN_MARKER = os.path.join(V.STATE_DIR, "first-run-done")
DICT = V.DICT_PATH
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
        self.recent = {}                # text -> when we last spoke it (REPEAT_WINDOW_S)
        self.capture_attempt = 0        # 0 = the plain default recorder; after a recorder failure, the explicit candidates in turn
        self.gate_enabled = True        # feed pocketsphinx only around speech-like audio (GATE_* above); tests may switch it off
        self.gate_stats = [0, 0]        # chunks fed to the decoder, chunks dropped by the gate (since the spotter started)
        self.policy_ts = 0.0            # when lock / power / idle state was last read
        self.hold = None                # why the spotter is paused right now ("locked", "battery-idle"), or None

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

    def speak(self, text, kind="line", key=None, state=None):
        """Say `text` once. With a follow `state`, the (kind, key, text) triple is spoken at most once for that task;
        unless `kind` is a prompt the user must answer or an acknowledgement of their answer (NO_DEDUPE_KINDS), a text
        spoken in the last REPEAT_WINDOW_S is skipped too. Returns True when something was said (or shown)."""
        text = " ".join((text or "").split())
        if not text:
            return False
        now = time.time()
        triple = (kind, key, text)
        if state is not None and triple in state["spoken"]:
            log("skip (already spoken for this task, %s %s): %s" % (kind, key, text))
            return False
        if kind not in NO_DEDUPE_KINDS:
            last = self.recent.get(text)
            if last is not None and now - last < REPEAT_WINDOW_S:
                log("skip (spoken %.0f s ago): %s" % (now - last, text))
                return False
        if state is not None:
            state["spoken"].add(triple)
        self.recent = {t: ts for t, ts in self.recent.items() if now - ts < REPEAT_WINDOW_S}
        self.recent[text] = now
        V.write_state(speaking=True)
        try:
            backend = V.speak(text, self.agent, self.settings)
            log("spoke [%s] (%s via %s -> %s): %s" % (kind, backend, V.LAST_PLAYBACK.get("player") or "-", V.LAST_PLAYBACK.get("sink") or "default sink", text))
            self.record_spoken(text, backend)
        except V.NoBackend as e:
            self.warn_once("no text-to-speech (%s); showing replies as notifications instead" % e)
            self.notify(APP, text, 8000)
            self.record_spoken(text, "notification")
        except Exception as e:  # never let a speech hiccup kill the listener
            log("speak failed:", e)
        finally:
            V.write_state(speaking=False)
            self.last_spoke_end = time.time()
        return True

    def record_spoken(self, text, backend):
        """Append one line per utterance to $XDG_STATE_HOME/fabos-voice/spoken.log (tests assert no line repeats)."""
        try:
            os.makedirs(V.STATE_DIR, exist_ok=True)
            p = V.SPOKEN_LOG
            if os.path.exists(p) and os.path.getsize(p) > SPOKEN_LOG_MAX:
                with open(p, errors="replace") as f:
                    tail = f.read()[-SPOKEN_LOG_MAX // 2:]
                with open(p, "w") as f:
                    f.write(tail[tail.find("\n") + 1:])
            with open(p, "a") as f:
                f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), backend, text))
        except OSError as e:
            self.warn_once("cannot write %s: %s" % (V.SPOKEN_LOG, e))

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
    def capture_cmd(self):
        """The recorder for the spotter: the plain default (pw-record, automatic target); once the recorder itself has
        failed, the explicit candidates in turn — pw-record --target <default source>, parec --device, arecord — the
        same ladder `fabos-voice listen-once` climbs, so a session whose automatic target delivers nothing still wakes."""
        if self.capture_attempt == 0:
            return V.capture_command()
        cands = V.capture_candidates(V.default_source().get("name"))
        if not cands:
            return None
        return cands[self.capture_attempt % len(cands)]

    def start_spotter(self):
        cmd = self.capture_cmd()
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
        log("listening for '%s' (threshold %s) via %s" % (wake, threshold, " ".join(a for a in cmd if a != "-")))
        return True

    def _pump(self, rec, spot):
        """recorder -> ring buffer -> (voice-activity gate) -> pocketsphinx. While anything speaks (our own 'speaking' lock
        or Fab AI Controls' `fabos-voice say`) the audio is dropped instead of fed, so the spotter never hears the
        loudspeaker; that stretch does not enter the ring either. Every other chunk enters the ring (the wake
        verification and a run-on request need the whole clip), but the decoder is fed only around speech-like audio
        (GATE_*): silence and steady room noise never wake it."""
        paused = False
        floor = None
        hang = 0
        pre = collections.deque(maxlen=GATE_PREROLL_CHUNKS)
        self.gate_stats = [0, 0]
        try:
            while True:
                chunk = rec.stdout.read(V.CHUNK_BYTES)
                if not chunk:
                    break
                if V.lock_active("speaking"):
                    if not paused:
                        paused = True
                        log("speech playing; spotter paused")
                    continue
                if paused:
                    paused = False
                    log("speech over; spotter resumed")
                self.ring.append(chunk)
                if self.gate_enabled:
                    level = V.rms(chunk)
                    if floor is None or level < floor:
                        floor = max(level, 1.0)
                    else:
                        floor = min(level, floor * GATE_FLOOR_RISE)
                    speech = level >= GATE_MIN_FLOOR and level >= floor * GATE_RATIO
                    if speech:
                        hang = GATE_HANGOVER_CHUNKS
                    elif hang > 0:
                        hang -= 1
                    else:
                        pre.append(chunk)
                        self.gate_stats[1] += 1
                        continue
                    for c in pre:                      # the 300 ms before the onset were "held back" until now
                        spot.stdin.write(c)
                        self.gate_stats[0] += 1
                        self.gate_stats[1] -= 1
                    pre.clear()
                spot.stdin.write(chunk)
                spot.stdin.flush()
                self.gate_stats[0] += 1
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
        fed, dropped = self.gate_stats
        if fed or dropped:
            log("spotter closed: decoder fed %.1f s, gate held back %.1f s (%d %% of the audio)" % (
                fed * V.CHUNK_MS / 1000.0, dropped * V.CHUNK_MS / 1000.0, 100 * dropped // max(1, fed + dropped)))
        self.gate_stats = [0, 0]
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
        if self.rec.returncode not in (None, 0):
            self.capture_attempt += 1        # the recorder itself failed: next time name the default source, then parec, arecord
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
            if time.time() - self.policy_ts > POLICY_POLL_S:
                hold = self.spotter_hold()
                if hold:
                    self.stop_spotter()
                    V.write_state(spotter="paused:" + hold if hold != "off" else "off")
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

    # ------------------------------------------------------------------ spotter policy
    def spotter_policy(self):
        """on | battery-off | off — voicelib.spotter_policy over the current settings and performance mode."""
        return V.spotter_policy(self.settings)

    def spotter_hold(self, policy=None):
        """Why the microphone must stay closed right now: "off" (policy), "locked" (screen locker active),
        "battery-idle" (battery-off policy, discharging, no input for IDLE_ON_BATTERY_S), or None. Reads sysfs and, at
        most, two busctl calls; remembers the time so spot_loop re-checks only every POLICY_POLL_S."""
        self.policy_ts = time.time()
        policy = policy or self.spotter_policy()
        hold = None
        if policy == "off":
            hold = "off"
        elif V.screen_locked() is True:
            hold = "locked"
        elif policy == "battery-off" and V.on_battery():
            idle = V.session_idle_s()
            if idle is not None and idle >= IDLE_ON_BATTERY_S:
                hold = "battery-idle"
        if hold != self.hold:
            log("spotter %s" % ("paused: %s" % hold if hold else "may listen again (%s over)" % self.hold))
        self.hold = hold
        return hold

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
            self.speak(P.NOT_HEARD, "prompt")
            self.notify(APP, P.LISTENING_AGAIN, 8000)
            text = self.listen(V.DEFAULT_TIMEOUT_S, start_timeout=WAKE_START_S)
        if not text:
            self.speak(P.NOT_HEARD_FINAL, "prompt")
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
            self.speak(P.STARTED, "ack")
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
        state = {"seen": {}, "approvals": set(), "questions": set(), "spoken": set(), "started": time.time()}
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
                self.speak(P.RESUMING, "ack")
                continue
            if P.is_stop(text):
                self.cancel(tid)
                return
            self.speak(P.LEFT_RUNNING, "ack")
            self.handle_text(text)
            return

    def cancel(self, tid):
        try:
            r = self.agent.post("/tasks/%d/cancel" % tid, {})
        except V.NoBackend:
            self.speak(P.AGENT_DOWN)
            return
        if isinstance(r, dict) and r.get("status") == "cancelled":
            self.speak(P.CANCELLED, "ack")
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
                # The agent replaces a waiting step's narration with its own permission question; that question is
                # asked once, by ask_approval() below, so it is not read out again here as if it were a narration.
                line = s.get("narration") or P.narration(s.get("name"), s.get("input"))
                done_line = s.get("narration_done") or ""
                if sid not in seen:
                    seen[sid] = has_out
                    if speak_replies and line and not line.startswith("This needs your permission"):
                        self.speak(line, "narration", sid, state)
                    if has_out and speak_replies and done_line and done_line != line:
                        self.speak(done_line, "done", sid, state)
                elif has_out and not seen[sid]:
                    seen[sid] = True
                    if speak_replies and done_line and done_line != line:
                        self.speak(done_line, "done", sid, state)
            st = task["status"]
            if st == "waiting_approval":
                for a in task.get("approvals") or []:
                    if a.get("status") == "pending" and a["id"] not in handled_approvals:
                        handled_approvals.add(a["id"])
                        self.ask_approval(a, state)
            elif st == "waiting_user":
                for q in task.get("questions") or []:
                    if not q.get("answer") and q["id"] not in handled_questions:
                        handled_questions.add(q["id"])
                        self.ask_question(tid, q, state)
            elif st in TERMINAL:
                self.finish(task, state)
                return "done"
            if time.time() - state["started"] > FOLLOW_MAX_S:
                self.speak(P.TIMED_OUT)
                return "timeout"
            self._sleep(POLL_S, until=lambda: self.interrupt is not None)
        return "stopped"

    def ask_approval(self, a, state=None):
        """Ask by voice (once per approval id) and listen for a short answer. Only an answer-shaped reply counts
        (phrases.intent); for a CRITICAL step or anything run as administrator only a clear yes-word up front approves —
        everything else is left to the approval card on screen."""
        inp = P._as_dict(a.get("input"))
        strict = str(a.get("risk") or "").upper() == "CRITICAL" or (a.get("tool") == "run_shell" and bool(inp.get("as_root")))
        summary = P.approval_summary(a.get("tool"), inp, a.get("reason") or "", raw=self.flag("ui.show_raw"))
        if not self.speak(P.PERMISSION.format(summary=summary), "approval", a.get("id"), state):
            return                       # already asked for this approval: the decision is on screen now
        answer = self.listen(APPROVAL_LISTEN_S, chime=True)
        decision = P.intent(answer, strict=strict)
        if answer:
            log("approval answer %r -> %s%s" % (answer, decision, " (strict)" if strict else ""))
        if decision is None:
            self.speak(P.WAIT_ON_SCREEN, "ack")
            return
        try:
            r = self.agent.post("/approvals/%d" % a["id"], {"decision": "approved" if decision == "approve" else "denied"})
        except V.NoBackend:
            self.speak(P.AGENT_DOWN)
            return
        if isinstance(r, dict) and r.get("ok"):
            self.speak(P.APPROVED if decision == "approve" else P.DENIED, "ack")
        else:
            self.speak(P.WAIT_ON_SCREEN, "ack")

    def ask_question(self, tid, q, state=None):
        if not self.speak(P.QUESTION.format(question=P.shorten(q.get("question") or "", full=True)), "question", q.get("id"), state):
            return
        answer = self.listen(ANSWER_LISTEN_S, chime=True)
        if not answer:
            self.speak(P.ANSWER_NOT_HEARD, "ack")
            return
        try:
            r = self.agent.post("/tasks/%d/answer" % tid, {"text": answer})
        except V.NoBackend:
            self.speak(P.AGENT_DOWN)
            return
        self.speak(P.ANSWER_TAKEN if isinstance(r, dict) and r.get("ok") else P.ANSWER_NOT_HEARD, "ack")

    def finish(self, task, state=None):
        """The final line, once per task."""
        st = task["status"]
        tid = task.get("id")
        if st == "done":
            if self.flag("voice.speak_replies"):
                self.speak(P.shorten(task.get("result") or "", full=self.flag("voice.speak_full")) or P.DONE_EMPTY, "final", tid, state)
        elif st == "cancelled":
            self.speak(P.CANCELLED, "ack")
        else:
            self.speak(P.ERROR.format(reason=P.error_reason(task.get("error") or task.get("result") or "")), "final", tid, state)

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
            policy = self.spotter_policy()
            hold = self.spotter_hold(policy)
            if hold:
                if hold == "off":
                    self.warn_once("wake-word listener off by policy (voice.spotter=%s, performance mode %s); say-and-listen still work from the UI"
                                   % (self.settings.get("voice.spotter") or "on", V.performance_mode() or "unset"))
                V.write_state(wake=False, spotter="paused:" + hold if hold != "off" else "off", spotter_policy=policy)
                self._sleep(POLICY_PAUSED_POLL_S if hold != "off" else SETTINGS_REFRESH_S)
                continue
            self.first_run()
            if not self.start_spotter():
                self._sleep(10)
                continue
            V.write_state(spotter="listening", spotter_policy=policy)
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
