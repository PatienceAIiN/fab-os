#!/usr/bin/env python3
"""Offline tests for fabos-voice (no microphone, no network, no GUI).

Always: RMS voice-activity segmentation on synthetic audio, WAV round-trip, chime, phrase table covers every agent
tool, yes/no intent mapping, banned-word hygiene, the CLI contract with nothing available (status JSON keys and
reasons, listen-once -> exit 4, say -> exit 4, doctor -> FAIL lines with hints), the microphone decision logic on
synthetic PCM through scripted recorders (all-zero stream -> exit 3 "muted or silent", tone burst -> a segment and
a transcript, a recorder without data -> the next candidate, no audio session -> exit 4, the timeout + 3 s hard cap),
playback rules (espeak-ng profile flags, a player that played is never followed by a second one, the machine-wide
playback lock), the daemon's no-repeat rule over a scripted task (each line once across the polls), the spotter
pause while speech plays, first-run notification, and the wake daemon's post-wake path end to end against
fabos-agentd's FakeProvider with fake speak/listen hooks (narration, voice approval, denial, AI off).
When the engines exist (Ubuntu 26.04 image / container): espeak-ng "hey fab" -> pocketsphinx keyphrase spotting
(file and real-time stream, plus a negative control) and whisper.cpp transcription with the shipped tiny.en model.

    python3 tests/voice-test.py            # host: engine tests skip
    FABOS_VOICE_MODEL=/path/ggml-tiny.en.bin python3 tests/voice-test.py   # container with pocketsphinx/whisper.cpp
"""
import base64
import collections
import contextlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
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
os.environ["FABOS_VOICE_SESSION"] = "1"     # the recorder tests script their own microphone; pretend PipeWire is up
os.environ["FABOS_VOICE_MIC_ALLOWED"] = "1" # the microphone permission (1.0-8) is on for every test unless a test says otherwise

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


def with_candidates(cands, fn):
    """Run fn() with the recorder candidates replaced by scripted commands (the default-source lookup is skipped too)."""
    saved = V.capture_candidates, V.default_source
    V.capture_candidates = lambda target=None: [list(c) for c in cands]
    V.default_source = lambda: {"name": None, "description": None, "muted": None, "volume": None, "via": None, "reason": ""}
    try:
        return fn()
    finally:
        V.capture_candidates, V.default_source = saved


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


ZEROS_FOREVER = "import sys,time\nwhile True:\n sys.stdout.buffer.write(b'\\0'*3200); sys.stdout.buffer.flush(); time.sleep(0.1)"
NOISE_FOREVER = ("import sys,time,random,struct\nr=random.Random(3)\n"
                 "while True:\n sys.stdout.buffer.write(b''.join(struct.pack('<h', r.randint(-25,25)) for _ in range(1600))); sys.stdout.buffer.flush(); time.sleep(0.1)")
TONE_THEN_SILENCE = ("import sys,time,math,struct\n"
                     "def tone(n,a):\n return b''.join(struct.pack('<h', int(math.sin(2*math.pi*440*i/16000)*a)) for i in range(n))\n"
                     "sys.stdout.buffer.write(tone(8000,20)); sys.stdout.buffer.write(tone(12800,3000)); sys.stdout.buffer.flush()\n"
                     "while True:\n sys.stdout.buffer.write(b'\\0'*3200); sys.stdout.buffer.flush(); time.sleep(0.1)")


class Recorder(unittest.TestCase):
    """record_utterance against a scripted 'microphone' (a python process writing raw s16 to stdout)."""

    def fake_mic(self, script):
        return [sys.executable, "-c", script]

    def with_mic(self, cmd, fn):
        return with_candidates([cmd], fn)

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


class MicDecision(unittest.TestCase):
    """The listen-once decision logic on synthetic PCM (spec: silence -> exit 3 with the muted reason, tone burst -> a
    segment; a recorder without data -> the next candidate; no audio session -> exit 4; never longer than timeout + 3 s)."""

    def mic(self, script):
        return [sys.executable, "-c", script]

    def test_all_zero_stream_is_a_muted_microphone(self):
        t0 = time.time()
        with self.assertRaises(V.NothingHeard) as cm:
            with_candidates([self.mic(ZEROS_FOREVER)], lambda: V.record_utterance(10))
        self.assertEqual(cm.exception.reason, P.MIC_SILENT)
        self.assertLess(time.time() - t0, 4.0, "a dead stream must be recognised after its first second, not at the timeout")

    def test_tone_burst_is_one_segment(self):
        pcm = with_candidates([self.mic(TONE_THEN_SILENCE)], lambda: V.record_utterance(10))
        self.assertTrue(2.0 <= len(pcm) / 32000.0 <= 2.8, "segment length %.2f s" % (len(pcm) / 32000.0))
        self.assertGreater(V.rms(pcm), 500)
        self.assertEqual(V.LAST_CAPTURE["cmd"][0], sys.executable)

    def test_faint_noise_only_is_nothing_heard_without_the_muted_reason(self):
        t0 = time.time()
        with self.assertRaises(V.NothingHeard) as cm:
            with_candidates([self.mic(NOISE_FOREVER)], lambda: V.record_utterance(1.5))
        self.assertEqual(cm.exception.reason, "")
        self.assertLess(time.time() - t0, 1.5 + V.RECORD_GRACE_S)

    def test_segmenter_dead_stream_flag(self):
        seg = V.Segmenter()
        for c in chunks(silence(1.0)):
            seg.feed(c)
        self.assertTrue(seg.dead_stream)
        seg = V.Segmenter()
        for c in chunks(silence(0.5)):
            seg.feed(c)
        self.assertFalse(seg.dead_stream, "half a second is too early to call the stream dead")
        seg = V.Segmenter()
        for c in chunks(pcm_noise(1.0)):
            seg.feed(c)
        self.assertFalse(seg.dead_stream)
        seg = V.Segmenter()
        for c in chunks(silence(0.5) + pcm_noise(0.5)):
            seg.feed(c)
        self.assertFalse(seg.dead_stream, "a stream that comes alive is not dead")

    def test_recorder_without_data_hands_over_to_the_next_candidate(self):
        dead = self.mic("import sys; sys.exit(0)")                              # ends at once without a byte
        good = self.mic(TONE_THEN_SILENCE)
        pcm = with_candidates([dead, good], lambda: V.record_utterance(10))
        self.assertGreater(len(pcm), 32000)
        self.assertEqual(V.LAST_CAPTURE["cmd"], good)
        self.assertEqual(len(V.LAST_CAPTURE["tried"]), 1, V.LAST_CAPTURE["tried"])

    def test_recorder_that_never_delivers_cannot_hang(self):
        hang = self.mic("import time; time.sleep(60)")
        t0 = time.time()
        with self.assertRaises(V.NoBackend) as cm:
            with_candidates([hang], lambda: V.record_utterance(2))
        self.assertLess(time.time() - t0, 2 + V.RECORD_GRACE_S + 1.0)
        self.assertIn("no audio within", str(cm.exception))

    def test_no_audio_session_is_no_backend(self):
        os.environ["FABOS_VOICE_SESSION"] = "0"
        try:
            with self.assertRaises(V.NoBackend) as cm:
                with_candidates([self.mic(ZEROS_FOREVER)], lambda: V.record_utterance(2))
            self.assertIn("No audio session", str(cm.exception))
            self.assertFalse(V.playback_possible()[0])
        finally:
            os.environ["FABOS_VOICE_SESSION"] = "1"

    def cli_env(self, recorder_script, whisper_text):
        """A PATH with a scripted pw-record and whisper-cli, a fake model file and a forced audio session."""
        fake = os.path.join(TMP, "fakebin-%d" % abs(hash(recorder_script + whisper_text)))
        os.makedirs(fake, exist_ok=True)
        with open(os.path.join(fake, "pw-record"), "w") as f:
            f.write("#!%s\n%s\n" % (sys.executable, recorder_script))
        with open(os.path.join(fake, "whisper-cli"), "w") as f:
            f.write("#!/bin/sh\nprintf '%%s\\n' %s\n" % json.dumps(whisper_text))
        for n in ("pw-record", "whisper-cli"):
            os.chmod(os.path.join(fake, n), 0o755)
        model = os.path.join(fake, "ggml-tiny.en.bin")
        with open(model, "wb") as f:
            f.write(b"\0" * 64)
        run = os.path.join(TMP, "cli-run")
        os.makedirs(run, exist_ok=True)
        return dict(os.environ, PATH=fake + ":" + BIN, XDG_RUNTIME_DIR=run, FABOS_VOICE_MODEL=model, FABOS_VOICE_SESSION="1", FABOS_VOICE_MIC="1",
                    FABOS_VOICE_STATE_DIR=os.path.join(TMP, "cli-state"))

    def test_cli_silent_microphone_exits_3_with_the_reason_on_stderr(self):
        t0 = time.time()
        r = subprocess.run([sys.executable, CLI, "listen-once", "--timeout", "4"], env=self.cli_env(ZEROS_FOREVER, "should not be called"), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("muted or silent", r.stderr)
        self.assertLess(time.time() - t0, 8.0)

    def test_cli_tone_burst_prints_the_transcript(self):
        r = subprocess.run([sys.executable, CLI, "-v", "listen-once", "--timeout", "6"], env=self.cli_env(TONE_THEN_SILENCE, " Open my downloads folder."), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "Open my downloads folder.")
        self.assertIn("backend: whisper.cpp", r.stderr)
        self.assertIn("recorder: pw-record", r.stderr)

    def test_capture_candidates_name_the_default_source(self):
        orig = V.which
        V.which = lambda *names: "/usr/bin/" + names[0] if names[0] in ("pw-record", "parec", "arecord") else None
        try:
            c = V.capture_candidates("alsa_input.usb-mic")
            self.assertEqual([x[0] for x in c], ["pw-record", "pw-record", "parec", "arecord"])
            self.assertIn("--target", c[1])
            self.assertEqual(c[1][c[1].index("--target") + 1], "alsa_input.usb-mic")
            self.assertIn("--device=alsa_input.usb-mic", c[2])
            c = V.capture_candidates()
            self.assertEqual([x[0] for x in c], ["pw-record", "parec", "arecord"])
            self.assertIn("--device=@DEFAULT_SOURCE@", c[1])
        finally:
            V.which = orig


class Playback(unittest.TestCase):
    """Text-to-speech rules: the tuned espeak-ng profile, one player per utterance, one utterance at a time."""

    def test_espeak_profile_flags(self):
        cmd = V.espeak_command("hello there", "/tmp/x.wav")
        self.assertEqual(cmd[0], "espeak-ng")
        for flag, val in (("-v", "en-gb-x-rp"), ("-s", "150"), ("-p", "45"), ("-a", "175"), ("-g", "6")):
            self.assertIn(flag, cmd)
            self.assertEqual(cmd[cmd.index(flag) + 1], val, flag)
        self.assertLessEqual(int(cmd[cmd.index("-a") + 1]), 200)      # espeak-ng: amplitude 0..200
        self.assertEqual(cmd[-4:], ["-w", "/tmp/x.wav", "--", "hello there"])
        self.assertNotIn("-w", V.espeak_command("x"))

    @unittest.skipUnless(shutil.which("espeak-ng"), "espeak-ng not installed")
    def test_rendered_sample_is_louder_than_stock(self):
        loud = os.path.join(TMP, "loud.wav")
        V.render_espeak(P.SAY_TEST, loud)
        params, pcm = V.read_wav(loud)
        self.assertEqual(params[:2], (1, 2))
        self.assertGreater(V.wav_seconds(loud), 3.0)
        stock = os.path.join(TMP, "stock.wav")
        subprocess.run(["espeak-ng", "-v", "en-gb-x-rp", "-w", stock, "--", P.SAY_TEST], check=True, capture_output=True, timeout=60)
        self.assertGreater(V.rms(pcm), V.rms(V.read_wav(stock)[1]) * 1.3, "the -a 175 profile must be clearly louder than the default")

    def test_player_that_played_is_never_followed_by_another(self):
        wav = V.write_wav(os.path.join(TMP, "one-second.wav"), pcm_tone(1.0))
        marker = os.path.join(TMP, "second-player-ran")
        tearing_down = [sys.executable, "-c", "import time,sys; time.sleep(1.0); sys.exit(1)"]     # played, then exited non-zero
        second = [sys.executable, "-c", "open(%r, 'w').write('x')" % marker]
        saved = V.player_candidates
        V.player_candidates = lambda path, sink=None: [tearing_down, second]
        try:
            self.assertTrue(V.play_file(wav))
        finally:
            V.player_candidates = saved
        self.assertFalse(os.path.exists(marker), "the second player must not replay audio the first one already played")
        self.assertEqual(V.LAST_PLAYBACK["player"], sys.executable)

    def test_player_failing_at_once_hands_over(self):
        wav = V.write_wav(os.path.join(TMP, "one-second-b.wav"), pcm_tone(1.0))
        marker = os.path.join(TMP, "fallback-player-ran")
        broken = [sys.executable, "-c", "import sys; sys.stderr.write('Host is down'); sys.exit(1)"]
        good = [sys.executable, "-c", "open(%r, 'w').write('x')" % marker]
        saved = V.player_candidates
        V.player_candidates = lambda path, sink=None: [broken, good]
        try:
            self.assertTrue(V.play_file(wav))
        finally:
            V.player_candidates = saved
        self.assertTrue(os.path.exists(marker))

    def test_playback_lock_serialises_and_gives_up_after_max_wait(self):
        got = []
        with V.playback_lock() as mine:
            self.assertTrue(mine)
            t0 = time.time()
            t = threading.Thread(target=lambda: got.append(V.playback_lock(max_wait=0.4).__enter__()))
            t.start()
            t.join(5)
            self.assertEqual(got, [False], "a second speaker must wait, then give up after max_wait")
            self.assertGreaterEqual(time.time() - t0, 0.35)
        with V.playback_lock(max_wait=0.4) as again:
            self.assertTrue(again, "released lock is free again")

    def test_wait_for_silence_respects_another_speaker(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            V.ensure_run_dir()
            with open(os.path.join(V.RUN_DIR, "speaking.lock"), "w") as f:
                f.write(str(other.pid))
            self.assertTrue(V.lock_active("speaking"))
            t0 = time.time()
            self.assertFalse(V.wait_for_silence(max_wait=0.5))
            self.assertGreaterEqual(time.time() - t0, 0.45)
            other.kill()
            other.wait(5)
            self.assertTrue(V.wait_for_silence(max_wait=0.5))
        finally:
            with contextlib.suppress(Exception):
                other.kill()
            with contextlib.suppress(OSError):
                os.remove(os.path.join(V.RUN_DIR, "speaking.lock"))


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
        self.assertEqual(P.approval_summary("run_shell", {"command": "rm -rf ~/tmp"}), "run a command")                       # raw text hidden by default
        self.assertEqual(P.approval_summary("run_shell", {"command": "rm -rf ~/tmp"}, raw=True), "run the command rm -rf ~/tmp")  # only with ui.show_raw
        self.assertEqual(P.approval_summary("run_shell", {"command": "apt install x", "as_root": True}), "run a command as administrator")
        self.assertEqual(P.approval_summary("send_email", json.dumps({"to": "priya@example.com"})), "send an email to priya@example.com")
        self.assertEqual(P.approval_summary("write_file", {"path": "/etc/hosts"}), "write to hosts")
        s = P.PERMISSION.format(summary=P.approval_summary("open_app", {"app": "konsole"}))
        self.assertEqual(s, "This needs your permission: open Fab Terminal. Shall I go ahead?")

    def test_yes_no_intents(self):
        for yes in ("yes", "Yes please", "ok", "okay go ahead", "go ahead", "sure", "haan", "haan ji kar do", "theek hai", "proceed", "yep do it", "Yeah, fine.",
                    "fine", "ya", "ya ya", "allow it", "continue", "no problem", "sure why not", "go ahead please", "please yes", "haan karo", "that's fine", "okay fab"):
            self.assertEqual(P.intent(yes), "approve", yes)
        for no in ("no", "No thanks", "nahi", "nahin", "cancel", "stop", "wait", "not now", "don't", "yes, wait", "hold on", "mat karo", "Nope.",
                   "okay, stop", "never mind", "not really", "later", "no, I do not want you to do that right now"):
            self.assertEqual(P.intent(no), "deny", no)
        for unclear in ("", None, "banana", "what does this do", "hmm", "please explain", "is that right", "I am not sure", "maybe", "let me think", "you", "Thank you."):
            self.assertIsNone(P.intent(unclear), repr(unclear))

    def test_only_answer_shaped_utterances_approve(self):
        """A yes-word inside a longer sentence (overheard talk, thinking aloud, a whisper mis-hearing) must never approve."""
        for sentence in ("hmm that is a fine question let me think about it", "the weather is fine today",
                         "I think you should probably continue with the plan", "accept the terms and conditions of the website",
                         "the download should continue automatically", "sure, but only if it is safe",
                         "yes if you think it is fine to do so", "Right, I know it's not as calm at 6.", "fine weather today",
                         "allow me to explain what I meant by that", "ok so what does this command actually do"):
            self.assertIsNone(P.intent(sentence), sentence)
        self.assertEqual(P.MAX_ANSWER_TOKENS, 4)
        # strict (as_root / CRITICAL): only a strong yes-word up front
        for weak in ("fine", "ya", "allow it", "continue", "go", "correct"):
            self.assertEqual(P.intent(weak), "approve", weak)
            self.assertIsNone(P.intent(weak, strict=True), weak)
        for strong in ("yes", "haan", "okay go ahead", "go ahead", "theek hai", "yes please"):
            self.assertEqual(P.intent(strong, strict=True), "approve", strong)
        for no in ("no", "yes, wait", "not now"):
            self.assertEqual(P.intent(no, strict=True), "deny", no)

    def test_stop_intent(self):
        for stop in ("stop", "stop that", "cancel it", "please stop", "band karo", "never mind", "ruko", "Stop!"):
            self.assertTrue(P.is_stop(stop), stop)
        for other in ("", None, "open my downloads", "stop the music and open files", "yes", "what is the time", "do not stop"):
            self.assertFalse(P.is_stop(other), repr(other))

    def test_app_names(self):
        self.assertEqual(P.app_name("systemsettings"), "Fab Settings")
        self.assertEqual(P.app_name("ark"), "Fab Archives")
        self.assertEqual(P.app_name("/usr/bin/dolphin --select x"), "Fab Files")
        self.assertNotIn("System Settings", P.APP_NAMES.values())

    def test_hindi_yes_words(self):
        for yes in ("bilkul", "zaroor", "haan bilkul", "bilkul karo"):
            self.assertEqual(P.intent(yes), "approve", yes)
            self.assertEqual(P.intent(yes, strict=True), "approve", yes)
        for no in ("bilkul nahi", "zaroor nahi", "nahi, bilkul nahi"):
            self.assertEqual(P.intent(no), "deny", no)
            self.assertEqual(P.intent(no, strict=True), "deny", no)

    def test_wake_transcript_check(self):
        for ok in ("Hey Fab.", " Okay, fam.", "hey fab", "Hey, Fap!", "Hey.", "A fab", "Hey Feb"):
            self.assertTrue(V.wake_transcript_ok(ok), ok)
        for bad in ("", "Hey Bob, that was a fabulous match yesterday.", "Please open the files application and read the notes.", "the fabric of the tent was torn", "good morning everyone, how are you today"):
            self.assertFalse(V.wake_transcript_ok(bad), bad)

    def test_request_after_wake(self):
        """pocketsphinx reports the phrase at utterance end: a run-on 'Hey Fab open my downloads' is already in the clip."""
        self.assertEqual(V.request_after_wake("Hey Fab, open my downloads folder."), "Open my downloads folder.")
        self.assertEqual(V.request_after_wake(" hey fam open my downloads"), "Open my downloads")
        self.assertEqual(V.request_after_wake("Hey Fab."), "")            # nothing after the phrase
        self.assertEqual(V.request_after_wake("Hey Fab, please"), "")     # one stray word is not a request
        self.assertEqual(V.request_after_wake("good morning everyone"), "")
        self.assertEqual(V.request_after_wake(""), "")
        # verdict + transcript pair keeps the old boolean helper intact
        self.assertEqual(V.hear_wake_clip(b""), (None, ""))
        self.assertIsNone(V.verify_wake_clip(b""))

    def test_whisper_scope_prefix_only_under_systemd(self):
        """Outside a systemd service (no INVOCATION_ID) whisper-cli runs directly; under one, in a transient scope with its own MemoryHigh."""
        saved = os.environ.pop("INVOCATION_ID", None)
        try:
            V._scope_ok = None
            self.assertEqual(V.scope_prefix(), [])
            os.environ["INVOCATION_ID"] = "test"
            os.environ["FABOS_VOICE_NO_SCOPE"] = "1"
            self.assertEqual(V.scope_prefix(), [])                        # explicit opt-out
            os.environ.pop("FABOS_VOICE_NO_SCOPE")
            V._scope_ok = True                                           # as after a successful probe
            pre = V.scope_prefix()
            self.assertEqual(pre[:3], ["systemd-run", "--user", "--scope"]) if shutil.which("systemd-run") else self.assertEqual(pre, [])
            if pre:
                self.assertIn("MemoryHigh=" + V.WHISPER_SCOPE_MEMORY_HIGH, pre)
                self.assertEqual(pre[-1], "--")
        finally:
            V._scope_ok = None
            os.environ.pop("INVOCATION_ID", None)
            if saved is not None:
                os.environ["INVOCATION_ID"] = saved

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
                    FABOS_VOICE_SESSION="0", FABOS_VOICE_STATE_DIR=os.path.join(TMP, "empty-state"))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI] + list(args), env=self.env(), capture_output=True, text=True, timeout=60)

    def test_status_json_keys(self):
        r = self.run_cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout.strip())
        self.assertEqual(set(st), {"wake", "listening", "stt", "tts", "mic", "mic_allowed", "service", "mic_reason", "stt_reason", "tts_reason"})
        self.assertEqual((st["mic_allowed"], st["service"]), (True, False))
        self.assertEqual({k: st[k] for k in ("wake", "listening", "stt", "tts", "mic")}, {"wake": False, "listening": False, "stt": "none", "tts": "none", "mic": False})
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)
        # every unavailable piece says why
        self.assertIn("No audio session", st["mic_reason"])
        self.assertIn("not available", st["stt_reason"])
        self.assertIn("No audio session", st["tts_reason"])

    def test_status_reasons_are_empty_when_a_piece_works(self):
        saved = V.mic_state, V.stt_backends, V.tts_backends
        V.mic_state = lambda session=None: (True, "", {"name": "mic", "muted": False})
        V.stt_backends = lambda agent, settings=None: ["whisper.cpp"]
        V.tts_backends = lambda agent, settings=None, session=None: ["espeak-ng"]
        try:
            st = V.status(V.Agent(run_dir=os.path.join(TMP, "no-agent")))
        finally:
            V.mic_state, V.stt_backends, V.tts_backends = saved
        self.assertEqual((st["mic"], st["stt"], st["tts"]), (True, "whisper.cpp", "espeak-ng"))
        self.assertEqual((st["mic_reason"], st["stt_reason"], st["tts_reason"]), ("", "", ""))

    def test_muted_microphone_is_present_but_says_so(self):
        saved = V.mic_present, V.default_source
        V.mic_present = lambda: True
        V.default_source = lambda: {"name": "alsa_input.x", "description": "Mic", "muted": True, "volume": 1.0, "via": "test", "reason": ""}
        try:
            ok, why, src = V.mic_state({"ok": True, "server": "PipeWire", "version": "", "via": "forced", "reason": ""})
        finally:
            V.mic_present, V.default_source = saved
        self.assertTrue(ok)
        self.assertEqual(why, P.MIC_MUTED)

    def test_doctor_reports_every_stage_with_hints_and_fails(self):
        r = self.run_cli("doctor", "--quiet")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        for stage in V.DOCTOR_STAGES:
            self.assertTrue(any(l.split()[1] == stage for l in lines if l.startswith(("OK", "FAIL"))), "no line for stage " + stage + "\n" + r.stdout)
        self.assertTrue(all(l.startswith(("OK ", "FAIL ", "   ")) or "required stages" in l for l in lines), r.stdout)
        self.assertIn("fix:", r.stdout)
        self.assertIn("No audio session", r.stdout)
        r = self.run_cli("doctor", "--quiet", "--json")
        self.assertEqual(r.returncode, 1)
        d = json.loads(r.stdout)
        self.assertFalse(d["ok"])
        self.assertEqual([s["stage"] for s in d["stages"]], list(V.DOCTOR_STAGES))
        self.assertEqual({s["stage"] for s in d["stages"] if not s["required"]}, set(V.DOCTOR_OPTIONAL))
        for s in d["stages"]:
            if not s["ok"]:
                self.assertTrue(s["hint"], "no hint for failed stage " + s["stage"])
        self.assertFalse(next(s for s in d["stages"] if s["stage"] == "audio-session")["ok"])

    def test_say_test_exit_4_without_tts(self):
        r = self.run_cli("say", "--test")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("No audio session", r.stderr)

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
        for cmd in ("listen-once", "say", "status", "doctor", "wake"):
            self.assertIn(cmd, r.stdout)


class MicPermissionAndProgress(unittest.TestCase):
    """1.0-8: the microphone permission (voice.mic_allowed — permissions only enable: listen-once, the spotter and the doctor's
    capture refuse while it is off) and the live progress file of listen-once (--progress PATH) the ask bar and Fab AI
    Controls show as "starting / speak now + level / understanding / error"."""

    def env(self, **kw):
        run = os.path.join(TMP, "perm-run")
        os.makedirs(run, exist_ok=True)
        e = dict(os.environ, PATH=BIN, XDG_RUNTIME_DIR=run, FABOS_VOICE_MODEL="/nonexistent/ggml-tiny.en.bin", FABOS_VOICE_MIC="1",
                 FABOS_VOICE_SESSION="1", FABOS_VOICE_STATE_DIR=os.path.join(TMP, "perm-state"))
        e.update(kw)
        return e

    def run_cli(self, *args, **envkw):
        return subprocess.run([sys.executable, CLI] + list(args), env=self.env(**envkw), capture_output=True, text=True, timeout=60)

    def test_listen_once_refuses_while_the_microphone_is_off_in_settings(self):
        heard = os.path.join(TMP, "perm-heard.txt")
        with open(heard, "w") as f:
            f.write("open the files app\n")
        r = self.run_cli("listen-once", "--timeout", "2", FABOS_VOICE_MIC_ALLOWED="0", FABOS_VOICE_FAKE_LISTEN=heard)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("Microphone is off in Settings", r.stderr)
        self.assertIn("Allow Fab OS to use the microphone", r.stderr)
        self.assertEqual(open(heard).read(), "open the files app\n", "nothing may be 'heard' while the permission is off")
        r = self.run_cli("listen-once", "--timeout", "2", FABOS_VOICE_MIC_ALLOWED="1", FABOS_VOICE_FAKE_LISTEN=heard)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "open the files app"), r.stderr)

    def test_status_and_doctor_show_the_permission(self):
        st = json.loads(self.run_cli("status", FABOS_VOICE_MIC_ALLOWED="0").stdout)
        self.assertFalse(st["mic_allowed"])
        self.assertIn("service", st)
        st = json.loads(self.run_cli("status", FABOS_VOICE_MIC_ALLOWED="1").stdout)
        self.assertTrue(st["mic_allowed"])
        d = json.loads(self.run_cli("doctor", "--quiet", "--json", FABOS_VOICE_MIC_ALLOWED="0").stdout)
        stage = next(x for x in d["stages"] if x["stage"] == "mic-permission")
        self.assertFalse(stage["ok"]); self.assertTrue(stage["required"]); self.assertIn("Allow Fab OS to use the microphone", stage["hint"])
        cap = next(x for x in d["stages"] if x["stage"] == "capture")
        self.assertFalse(cap["ok"]); self.assertIn("skipped", cap["detail"]); self.assertIn("Microphone is off", cap["detail"])
        self.assertEqual([x["stage"] for x in d["stages"]][:4], ["audio-session", "default-source", "mic-permission", "capture"])
        self.assertTrue(next(x for x in json.loads(self.run_cli("doctor", "--quiet", "--json").stdout)["stages"] if x["stage"] == "mic-permission")["ok"])

    def test_mic_allowed_follows_the_agent_and_remembers_its_last_answer(self):
        saved = os.environ.pop("FABOS_VOICE_MIC_ALLOWED", None)
        cache = V.MIC_PERMISSION_CACHE
        V.MIC_PERMISSION_CACHE = os.path.join(TMP, "perm-cache-%d" % os.getpid())
        try:
            self.assertFalse(V.mic_allowed({}), "unknown counts as off")
            self.assertFalse(V.mic_allowed({"_agent_up": True}), "the agent's default is off")
            self.assertTrue(V.mic_allowed({"_agent_up": True, "voice.mic_allowed": "true"}))
            self.assertTrue(V.mic_allowed({"_agent_up": False}), "without the agent the last answer holds")
            self.assertFalse(V.mic_allowed({"_agent_up": True, "voice.mic_allowed": "false"}))
            self.assertFalse(V.mic_allowed({}))
        finally:
            V.MIC_PERMISSION_CACHE = cache
            if saved is not None:
                os.environ["FABOS_VOICE_MIC_ALLOWED"] = saved

    def test_the_wake_daemon_does_not_listen_while_the_permission_is_off(self):
        import fabos_voiced as D
        saved = os.environ.pop("FABOS_VOICE_MIC_ALLOWED", None)
        try:
            d = D.Voiced.__new__(D.Voiced)
            d._warned = set()
            d.settings = dict(V.DEFAULT_SETTINGS, **{"_agent_up": True, "voice.enabled": "true"})
            self.assertFalse(d.enabled())
            d.settings["voice.mic_allowed"] = "true"
            self.assertTrue(d.enabled())
            d.settings["voice.enabled"] = "false"
            self.assertFalse(d.enabled())
        finally:
            if saved is not None:
                os.environ["FABOS_VOICE_MIC_ALLOWED"] = saved

    def test_sighup_makes_the_wake_daemon_re_read_its_settings_at_once(self):
        """fabos-agentd SIGHUPs fabos-voiced (systemctl --user kill -s HUP) when voice.mic_allowed or voice.enabled changes: the
        handler forgets the settings' age, so spot_loop's next tick (1 s) re-reads them and stops the spotter — not the 30 s refresh."""
        import inspect
        import fabos_voiced as D
        d = D.Voiced.__new__(D.Voiced)
        d.settings_ts = time.time()
        d._on_hup(1, None)
        self.assertEqual(d.settings_ts, 0.0)
        self.assertGreater(time.time() - d.settings_ts, D.SETTINGS_REFRESH_S, "spot_loop's staleness check fires at once")
        self.assertIn("signal.SIGHUP, self._on_hup", inspect.getsource(D.Voiced.run), "the main loop installs the handler")

    def test_the_wake_while_busy_spotter_stops_when_the_permission_goes_off_mid_follow(self):
        """While a task is followed the spotter keeps running in the background; a permission (or voice) switched off during that
        stretch must release the microphone on the next poll, not when the task ends (FOLLOW_MAX_S is 30 min)."""
        import fabos_voiced as D
        saved = os.environ.pop("FABOS_VOICE_MIC_ALLOWED", None)
        try:
            d = D.Voiced.__new__(D.Voiced)
            d.stop, d.interrupt, d._warned, d.bg = False, None, set(), object()
            d.settings = dict(V.DEFAULT_SETTINGS, **{"_agent_up": True, "voice.enabled": "true", "voice.mic_allowed": "true"})
            d.settings_ts = 0.0                                                     # as after _on_hup: stale, re-read on the next poll
            fresh = dict(d.settings, **{"voice.mic_allowed": "false"})            # the agent now says: off
            d.agent = types.SimpleNamespace(get=lambda path, timeout=None: {"status": "done", "steps": [], "result": "ok"},
                                            settings=lambda max_age=0.0: fresh)
            stopped = []
            d.bg_spotter_stop = lambda: (stopped.append(1), setattr(d, "bg", None))
            d.finish = lambda task, state: None
            d.speak = lambda *a, **k: None
            state = {"seen": {}, "approvals": set(), "questions": set(), "spoken": set(), "started": time.time()}
            self.assertEqual(d._follow(7, state), "done")
            self.assertEqual(stopped, [1], "the background spotter was stopped on the poll that saw the permission off")
            self.assertIsNone(d.bg)
            # with the permission still on nothing is stopped
            d.bg, stopped[:] = object(), []
            d.settings_ts, fresh["voice.mic_allowed"] = 0.0, "true"
            self.assertEqual(d._follow(7, state), "done")
            self.assertEqual(stopped, [])
        finally:
            if saved is not None:
                os.environ["FABOS_VOICE_MIC_ALLOWED"] = saved

    def test_progress_levels_and_states_from_a_scripted_microphone(self):
        path = os.path.join(TMP, "progress-%d.json" % os.getpid())
        prog = V.Progress(path)
        pcm = with_candidates([[sys.executable, "-c", TONE_THEN_SILENCE]], lambda: V.record_utterance(10, progress=prog))
        self.assertGreater(len(pcm), 32000)
        d = V.read_progress(path)
        self.assertEqual(d["state"], "recording")
        self.assertTrue(d["speech"])
        self.assertGreater(d["peak"], 0.3, d)
        self.assertGreaterEqual(d["seconds"], 1.0)
        self.assertEqual(V.Progress.level_of(40.0, None), 0.0)
        self.assertEqual(V.Progress.level_of(3040.0, 40.0), 1.0)
        self.assertAlmostEqual(V.Progress.level_of(790.0, 40.0), 0.5, places=2)
        self.assertEqual(V.Progress.level_of(100.0, 270.0), 0.0, "below the noise floor is silence")
        self.assertEqual(V.read_progress(path + ".missing"), {})

    def test_cli_progress_file_ends_in_done_or_error(self):
        heard = os.path.join(TMP, "prog-heard.txt")
        with open(heard, "w") as f:
            f.write("hello there\n")
        path = os.path.join(TMP, "cli-progress-%d.json" % os.getpid())
        r = self.run_cli("listen-once", "--timeout", "2", "--progress", path, FABOS_VOICE_FAKE_LISTEN=heard)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = V.read_progress(path)
        self.assertEqual((d["state"], d["text"], d["backend"]), ("done", "hello there", "fake"))
        r = self.run_cli("listen-once", "--timeout", "2", "--progress", path, FABOS_VOICE_MIC_ALLOWED="0")
        self.assertEqual(r.returncode, 4)
        d = V.read_progress(path)
        self.assertEqual((d["state"], d["code"]), ("error", 4)); self.assertIn("Microphone is off", d["reason"])
        empty = os.path.join(TMP, "empty-heard.txt")
        open(empty, "w").close()
        r = self.run_cli("listen-once", "--timeout", "2", "--progress", path, FABOS_VOICE_FAKE_LISTEN=empty)
        self.assertEqual(r.returncode, 3)
        d = V.read_progress(path)
        self.assertEqual((d["state"], d["code"]), ("error", 3)); self.assertTrue(d["reason"])

    def test_speech_to_text_memory_threshold_is_measured_plus_20_percent(self):
        self.assertEqual(V.MEM_NEEDED_KB, int(V.WHISPER_PEAK_RSS_MB * 1.2) * 1024)
        self.assertLess(V.MEM_NEEDED_KB, 600 * 1024, "1.0-7's 600 MB refused a 4 GB laptop with a browser open")
        self.assertIn("%d MB" % (V.MEM_NEEDED_KB // 1024), V.low_memory_text())
        self.assertNotIn("{mb}", V.low_memory_text())
        saved = V.mem_available_kb, V.whisper_ready
        V.mem_available_kb = lambda: V.MEM_NEEDED_KB - 1
        V.whisper_ready = lambda: True
        try:
            with self.assertRaises(V.NoBackend) as cm:
                V.transcribe_whisper("/nonexistent.wav")
            self.assertEqual(str(cm.exception), V.low_memory_text())
        finally:
            V.mem_available_kb, V.whisper_ready = saved
        stages = [{"stage": "speech-to-text", "required": True, "ok": False, "detail": "x", "hint": P.DOCTOR_HINTS["speech-to-text"].replace("{mb}", str(V.MEM_NEEDED_KB // 1024))}]
        self.assertIn("%d MB" % (V.MEM_NEEDED_KB // 1024), V.format_doctor(stages))

    def test_chime_player_cannot_hold_the_recording_back(self):
        saved = V.player_candidates, V.which
        V.player_candidates = lambda path, sink=None: [[sys.executable, "-c", "import time; time.sleep(30)"]]
        V.which = lambda *names: names[0]
        try:
            t0 = time.time()
            V.play_file(V.chime_path(), timeout=0.5)
        finally:
            V.player_candidates, V.which = saved
        self.assertLess(time.time() - t0, 3.0, "a hanging player must be given up after the cap")
        self.assertEqual(V.CHIME_PLAY_TIMEOUT_S, 2.0)


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


class DaemonUnit(unittest.TestCase):
    """Daemon internals that need neither audio nor the agent."""

    def test_ring_covers_a_run_on_request(self):
        import fabos_voiced as D
        d = D.Voiced()
        self.assertGreaterEqual(D.RING_SECONDS, 5)                               # "hey fab open my downloads folder" + end-of-utterance delay
        self.assertEqual(d.ring.maxlen, D.RING_SECONDS * 1000 // V.CHUNK_MS)
        self.assertLessEqual(d.ring.maxlen * V.CHUNK_BYTES, 200 * 1024)          # stays a small in-memory buffer

    def test_follow_clears_a_stale_interrupt(self):
        """A wake caught on the previous stretch must not fire again when the spotter cannot restart (e.g. voice turned off
        meanwhile) — otherwise follow() would chime and listen forever."""
        import fabos_voiced as D
        d = D.Voiced()
        seen = []
        d.interrupt = b"stale"
        d.bg_spotter_start = lambda: False
        d._follow = lambda tid, state: (seen.append(d.interrupt), "done")[1]
        d.follow(1)
        self.assertEqual(seen, [None])

    def scripted_daemon(self, polls, spoken):
        """A Voiced whose agent returns the scripted task JSONs one per poll (the last one repeats), whose speech is
        collected in `spoken`, and which hears nothing when it listens."""
        import fabos_voiced as D
        D.POLL_S = 0.02
        d = D.Voiced()
        d.settings = dict(V.DEFAULT_SETTINGS, mode="auto", **{"ai.enabled": "true", "_agent_up": True, "voice.mic_allowed": "true"})
        d.settings_ts = time.time() + 3600
        d.agent = types.SimpleNamespace(get=lambda path, timeout=None: polls.pop(0) if len(polls) > 1 else polls[0],
                                        post=lambda path, body, timeout=None: {"ok": True},
                                        settings=lambda max_age=0.0: d.settings, status=lambda: None)
        d.listen = lambda *a, **k: None
        d.notify = lambda *a, **k: None
        return d

    def test_each_line_is_spoken_once_across_the_polls(self):
        """Scripted task over six polls: a step appears (narration), finishes (done-line), a step whose done-line equals
        its narration, an approval the agent also narrates as 'This needs your permission…', the same waiting poll
        again, then the final reply. Every sentence must be spoken exactly once, and a second look at the finished task
        within 30 s adds nothing."""
        import fabos_voiced as D
        step1 = {"id": 1, "kind": "tool_call", "name": "open_app", "input": json.dumps({"app": "dolphin"}), "narration": "Opening Fab Files for you now.", "output": None, "narration_done": None}
        step1_done = dict(step1, output=json.dumps({"ok": True}), narration_done="Done, I have opened Fab Files for you.")
        step2 = {"id": 2, "kind": "tool_call", "name": "schedule_watch", "input": "{}", "narration": "The watch is set.", "output": "{}", "narration_done": "The watch is set."}
        step3 = {"id": 3, "kind": "tool_call", "name": "run_shell", "input": json.dumps({"command": "ls"}), "narration": "This needs your permission: run a command. Shall I go ahead?", "output": None, "narration_done": None}
        step3_done = dict(step3, narration="Running a command for you now.", output=json.dumps({"stdout": "x"}), narration_done="That command finished.")
        approval = {"id": 7, "status": "pending", "tool": "run_shell", "input": json.dumps({"command": "ls"}), "risk": "MEDIUM", "reason": "shell"}
        waiting = {"id": 9, "status": "waiting_approval", "steps": [step1_done, step2, step3], "approvals": [approval]}
        done = {"id": 9, "status": "done", "steps": [step1_done, step2, step3_done], "approvals": [dict(approval, status="approved")], "result": "All done. Your files are open and the watch is set."}
        polls = [{"id": 9, "status": "running", "steps": [step1]},
                 {"id": 9, "status": "running", "steps": [step1_done]},
                 {"id": 9, "status": "running", "steps": [step1_done, step2]},
                 waiting, dict(waiting), done]
        spoken = []
        saved_speak = V.speak
        V.speak = lambda text, agent=None, settings=None: (spoken.append(text), "fake")[1]
        if os.path.exists(V.SPOKEN_LOG):
            os.remove(V.SPOKEN_LOG)
        try:
            d = self.scripted_daemon(polls, spoken)
            state = {"seen": {}, "approvals": set(), "questions": set(), "spoken": set(), "started": time.time()}
            self.assertEqual(d._follow(9, state), "done")
            counts = collections.Counter(spoken)
            self.assertEqual(max(counts.values()), 1, "repeated lines: %s" % [t for t, n in counts.items() if n > 1])
            expected = ["Opening Fab Files for you now.", "Done, I have opened Fab Files for you.", "The watch is set.",
                        "This needs your permission: run a command. Shall I go ahead?", P.WAIT_ON_SCREEN, "That command finished.",
                        "All done. Your files are open and the watch is set."]
            self.assertEqual(spoken, expected)
            self.assertEqual(sum(1 for t in spoken if t.startswith("This needs your permission")), 1)
            # the finished task looked at again with a fresh follow state within 30 s: nothing already said is said twice
            # (the one new line is step 3's restored narration, which the first follow rightly skipped while it read
            # "This needs your permission…" — it was never spoken, so it is not a repeat)
            before = len(spoken)
            d._follow(9, {"seen": {}, "approvals": set(), "questions": set(), "spoken": set(), "started": time.time()})
            again = spoken[before:]
            self.assertFalse(set(again) & set(expected), "a second follow of the same task repeated: %s" % sorted(set(again) & set(expected)))
            self.assertLessEqual(again, ["Running a command for you now."])
            self.assertEqual(max(collections.Counter(spoken).values()), 1)
            with open(V.SPOKEN_LOG) as fh:
                logged = [l.split("\t", 2)[2] for l in fh.read().splitlines()]
            self.assertEqual(logged, spoken)
            self.assertEqual(len(set(logged)), len(logged), "spoken.log has a repeated line")
        finally:
            V.speak = saved_speak
            D.POLL_S = 1.5

    def test_prompts_and_acknowledgements_are_never_deduplicated(self):
        """Two approvals with the same wording in a row must both be asked; 'Okay, going ahead.' after each must be heard."""
        import fabos_voiced as D
        spoken = []
        saved_speak = V.speak
        V.speak = lambda text, agent=None, settings=None: (spoken.append(text), "fake")[1]
        try:
            d = self.scripted_daemon([{"id": 1, "status": "done", "steps": []}], spoken)
            state = {"seen": {}, "approvals": set(), "questions": set(), "spoken": set(), "started": time.time()}
            for aid in (11, 12):
                self.assertTrue(d.speak(P.PERMISSION.format(summary="run a command"), "approval", aid, state))
                self.assertTrue(d.speak(P.APPROVED, "ack"))
            self.assertFalse(d.speak(P.PERMISSION.format(summary="run a command"), "approval", 11, state), "the same approval id is asked once")
            self.assertTrue(d.speak("Sure, doing it now.", "ack"))
            self.assertTrue(d.speak("Sure, doing it now.", "ack"))
            self.assertTrue(d.speak("Running a command for you now.", "narration", 1, state))
            self.assertFalse(d.speak("Running a command for you now.", "narration", 2, state), "same text within 30 s is not repeated")
            self.assertEqual(spoken.count(P.APPROVED), 2)
            self.assertEqual(spoken.count("Sure, doing it now."), 2)
        finally:
            V.speak = saved_speak

    def test_spotter_is_fed_nothing_while_speech_plays(self):
        """The pump drops microphone audio while the 'speaking' lock is held (ours or Fab AI Controls'), so the wake
        spotter cannot hear the loudspeaker."""
        import io
        import fabos_voiced as D

        class Sink:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b

            def flush(self):
                pass

            def close(self):
                pass

        pcm = pcm_noise(1.0)
        d = D.Voiced()
        quiet = Sink()
        with V.lock("speaking"):
            d._pump(types.SimpleNamespace(stdout=io.BytesIO(pcm)), types.SimpleNamespace(stdin=quiet))
        self.assertEqual(quiet.data, b"", "audio must not reach pocketsphinx while speech plays")
        self.assertEqual(len(d.ring), 0, "audio dropped while speech plays does not enter the ring either")
        loud = Sink()
        d.gate_enabled = False
        d._pump(types.SimpleNamespace(stdout=io.BytesIO(pcm)), types.SimpleNamespace(stdin=loud))
        self.assertEqual(loud.data, pcm)
        self.assertEqual(b"".join(d.ring), pcm)

    def test_voice_activity_gate_feeds_the_decoder_only_around_speech(self):
        """The recorder's whole stream enters the ring, but pocketsphinx gets nothing for silence or steady room noise
        (RMS ~17 here, below GATE_MIN_FLOOR): a loud burst opens the gate with GATE_PREROLL_CHUNKS of context first,
        keeps it open GATE_HANGOVER_CHUNKS after the last loud chunk, and closes it with GATE_TAIL_CHUNKS of digital
        silence so the decoder's endpointer ends the utterance at once, whatever the room noise. 20 quiet + 4 loud +
        15 quiet chunks -> preroll + 4 + hangover real chunks fed (plus the zero tail), the rest held back."""
        import io
        import fabos_voiced as D

        class Sink:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b

            def flush(self):
                pass

            def close(self):
                pass

        nq1, nq2 = 20, 15
        quiet1, burst, quiet2 = pcm_noise(nq1 / 10.0, seed=3), pcm_noise(0.4, amp=3000, seed=4), pcm_noise(nq2 / 10.0, seed=5)
        self.assertLess(V.rms(quiet1[:V.CHUNK_BYTES]), D.GATE_MIN_FLOOR)
        self.assertGreater(V.rms(burst[:V.CHUNK_BYTES]), D.GATE_MIN_FLOOR * D.GATE_RATIO)
        self.assertGreater(nq1, D.GATE_PREROLL_CHUNKS)
        self.assertGreater(nq2, D.GATE_HANGOVER_CHUNKS)
        d = D.Voiced()
        sink = Sink()
        d._pump(types.SimpleNamespace(stdout=io.BytesIO(quiet1 + burst + quiet2)), types.SimpleNamespace(stdin=sink))
        fed = D.GATE_PREROLL_CHUNKS + 4 + D.GATE_HANGOVER_CHUNKS
        self.assertEqual(b"".join(d.ring), quiet1 + burst + quiet2, "everything enters the ring")
        self.assertEqual(len(sink.data), (fed + D.GATE_TAIL_CHUNKS) * V.CHUNK_BYTES, "preroll + burst + hangover + silence tail reach the decoder")
        self.assertEqual(sink.data[:D.GATE_PREROLL_CHUNKS * V.CHUNK_BYTES], quiet1[-D.GATE_PREROLL_CHUNKS * V.CHUNK_BYTES:], "the second before the onset goes first")
        self.assertIn(burst, sink.data)
        self.assertEqual(sink.data[-D.GATE_TAIL_CHUNKS * V.CHUNK_BYTES:], b"\0" * (D.GATE_TAIL_CHUNKS * V.CHUNK_BYTES), "the gate closes with digital silence")
        self.assertEqual(d.gate_stats, [fed, (nq1 - D.GATE_PREROLL_CHUNKS) + (nq2 - D.GATE_HANGOVER_CHUNKS)])
        # digital silence (a muted or emulated microphone) costs the decoder nothing at all
        d = D.Voiced()
        sink = Sink()
        d._pump(types.SimpleNamespace(stdout=io.BytesIO(silence(3.0))), types.SimpleNamespace(stdin=sink))
        self.assertEqual(sink.data, b"")
        self.assertEqual(d.gate_stats, [0, 30])

    def test_spotter_policy_from_setting_and_performance_mode(self):
        """voice.spotter on | battery-off | off, tightened by the performance mode: Server -> off, Power saver -> an "on"
        listener rests on battery; an unknown value reads as on. on_battery() from a scripted sysfs tree."""
        import fabos_voiced as D
        self.assertEqual(V.spotter_policy({"voice.spotter": "on"}, mode="balanced"), "on")
        self.assertEqual(V.spotter_policy({"voice.spotter": "battery-off"}, mode="gaming"), "battery-off")
        self.assertEqual(V.spotter_policy({"voice.spotter": "off"}, mode="performance"), "off")
        self.assertEqual(V.spotter_policy({}, mode=""), "on")
        self.assertEqual(V.spotter_policy({"voice.spotter": "sometimes"}, mode=""), "on")
        self.assertEqual(V.spotter_policy({"voice.spotter": "on"}, mode="server"), "off")
        self.assertEqual(V.spotter_policy({"voice.spotter": "battery-off"}, mode="server"), "off")
        self.assertEqual(V.spotter_policy({"voice.spotter": "on"}, mode="power-saver"), "battery-off")
        self.assertEqual(V.spotter_policy({"voice.spotter": "off"}, mode="power-saver"), "off")
        modefile = os.path.join(TMP, "performance-mode")
        with open(modefile, "w") as f:
            f.write("gaming\n")
        self.assertEqual(V.performance_mode(modefile), "gaming")
        with open(modefile, "w") as f:
            f.write("turbo\n")
        self.assertEqual(V.performance_mode(modefile), "")
        self.assertEqual(V.performance_mode(os.path.join(TMP, "no-such-file")), "")
        ps = os.path.join(TMP, "power_supply")
        for name, kv in (("AC", {"type": "Mains", "online": "0"}), ("BAT0", {"type": "Battery", "status": "Discharging"})):
            os.makedirs(os.path.join(ps, name), exist_ok=True)
            for k, v in kv.items():
                with open(os.path.join(ps, name, k), "w") as f:
                    f.write(v + "\n")
        self.assertTrue(V.on_battery(ps))
        with open(os.path.join(ps, "AC", "online"), "w") as f:
            f.write("1\n")
        self.assertFalse(V.on_battery(ps), "mains online: not on battery even while the battery says Discharging for a moment")
        with open(os.path.join(ps, "AC", "online"), "w") as f:
            f.write("0\n")
        with open(os.path.join(ps, "BAT0", "status"), "w") as f:
            f.write("Charging\n")
        self.assertFalse(V.on_battery(ps))
        self.assertFalse(V.on_battery(os.path.join(TMP, "no-power-supply")), "no power supply at all (desktop, VM): never on battery")
        # the daemon: an "off" policy is a hold; with the locker and the power source unreadable, "on" is never held
        d = D.Voiced()
        d.settings["voice.spotter"] = "off"
        saved = (V.performance_mode, V.screen_locked, V.on_battery)
        V.performance_mode = lambda path=None: ""
        V.screen_locked = lambda: None
        V.on_battery = lambda base=None: False
        try:
            self.assertEqual(d.spotter_hold(), "off")
            d.settings["voice.spotter"] = "on"
            self.assertIsNone(d.spotter_hold())
            V.screen_locked = lambda: True
            self.assertEqual(d.spotter_hold(), "locked")
            V.screen_locked = lambda: False
            d.settings["voice.spotter"] = "battery-off"
            V.on_battery = lambda base=None: True
            V.session_idle_s_saved = V.session_idle_s
            V.session_idle_s = lambda: D.IDLE_ON_BATTERY_S + 1
            self.assertEqual(d.spotter_hold(), "battery-idle")
            V.session_idle_s = lambda: 5
            self.assertIsNone(d.spotter_hold(), "the user is back: the microphone reopens")
            V.session_idle_s = V.session_idle_s_saved
        finally:
            V.performance_mode, V.screen_locked, V.on_battery = saved

    def test_sleep_wakes_on_interrupt_and_on_stop(self):
        import threading
        import fabos_voiced as D
        d = D.Voiced()
        threading.Timer(0.3, lambda: setattr(d, "interrupt", b"wake")).start()
        t0 = time.time()
        d._sleep(5, until=lambda: d.interrupt is not None)
        self.assertLess(time.time() - t0, 2.0)
        d = D.Voiced()
        threading.Timer(0.3, lambda: setattr(d, "stop", True)).start()
        t0 = time.time()
        d._sleep(5)
        self.assertLess(time.time() - t0, 2.0)


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

    def test_run_on_request_is_read_from_the_wake_clip(self):
        """'Hey Fab, open my downloads folder' said in one breath: pocketsphinx reports the phrase at utterance end, so the
        request is already in the ring buffer; whisper's transcript of that clip yields the request (fabos_voiced.on_wake)."""
        import fabos_voiced as D
        pcm = silence(0.5) + espeak_16k("hey fab, open my downloads folder") + silence(0.8)
        self.assertGreaterEqual(len(V.spot_in_pcm(pcm, "hey fab", V.DEFAULT_SETTINGS["voice.kws_threshold"])), 1, "phrase not spotted")
        clip = pcm[-D.RING_SECONDS * V.RATE * 2:]
        ok, text = V.hear_wake_clip(clip)
        self.assertTrue(ok, text)
        tail = V.request_after_wake(text)
        self.assertIn("folder", tail.lower(), text)
        self.assertFalse(tail.lower().startswith(("hey", "fab", "fam", "fev")), tail)
        ok2, text2 = V.hear_wake_clip(silence(0.4) + espeak_16k("hey fab") + silence(0.4))
        self.assertTrue(ok2, text2)
        self.assertEqual(V.request_after_wake(text2), "")          # nothing after the phrase: the daemon records normally


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
        # In ask mode the agent replaces the pre-narration of a waiting step with ONE permission question, which the
        # voice daemon asks exactly once (never twice), then speaks the agent's completion line.
        self.assertEqual(spoken.count("This needs your permission: write to fabos-note.txt. Shall I go ahead?"), 1, joined)
        self.assertEqual(sum(1 for l in spoken if l.startswith("This needs your permission") and "fabos-note" in l), 1, joined)
        self.assertIn("Saved fabos-note.txt.", joined)
        self.assertIn(P.APPROVED, joined)
        self.assertIn("Opening Fab Editor for you now.", joined)
        self.assertEqual(spoken.count("This needs your permission: run a command. Shall I go ahead?"), 1, joined)
        self.assertNotIn("pgrep", joined)           # raw commands are never read out unless ui.show_raw is on
        self.assertIn("That command finished.", joined)
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

    def waits_on_screen(self, request, answer, tag):
        """Run the post-wake path with `answer` as the spoken reply to the approval; assert the daemon says it will wait,
        the approval is still pending (nothing was approved), then decide on screen and let the daemon finish."""
        spoken_path = os.path.join(TMP, "spoken-%s.log" % tag)
        heard = spoken_path + ".listen"
        with open(heard, "w") as f:
            f.write(answer + "\n")
        self.api("PUT", "/settings", {"mode": "ask"})
        env = dict(self.env, FABOS_VOICE_FAKE_SPEAK=spoken_path, FABOS_VOICE_FAKE_LISTEN=heard)
        p = subprocess.Popen([sys.executable, VOICED, "--handle", request], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(spoken_path):
                with open(spoken_path) as fh:
                    if P.WAIT_ON_SCREEN in fh.read():
                        break
            time.sleep(0.3)
        else:
            p.kill()
            self.fail("daemon never said it would wait on screen for %r" % answer)
        with open(spoken_path) as fh:
            spoken = fh.read()
        self.assertNotIn(P.APPROVED, spoken)
        self.assertNotIn(P.DENIED, spoken)
        pending = self.api("GET", "/approvals/pending")
        self.assertEqual(len(pending), 1, pending)
        self.api("POST", "/approvals/%d" % pending[0]["id"], {"decision": "denied"})   # the user decides on screen
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        return pending[0], err

    def test_3_unclear_answer_waits_on_screen(self):
        self.waits_on_screen("run something privileged", "what is this", "unclear")

    def test_3b_free_form_sentence_with_a_yes_word_never_approves(self):
        """The review's case: thinking aloud during the 6 s approval window contains 'fine' — must not approve."""
        a, err = self.waits_on_screen("run something privileged", "hmm that is a fine question let me think about it", "sentence")
        self.assertIn("-> None", err)

    def test_3c_as_root_needs_a_clear_yes(self):
        """A weak yes ('fine') approves an ordinary step but never an as_root / CRITICAL one (strict mode)."""
        a, err = self.waits_on_screen("do this as root please", "fine", "strict")
        self.assertEqual(a["tool"], "run_shell")
        self.assertTrue(json.loads(a["input"]).get("as_root"), a)
        self.assertIn("(strict)", err)

    def test_4_ai_off_is_spoken(self):
        self.api("PUT", "/settings", {"ai.enabled": "false"})
        try:
            spoken, err = self.handle("anything", [], "auto")
            self.assertEqual(spoken, [P.AI_OFF])
        finally:
            self.api("PUT", "/settings", {"ai.enabled": "true"})

    def test_6_hey_fab_while_a_task_runs_can_stop_it(self):
        """While a task is followed the spotter keeps running (background thread); 'Hey Fab' + 'stop' cancels the task.
        Audio is faked: a silent 'microphone' and a 'pocketsphinx' that prints the wake line once the daemon has said
        it will wait on screen (deterministic order: approval asked -> unclear -> waiting -> wake -> 'stop that')."""
        import fabos_voiced as D
        spoken_path = os.path.join(TMP, "spoken-interrupt.log")
        heard = spoken_path + ".listen"
        with open(heard, "w") as f:
            f.write("\nstop that\n")          # 1st listen (approval): nothing heard; 2nd listen (after the wake): stop
        self.api("PUT", "/settings", {"mode": "ask", "voice.enabled": "true", "voice.mic_allowed": "true"})
        fake_mic = [sys.executable, "-c", "import sys,time\nwhile True:\n sys.stdout.buffer.write(b'\\0'*3200); sys.stdout.buffer.flush(); time.sleep(0.1)"]
        fake_spotter = [sys.executable, "-c",
                        "import sys,time,threading,json,os\n"
                        "def drain():\n"
                        " while sys.stdin.buffer.read(3200): pass\n"
                        "threading.Thread(target=drain, daemon=True).start()\n"
                        "while True:\n"
                        " time.sleep(0.2)\n"
                        " if os.path.exists(%r) and %r in open(%r).read(): break\n"
                        "time.sleep(1.5)\n"     # past SPEECH_GRACE_S after the daemon's own sentence, as a real detection would be
                        "print(json.dumps({'b':0,'d':1.2,'p':1,'t':'hey fab','w':[]}), flush=True)\n"
                        "time.sleep(600)\n" % (spoken_path, P.WAIT_ON_SCREEN, spoken_path)]
        names = ("FAKE_SPEAK", "FAKE_LISTEN", "capture_command", "capture_shared", "spotter_command", "spotter_ready", "verify_wake_clip", "play_chime")
        saved = {n: getattr(V, n) for n in names}
        V.FAKE_SPEAK, V.FAKE_LISTEN = spoken_path, heard
        V.capture_command = lambda: fake_mic
        V.capture_shared = lambda: True                 # "PipeWire present": the spotter may stay open while we record
        V.spotter_command = lambda wake, thr: fake_spotter
        V.spotter_ready = lambda: True
        V.verify_wake_clip = lambda pcm: None
        V.play_chime = lambda: True
        d = D.Voiced()
        d.agent = V.Agent(run_dir=os.path.join(self.rundir, "fabos-agent"))
        d.refresh_settings(force=True)
        d.spotting_allowed = True
        try:
            t0 = time.time()
            d.handle_text("run something privileged")
            elapsed = time.time() - t0
        finally:
            d.stop_spotter()
            for n in names:
                setattr(V, n, saved[n])
        with open(spoken_path) as fh:
            spoken = fh.read().splitlines()
        joined = "\n".join(spoken)
        self.assertIn(P.WAIT_ON_SCREEN, joined)
        self.assertEqual(spoken[-1], P.CANCELLED, joined)
        self.assertLess(elapsed, 40, "interrupt took %.1f s" % elapsed)
        t = self.api("GET", "/tasks/%d" % self.api("GET", "/tasks")[0]["id"])
        self.assertEqual(t["status"], "cancelled", t)
        self.assertEqual(self.api("GET", "/approvals/pending"), [])
        self.assertIsNone(d.spot)
        self.assertIsNone(d.rec)

    def test_7_spotter_backoff_grows_then_resets(self):
        import fabos_voiced as D
        d = D.Voiced()

        class Dead:
            returncode = 1

        d.spot = d.rec = Dead()
        d.spot_err = None
        d.stop_spotter = lambda: None
        d.spot_started_at = time.time()
        self.assertEqual([d.spotter_died() for _ in range(6)], [3, 6, 12, 24, 48, 60])
        self.assertEqual(len(d._warned), 1)                      # repeats are logged once, not per death
        d.spot_started_at = time.time() - D.SPOTTER_HEALTHY_S - 1  # a spotter that ran for a while resets the backoff
        self.assertEqual(d.spotter_died(), 3)
        # every recorder failure moves the spotter one step down the recorder ladder (explicit target, parec, arecord)
        self.assertEqual(d.capture_attempt, 7)
        saved = V.which, V.default_source
        V.which = lambda *names: "/usr/bin/" + names[0] if names[0] in ("pw-record", "parec") else None
        V.default_source = lambda: {"name": "alsa_input.test"}
        try:
            d.capture_attempt = 0
            self.assertEqual(d.capture_cmd()[0], "pw-record")
            self.assertNotIn("--target", d.capture_cmd())
            d.capture_attempt = 1
            self.assertIn("--target", d.capture_cmd())
            self.assertIn("alsa_input.test", d.capture_cmd())
            d.capture_attempt = 2
            self.assertEqual(d.capture_cmd()[0], "parec")
            d.capture_attempt = 3                                 # wraps around to the plain default
            self.assertEqual(d.capture_cmd()[0], "pw-record")
        finally:
            V.which, V.default_source = saved
        # a recorder that ended cleanly (rc 0 / killed by us) does not move the ladder
        class Clean:
            returncode = 0
        d.rec = Clean()
        d.capture_attempt = 0
        d.spotter_died()
        self.assertEqual(d.capture_attempt, 0)

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
