#!/usr/bin/env python3
"""voicelib — shared plumbing for Fab OS voice (fabos-voice CLI and the fabos-voiced wake-word daemon).

  * the audio session (PipeWire / PulseAudio through pactl, pw-cli, pw-dump), the default source and sink, mute state
  * audio capture, 16 kHz mono s16, 100 ms chunks, through a reader thread so a recorder that starts but never delivers
    cannot stall anyone: pw-record (automatic target) -> pw-record --target <default source> -> parec --device -> arecord
  * RMS voice-activity detection: noise floor from the first 300 ms, speech = RMS > 3x floor, stop after 1.2 s below,
    hard cap = timeout; a stream that is exactly zero for its first second is a muted or absent microphone
  * WAV writer/reader (stdlib wave)
  * speech-to-text: agent POST /speech/transcribe (cloud, when allowed) -> whisper.cpp with the shipped
    tiny.en model (skipped when MemAvailable < 600 MB) -> NoBackend
  * text-to-speech: agent POST /speech/say (cloud, Indian-English voice) -> espeak-ng (en-gb-x-rp, slower and louder)
    rendered to WAV and played with pw-play -> paplay -> aplay at the default sink; one utterance at a time across every
    Fab OS process (an flock in $XDG_RUNTIME_DIR/fabos-voice) and never the same sentence twice from two players
  * wake-word spotting through `pocketsphinx -keyphrase ... live -` (raw s16 on stdin, JSON lines out)
  * doctor(): every stage of the pipeline checked for real, with a fix hint per failure
  * tiny HTTP client for fabos-agentd (token in $XDG_RUNTIME_DIR/fabos-agent/token)

No third-party Python modules are required; numpy is used for the RMS maths and the chime when present.
Test hooks (only when the variable is set): FABOS_VOICE_FAKE_SPEAK=<file> appends spoken text to <file>
instead of playing audio; FABOS_VOICE_FAKE_LISTEN=<file> pops one line of <file> as the recognised text;
FABOS_VOICE_SESSION=0|1 pretends there is no / an audio session; FABOS_VOICE_MIC=0|1 forces the microphone check.
"""
import array
import base64
import contextlib
import fcntl
import glob
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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
DEAD_STREAM_MS = 1000                                         # exactly-zero samples for this long = muted / absent microphone
RECORD_GRACE_S = 3.0                                          # listen-once never runs longer than timeout + this
FIRST_CHUNK_S = 1.5                                           # a recorder that delivers nothing within this is skipped
MEM_NEEDED_KB = 600 * 1024
CHIME_MS = 180
PLAYBACK_WAIT_S = 20.0                                        # wait this long for another utterance to finish, then speak anyway
PROBE_TIMEOUT_S = 3.0                                         # pactl / pw-cli / pw-dump answer within this or count as absent
# whisper-cli tiny.en peaks at ~175 MB RSS (measured in the Ubuntu 26.04 container), more than the 200M MemoryHigh of the
# fabos-voiced unit. Under systemd the daemon therefore runs each transcription in its own transient scope with this
# soft cap (systemd-run --user --scope); without systemd-run it runs in place, merely under reclaim pressure.
WHISPER_SCOPE_MEMORY_HIGH = os.environ.get("FABOS_VOICE_WHISPER_MEMORY_HIGH", "400M")
WHISPER_TIMEOUT_S = 60

EXIT_OK, EXIT_NOTHING_HEARD, EXIT_NO_BACKEND = 0, 3, 4

MODEL_PATH = os.environ.get("FABOS_VOICE_MODEL", "/usr/share/fabos/voice/ggml-tiny.en.bin")
DICT_PATH = "/usr/share/pocketsphinx/model/en-us/cmudict-en-us.dict"
RUN_BASE = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
RUN_DIR = os.path.join(RUN_BASE, "fabos-voice")
AGENT_RUN = os.path.join(RUN_BASE, "fabos-agent")
STATE_DIR = os.environ.get("FABOS_VOICE_STATE_DIR") or os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state"), "fabos-voice")
STATE_FILE = os.path.join(RUN_DIR, "state.json")
SPOKEN_LOG = os.path.join(STATE_DIR, "spoken.log")            # one line per utterance the daemon spoke (tests grep it)

DEFAULT_SETTINGS = {
    "voice.enabled": "true",
    "voice.wake_word": "hey fab",
    "voice.speak_replies": "true",
    "voice.offline_only": "false",
    "voice.speak_full": "false",
    "voice.kws_threshold": "1e-50",   # pocketsphinx p(hyp)/p(alt); 1e-50 = detection, 0 false hits on 22 s of hard negatives
    "voice.verify_wake": "true",      # second look at the wake clip with whisper.cpp before acting (near-misses like "hey bob ... fabulous")
    "voice.spotter": "on",            # on | battery-off | off — when the always-on "Hey Fab" listener may hold the microphone (spotter_policy)
}
SPOTTER_POLICIES = ("on", "battery-off", "off")
PERF_MODE_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "fabos", "performance-mode")
CLOUD_PROVIDERS = ("openai", "gemini")   # the agent's /speech endpoints use one of these

# The offline voice. Received-Pronunciation English at 150 words a minute (default 175), pitch 45 (default 50),
# amplitude 175 of 200 (default 100 was too quiet next to system sounds) and a 60 ms gap between words: slower and
# louder than stock espeak-ng, which the owner found too fast and too faint on a laptop speaker. Every flag verified
# against `espeak-ng --help` (1.52.0 in the Ubuntu 26.04 image).
ESPEAK_VOICE = "en-gb-x-rp"
ESPEAK_ARGS = ["-v", ESPEAK_VOICE, "-s", "150", "-p", "45", "-a", "175", "-g", "6"]

FAKE_SPEAK = os.environ.get("FABOS_VOICE_FAKE_SPEAK")
FAKE_LISTEN = os.environ.get("FABOS_VOICE_FAKE_LISTEN")

# What the last capture / playback used; read by `fabos-voice -v`, `say --test`, doctor and the daemon's log lines.
LAST_CAPTURE = {"cmd": None, "tried": []}
LAST_PLAYBACK = {"backend": None, "player": None, "sink": None, "path": None}


class NothingHeard(Exception):
    """No speech in the recording (exit 3). `reason` says why when it is more than silence (a muted microphone)."""

    def __init__(self, reason=""):
        super().__init__(reason)
        self.reason = reason


class NoBackend(Exception):
    """No microphone / no audio session / no speech-to-text / no text-to-speech available (exit 4)."""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def ensure_run_dir():
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    return RUN_DIR


def _run(cmd, timeout=PROBE_TIMEOUT_S, input=None):
    """subprocess.run that never raises: (rc, stdout, stderr); rc -1 when the binary is missing or did not answer."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return -1, "", "%s: not installed" % cmd[0]
    except subprocess.TimeoutExpired:
        return -1, "", "%s: no answer within %.0f s" % (cmd[0], timeout)
    except OSError as e:
        return -1, "", "%s: %s" % (cmd[0], e)


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
    silence is reached, else None. finish() returns what was captured when the caller hits its timeout.
    dead_stream is True once the first DEAD_STREAM_MS of audio were exactly zero: a muted or absent microphone
    (a live microphone always carries a noise floor, even in a quiet room)."""

    def __init__(self, ratio=SPEECH_RATIO, trailing_s=TRAILING_SILENCE_S, calibrate_chunks=CALIBRATE_CHUNKS,
                 preroll_chunks=PREROLL_CHUNKS, min_floor=MIN_FLOOR, chunk_ms=CHUNK_MS, dead_ms=DEAD_STREAM_MS):
        self.ratio, self.trailing_s, self.calibrate_chunks = ratio, trailing_s, calibrate_chunks
        self.preroll_chunks, self.min_floor, self.chunk_ms, self.dead_ms = preroll_chunks, min_floor, chunk_ms, dead_ms
        self.levels = []
        self.floor = None
        self.threshold = None
        self.speech_started = False
        self.pre = []
        self.buf = bytearray()
        self.silent_ms = 0
        self.speech_ms = 0
        self.fed_ms = 0
        self.zero_ms = 0           # exactly-zero audio since the start (stops counting at the first non-zero chunk)
        self.max_level = 0.0
        self.done = False

    @property
    def dead_stream(self):
        return self.fed_ms >= self.dead_ms and self.zero_ms >= self.fed_ms

    def feed(self, chunk):
        if self.done:
            return None
        level = rms(chunk)
        self.fed_ms += self.chunk_ms
        if level == 0.0 and self.zero_ms + self.chunk_ms >= self.fed_ms:
            self.zero_ms += self.chunk_ms
        self.max_level = max(self.max_level, level)
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


def wav_seconds(path):
    """Duration of a WAV file in seconds (0.0 when it cannot be read)."""
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


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


# --------------------------------------------------------------------------- the audio session (PipeWire / PulseAudio)
def which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def audio_session():
    """The user's audio server: {"ok", "server", "version", "via", "reason"}. pactl info answers for PipeWire (through
    pipewire-pulse) and for PulseAudio; pw-cli info 0 for a PipeWire without the Pulse shim; the runtime socket when
    neither tool is installed. Without a session pw-record / pw-play cannot work, so this is the first thing to check."""
    ov = os.environ.get("FABOS_VOICE_SESSION")
    if ov is not None:
        ok = truthy(ov)
        return {"ok": ok, "server": "PipeWire" if ok else None, "version": "", "via": "forced", "reason": "" if ok else phrases.NO_AUDIO_SESSION}
    tried = []
    if which("pactl"):
        rc, out, err = _run(["pactl", "info"])
        if rc == 0:
            name = version = ""
            for line in out.splitlines():
                if line.startswith("Server Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Server Version:"):
                    version = line.split(":", 1)[1].strip()
            m = re.search(r"PipeWire\s+([\d.]+)", name)
            server = "PipeWire" if "PipeWire" in name else "PulseAudio"
            return {"ok": True, "server": server, "version": m.group(1) if m else version, "via": "pactl", "reason": ""}
        tried.append("pactl: " + " ".join((err or out).split())[:120])
    if which("pw-cli"):
        rc, out, err = _run(["pw-cli", "info", "0"])
        if rc == 0:
            m = re.search(r'version:\s*"([^"]+)"', out)
            return {"ok": True, "server": "PipeWire", "version": m.group(1) if m else "", "via": "pw-cli", "reason": ""}
        tried.append("pw-cli: " + " ".join((err or out).split())[:120])
    sock = os.path.join(RUN_BASE, "pipewire-0")
    if not tried and os.path.exists(sock):
        return {"ok": True, "server": "PipeWire", "version": "", "via": "socket", "reason": ""}
    if not tried:
        tried.append("neither pactl nor pw-cli is installed and %s does not exist" % sock)
    return {"ok": False, "server": None, "version": "", "via": None, "reason": "%s (%s)" % (phrases.NO_AUDIO_SESSION, "; ".join(tried))}


def _pw_dump():
    if not which("pw-dump"):
        return None
    rc, out, err = _run(["pw-dump"], timeout=6)
    try:
        d = json.loads(out)
    except ValueError:
        return None
    return d if isinstance(d, list) else None


def _pw_default(dump, key):
    for o in dump or []:
        if o.get("type") == "PipeWire:Interface:Metadata":
            for m in o.get("metadata") or []:
                if m.get("key") == key:
                    v = m.get("value")
                    if isinstance(v, dict):
                        v = v.get("name")
                    if v:
                        return str(v)
    return None


def _pw_nodes(dump, klass):
    out = []
    for o in dump or []:
        if o.get("type") == "PipeWire:Interface:Node":
            p = (o.get("info") or {}).get("props") or {}
            mc = p.get("media.class") or ""
            if mc == klass or mc.startswith(klass + "/"):
                out.append({"id": o.get("id"), "name": p.get("node.name"), "description": p.get("node.description") or p.get("node.nick") or p.get("node.name")})
    return out


def _wpctl_level(target):
    """(volume 0..1 or None, muted or None) from `wpctl get-volume @DEFAULT_AUDIO_SOURCE@` style output."""
    if not which("wpctl"):
        return None, None
    rc, out, err = _run(["wpctl", "get-volume", target])
    if rc != 0:
        return None, None
    m = re.search(r"Volume:\s*([\d.]+)", out)
    return (float(m.group(1)) if m else None), ("[MUTED]" in out)


def _pactl_level(kind, name):
    """(volume 0..1 or None, muted or None) for a pactl source/sink name."""
    vol = muted = None
    rc, out, err = _run(["pactl", "get-%s-mute" % kind, name])
    if rc == 0:
        muted = "yes" in out.lower()
    rc, out, err = _run(["pactl", "get-%s-volume" % kind, name])
    m = re.search(r"(\d+)%", out) if rc == 0 else None
    if m:
        vol = int(m.group(1)) / 100.0
    return vol, muted


def _pactl_description(kind, name):
    rc, out, err = _run(["pactl", "list", "%ss" % kind])
    if rc != 0:
        return name
    cur = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Name:"):
            cur = s.split(":", 1)[1].strip()
        elif s.startswith("Description:") and cur == name:
            return s.split(":", 1)[1].strip()
    return name


def _default_endpoint(kind):
    """kind = "source" | "sink". {"name", "description", "muted", "volume", "via", "reason"}; name None when absent."""
    none = {"name": None, "description": None, "muted": None, "volume": None, "via": None, "reason": ""}
    if which("pactl"):
        rc, out, err = _run(["pactl", "get-default-%s" % kind])
        name = out.strip() if rc == 0 else ""
        if name and not (kind == "source" and name.endswith(".monitor")):
            vol, muted = _pactl_level(kind, name)
            return {"name": name, "description": _pactl_description(kind, name), "muted": muted, "volume": vol, "via": "pactl", "reason": ""}
        if name:
            none["reason"] = phrases.SOURCE_IS_MONITOR
        elif rc != 0 and err:
            none["reason"] = " ".join(err.split())[:120]
    dump = _pw_dump()
    if dump is not None:
        klass = "Audio/Source" if kind == "source" else "Audio/Sink"
        name = _pw_default(dump, "default.audio." + kind)
        nodes = [n for n in _pw_nodes(dump, klass) if n["name"]]
        if not name and nodes:
            name = nodes[0]["name"]
        if name:
            desc = next((n["description"] for n in nodes if n["name"] == name), name)
            vol, muted = _wpctl_level("@DEFAULT_AUDIO_SOURCE@" if kind == "source" else "@DEFAULT_AUDIO_SINK@")
            return {"name": name, "description": desc, "muted": muted, "volume": vol, "via": "pw-dump", "reason": ""}
    if not none["reason"]:
        none["reason"] = phrases.NO_SOURCE if kind == "source" else phrases.NO_SINK
    return none


def default_source():
    """The default capture device (microphone) of the session, with its mute state and volume."""
    return _default_endpoint("source")


def default_sink():
    """The default playback device of the session."""
    return _default_endpoint("sink")


_sink_cache = (0.0, None)


def default_sink_name(max_age=10.0):
    """Name of the default sink, cached briefly (speak() reports it for every utterance)."""
    global _sink_cache
    ts, name = _sink_cache
    if time.time() - ts < max_age:
        return name
    name = default_sink().get("name") if audio_session()["ok"] else None
    _sink_cache = (time.time(), name)
    return name


def _session_forced_off():
    ov = os.environ.get("FABOS_VOICE_SESSION")
    return ov is not None and not truthy(ov)


def alsa_capture_devices():
    """ALSA capture nodes (the no-session fallback path); none when FABOS_VOICE_SESSION=0 pretends there is no audio."""
    return [] if _session_forced_off() else sorted(glob.glob("/dev/snd/pcmC*D*c"))


def alsa_playback_devices():
    return [] if _session_forced_off() else sorted(glob.glob("/dev/snd/pcmC*D*p"))


def mic_present():
    """True when a capture device exists. Cheap: ALSA capture nodes, then PipeWire/Pulse sources (no monitors)."""
    if FAKE_LISTEN:
        return True
    ov = os.environ.get("FABOS_VOICE_MIC")
    if ov is not None:
        return truthy(ov)
    if alsa_capture_devices():
        return True
    pactl = which("pactl")
    if pactl:
        rc, out, err = _run([pactl, "list", "short", "sources"])
        if rc == 0:
            return any(line.strip() and ".monitor" not in line for line in out.splitlines())
    return False


# --------------------------------------------------------------------------- spotter policy (docs/PERFORMANCE.md)
def _read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def performance_mode(path=None):
    """The Fab OS performance mode chosen in quick settings / `fabos-perf-mode set` (power-saver | balanced | performance |
    gaming | server), "" when none was ever chosen. One word in ~/.config/fabos/performance-mode; no process is spawned."""
    m = _read_text(path or PERF_MODE_FILE).lower()
    return m if m in ("power-saver", "balanced", "performance", "gaming", "server") else ""


def spotter_policy(settings, mode=None):
    """on | battery-off | off: the user's voice.spotter, tightened by the performance mode — Server turns the listener off
    (a machine used as a server has nobody to talk to it), Power saver makes an "on" listener rest while on battery."""
    pol = str((settings or {}).get("voice.spotter") or DEFAULT_SETTINGS["voice.spotter"]).strip().lower()
    if pol not in SPOTTER_POLICIES:
        pol = "on"
    mode = performance_mode() if mode is None else mode
    if mode == "server":
        return "off"
    if mode == "power-saver" and pol == "on":
        return "battery-off"
    return pol


def on_battery(base="/sys/class/power_supply"):
    """True when the machine runs on its battery: no mains/USB supply reports online and a battery reports Discharging.
    sysfs only (no process). False on a desktop or in a VM without a power supply."""
    mains_online = False
    discharging = False
    for d in glob.glob(os.path.join(base, "*")):
        t = _read_text(os.path.join(d, "type"))
        if t in ("Mains", "USB", "UPS"):
            if _read_text(os.path.join(d, "online")) == "1":
                mains_online = True
        elif t == "Battery" and _read_text(os.path.join(d, "status")) == "Discharging":
            discharging = True
    return discharging and not mains_online


def _session_bus_ok():
    return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")) or os.path.exists(os.path.join(RUN_BASE, "bus"))


def screen_locked():
    """True while the screen locker is active (org.freedesktop.ScreenSaver GetActive, served by kscreenlocker); None when
    the session bus or the interface is not reachable. One busctl call."""
    if not _session_bus_ok() or not which("busctl"):
        return None
    rc, out, _ = _run(["busctl", "--user", "--timeout=2", "call", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver", "GetActive"], timeout=3)
    if rc != 0 or not out.startswith("b "):
        return None
    return out.split()[1] == "true"


def session_idle_s():
    """Seconds since the last keyboard/mouse input in the graphical session (org.freedesktop.ScreenSaver
    GetSessionIdleTime), or None when unavailable. One busctl call."""
    if not _session_bus_ok() or not which("busctl"):
        return None
    rc, out, _ = _run(["busctl", "--user", "--timeout=2", "call", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver", "GetSessionIdleTime"], timeout=3)
    parts = out.split()
    if rc != 0 or len(parts) != 2 or not parts[1].isdigit():
        return None
    return int(parts[1])


def mic_state(session=None):
    """(usable: bool, reason: str, source: dict) — why the microphone is or is not usable right now. Recording needs a
    capture device AND a way to reach it: the audio session (pw-record / parec) or, without one, plain ALSA (arecord)."""
    session = session or audio_session()
    present = mic_present()
    src = default_source() if session["ok"] else {"name": None, "description": None, "muted": None, "volume": None, "via": None, "reason": ""}
    if not session["ok"]:
        if present and which("arecord") and alsa_capture_devices():
            return True, phrases.NO_SESSION_ALSA_ONLY, src
        return False, session["reason"], src
    if not present and not src.get("name"):
        return False, phrases.NO_MIC, src
    if not src.get("name"):
        return False, src.get("reason") or phrases.NO_SOURCE, src
    if src.get("muted"):
        return True, phrases.MIC_MUTED, src
    if src.get("volume") is not None and src["volume"] <= 0.0:
        return True, phrases.MIC_VOLUME_ZERO, src
    return True, "", src


def playback_possible(session=None):
    """(ok, reason): can anything play sound at all?"""
    session = session or audio_session()
    if session["ok"]:
        return True, ""
    if which("aplay") and alsa_playback_devices():
        return True, phrases.NO_SESSION_ALSA_ONLY
    return False, session["reason"]


# --------------------------------------------------------------------------- recorders
def capture_candidates(target=None):
    """Recorder command lines to try in order, raw s16le 16 kHz mono on stdout. `target` (the default source's name)
    goes explicitly to the second pw-record attempt and to parec: on some sessions the automatic target is a monitor
    or a device that never delivers a byte. arecord (plain ALSA, no session) is the last resort."""
    out = []
    if which("pw-record"):
        base = ["pw-record", "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "s16"]
        out.append(base + ["-"])
        if target:
            out.append(base + ["--target", str(target), "-"])
    if which("parec"):
        base = ["parec", "--rate=%d" % RATE, "--channels=%d" % CHANNELS, "--format=s16le", "--raw"]
        out.append(base + ["--device=%s" % (target or "@DEFAULT_SOURCE@")])
    if which("arecord"):
        out.append(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw"])
    return out


def capture_command():
    """The first recorder (the daemon's always-on spotter uses it), or None when no recorder is installed."""
    c = capture_candidates()
    return c[0] if c else None


def capture_shared():
    """True when the recorder shares the microphone with other streams (PipeWire, PulseAudio); plain ALSA capture is
    exclusive, so the wake spotter cannot stay open while an approval answer is recorded."""
    cmd = capture_command()
    return bool(cmd) and cmd[0] in ("pw-record", "parec")


def _read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        part = stream.read(n - len(buf))
        if not part:
            break
        buf += part
    return buf


class Capture:
    """One recorder process with a reader thread: read(timeout) never blocks longer than asked, so a recorder that
    starts but never delivers (a target without data, a session that hangs) cannot stall the caller."""

    def __init__(self, cmd):
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.q = queue.Queue()
        self.thread = threading.Thread(target=self._reader, daemon=True, name="capture-reader")
        self.thread.start()

    def _reader(self):
        try:
            while True:
                chunk = _read_exact(self.proc.stdout, CHUNK_BYTES)
                if len(chunk) < CHUNK_BYTES:
                    break
                self.q.put(chunk)
        except (OSError, ValueError):
            pass
        finally:
            self.q.put(None)

    def read(self, timeout):
        """A 100 ms chunk; None when nothing arrived within `timeout`; b"" once the recorder has ended."""
        try:
            c = self.q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        return b"" if c is None else c

    def close(self):
        """Stop the recorder; returns the tail of its stderr (why it ended, if it did)."""
        with contextlib.suppress(Exception):
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.proc.wait(2)
        err = b""
        with contextlib.suppress(Exception):
            err = self.proc.stderr.read()[-300:]
        for f in (self.proc.stdout, self.proc.stderr):
            with contextlib.suppress(Exception):
                f.close()
        return " ".join(err.decode(errors="replace").split())


def open_capture(target=None, deadline=None, first_chunk_s=FIRST_CHUNK_S):
    """Start the first recorder that actually delivers audio. Returns (Capture, first_chunk); raises NoBackend with the
    reasons every candidate gave. `target` is the default source name for the explicit attempts (looked up when None
    and the automatic attempt failed)."""
    tried = []
    cands = capture_candidates(target)
    if not cands:
        raise NoBackend(phrases.NO_RECORDER)
    looked_up = target is not None
    i = 0
    while i < len(cands):
        cmd = cands[i]
        i += 1
        if deadline is not None and time.time() > deadline:
            tried.append("out of time before trying %s" % cmd[0])
            break
        try:
            cap = Capture(cmd)
        except OSError as e:
            tried.append("%s: %s" % (cmd[0], e))
            continue
        wait = first_chunk_s if deadline is None else max(0.2, min(first_chunk_s, deadline - time.time()))
        chunk = cap.read(wait)
        if chunk:
            LAST_CAPTURE.update(cmd=cmd, tried=list(tried))
            return cap, chunk
        err = cap.close()
        explicit = any(a.startswith("--target") or a.startswith("--device") for a in cmd)
        tried.append("%s%s: %s" % (cmd[0], " (explicit target)" if explicit else "",
                                   err or ("ended without audio" if chunk == b"" else "no audio within %.1f s" % wait)))
        if not looked_up:
            # the automatic target gave nothing: look the default source up once and retry with it named explicitly
            looked_up = True
            src = default_source().get("name")
            if src:
                target = src
                cands = cands[:i] + [c for c in capture_candidates(target) if c != cmd]
    LAST_CAPTURE.update(cmd=None, tried=list(tried))
    raise NoBackend("%s (%s)" % (phrases.NO_MIC, "; ".join(tried) or "no recorder produced audio"))


def capture_seconds(seconds=1.0, target=None):
    """Diagnostic: record `seconds` of audio from the first working recorder. Returns (pcm, cmd)."""
    cap, chunk = open_capture(target, deadline=time.time() + seconds + RECORD_GRACE_S)
    buf = bytearray(chunk)
    need = int(RATE * SAMPLE_WIDTH * seconds)
    deadline = time.time() + seconds + RECORD_GRACE_S
    try:
        while len(buf) < need and time.time() < deadline:
            c = cap.read(min(1.0, deadline - time.time()))
            if c == b"":
                break
            if c:
                buf += c
    finally:
        cap.close()
    return bytes(buf), cap.cmd


# --------------------------------------------------------------------------- players
def _pdeathsig():
    """preexec_fn for players: die (SIGTERM) with the parent, so a killed `fabos-voice say` also silences the audio."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, int(signal.SIGTERM), 0, 0, 0)          # PR_SET_PDEATHSIG = 1
    except Exception:
        pass


_current_player = None


def player_candidates(path, sink=None):
    """Player command lines in order of preference; `sink` (a name) is passed only when the caller insists."""
    ext = os.path.splitext(path)[1].lower()
    out = [["pw-play"] + (["--target", sink] if sink else []) + [path],
           ["paplay"] + (["--device=" + sink] if sink else []) + [path]]
    if ext == ".wav":
        out.append(["aplay", "-q", path])
    out += [["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path], ["mpv", "--really-quiet", "--no-video", path]]
    return out


def stop_player():
    """Kill the player of the current utterance (SIGTERM handler of `fabos-voice say`)."""
    p = _current_player
    if p is not None:
        with contextlib.suppress(Exception):
            p.kill()


def play_file(path, block=True, sink=None):
    """Play a WAV/MP3 file with the first available player. Returns True when the audio was played.

    A player that ran for most of the file's duration DID play it, whatever its exit code (pw-play and paplay can exit
    non-zero after the last sample when the stream is torn down) — the next candidate must not play the same audio
    again. Only a player that fails at once (no session, no device) hands over to the next one."""
    global _current_player
    dur = wav_seconds(path) if path.lower().endswith(".wav") else 0.0
    LAST_PLAYBACK.update(player=None, sink=sink or default_sink_name(), path=path)
    for cmd in player_candidates(path, sink):
        if not which(cmd[0]):
            continue
        t0 = time.time()
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, preexec_fn=_pdeathsig)
        except OSError:
            continue
        _current_player = proc
        try:
            rc = proc.wait(timeout=(dur + 30 if block else 5) if dur else (120 if block else 5))
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = -9
        finally:
            _current_player = None
        elapsed = time.time() - t0
        err = ""
        with contextlib.suppress(Exception):
            err = " ".join(proc.stderr.read().decode(errors="replace").split())[-200:]
        with contextlib.suppress(Exception):
            proc.stderr.close()
        played = rc == 0 or (dur > 0 and elapsed >= 0.6 * dur) or (dur == 0 and elapsed >= 1.0)
        if played:
            LAST_PLAYBACK["player"] = cmd[0]
            if rc != 0:
                log("%s exited %s after playing %.1f s of %.1f s (%s); not replaying" % (cmd[0], rc, elapsed, dur, err))
            return True
        log("%s could not play (%s): %s" % (cmd[0], rc, err or "no error text"))
    return False


def play_chime():
    try:
        return play_file(chime_path())
    except Exception:
        return False


# --------------------------------------------------------------------------- pid locks (speaking / listening) + the playback queue
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


def lock_owner(name):
    """pid holding the named lock, or None when nobody alive does."""
    try:
        with open(_lock_path(name)) as f:
            pid = int(f.read().strip())
        return pid if _pid_alive(pid) else None
    except (OSError, ValueError):
        return None


def lock_active(name):
    return lock_owner(name) is not None


def wait_for_silence(max_wait=PLAYBACK_WAIT_S):
    """Block while another process holds 'speaking' (Fab AI Controls' Speak button, another fabos-voice say), up to
    max_wait seconds. Returns True when the other voice finished, False when we gave up waiting."""
    deadline = time.time() + max_wait
    while True:
        owner = lock_owner("speaking")
        if owner is None or owner == os.getpid():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


@contextlib.contextmanager
def playback_lock(max_wait=PLAYBACK_WAIT_S):
    """Serialises playback across every Fab OS voice process: an flock on $XDG_RUNTIME_DIR/fabos-voice/playback.lock,
    waited for up to max_wait seconds — then we speak anyway rather than stay silent for ever. Yields True when the
    lock was ours, False when we speak without it. A killed holder releases it at once (the kernel closes the fd)."""
    ensure_run_dir()
    got = False
    try:
        fd = os.open(os.path.join(RUN_DIR, "playback.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield False
        return
    deadline = time.time() + max_wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.time() >= deadline:
                    log("another utterance has been playing for %.0f s; speaking anyway" % max_wait)
                    break
                time.sleep(0.1)
        yield got
    finally:
        if got:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


# --------------------------------------------------------------------------- recording
def record_utterance(timeout=DEFAULT_TIMEOUT_S, on_state=None, start_timeout=None, source=None):
    """Record from the microphone until 1.2 s of silence after speech, or `timeout` seconds.
    `start_timeout` (daemon only; the CLI keeps to the contract) gives up early when speech has not begun by then.
    Never runs longer than timeout + RECORD_GRACE_S. Returns raw s16 PCM.
    Raises NoBackend (no audio session / no recorder / no device) or NothingHeard (silence, or a muted microphone:
    reason MIC_SILENT when the first second of the stream is exactly zero)."""
    timeout = max(1.0, float(timeout))
    start = time.time()
    hard_deadline = start + timeout + RECORD_GRACE_S
    session = audio_session()
    if not session["ok"] and not (which("arecord") and alsa_capture_devices()):
        raise NoBackend(session["reason"])
    cap, chunk = open_capture(source, deadline=hard_deadline)
    seg = Segmenter()
    deadline = min(start + timeout, hard_deadline)
    start_by = start + float(start_timeout) if start_timeout else None
    try:
        with lock("listening"):
            if on_state:
                on_state(listening=True)
            while True:
                if chunk:
                    done = seg.feed(chunk)
                    if done is not None:
                        return done
                    if seg.dead_stream and not seg.speech_started:
                        raise NothingHeard(phrases.MIC_SILENT)
                elif chunk == b"":
                    log("recorder %s ended early" % cap.cmd[0])
                    break                      # recorder ended (device vanished)
                now = time.time()
                if now >= deadline or now >= hard_deadline:
                    break
                if start_by and not seg.speech_started and now > start_by:
                    break                      # nobody spoke: give the microphone back
                chunk = cap.read(min(1.0, hard_deadline - now))
    finally:
        if on_state:
            on_state(listening=False)
        cap.close()
    pcm = seg.finish()
    if pcm is None:
        raise NothingHeard(phrases.MIC_SILENT if seg.dead_stream else "")
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


def tts_backends(agent, settings=None, session=None):
    """Text-to-speech backends that can actually be heard: a cloud voice or espeak-ng, and something to play through."""
    out = []
    if FAKE_SPEAK:
        out.append("fake")
        return out
    ok, _ = playback_possible(session)
    if not ok:
        return out
    if cloud_ready(agent, settings):
        out.append("cloud")
    if which("espeak-ng"):
        out.append("espeak-ng")
    return out


def stt_reason(agent, settings=None):
    """Why speech-to-text is unavailable ("" when it is)."""
    settings = settings or agent.settings(max_age=5)
    if FAKE_LISTEN or cloud_ready(agent, settings) or whisper_ready():
        return ""
    parts = []
    if not which("whisper-cli"):
        parts.append("whisper-cli is not installed")
    elif not os.path.isfile(MODEL_PATH):
        parts.append("the offline model %s is missing" % MODEL_PATH)
    if truthy(settings.get("voice.offline_only", "false")):
        parts.append("voice.offline_only is on, so the cloud is not used")
    elif not settings.get("_agent_up"):
        parts.append("the Fab agent is not running, so no cloud speech provider can be used")
    else:
        parts.append("no cloud speech provider key is set (Fab AI Controls › AI provider)")
    return "%s (%s)" % (phrases.NO_STT, "; ".join(parts))


def tts_reason(agent, settings=None, session=None):
    """Why text-to-speech is unavailable ("" when it is)."""
    if FAKE_SPEAK:
        return ""
    ok, why = playback_possible(session)
    if not ok:
        return why
    settings = settings or agent.settings(max_age=5)
    if cloud_ready(agent, settings) or which("espeak-ng"):
        return ""
    return "%s (espeak-ng is not installed and no cloud voice is configured)" % phrases.NO_TTS


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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=WHISPER_TIMEOUT_S)
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
        raise NoBackend(stt_reason(agent, settings) or phrases.NO_STT)
    session = audio_session()
    if not session["ok"] and not (which("arecord") and alsa_capture_devices()):
        raise NoBackend(session["reason"])          # before the chime: nothing could play it anyway
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
    LAST_PLAYBACK["backend"] = "cloud"
    return "cloud"


def espeak_command(text, wav=None):
    """espeak-ng with the Fab OS offline profile; renders to `wav` when given, else speaks through its own audio output."""
    return ["espeak-ng"] + ESPEAK_ARGS + (["-w", wav] if wav else []) + ["--", text]


def render_espeak(text, path):
    """Render `text` to a WAV at `path` with the offline profile. Raises NoBackend when espeak-ng fails."""
    if not which("espeak-ng"):
        raise NoBackend(phrases.NO_TTS)
    r = subprocess.run(espeak_command(text, path), capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(path) or os.path.getsize(path) <= 44:
        raise NoBackend("espeak-ng failed: %s" % ((r.stderr or "").strip()[-200:] or "no audio written"))
    return path


def say_espeak(text):
    if not which("espeak-ng"):
        raise NoBackend(phrases.NO_TTS)
    path = os.path.join(ensure_run_dir(), "say-%d.wav" % os.getpid())
    try:
        render_espeak(text, path)
        if not play_file(path):
            ok, why = playback_possible()
            if not ok:
                raise NoBackend(why)
            # no player at all: let espeak-ng drive the audio device itself (blocking)
            r = subprocess.run(espeak_command(text), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if r.returncode != 0:
                raise NoBackend(phrases.NO_TTS)
            LAST_PLAYBACK["player"] = "espeak-ng"
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    LAST_PLAYBACK["backend"] = "espeak-ng"
    return "espeak-ng"


def speak(text, agent=None, settings=None):
    """Speak `text` (blocking). Order: cloud (Indian-English voice) -> espeak-ng -> NoBackend. Returns the backend.
    One utterance at a time, machine-wide: waits (up to PLAYBACK_WAIT_S) for whatever Fab AI Controls or another
    fabos-voice process is saying, then holds the 'speaking' lock so the wake listener stays deaf meanwhile."""
    text = " ".join((text or "").split())
    if not text:
        return "none"
    if FAKE_SPEAK:
        with open(FAKE_SPEAK, "a") as f:
            f.write(text + "\n")
        LAST_PLAYBACK.update(backend="fake", player="fake", sink=None)
        return "fake"
    agent = agent or Agent()
    settings = settings or agent.settings(max_age=5)
    ok, why = playback_possible()
    if not ok:
        raise NoBackend(why)
    wait_for_silence()
    with playback_lock():
        with lock("speaking"):
            if cloud_ready(agent, settings):
                try:
                    return say_cloud(agent, text)
                except NoBackend as e:
                    log("cloud say failed, falling back to espeak-ng:", e)
            return say_espeak(text)


def speak_test(agent=None):
    """`fabos-voice say --test`: speak the two-sentence sample; returns {"backend", "player", "sink"}."""
    backend = speak(phrases.SAY_TEST, agent)
    return {"backend": backend, "player": LAST_PLAYBACK.get("player"), "sink": LAST_PLAYBACK.get("sink")}


# --------------------------------------------------------------------------- wake-word spotting (pocketsphinx)
def spotter_command(wake_word="hey fab", threshold="1e-50"):
    """`pocketsphinx -keyphrase WAKE -kws_threshold T live -`: raw s16 16 kHz on stdin, one JSON line per utterance
    ({"t": "hey fab", ...} when the phrase is spotted), flushed while the stream is still open (measured: the line
    arrived 0.7 s before the feed ended). pocketsphinx 5.0.4 / Ubuntu 26.04; 60 s of audio cost 0.9 s of CPU.
    1e-50 is the only threshold that spotted an espeak-ng "hey fab" (a synthetic voice; the acoustic model is trained
    on people), and at that sensitivity it also fires on look-alikes such as "a fabulous day" - hence verify_wake_clip."""
    return ["pocketsphinx", "-keyphrase", wake_word, "-kws_threshold", str(threshold), "-loglevel", "ERROR", "live", "-"]


def spotter_ready():
    return bool(which("pocketsphinx")) and os.path.isfile(DICT_PATH)


def dictionary_missing(words):
    """Words of a wake phrase the acoustic dictionary does not know (all of them when the dictionary is missing)."""
    words = [w for w in re.sub(r"[^a-z' ]", " ", (words or "").lower()).split()]
    if not words:
        return []
    known = set()
    try:
        with open(DICT_PATH, errors="replace") as f:
            for line in f:
                w = line.split(" ", 1)[0]
                if w in words:
                    known.add(w)
    except OSError:
        return words
    return [w for w in words if w not in known]


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
    """The VOICE CONTRACT status line: {"wake", "listening", "stt", "tts", "mic"} plus "mic_reason" / "stt_reason" /
    "tts_reason" — "" when the piece works, otherwise one sentence a UI can show next to the greyed-out control
    (mic_reason is also set for a muted microphone, which still counts as present)."""
    agent = agent or Agent()
    settings = agent.settings(max_age=5)
    st = daemon_state()
    session = audio_session()
    stt = stt_backends(agent, settings)
    tts = tts_backends(agent, settings, session)
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
    mic, mic_why, src = mic_state(session)
    return {
        "wake": bool(st.get("wake")) and truthy(settings.get("voice.enabled", "true")),
        "listening": bool(st.get("listening")) or lock_active("listening"),
        "stt": stt_name,
        "tts": tts_name,
        "mic": mic,
        "mic_reason": mic_why,
        "stt_reason": "" if stt_name != "none" else stt_reason(agent, settings),
        "tts_reason": "" if tts_name != "none" else tts_reason(agent, settings, session),
        # extra, non-contract detail (UIs may ignore): why something is off, and the daemon's own view
        "enabled": truthy(settings.get("voice.enabled", "true")),
        "agent": bool(settings.get("_agent_up")),
        "speaking": bool(st.get("speaking")) or lock_active("speaking"),
        "wake_word": settings.get("voice.wake_word", "hey fab"),
        "offline_only": truthy(settings.get("voice.offline_only", "false")),
        "model": MODEL_PATH if os.path.isfile(MODEL_PATH) else None,
        "spotter": spotter_ready(),
        "spotter_policy": spotter_policy(settings),                # on | battery-off | off (voice.spotter + performance mode)
        "spotter_state": st.get("spotter") or ("off" if not st else "idle"),   # listening | paused:<why> | off | idle (the daemon's own view)
        "session": session.get("server"),
        "source": src.get("name"),
        "source_muted": src.get("muted"),
        "sink": default_sink().get("name") if session["ok"] else None,
    }


# --------------------------------------------------------------------------- doctor
DOCTOR_STAGES = ("audio-session", "default-source", "capture", "speech-to-text", "wake-word", "default-sink", "text-to-speech", "agent", "listener", "settings")
DOCTOR_OPTIONAL = ("agent", "listener", "settings")


def doctor(agent=None, play=True, tts_text=None):
    """Check every stage of the voice pipeline for real (a 1 s capture, a whisper.cpp run on 0.5 s of silence, a rendered
    and played espeak-ng line) and return a list of {"stage", "required", "ok", "detail", "hint"} in pipeline order.
    Stages in DOCTOR_OPTIONAL do not decide the exit status: listen-once and say work without the agent."""
    agent = agent or Agent()
    out = []

    def add(stage, ok, detail, hint=""):
        out.append({"stage": stage, "required": stage not in DOCTOR_OPTIONAL, "ok": bool(ok), "detail": detail, "hint": "" if ok else (hint or phrases.DOCTOR_HINTS.get(stage, ""))})

    # 1. the audio session
    session = audio_session()
    add("audio-session", session["ok"], "%s %s (via %s)" % (session["server"], session["version"] or "", session["via"]) if session["ok"] else session["reason"])

    # 2. the default source
    src = default_source() if session["ok"] else {"name": None, "description": None, "muted": None, "volume": None, "reason": session["reason"]}
    if src.get("name"):
        flags = []
        if src.get("muted"):
            flags.append("MUTED")
        if src.get("volume") is not None:
            flags.append("volume %d%%" % round(src["volume"] * 100))
        add("default-source", True, "%s [%s]%s" % (src.get("description") or src["name"], src["name"], (" — " + ", ".join(flags)) if flags else ""))
    else:
        add("default-source", False, src.get("reason") or phrases.NO_SOURCE)

    # 3. a one-second capture
    if session["ok"] or (which("arecord") and alsa_capture_devices()):
        try:
            pcm, cmd = capture_seconds(1.0, target=src.get("name"))
            level = rms(pcm)
            note = " — all zeros: the microphone is muted or the device is silent (expected on an emulated sound card)" if pcm and level == 0.0 else ""
            shown = cmd[:1] + [a for i, a in enumerate(cmd) if a.startswith("--device") or a == "--target" or (i > 0 and cmd[i - 1] == "--target")]
            add("capture", len(pcm) > 0, "%d bytes in 1 s via %s, level RMS %.0f%s" % (len(pcm), " ".join(shown), level, note))
        except NoBackend as e:
            add("capture", False, str(e))
    else:
        add("capture", False, "skipped: " + (session["reason"] if not session["ok"] else phrases.NO_RECORDER))

    # 4. speech-to-text: whisper-cli + model, and a 0.5 s silent WAV round trip
    if whisper_ready():
        wav = os.path.join(ensure_run_dir(), "doctor-%d.wav" % os.getpid())
        write_wav(wav, b"\0" * (RATE * SAMPLE_WIDTH // 2))
        t0 = time.time()
        try:
            text = transcribe_whisper(wav)
            add("speech-to-text", True, "whisper-cli + %s, %.1f s for 0.5 s of silence -> %r" % (os.path.basename(MODEL_PATH), time.time() - t0, text))
        except NoBackend as e:
            add("speech-to-text", False, str(e))
        finally:
            with contextlib.suppress(OSError):
                os.remove(wav)
    else:
        add("speech-to-text", False, stt_reason(agent) or phrases.NO_STT)

    # 5. the wake word: pocketsphinx + dictionary words
    settings = agent.settings(max_age=5)
    wake = str(settings.get("voice.wake_word") or "hey fab")
    if not which("pocketsphinx"):
        add("wake-word", False, "pocketsphinx is not installed")
    elif not os.path.isfile(DICT_PATH):
        add("wake-word", False, "the en-us acoustic model / dictionary is missing (%s)" % DICT_PATH)
    else:
        missing = dictionary_missing(wake)
        add("wake-word", not missing, "pocketsphinx + en-us model; wake phrase '%s' %s" % (wake, "is in the dictionary" if not missing else "has unknown words: " + ", ".join(missing)))

    # 6. the default sink
    sink = default_sink() if session["ok"] else {"name": None, "reason": session["reason"]}
    if sink.get("name"):
        flags = []
        if sink.get("muted"):
            flags.append("MUTED")
        if sink.get("volume") is not None:
            flags.append("volume %d%%" % round(sink["volume"] * 100))
        add("default-sink", True, "%s [%s]%s" % (sink.get("description") or sink["name"], sink["name"], (" — " + ", ".join(flags)) if flags else ""))
    else:
        add("default-sink", False, sink.get("reason") or phrases.NO_SINK)

    # 7. text-to-speech: render, then play
    if not which("espeak-ng"):
        add("text-to-speech", False, "espeak-ng is not installed")
    else:
        wav = os.path.join(ensure_run_dir(), "doctor-tts-%d.wav" % os.getpid())
        try:
            render_espeak(tts_text or phrases.DOCTOR_TTS_LINE, wav)
            dur = wav_seconds(wav)
            if not play:
                add("text-to-speech", True, "espeak-ng rendered %.1f s of audio (%s); playback skipped" % (dur, " ".join(ESPEAK_ARGS)))
            else:
                ok_play, why = playback_possible(session)
                if not ok_play:
                    add("text-to-speech", False, "espeak-ng rendered %.1f s but nothing can play it: %s" % (dur, why))
                else:
                    with playback_lock(), lock("speaking"):
                        played = play_file(wav)
                    add("text-to-speech", played, "espeak-ng (%s) rendered %.1f s, %s" % (" ".join(ESPEAK_ARGS), dur,
                        ("played with %s on sink %s" % (LAST_PLAYBACK.get("player"), LAST_PLAYBACK.get("sink") or sink.get("name") or "default")) if played else "no player could play it"))
        except NoBackend as e:
            add("text-to-speech", False, str(e))
        finally:
            with contextlib.suppress(OSError):
                os.remove(wav)

    # 8. the agent (optional for listen-once / say; needed for "Hey Fab" tasks and the cloud voice)
    ast = agent.status()
    if ast:
        cloud = cloud_ready(agent, settings)
        add("agent", True, "fabos-agent reachable, mode %s; cloud speech %s" % (ast.get("mode"), "configured" if cloud else "not configured (offline engines are used)"))
    else:
        add("agent", False, phrases.AGENT_DOWN)

    # 9. the listener daemon
    dst = daemon_state()
    if dst:
        add("listener", True, "fabos-voiced running (pid %s), wake %s, listening %s, speaking %s" % (dst.get("pid"), "on" if dst.get("wake") else "off", bool(dst.get("listening")), bool(dst.get("speaking"))))
    else:
        add("listener", False, "fabos-voiced is not running (no live state in %s)" % STATE_FILE)

    # 10. voice.* settings
    keys = ("voice.enabled", "voice.wake_word", "voice.speak_replies", "voice.offline_only", "voice.speak_full", "voice.verify_wake", "voice.kws_threshold")
    add("settings", bool(settings.get("_agent_up")), ", ".join("%s=%s" % (k.split(".", 1)[1], settings.get(k, DEFAULT_SETTINGS.get(k))) for k in keys) + ("" if settings.get("_agent_up") else " (defaults; the agent is not running)"))
    return out


def doctor_ok(stages):
    """True when every required stage passed."""
    return all(s["ok"] for s in stages if s["required"])


def format_doctor(stages):
    lines = []
    for s in stages:
        name = s["stage"] + ("" if s["required"] else " (optional)")
        lines.append("%-5s %-26s %s" % ("OK" if s["ok"] else "FAIL", name, s["detail"]))
        if not s["ok"] and s["hint"]:
            lines.append("%-5s %-26s fix: %s" % ("", "", s["hint"]))
    req = [s for s in stages if s["required"]]
    lines.append("%d of %d required stages OK%s" % (sum(1 for s in req if s["ok"]), len(req), "" if doctor_ok(stages) else " — voice will not work until the FAIL lines above are fixed"))
    return "\n".join(lines)
