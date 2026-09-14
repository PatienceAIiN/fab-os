#!/usr/bin/env python3
"""Offline tests for fabos-voice (no microphone, no network, no GUI).

Always: RMS voice-activity segmentation on synthetic audio, WAV round-trip, chime, phrase table covers every agent
tool, yes/no intent mapping, banned-word hygiene, the CLI contract with nothing available (status JSON keys,
listen-once -> exit 4, say -> exit 4), first-run notification, and the wake daemon's post-wake path end to end
against fabos-agentd's FakeProvider with fake speak/listen hooks (narration, voice approval, denial, AI off).
When the engines exist (Ubuntu 26.04 image / container): espeak-ng "hey fab" -> pocketsphinx keyphrase spotting
(file and real-time stream, plus a negative control) and whisper.cpp transcription with the shipped tiny.en model.

    python3 tests/voice-test.py            # host: engine tests skip
    FABOS_VOICE_MODEL=/path/ggml-tiny.en.bin python3 tests/voice-test.py   # container with pocketsphinx/whisper.cpp
"""
import base64
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packages", "fabos-voice")
LIB = os.path.join(PKG, "usr", "lib", "fabos", "voice")
CLI = os.path.join(PKG, "usr", "bin", "fabos-voice")
VOICED = os.path.join(LIB, "fabos_voiced.py")
AGENTD = os.path.join(ROOT, "packages", "fabos-agent", "usr", "lib", "fabos", "agent", "fabos_agentd.py")

# Isolated runtime/state for the whole run, and a notify-send stub so no desktop notification pops up.
TMP = tempfile.mkdtemp(prefix="fabos-voice-test-")
BIN = os.path.join(TMP, "bin")
os.makedirs(BIN)
NOTIFY_LOG = os.path.join(TMP, "notify.log")
with open(os.path.join(BIN, "notify-send"), "w") as f:
    f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> '" + NOTIFY_LOG + "'\necho 42\n")
os.chmod(os.path.join(BIN, "notify-send"), 0o755)
os.symlink(sys.executable, os.path.join(BIN, "python3"))
os.environ["PATH"] = BIN + ":" + os.environ.get("PATH", "/usr/bin:/bin")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(TMP, "run")
os.environ["FABOS_VOICE_STATE_DIR"] = os.path.join(TMP, "state")
os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)
os.environ.pop("FABOS_VOICE_FAKE_SPEAK", None)
os.environ.pop("FABOS_VOICE_FAKE_LISTEN", None)

sys.path.insert(0, LIB)
import phrases as P  # noqa: E402
import voicelib as V  # noqa: E402

try:
    import numpy as np
except Exception:
    np = None

# Third-party product names that must never appear in this package (base64 so that a plain grep of the repository
# does not find the words in the test itself)
BANNED = tuple(base64.b64decode(b).decode() for b in ("Q2hhdEdQVA==", "R1BU", "U25vd1VJ", "U29yYQ==", "REFMTA==", "VXBncmFkZSBwbGFu", "Y2FuIG1ha2UgbWlzdGFrZXM="))


# --------------------------------------------------------------------------- synthetic audio
def pcm_noise(seconds, amp=30, seed=1):
    rnd = random.Random(seed)
    n = int(V.RATE * seconds)
    if np is not None:
        a = (np.random.default_rng(seed).uniform(-amp, amp, n)).astype("<i2")
        return a.tobytes()
    return b"".join(int(rnd.uniform(-amp, amp)).to_bytes(2, "little", signed=True) for _ in range(n))


def pcm_tone(seconds, freq=440.0, amp=3000):
    n = int(V.RATE * seconds)
    if np is not None:
        t = np.arange(n) / V.RATE
        return (np.sin(2 * np.pi * freq * t) * amp).astype("<i2").tobytes()
    return b"".join(int(math.sin(2 * math.pi * freq * i / V.RATE) * amp).to_bytes(2, "little", signed=True) for i in range(n))


def chunks(pcm):
    for i in range(0, len(pcm), V.CHUNK_BYTES):
        yield pcm[i:i + V.CHUNK_BYTES]


def which_all(*names):
    return all(shutil.which(n) for n in names)


def espeak_16k(text, voice="en-us", speed=120):
    """espeak-ng WAV resampled to 16 kHz mono s16 with sox (both must exist). Returns PCM bytes."""
    raw = os.path.join(TMP, "es-%d.wav" % abs(hash(text)))
    out = raw + ".16k.wav"
    subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-w", raw, text], check=True, capture_output=True, timeout=30)
    subprocess.run(["sox", raw, "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer", out], check=True, capture_output=True, timeout=30)
    return V.read_wav(out)[1]


def silence(seconds):
    return b"\0" * (int(V.RATE * seconds) * 2)


# --------------------------------------------------------------------------- unit tests
class VAD(unittest.TestCase):
    def test_silence_tone_silence_is_one_utterance(self):
        pcm = pcm_noise(0.5) + pcm_tone(1.0) + pcm_noise(2.0, seed=2)
        seg = V.Segmenter()
        utterances, at = [], []
        for i, c in enumerate(chunks(pcm)):
            r = seg.feed(c)
            if r is not None:
                utterances.append(r)
                at.append(i)
                seg = V.Segmenter()  # a caller would stop here; keep feeding to prove nothing else fires
        self.assertEqual(len(utterances), 1, "expected exactly one utterance, got %d" % len(utterances))
        # ends 1.2 s after the tone: chunk index ~ (0.5 + 1.0 + 1.2) / 0.1 = 27
        self.assertTrue(25 <= at[0] <= 29, "utterance closed at chunk %d" % at[0])
        secs = len(utterances[0]) / (V.RATE * 2)
        self.assertTrue(2.1 <= secs <= 2.7, "utterance length %.2f s (tone 1.0 + 1.2 trailing + 0.3 pre-roll)" % secs)

    def test_noise_only_is_nothing(self):
        seg = V.Segmenter()
        self.assertTrue(all(seg.feed(c) is None for c in chunks(pcm_noise(3.0))))
        self.assertIsNone(seg.finish())
        self.assertFalse(seg.speech_started)

    def test_timeout_keeps_partial_speech(self):
        seg = V.Segmenter()
        for c in chunks(pcm_noise(0.4) + pcm_tone(2.0)):
            self.assertIsNone(seg.feed(c))
        self.assertTrue(seg.speech_started)
        self.assertGreater(len(seg.finish()), V.RATE * 2 * 1.9)

    def test_digital_silence_floor_never_collapses(self):
        seg = V.Segmenter()
        for c in chunks(silence(0.5)):
            seg.feed(c)
        self.assertGreaterEqual(seg.floor, V.MIN_FLOOR)
        self.assertGreaterEqual(seg.threshold, V.MIN_FLOOR * V.SPEECH_RATIO)
        fired = [seg.feed(c) for c in chunks(pcm_tone(0.5) + silence(1.5) + pcm_tone(0.5))]
        self.assertEqual(sum(1 for f in fired if f is not None), 1)   # one utterance; a finished segmenter stays quiet
        self.assertTrue(seg.done)

    def test_rms(self):
        self.assertAlmostEqual(V.rms(silence(0.1)), 0.0)
        self.assertTrue(2000 < V.rms(pcm_tone(0.1)) < 2300)   # 3000 / sqrt(2) = 2121


class Transcripts(unittest.TestCase):
    def test_junk_and_hallucinations_are_nothing(self):
        for t in ("", " you", "You.", "Thank you.", "[BLANK_AUDIO]", "[BLANK_AUDIO] you", "(silence)", "Thanks for watching!"):
            self.assertEqual(V._clean_transcript(t), "", repr(t))
        self.assertEqual(V._clean_transcript("\n Open my downloads folder.  "), "Open my downloads folder.")
        self.assertEqual(V._clean_transcript("[BLANK_AUDIO] hey fab, thank you for the tea"), "hey fab, thank you for the tea")

    def test_spotter_line_parsing(self):
        self.assertTrue(V.parse_spot_line('{"b":0.600,"d":1.200,"p":1.000,"t":"hey fab","w":[{"b":0.940,"d":0.160,"p":0.779,"t":"hey fab"}]}'))
        self.assertFalse(V.parse_spot_line('{"b":0.000,"d":2.280,"p":1.000,"t":"","w":[]}'))
        self.assertFalse(V.parse_spot_line("ERROR: something"))
        self.assertFalse(V.parse_spot_line(""))
        self.assertEqual(V.spotter_command("hey fab", "1e-50")[:5], ["pocketsphinx", "-keyphrase", "hey fab", "-kws_threshold", "1e-50"])
        self.assertEqual(V.spotter_command()[-2:], ["live", "-"])


class Recorder(unittest.TestCase):
    """record_utterance against a scripted 'microphone' (a python process writing raw s16 to stdout)."""

    def fake_mic(self, script):
        return [sys.executable, "-c", script]

    def with_mic(self, cmd, fn):
        orig = V.capture_command
        V.capture_command = lambda: cmd
        try:
            return fn()
        finally:
            V.capture_command = orig

    def test_capture_command_order(self):
        orig = V.which
        V.which = lambda *names: "/usr/bin/" + names[0] if names[0] in self.present else None
        try:
            self.present = {"pw-record"}
            self.assertEqual(V.capture_command()[0], "pw-record")
            self.present = {"parec"}
            self.assertEqual(V.capture_command()[0], "parec")
            self.present = {"arecord"}
            self.assertEqual(V.capture_command()[0], "arecord")
            self.present = set()
            self.assertIsNone(V.capture_command())
        finally:
            V.which = orig

    def test_start_timeout_gives_the_mic_back(self):
        # silence forever: the wake flow must give up after start_timeout, well before the 10 s hard cap
        mic = self.fake_mic("import sys,time\nwhile True:\n sys.stdout.buffer.write(b'\\0'*3200); sys.stdout.buffer.flush(); time.sleep(0.1)")
        t0 = time.time()
        with self.assertRaises(V.NothingHeard):
            self.with_mic(mic, lambda: V.record_utterance(10, start_timeout=1.0))
        self.assertLess(time.time() - t0, 4.0)

    def test_utterance_is_cut_at_trailing_silence(self):
        # 0.5 s faint noise, 0.8 s tone, then silence forever -> one utterance, returned about 1.2 s after the tone ends
        mic = self.fake_mic(
            "import sys,time,math,struct\n"
            "def tone(n,a):\n return b''.join(struct.pack('<h', int(math.sin(2*math.pi*440*i/16000)*a)) for i in range(n))\n"
            "sys.stdout.buffer.write(tone(8000,20)); sys.stdout.buffer.write(tone(12800,3000)); sys.stdout.buffer.flush()\n"
            "while True:\n sys.stdout.buffer.write(b'\\0'*3200); sys.stdout.buffer.flush(); time.sleep(0.1)")
        t0 = time.time()
        pcm = self.with_mic(mic, lambda: V.record_utterance(10))
        self.assertLess(time.time() - t0, 6.0)
        secs = len(pcm) / 32000.0
        self.assertTrue(2.0 <= secs <= 2.8, "utterance length %.2f s" % secs)

    def test_no_audio_at_all_is_no_backend(self):
        mic = self.fake_mic("import sys; sys.stderr.write('no such device'); sys.exit(1)")
        with self.assertRaises(V.NoBackend) as cm:
            self.with_mic(mic, lambda: V.record_utterance(2))
        self.assertIn("no such device", str(cm.exception))


class Wav(unittest.TestCase):
    def test_round_trip(self):
        pcm = pcm_tone(0.25) + pcm_noise(0.25)
        p = V.write_wav(os.path.join(TMP, "rt.wav"), pcm)
        params, back = V.read_wav(p)
        self.assertEqual(params, (1, 2, 16000))
        self.assertEqual(back, pcm)
        with wave.open(p) as w:
            self.assertEqual(w.getnframes(), len(pcm) // 2)

    def test_chime_is_180ms_16k_mono(self):
        p = V.chime_path()
        with wave.open(p) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 16000))
            self.assertEqual(w.getnframes() * 1000 // w.getframerate(), 180)
        self.assertGreater(V.rms(V.chime_pcm()), 3000)


class Phrases(unittest.TestCase):
    def tools(self):
        sys.path.insert(0, os.path.dirname(AGENTD))
        import fabos_agentd as fa
        return [t["name"] for t in fa.TOOLS]

    def test_every_agent_tool_has_narration(self):
        tools = self.tools()
        self.assertGreaterEqual(len(tools), 10)
        for t in tools:
            self.assertIn(t, P.NARRATION, "no narration for tool " + t)
            self.assertIn(t, P.NARRATION_DONE, "no done-narration for tool " + t)
            line = P.narration(t, {"path": "/home/x/Documents/a.txt", "app": "dolphin", "to": "a@b.in", "url": "https://x.in/p", "command": "ls"})
            self.assertTrue(line.endswith("."), line)
            self.assertLess(len(line), 120, line)
            self.assertNotEqual(P.approval_summary(t, "{}"), "")

    def test_system_apps_use_fab_names(self):
        self.assertEqual(P.narration("open_app", {"app": "dolphin"}), "Opening Fab Files for you now.")
        self.assertEqual(P.narration("open_app", json.dumps({"app": "kate", "args": ["x"]})), "Opening Fab Editor for you now.")
        self.assertNotIn("dolphin", P.narration_done("open_app", {"app": "dolphin"}))
        self.assertEqual(P.narration("run_shell", {"command": "apt update", "as_root": True}), "Running a system command as administrator now.")
        self.assertEqual(P.narration("nonexistent_tool", {}), P.GENERIC_NARRATION)
        self.assertEqual(P.narration("write_file", "not json"), "Writing that file now.")

    def test_approval_summary(self):
        self.assertEqual(P.approval_summary("run_shell", {"command": "rm -rf ~/tmp"}), "run the command rm -rf ~/tmp")
        self.assertEqual(P.approval_summary("send_email", json.dumps({"to": "priya@example.com"})), "send an email to priya@example.com")
        self.assertEqual(P.approval_summary("write_file", {"path": "/etc/hosts"}), "write to hosts")
        s = P.PERMISSION.format(summary=P.approval_summary("open_app", {"app": "konsole"}))
        self.assertEqual(s, "This needs your permission: open Fab Terminal. Shall I go ahead?")

    def test_yes_no_intents(self):
        for yes in ("yes", "Yes please", "ok", "okay go ahead", "go ahead", "sure", "haan", "haan ji kar do", "theek hai", "proceed", "yep do it", "Yeah, fine."):
            self.assertEqual(P.intent(yes), "approve", yes)
        for no in ("no", "No thanks", "nahi", "nahin", "cancel", "stop", "wait", "not now", "don't", "yes, wait", "hold on", "mat karo", "Nope."):
            self.assertEqual(P.intent(no), "deny", no)
        for unclear in ("", None, "banana", "what does this do", "hmm", "please explain", "is that right"):
            self.assertIsNone(P.intent(unclear), repr(unclear))

    def test_wake_transcript_check(self):
        for ok in ("Hey Fab.", " Okay, fam.", "hey fab", "Hey, Fap!", "Hey.", "A fab", "Hey Feb"):
            self.assertTrue(V.wake_transcript_ok(ok), ok)
        for bad in ("", "Hey Bob, that was a fabulous match yesterday.", "Please open the files application and read the notes.", "the fabric of the tent was torn", "good morning everyone, how are you today"):
            self.assertFalse(V.wake_transcript_ok(bad), bad)

    def test_shorten(self):
        t = "First sentence here. Second one follows! Third should be dropped? Fourth too."
        self.assertEqual(P.shorten(t), "First sentence here. Second one follows!")
        self.assertEqual(P.shorten(t, full=True), t)
        long = "word " * 100
        self.assertLessEqual(len(P.shorten(long)), 240)
        self.assertTrue(P.shorten(long).endswith("…"))
        self.assertEqual(P.shorten("**Done** — `x`"), "Done — x")
        self.assertEqual(P.shorten(""), "")
        self.assertEqual(P.error_reason("RuntimeError: No Claude API key configured. Open X."), "No Claude API key configured. Open X.")

    def test_indian_english_tone_and_hygiene(self):
        for s in (P.STARTED, P.NOT_HEARD, P.PERMISSION, P.WAIT_ON_SCREEN, P.FIRST_RUN_BODY, P.NOT_HEARD_FINAL):
            self.assertTrue(s[0].isupper() and s.rstrip()[-1] in ".?…", s)
        self.assertIn("Shall I go ahead?", P.PERMISSION)
        self.assertIn("Fab AI Controls", P.FIRST_RUN_BODY)
        for root, dirs, files in os.walk(PKG):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                with open(os.path.join(root, fn), errors="replace") as fh:
                    text = fh.read()
                for b in BANNED:
                    self.assertNotIn(b, text, "%s contains banned word %r" % (fn, b))


# --------------------------------------------------------------------------- CLI contract with nothing available
class CLIContract(unittest.TestCase):
    def env(self):
        run = os.path.join(TMP, "empty-run")
        os.makedirs(run, exist_ok=True)
        return dict(os.environ, PATH=BIN, XDG_RUNTIME_DIR=run, FABOS_VOICE_MODEL="/nonexistent/ggml-tiny.en.bin", FABOS_VOICE_MIC="0",
                    FABOS_VOICE_STATE_DIR=os.path.join(TMP, "empty-state"))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI] + list(args), env=self.env(), capture_output=True, text=True, timeout=60)

    def test_status_json_keys(self):
        r = self.run_cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout.strip())
        self.assertEqual(set(st), {"wake", "listening", "stt", "tts", "mic"})
        self.assertEqual(st, {"wake": False, "listening": False, "stt": "none", "tts": "none", "mic": False})
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)

    def test_listen_once_exit_4_without_backend(self):
        r = self.run_cli("listen-once", "--timeout", "2")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("not available", r.stderr)

    def test_say_exit_4_without_tts(self):
        r = self.run_cli("say", "hello")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)

    def test_wake_exit_4_without_agent(self):
        r = self.run_cli("wake", "off")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)

    def test_help(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)
        for cmd in ("listen-once", "say", "status", "wake"):
            self.assertIn(cmd, r.stdout)


class FirstRun(unittest.TestCase):
    def test_notification_once_and_only_with_mic(self):
        sys.path.insert(0, LIB)
        import fabos_voiced as D
        d = D.Voiced()
        marker = D.FIRST_RUN_MARKER
        if os.path.exists(marker):
            os.remove(marker)
        if os.path.exists(NOTIFY_LOG):
            os.remove(NOTIFY_LOG)
        orig = V.mic_present
        try:
            V.mic_present = lambda: False
            d.first_run()
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(os.path.exists(NOTIFY_LOG))
            V.mic_present = lambda: True
            d.first_run()
            self.assertTrue(os.path.exists(marker))
            with open(NOTIFY_LOG) as fh:
                self.assertIn(P.FIRST_RUN_BODY, fh.read())
            self.assertEqual(d.notify_id, 42)       # -p printed id is remembered for -r replace
            d.first_run()
            with open(NOTIFY_LOG) as fh:
                self.assertEqual(fh.read().count(P.FIRST_RUN_BODY), 1)
        finally:
            V.mic_present = orig


# --------------------------------------------------------------------------- engines (skip when absent)
@unittest.skipUnless(which_all("pocketsphinx", "espeak-ng", "sox") and V.spotter_ready(), "pocketsphinx/espeak-ng/sox not installed")
class WakeWordSpotting(unittest.TestCase):
    def test_hey_fab_detected_and_negative_control(self):
        pcm = silence(0.6) + espeak_16k("hey fab") + silence(0.6)
        hits = V.spot_in_pcm(pcm, "hey fab", V.DEFAULT_SETTINGS["voice.kws_threshold"])
        self.assertGreaterEqual(len(hits), 1, "pocketsphinx did not spot 'hey fab' in the espeak-ng sample")
        self.assertIn("hey fab", hits[0]["t"])
        neg = silence(1.0) + espeak_16k("good morning everyone, how are you today") + silence(1.0) + espeak_16k("please open the files application and read the notes", voice="en-gb-x-rp", speed=130) + silence(1.0)
        self.assertEqual(V.spot_in_pcm(neg, "hey fab", V.DEFAULT_SETTINGS["voice.kws_threshold"]), [])

    def test_detection_arrives_before_eof_on_a_live_stream(self):
        """The daemon reads one JSON line per utterance while the microphone stream stays open."""
        pcm = silence(0.6) + espeak_16k("hey fab") + silence(0.6)
        p = subprocess.Popen(V.spotter_command("hey fab", V.DEFAULT_SETTINGS["voice.kws_threshold"]), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        got = []
        import threading

        def reader():
            for line in p.stdout:
                got.append(line.decode(errors="replace"))
        threading.Thread(target=reader, daemon=True).start()
        try:
            for c in chunks(pcm):
                p.stdin.write(c)
                p.stdin.flush()
                time.sleep(V.CHUNK_MS / 1000.0)
            p.stdin.write(silence(0.1))
            p.stdin.flush()
            deadline = time.time() + 4.0
            while time.time() < deadline and not any(V.parse_spot_line(l, "hey fab") for l in got):
                time.sleep(0.1)
            self.assertTrue(any(V.parse_spot_line(l, "hey fab") for l in got), "no detection while the stream was still open: %r" % got)
        finally:
            p.stdin.close()
            p.kill()
            p.wait(5)
            p.stdout.close()


@unittest.skipUnless(shutil.which("whisper-cli") and os.path.isfile(V.MODEL_PATH) and which_all("espeak-ng", "sox"), "whisper-cli + model + espeak-ng/sox not available")
class WhisperTranscription(unittest.TestCase):
    def test_transcribes_espeak_sentence(self):
        pcm = espeak_16k("good morning everyone, how are you today", voice="en-gb-x-rp", speed=150)
        wav = V.write_wav(os.path.join(TMP, "w.wav"), pcm)
        text = V.transcribe_whisper(wav)
        self.assertIn("good morning", text.lower(), text)
        agent = V.Agent(run_dir=os.path.join(TMP, "no-agent"))
        text2, backend = V.transcribe(pcm, agent, dict(V.DEFAULT_SETTINGS))
        self.assertEqual(backend, "whisper.cpp")
        self.assertIn("morning", text2.lower())

    def test_wake_verification_accepts_hey_fab_and_rejects_near_miss(self):
        """pocketsphinx fires on 'hey bob ... fabulous' (verified in the container); the second look must reject it."""
        good = silence(0.4) + espeak_16k("hey fab") + silence(0.4)
        self.assertTrue(V.verify_wake_clip(good), "real wake clip was rejected")
        near = espeak_16k("hey bob, that was a fabulous match yesterday")[-3 * V.RATE * 2:]
        self.assertFalse(V.verify_wake_clip(near), "near-miss clip was accepted")
        self.assertIsNone(V.verify_wake_clip(b""))


# --------------------------------------------------------------------------- end to end with the agent's FakeProvider
@unittest.skipUnless(os.path.isfile(AGENTD), "fabos_agentd.py not in this checkout")
class EndToEnd(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.PORT = str(s.getsockname()[1])
        cls.rundir = os.path.join(TMP, "e2e-run")
        os.makedirs(cls.rundir, mode=0o700, exist_ok=True)
        cls.home = os.path.join(TMP, "e2e-home")
        os.makedirs(cls.home, exist_ok=True)
        cls.env = dict(os.environ, XDG_RUNTIME_DIR=cls.rundir, HOME=cls.home, FABOS_AGENT_DATA=os.path.join(TMP, "e2e-data"),
                       XDG_CONFIG_HOME=os.path.join(TMP, "e2e-cfg"), FABOS_AGENT_PROVIDER="fake", FABOS_AGENT_PORT=cls.PORT,
                       FABOS_VOICE_STATE_DIR=os.path.join(TMP, "e2e-state"), FABOS_VOICE_MODEL="/nonexistent")
        cls.log = open(os.path.join(TMP, "agentd.log"), "w")
        cls.proc = subprocess.Popen([sys.executable, AGENTD], env=cls.env, stdout=cls.log, stderr=subprocess.STDOUT, text=True)
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/health" % cls.PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            cls.proc.kill()
            raise unittest.SkipTest("fabos-agentd did not start on port " + cls.PORT)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except Exception:
            cls.proc.kill()
        cls.log.close()

    def api(self, method, path, body=None):
        with open(os.path.join(self.rundir, "fabos-agent", "token")) as fh:
            token = fh.read().strip()
        req = urllib.request.Request("http://127.0.0.1:%s%s" % (self.PORT, path), method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def handle(self, text, listen_lines, mode):
        spoken = os.path.join(TMP, "spoken-%d.log" % int(time.time() * 1000))
        heard = spoken + ".listen"
        with open(heard, "w") as f:
            f.write("\n".join(listen_lines) + "\n")
        self.api("PUT", "/settings", {"mode": mode})
        env = dict(self.env, FABOS_VOICE_FAKE_SPEAK=spoken, FABOS_VOICE_FAKE_LISTEN=heard)
        r = subprocess.run([sys.executable, VOICED, "--handle", text], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = []
        if os.path.exists(spoken):
            with open(spoken) as fh:
                lines = fh.read().splitlines()
        return lines, r.stderr

    def test_1_narration_and_voice_approvals(self):
        spoken, err = self.handle("open editor and write hello there", ["yes go ahead", "haan", "ok"], "ask")
        joined = "\n".join(spoken)
        self.assertEqual(spoken[0], P.STARTED, joined)
        self.assertIn("Writing fabos-note.txt now.", joined)
        self.assertIn("This needs your permission: write to fabos-note.txt. Shall I go ahead?", joined)
        self.assertIn(P.APPROVED, joined)
        self.assertIn("Opening Fab Editor for you now.", joined)
        self.assertIn("Running a command for you now.", joined)
        self.assertTrue(spoken[-1].startswith("Done: executed 3 steps"), joined)
        self.assertNotIn("Opening kate", joined)    # the user hears Fab OS product names for apps
        tasks = self.api("GET", "/tasks")
        t = self.api("GET", "/tasks/%d" % tasks[0]["id"])
        self.assertEqual(t["status"], "done")
        self.assertTrue(all(a["status"] == "approved" for a in t["approvals"]), t["approvals"])
        self.assertGreaterEqual(len(t["approvals"]), 1)
        self.assertTrue(os.path.exists(os.path.join(self.home, "Documents", "fabos-note.txt")))

    def test_2_voice_denial(self):
        spoken, err = self.handle("open editor and write nothing", ["nahi", "no", "cancel"], "ask")
        joined = "\n".join(spoken)
        self.assertIn(P.DENIED, joined)
        self.assertNotIn(P.APPROVED, joined)
        t = self.api("GET", "/tasks/%d" % self.api("GET", "/tasks")[0]["id"])
        self.assertEqual(t["status"], "done")
        self.assertTrue(all(a["status"] == "denied" for a in t["approvals"]), t["approvals"])

    def test_3_unclear_answer_waits_on_screen(self):
        spoken_path = os.path.join(TMP, "spoken-unclear.log")
        heard = spoken_path + ".listen"
        with open(heard, "w") as f:
            f.write("what is this\n")
        self.api("PUT", "/settings", {"mode": "ask"})
        env = dict(self.env, FABOS_VOICE_FAKE_SPEAK=spoken_path, FABOS_VOICE_FAKE_LISTEN=heard)
        p = subprocess.Popen([sys.executable, VOICED, "--handle", "run something privileged"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(spoken_path):
                with open(spoken_path) as fh:
                    if P.WAIT_ON_SCREEN in fh.read():
                        break
            time.sleep(0.3)
        else:
            p.kill()
            self.fail("daemon never said it would wait on screen")
        pending = self.api("GET", "/approvals/pending")
        self.assertEqual(len(pending), 1)
        self.api("POST", "/approvals/%d" % pending[0]["id"], {"decision": "denied"})   # the user decides on screen
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)

    def test_4_ai_off_is_spoken(self):
        self.api("PUT", "/settings", {"ai.enabled": "false"})
        try:
            spoken, err = self.handle("anything", [], "auto")
            self.assertEqual(spoken, [P.AI_OFF])
        finally:
            self.api("PUT", "/settings", {"ai.enabled": "true"})

    def test_5_status_sees_agent_and_wake_setting(self):
        self.api("PUT", "/settings", {"voice.enabled": "false"})
        r = subprocess.run([sys.executable, CLI, "-v", "status"], env=dict(self.env, FABOS_VOICE_MIC="0"), capture_output=True, text=True, timeout=30)
        st = json.loads(r.stdout)
        self.assertTrue(st["agent"])
        self.assertFalse(st["enabled"])
        self.assertEqual(st["stt"], "none")           # no cloud key, no model in this environment
        r = subprocess.run([sys.executable, CLI, "wake", "on"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.api("GET", "/settings")["voice.enabled"], "true")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
