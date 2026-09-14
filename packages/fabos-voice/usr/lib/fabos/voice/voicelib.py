#!/usr/bin/env python3
"""voicelib — shared plumbing for Fab OS voice (fabos-voice CLI and the fabos-voiced wake-word daemon).

  * audio capture (pw-record -> parec -> arecord), 16 kHz mono s16, 100 ms chunks
  * RMS voice-activity detection: noise floor from the first 300 ms, speech = RMS > 3x floor,
    stop after 1.2 s below, hard cap = timeout
  * WAV writer/reader (stdlib wave)
  * speech-to-text: agent POST /speech/transcribe (cloud, when allowed) -> whisper.cpp with the shipped
    tiny.en model (skipped when MemAvailable < 600 MB) -> NoBackend
  * text-to-speech: agent POST /speech/say (cloud, Indian-English voice) -> espeak-ng en-gb-x-rp -> NoBackend
  * wake-word spotting through `pocketsphinx -keyphrase ... live -` (raw s16 on stdin, JSON lines out)
  * tiny HTTP client for fabos-agentd (token in $XDG_RUNTIME_DIR/fabos-agent/token)

No third-party Python modules are required; numpy is used for the RMS maths and the chime when present.
Test hooks (only when the variable is set): FABOS_VOICE_FAKE_SPEAK=<file> appends spoken text to <file>
instead of playing audio; FABOS_VOICE_FAKE_LISTEN=<file> pops one line of <file> as the recognised text.
"""
import array
import base64
import contextlib
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

try:
    import numpy as np
except Exception:  # numpy is a Depends, but stay honest and usable without it
    np = None

import phrases

# --------------------------------------------------------------------------- constants
RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_MS = 100
CHUNK_BYTES = RATE * SAMPLE_WIDTH * CHUNK_MS // 1000          # 3200 bytes = 100 ms
CALIBRATE_CHUNKS = 3                                          # 300 ms of noise floor
SPEECH_RATIO = 3.0
MIN_FLOOR = 40.0                                              # digital silence has RMS 0; never collapse the threshold
TRAILING_SILENCE_S = 1.2
PREROLL_CHUNKS = 3                                            # keep 300 ms before the onset
DEFAULT_TIMEOUT_S = 10.0
MEM_NEEDED_KB = 600 * 1024
CHIME_MS = 180
# whisper-cli tiny.en peaks at ~175 MB RSS (measured in the Ubuntu 26.04 container), more than the 200M MemoryHigh of the
# fabos-voiced unit. Under systemd the daemon therefore runs each transcription in its own transient scope with this
# soft cap (systemd-run --user --scope); without systemd-run it runs in place, merely under reclaim pressure.
WHISPER_SCOPE_MEMORY_HIGH = os.environ.get("FABOS_VOICE_WHISPER_MEMORY_HIGH", "400M")

EXIT_OK, EXIT_NOTHING_HEARD, EXIT_NO_BACKEND = 0, 3, 4

MODEL_PATH = os.environ.get("FABOS_VOICE_MODEL", "/usr/share/fabos/voice/ggml-tiny.en.bin")
RUN_BASE = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
RUN_DIR = os.path.join(RUN_BASE, "fabos-voice")
AGENT_RUN = os.path.join(RUN_BASE, "fabos-agent")
STATE_DIR = os.environ.get("FABOS_VOICE_STATE_DIR") or os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state"), "fabos-voice")
STATE_FILE = os.path.join(RUN_DIR, "state.json")

DEFAULT_SETTINGS = {
    "voice.enabled": "true",
    "voice.wake_word": "hey fab",
    "voice.speak_replies": "true",
    "voice.offline_only": "false",
    "voice.speak_full": "false",
    "voice.kws_threshold": "1e-50",   # pocketsphinx p(hyp)/p(alt); 1e-50 = detection, 0 false hits on 22 s of hard negatives
    "voice.verify_wake": "true",      # second look at the wake clip with whisper.cpp before acting (near-misses like "hey bob ... fabulous")
}
CLOUD_PROVIDERS = ("openai", "gemini")   # the agent's /speech endpoints use one of these

FAKE_SPEAK = os.environ.get("FABOS_VOICE_FAKE_SPEAK")
FAKE_LISTEN = os.environ.get("FABOS_VOICE_FAKE_LISTEN")


class NothingHeard(Exception):
    """No speech in the recording (exit 3)."""


class NoBackend(Exception):
    """No microphone / no speech-to-text / no text-to-speech available (exit 4)."""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def ensure_run_dir():
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    return RUN_DIR


# --------------------------------------------------------------------------- audio maths
def rms(chunk):
    """Root-mean-square of little-endian s16 PCM bytes (0 .. 32767)."""
    n = len(chunk) // SAMPLE_WIDTH
    if n == 0:
        return 0.0
    if np is not None:
        a = np.frombuffer(chunk[: n * SAMPLE_WIDTH], dtype="<i2").astype(np.float64)
        return float(math.sqrt(float(np.mean(a * a))))
    a = array.array("h")
    a.frombytes(chunk[: n * SAMPLE_WIDTH])
    if sys.byteorder != "little":
        a.byteswap()
    return math.sqrt(sum(x * x for x in a) / n)


class Segmenter:
    """Chunk-by-chunk voice-activity detector. feed() returns the finished utterance (bytes) once trailing
    silence is reached, else None. finish() returns what was captured when the caller hits its timeout."""

    def __init__(self, ratio=SPEECH_RATIO, trailing_s=TRAILING_SILENCE_S, calibrate_chunks=CALIBRATE_CHUNKS,
                 preroll_chunks=PREROLL_CHUNKS, min_floor=MIN_FLOOR, chunk_ms=CHUNK_MS):
        self.ratio, self.trailing_s, self.calibrate_chunks = ratio, trailing_s, calibrate_chunks
        self.preroll_chunks, self.min_floor, self.chunk_ms = preroll_chunks, min_floor, chunk_ms
        self.levels = []
        self.floor = None
        self.threshold = None
        self.speech_started = False
        self.pre = []
        self.buf = bytearray()
        self.silent_ms = 0
        self.speech_ms = 0
        self.done = False

    def feed(self, chunk):
        if self.done:
            return None
        level = rms(chunk)
        if self.floor is None:
            self.levels.append(level)
            self.pre.append(chunk)
            self.pre = self.pre[-self.preroll_chunks:]
            if len(self.levels) >= self.calibrate_chunks:
                self.floor = max(sum(self.levels) / len(self.levels), self.min_floor)
                self.threshold = self.floor * self.ratio
            return None
        loud = level > self.threshold
        if not self.speech_started:
            if loud:
                self.speech_started = True
                for p in self.pre:
                    self.buf += p
                self.buf += chunk
                self.speech_ms = self.chunk_ms
            else:
                self.pre.append(chunk)
                self.pre = self.pre[-self.preroll_chunks:]
            return None
        self.buf += chunk
        if loud:
            self.silent_ms = 0
            self.speech_ms += self.chunk_ms
        else:
            self.silent_ms += self.chunk_ms
            if self.silent_ms >= self.trailing_s * 1000:
                self.done = True
                return bytes(self.buf)
        return None

    def finish(self):
        return bytes(self.buf) if self.speech_started else None


# --------------------------------------------------------------------------- WAV
def write_wav(path, pcm, rate=RATE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(rate)
        w.writeframes(pcm)
    return path


def read_wav(path):
    with wave.open(path, "rb") as w:
        return (w.getnchannels(), w.getsampwidth(), w.getframerate()), w.readframes(w.getnframes())


def chime_pcm(ms=CHIME_MS, rate=RATE, f1=880.0, f2=1320.0, amp=0.35):
    """Two-tone rising chime, 180 ms, 5 ms fades: 'I heard you, go on'."""
    n = rate * ms // 1000
    half = n // 2
    fade = max(1, rate * 5 // 1000)
    if np is not None:
        t = np.arange(n) / rate
        freq = np.where(np.arange(n) < half, f1, f2)
        env = np.ones(n)
        env[:fade] = np.linspace(0, 1, fade)
        env[half - fade:half] *= np.linspace(1, 0.4, fade)
        env[half:half + fade] *= np.linspace(0.4, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        sig = np.sin(2 * np.pi * freq * t) * env * amp * 32767
        return sig.astype("<i2").tobytes()
    out = array.array("h")
    for i in range(n):
        f = f1 if i < half else f2
        e = min(1.0, i / fade, (n - 1 - i) / fade)
        out.append(int(math.sin(2 * math.pi * f * i / rate) * e * amp * 32767))
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()


def chime_path():
    p = os.path.join(ensure_run_dir(), "chime.wav")
    if not os.path.exists(p):
        write_wav(p, chime_pcm())
    return p


# --------------------------------------------------------------------------- devices, players, recorders
def which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def mic_present():
    """True when a capture device exists. Cheap: ALSA capture nodes, then PipeWire/Pulse sources (no monitors)."""
    if FAKE_LISTEN:
        return True
    ov = os.environ.get("FABOS_VOICE_MIC")
    if ov is not None:
        return truthy(ov)
    if glob.glob("/dev/snd/pcmC*D*c"):
        return True
    pactl = which("pactl")
    if pactl:
        try:
            out = subprocess.run([pactl, "list", "short", "sources"], capture_output=True, text=True, timeout=3).stdout
            return any(line.strip() and ".monitor" not in line for line in out.splitlines())
        except Exception:
            pass
    return False


def capture_command():
    """Raw s16le 16 kHz mono on stdout, or None when no recorder is installed."""
    if which("pw-record"):
        return ["pw-record", "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "s16", "-"]
    if which("parec"):
        return ["parec", "--rate=%d" % RATE, "--channels=%d" % CHANNELS, "--format=s16le", "--raw"]
    if which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw"]
    return None


def capture_shared():
    """True when the recorder shares the microphone with other streams (PipeWire, PulseAudio); plain ALSA capture is
    exclusive, so the wake spotter cannot stay open while an approval answer is recorded."""
    cmd = capture_command()
    return bool(cmd) and cmd[0] in ("pw-record", "parec")


def play_file(path, block=True):
    """Play a WAV/MP3 file with the first available player. Returns True when a player ran successfully."""
    ext = os.path.splitext(path)[1].lower()
    candidates = [["pw-play", path], ["paplay", path]]
    if ext == ".wav":
        candidates.append(["aplay", "-q", path])
    candidates += [["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path], ["mpv", "--really-quiet", "--no-video", path]]
    for cmd in candidates:
        if not which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120 if block else 5)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def play_chime():
    try:
        return play_file(chime_path())
    except Exception:
        return False


# --------------------------------------------------------------------------- pid locks (speaking / listening)
def _lock_path(name):
    return os.path.join(RUN_DIR, name + ".lock")


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


@contextlib.contextmanager
def lock(name):
    """A pid file other processes can look at: 'speaking' pauses wake detection, 'listening' feeds status."""
    ensure_run_dir()
    p = _lock_path(name)
    try:
        with open(p, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(p)


def lock_active(name):
    p = _lock_path(name)
    try:
        with open(p) as f:
            return _pid_alive(f.read().strip())
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- recording
def _read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        part = stream.read(n - len(buf))
        if not part:
            break
        buf += part
    return buf


def record_utterance(timeout=DEFAULT_TIMEOUT_S, on_state=None, start_timeout=None):
    """Record from the default microphone until 1.2 s of silence after speech, or `timeout` seconds.
    `start_timeout` (daemon only; the CLI keeps to the contract) gives up early when speech has not begun by then.
    Returns raw s16 PCM. Raises NoBackend (no recorder / device) or NothingHeard."""
    cmd = capture_command()
    if cmd is None:
        raise NoBackend(phrases.NO_MIC)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise NoBackend("%s: %s" % (phrases.NO_MIC, e))
    seg = Segmenter()
    start = time.time()
    deadline = start + max(1.0, float(timeout))
    start_by = start + float(start_timeout) if start_timeout else None
    got_any = False
    try:
        with lock("listening"):
            if on_state:
                on_state(listening=True)
            while time.time() < deadline:
                chunk = _read_exact(proc.stdout, CHUNK_BYTES)
                if len(chunk) < CHUNK_BYTES:
                    break            # recorder ended (device vanished / no device)
                got_any = True
                done = seg.feed(chunk)
                if done is not None:
                    return done
                if start_by and not seg.speech_started and time.time() > start_by:
                    break            # nobody spoke: give the microphone back
    finally:
        if on_state:
            on_state(listening=False)
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(2)
        err = b""
        with contextlib.suppress(Exception):
            err = proc.stderr.read()[-300:]
        for f in (proc.stdout, proc.stderr):
            with contextlib.suppress(Exception):
                f.close()
    if not got_any:
        raise NoBackend("%s (%s: %s)" % (phrases.NO_MIC, cmd[0], err.decode(errors="replace").strip() or "no audio"))
    pcm = seg.finish()
    if pcm is None:
        raise NothingHeard()
    return pcm


# --------------------------------------------------------------------------- agent client
class Agent:
    """Minimal client for fabos-agentd (HTTP on 127.0.0.1, bearer token from the runtime dir)."""

    def __init__(self, run_dir=AGENT_RUN, timeout=8.0):
        self.run_dir, self.timeout = run_dir, timeout
        self._settings_cache = (0.0, None)

    def _creds(self):
        try:
            with open(os.path.join(self.run_dir, "token")) as f:
                token = f.read().strip()
            port = "8790"
            with contextlib.suppress(OSError):
                with open(os.path.join(self.run_dir, "port")) as f:
                    port = f.read().strip() or port
            return token, port
        except OSError:
            return None, None

    def call(self, method, path, body=None, timeout=None):
        token, port = self._creds()
        if not token:
            raise NoBackend(phrases.AGENT_DOWN)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), method=method, data=data,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", str(e))
            except Exception:
                err = str(e)
            return {"error": err, "http": e.code}
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise NoBackend("%s (%s)" % (phrases.AGENT_DOWN, getattr(e, "reason", e)))

    def get(self, path, timeout=None):
        return self.call("GET", path, timeout=timeout)

    def post(self, path, body, timeout=None):
        return self.call("POST", path, body, timeout=timeout)

    def put(self, path, body):
        return self.call("PUT", path, body)

    def available(self):
        try:
            return bool(self.get("/status", timeout=2.0).get("mode"))
        except (NoBackend, AttributeError):
            return False

    def status(self):
        try:
            s = self.get("/status", timeout=2.0)
            return s if isinstance(s, dict) and "error" not in s else None
        except NoBackend:
            return None

    def settings(self, max_age=0.0):
        """Agent settings merged over the voice defaults. Never raises: returns defaults when the agent is down."""
        ts, cached = self._settings_cache
        if cached is not None and max_age and time.time() - ts < max_age:
            return cached
        merged = dict(DEFAULT_SETTINGS)
        merged["mode"] = "auto"
        merged["ai.enabled"] = "true"
        merged["_agent_up"] = False
        try:
            s = self.get("/settings", timeout=2.0)
            if isinstance(s, dict) and "error" not in s:
                merged.update({k: v for k, v in s.items() if k != "secrets"})
                merged["_agent_up"] = True
        except NoBackend:
            pass
        self._settings_cache = (time.time(), merged)
        return merged


# --------------------------------------------------------------------------- backend discovery
def mem_available_kb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def whisper_ready():
    return bool(which("whisper-cli")) and os.path.isfile(MODEL_PATH)


def cloud_ready(agent, settings=None):
    """The agent's /speech endpoints work when a cloud provider key is present and the user allows cloud use."""
    settings = settings or agent.settings(max_age=5)
    if truthy(settings.get("voice.offline_only", "false")):
        return False
    st = agent.status()
    if not st:
        return False
    provs = st.get("providers") or {}
    return any((provs.get(p) or {}).get("has_key") for p in CLOUD_PROVIDERS)


def stt_backends(agent, settings=None):
    out = []
    if FAKE_LISTEN:
        out.append("fake")
    if cloud_ready(agent, settings):
        out.append("cloud")
    if whisper_ready():
        out.append("whisper.cpp")
    return out


def tts_backends(agent, settings=None):
    out = []
    if FAKE_SPEAK:
        out.append("fake")
    if cloud_ready(agent, settings):
        out.append("cloud")
    if which("espeak-ng"):
        out.append("espeak-ng")
    return out


# --------------------------------------------------------------------------- speech to text
_JUNK = ("[BLANK_AUDIO]", "[ Silence ]", "[silence]", "(silence)", "[Music]", "[MUSIC]", "[inaudible]", "(inaudible)", "[ Inaudible ]", "[SOUND]", "[BLANK]")


# What whisper tiny.en writes for silence, breath or a click (seen in the Ubuntu 26.04 build container: 1.5 s of digital
# silence -> " you"). A transcript that is only one of these is treated as nothing heard.
_HALLUCINATIONS = {"you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye", "the end", "okay", "oh", "hmm", "um", "uh"}


def _clean_transcript(text):
    t = text or ""
    for j in _JUNK:
        t = t.replace(j, " ")
    t = " ".join(t.split()).strip()
    if re.sub(r"[^a-z ]", "", t.lower()).strip() in _HALLUCINATIONS:
        return ""
    return t


def transcribe_cloud(agent, wav_path):
    with open(wav_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    r = agent.post("/speech/transcribe", {"audio_b64": b64, "format": "wav"}, timeout=40)
    if isinstance(r, dict) and r.get("ok") and isinstance(r.get("text"), str):
        return _clean_transcript(r["text"])
    raise NoBackend("cloud transcribe unavailable: %s" % (r.get("error") if isinstance(r, dict) else r))


_scope_ok = None


def scope_prefix():
    """['systemd-run', '--user', '--scope', ...] when this process is a systemd service (INVOCATION_ID) and the user
    manager accepts transient scopes; [] otherwise (CLI use, containers, tests). Probed once per process."""
    global _scope_ok
    if truthy(os.environ.get("FABOS_VOICE_NO_SCOPE", "0")) or "INVOCATION_ID" not in os.environ or not which("systemd-run"):
        return []
    if _scope_ok is None:
        try:
            r = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "--collect", "-p", "MemoryHigh=" + WHISPER_SCOPE_MEMORY_HIGH, "--", "true"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
            _scope_ok = r.returncode == 0
            if not _scope_ok:
                log("systemd-run scopes unavailable (%s); whisper.cpp runs inside the service cgroup" % (r.stderr or b"").decode(errors="replace").strip()[-160:])
        except Exception as e:
            _scope_ok = False
            log("systemd-run probe failed (%s); whisper.cpp runs inside the service cgroup" % e)
    if not _scope_ok:
        return []
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--description=Fab OS voice: speech-to-text",
            "-p", "MemoryHigh=" + WHISPER_SCOPE_MEMORY_HIGH, "--"]


def transcribe_whisper(wav_path):
    if not whisper_ready():
        raise NoBackend(phrases.NO_STT)
    mem = mem_available_kb()
    if mem is not None and mem < MEM_NEEDED_KB:
        raise NoBackend(phrases.LOW_MEMORY)
    threads = str(max(1, min(4, os.cpu_count() or 2)))
    cmd = scope_prefix() + ["whisper-cli", "-m", MODEL_PATH, "-l", "en", "-nt", "-np", "-t", threads, "-f", wav_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise NoBackend("whisper.cpp timed out")
    if r.returncode != 0:
        raise NoBackend("whisper.cpp failed (%d): %s" % (r.returncode, (r.stderr or "")[-200:].strip()))
    return _clean_transcript(r.stdout)


def transcribe(pcm, agent=None, settings=None):
    """Returns (text, backend). Order: cloud (when allowed and configured) -> whisper.cpp -> NoBackend.
    Raises NothingHeard when the backend returned no words. The WAV lives only for the call."""
    agent = agent or Agent()
    settings = settings or agent.settings(max_age=5)
    wav = os.path.join(ensure_run_dir(), "utterance-%d.wav" % os.getpid())
    write_wav(wav, pcm)
    errors = []
    try:
        if cloud_ready(agent, settings):
            try:
                text = transcribe_cloud(agent, wav)
                if not text:
                    raise NothingHeard()
                return text, "cloud"
            except NoBackend as e:
                errors.append(str(e))
                log("cloud transcribe failed, trying whisper.cpp:", e)
        if which("whisper-cli") and os.path.isfile(MODEL_PATH):
            text = transcribe_whisper(wav)      # raises NoBackend on low memory, and says so
            if not text:
                raise NothingHeard()
            return text, "whisper.cpp"
        errors.append(phrases.NO_STT)
        raise NoBackend("; ".join(errors))
    finally:
        with contextlib.suppress(OSError):
            os.remove(wav)


def listen_once(timeout=DEFAULT_TIMEOUT_S, agent=None, settings=None, chime=True, on_state=None, start_timeout=None):
    """The VOICE CONTRACT 'listen-once': chime, record until silence, transcribe. Returns (text, backend)."""
    agent = agent or Agent()
    settings = settings or agent.settings(max_age=5)
    if FAKE_LISTEN:
        return _fake_listen(), "fake"
    if not stt_backends(agent, settings):
        raise NoBackend(phrases.NO_STT)
    if chime:
        play_chime()
    pcm = record_utterance(timeout, on_state=on_state, start_timeout=start_timeout)
    return transcribe(pcm, agent, settings)


def _fake_listen():
    try:
        with open(FAKE_LISTEN) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    if not lines:
        raise NothingHeard()
    with open(FAKE_LISTEN, "w") as f:
        f.write("\n".join(lines[1:]))
    text = lines[0].strip()
    if not text:
        raise NothingHeard()
    return text


# --------------------------------------------------------------------------- text to speech
def say_cloud(agent, text):
    r = agent.post("/speech/say", {"text": text}, timeout=40)
    if not (isinstance(r, dict) and r.get("ok") and r.get("audio_b64")):
        raise NoBackend("cloud say unavailable: %s" % (r.get("error") if isinstance(r, dict) else r))
    fmt = (r.get("format") or "mp3").lower()
    path = os.path.join(ensure_run_dir(), "say-%d.%s" % (os.getpid(), "wav" if fmt == "wav" else "mp3"))
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["audio_b64"]))
    try:
        if not play_file(path):
            raise NoBackend("no audio player could play the %s reply" % fmt)
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    return "cloud"


def say_espeak(text):
    if not which("espeak-ng"):
        raise NoBackend(phrases.NO_TTS)
    path = os.path.join(ensure_run_dir(), "say-%d.wav" % os.getpid())
    try:
        r = subprocess.run(["espeak-ng", "-v", "en-gb-x-rp", "-s", "165", "-p", "40", "-w", path, "--", text],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(path):
            raise NoBackend("espeak-ng failed: %s" % (r.stderr or "").strip()[-200:])
        if not play_file(path):
            # no player at all: let espeak-ng drive the audio device itself (blocking)
            r = subprocess.run(["espeak-ng", "-v", "en-gb-x-rp", "-s", "165", "-p", "40", "--", text],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if r.returncode != 0:
                raise NoBackend(phrases.NO_TTS)
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    return "espeak-ng"


def speak(text, agent=None, settings=None):
    """Speak `text` (blocking). Order: cloud (Indian-English voice) -> espeak-ng -> NoBackend. Returns the backend."""
    text = " ".join((text or "").split())
    if not text:
        return "none"
    if FAKE_SPEAK:
        with open(FAKE_SPEAK, "a") as f:
            f.write(text + "\n")
        return "fake"
    agent = agent or Agent()
    settings = settings or agent.settings(max_age=5)
    with lock("speaking"):
        if cloud_ready(agent, settings):
            try:
                return say_cloud(agent, text)
            except NoBackend as e:
                log("cloud say failed, falling back to espeak-ng:", e)
        return say_espeak(text)


# --------------------------------------------------------------------------- wake-word spotting (pocketsphinx)
def spotter_command(wake_word="hey fab", threshold="1e-50"):
    """`pocketsphinx -keyphrase WAKE -kws_threshold T live -`: raw s16 16 kHz on stdin, one JSON line per utterance
    ({"t": "hey fab", ...} when the phrase is spotted), flushed while the stream is still open (measured: the line
    arrived 0.7 s before the feed ended). pocketsphinx 5.0.4 / Ubuntu 26.04; 60 s of audio cost 0.9 s of CPU.
    1e-50 is the only threshold that spotted an espeak-ng "hey fab" (a synthetic voice; the acoustic model is trained
    on people), and at that sensitivity it also fires on look-alikes such as "a fabulous day" - hence verify_wake_clip."""
    return ["pocketsphinx", "-keyphrase", wake_word, "-kws_threshold", str(threshold), "-loglevel", "ERROR", "live", "-"]


def spotter_ready():
    return bool(which("pocketsphinx")) and os.path.isfile("/usr/share/pocketsphinx/model/en-us/cmudict-en-us.dict")


def parse_spot_line(line, wake_word="hey fab"):
    """True when a pocketsphinx JSON line carries the wake phrase."""
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        return False
    text = " ".join(str(d.get("t") or "").lower().split())
    return bool(text) and wake_word.lower() in text


def spot_in_pcm(pcm, wake_word="hey fab", threshold="1e-50", realtime=False):
    """Test/diagnostic helper: run the spotter over PCM bytes and return the detections (list of JSON dicts)."""
    if not spotter_ready():
        raise NoBackend("pocketsphinx is not installed")
    p = subprocess.Popen(spotter_command(wake_word, threshold), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        if realtime:
            for i in range(0, len(pcm), CHUNK_BYTES):
                p.stdin.write(pcm[i:i + CHUNK_BYTES])
                p.stdin.flush()
                time.sleep(CHUNK_MS / 1000.0)
        else:
            p.stdin.write(pcm)
        p.stdin.close()
        out, _ = p.communicate(timeout=120)
    finally:
        with contextlib.suppress(Exception):
            p.kill()
    hits = []
    for line in out.decode(errors="replace").splitlines():
        if parse_spot_line(line, wake_word):
            hits.append(json.loads(line))
    return hits


# --------------------------------------------------------------------------- second-stage wake verification
# A two-word keyphrase spotter is deliberately eager; before a false wake turns overheard talk into a task, the daemon
# lets whisper.cpp hear the last few seconds (fabos_voiced.RING_SECONDS, 6 s) again. Small models write "Fab" as fab/fam/fap/fav;
# a very short greeting passes too.
WAKE_LIKE = re.compile(r"\bf[aeiou]{1,2}[bmpv]s?\b", re.I)
WAKE_SHORT = re.compile(r"\b(hey|hi|okay|ok|oh|yo|hello)\b", re.I)


def wake_transcript_ok(text):
    """True when a transcript of the wake clip plausibly contains 'Hey Fab'."""
    t = " ".join((text or "").split())
    if not t:
        return False
    if WAKE_LIKE.search(t):
        return True
    words = re.findall(r"[a-z']+", t.lower())
    return len(words) <= 3 and bool(WAKE_SHORT.search(t))


def hear_wake_clip(pcm):
    """(verdict, transcript) for the last seconds before a detection. verdict is True/False when whisper.cpp could check
    the clip and None when it cannot (no model, low memory) — callers then trust the spotter alone. The clip is written
    for the call only and deleted straight after."""
    if not pcm or not whisper_ready():
        return None, ""
    mem = mem_available_kb()
    if mem is not None and mem < MEM_NEEDED_KB:
        return None, ""
    wav = os.path.join(ensure_run_dir(), "wake-%d.wav" % os.getpid())
    write_wav(wav, pcm)
    try:
        text = transcribe_whisper(wav)
    except NoBackend as e:
        log("wake verification skipped:", e)
        return None, ""
    finally:
        with contextlib.suppress(OSError):
            os.remove(wav)
    ok = wake_transcript_ok(text)
    log("wake clip heard as %r -> %s" % (text, "accepted" if ok else "rejected"))
    return ok, text


def verify_wake_clip(pcm):
    """True/False/None as hear_wake_clip, verdict only."""
    return hear_wake_clip(pcm)[0]


def request_after_wake(text):
    """pocketsphinx reports the phrase at the end of the utterance, so someone who runs on — "Hey Fab open my downloads"
    — has already said the request when the chime plays. Returns the words after the wake phrase in the clip transcript
    (at least two, so a stray syllable is not a request), or ""."""
    t = " ".join((text or "").split())
    m = WAKE_LIKE.search(t)
    if not m:
        return ""
    tail = t[m.end():].lstrip(" ,.!?;:-—–")
    if len(re.findall(r"[A-Za-z0-9']+", tail)) < 2:
        return ""
    return tail[0].upper() + tail[1:]


# --------------------------------------------------------------------------- daemon state + status
def write_state(**kw):
    ensure_run_dir()
    st = read_state()
    st.update(kw)
    st["pid"] = os.getpid()
    st["updated"] = time.time()
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass
    return st


def read_state():
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def daemon_state():
    """State of fabos-voiced if it is alive, else {}."""
    st = read_state()
    return st if st.get("pid") and _pid_alive(st["pid"]) else {}


def status(agent=None):
    """The VOICE CONTRACT status line: {"wake": bool, "listening": bool, "stt": ..., "tts": ..., "mic": bool}."""
    agent = agent or Agent()
    settings = agent.settings(max_age=5)
    st = daemon_state()
    stt = stt_backends(agent, settings)
    tts = tts_backends(agent, settings)
    stt_name = "none"
    for b in stt:
        if b in ("cloud", "whisper.cpp"):
            stt_name = b
            break
    tts_name = "none"
    for b in tts:
        if b in ("cloud", "espeak-ng"):
            tts_name = b
            break
    mic = mic_present()
    return {
        "wake": bool(st.get("wake")) and truthy(settings.get("voice.enabled", "true")),
        "listening": bool(st.get("listening")) or lock_active("listening"),
        "stt": stt_name,
        "tts": tts_name,
        "mic": mic,
        # extra, non-contract detail (UIs may ignore): why something is off, and the daemon's own view
        "enabled": truthy(settings.get("voice.enabled", "true")),
        "agent": bool(settings.get("_agent_up")),
        "speaking": bool(st.get("speaking")) or lock_active("speaking"),
        "wake_word": settings.get("voice.wake_word", "hey fab"),
        "offline_only": truthy(settings.get("voice.offline_only", "false")),
        "model": MODEL_PATH if os.path.isfile(MODEL_PATH) else None,
        "spotter": spotter_ready(),
    }
