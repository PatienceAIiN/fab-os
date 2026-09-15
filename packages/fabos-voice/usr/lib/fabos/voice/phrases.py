#!/usr/bin/env python3
"""phrases — every spoken and displayed sentence of Fab OS voice, in one reviewable place.

Tone: warm, natural Indian English. Short sentences, present tense, one thought each. The same table is
used by the wake-word daemon (spoken), by the CLI (printed) and by the tests (every agent tool must have
a narration line). Nothing here names a third-party product; system apps use their Fab OS names.

Public helpers:
  narration(tool, input_dict)       -> one sentence spoken when a tool step starts
  narration_done(tool, input_dict)  -> one sentence for a finished step (UIs; the daemon speaks a step JSON's own
                                       "narration_done" field when the agent supplies one)
  approval_summary(tool, input_dict, reason) -> "run the command ls -la" style fragment for PERMISSION
  intent(text, strict=False)        -> "approve" | "deny" | None from a spoken yes/no answer (answer-shaped only)
  is_stop(text)                     -> True when a short utterance means "stop that task"
  shorten(text, full=False)         -> first two sentences or 240 characters of a reply
"""
import json
import os
import re
from urllib.parse import urlparse

# --------------------------------------------------------------------------- fixed lines
STARTED = "Sure, doing it now."
LISTENING = "Listening…"
LISTENING_AGAIN = "Go on, I am listening."
NOT_HEARD = "Sorry, I did not catch that. Say it once more?"
NOT_HEARD_FINAL = "No problem. Say 'Hey Fab' whenever you are ready."
PERMISSION = "This needs your permission: {summary}. Shall I go ahead?"
WAIT_ON_SCREEN = "Okay, I will wait for you to decide on screen."
APPROVED = "Okay, going ahead."
DENIED = "Okay, I will not do that."
QUESTION = "Quick question: {question}"
ANSWER_NOT_HEARD = "I did not hear an answer. You can type it in Fab AI Controls."
ANSWER_TAKEN = "Got it, thanks."
DONE_EMPTY = "Done."
DONE_PREFIX = "Done. "
ERROR = "Sorry, that did not work: {reason}"
CANCELLED = "Okay, I have stopped that task."
RESUMING = "No problem, carrying on with the task."
LEFT_RUNNING = "Okay, I will leave that one running; you can watch it in Fab AI Controls."
TIMED_OUT = "This is taking a while. I will keep working on it; you can see the progress in Fab AI Controls."
AI_OFF = "System-Wide AI is off right now. Turn it on in Fab AI Controls and try again."
AGENT_DOWN = "Sorry, the Fab agent is not running right now."
FIRST_RUN_TITLE = "Talk to Fab"
FIRST_RUN_BODY = "Say 'Hey Fab', wait for the chime, then tell me what you need. You can turn this off in Fab AI Controls › Voice."
VOICE_UNAVAILABLE = "Voice is not available on this machine"
NO_MIC = "No microphone found on this computer."
NO_STT = "Speech recognition is not available: no offline model and no cloud provider key."
NO_TTS = "Text to speech is not available on this machine."
LOW_MEMORY = "Not enough free memory for offline speech recognition right now (it needs about 600 MB)."
WAKE_ON = "Okay, I am listening for 'Hey Fab'."
WAKE_OFF = "Okay, I will stop listening for 'Hey Fab'. You can turn it back on any time."
WAKE_UNSURE = "I thought I heard 'Hey Fab', but I am not sure. Say it once more if you need me."

# --------------------------------------------------------------------------- why the microphone or the speaker is not usable
# (fabos-voice status "mic_reason" / "tts_reason", listen-once's stderr, fabos-voice doctor)
NO_AUDIO_SESSION = "No audio session: PipeWire is not running for this user, so nothing can record or play."
NO_SESSION_ALSA_ONLY = "No audio session; using the sound card directly (one program at a time)."
NO_SOURCE = "No microphone in the audio session: there is no input device to record from."
SOURCE_IS_MONITOR = "The default input is a monitor of the speakers, not a microphone."
NO_SINK = "No output device in the audio session: there is nothing to play sound through."
NO_RECORDER = "No recorder is installed (pw-record, parec or arecord)."
MIC_MUTED = "The microphone is muted. Unmute it in the volume applet and try again."
MIC_VOLUME_ZERO = "The microphone volume is at zero. Raise it in the volume applet and try again."
MIC_SILENT = "The microphone is muted or silent: it sent only zeros. Check the input device and its level in the volume applet."
SAY_TEST = "Namaste, this is the Fab OS voice. If you can hear this clearly, speech is working."
DOCTOR_TTS_LINE = "Fab voice check."
DOCTOR_HINTS = {
    "audio-session": "Log in to the desktop (PipeWire starts with your session), or run: systemctl --user start pipewire pipewire-pulse wireplumber",
    "default-source": "Plug in or enable a microphone and pick it as the input device in the volume applet (or: pactl set-default-source NAME)",
    "capture": "Check the input device and its level in the volume applet; unmute with: pactl set-source-mute @DEFAULT_SOURCE@ 0",
    "speech-to-text": "whisper.cpp and ggml-tiny.en.bin ship in /usr/share/fabos/voice with Fab OS; if it says low memory, close some apps (it needs about 600 MB free)",
    "wake-word": "Install pocketsphinx and pocketsphinx-en-us; a custom voice.wake_word must use words from the shipped dictionary",
    "default-sink": "Pick an output device in the volume applet (or: pactl set-default-sink NAME)",
    "text-to-speech": "espeak-ng and pipewire-bin (pw-play) ship with Fab OS; check the output device and its volume in the volume applet",
    "agent": "Start the Fab agent: systemctl --user start fabos-agent (Hey Fab tasks and the cloud voice need it)",
    "listener": "Turn the wake word on: fabos-voice wake on (starts fabos-voiced.service)",
    "settings": "Start the Fab agent to read and change voice.* settings: fabos settings voice.enabled true",
}

# --------------------------------------------------------------------------- display names for system apps
# The agent launches programs by their command name; the user hears the Fab OS product name instead.
APP_NAMES = {
    "dolphin": "Fab Files", "konsole": "Fab Terminal", "kate": "Fab Editor", "kwrite": "Fab Editor",
    "plasma-discover": "Fab Software", "discover": "Fab Software", "gwenview": "Fab Photos", "okular": "Fab Documents",
    "kcalc": "Fab Calculator", "spectacle": "Fab Screenshot", "plasma-systemmonitor": "Fab Monitor",
    "kinfocenter": "Fab System Info", "systemsettings": "Fab Settings", "kwalletmanager6": "Fab Wallet", "ark": "Fab Archives",
    "fabos-command-center": "Fab AI Controls", "fabos-updates": "Fab Updates", "fabos-feedback": "Fab Feedback",
    "firefox": "Firefox", "libreoffice": "LibreOffice", "libreoffice --writer": "LibreOffice Writer",
    "libreoffice --calc": "LibreOffice Calc", "libreoffice --impress": "LibreOffice Impress",
    "vlc": "VLC", "kweather": "Weather", "xdg-open": "the default app",
}


def app_name(app):
    a = (app or "").strip()
    base = os.path.basename(a.split()[0]) if a else ""
    return APP_NAMES.get(a) or APP_NAMES.get(base) or (base or "the app")


def _name(path):
    p = (path or "").rstrip("/")
    return os.path.basename(p) or p or "that file"


def _host(url):
    try:
        return urlparse(url or "").hostname or (url or "the page")
    except Exception:
        return "the page"


def _short_cmd(cmd, n=60):
    c = " ".join((cmd or "").split())
    return c if len(c) <= n else c[: n - 1] + "…"


# --------------------------------------------------------------------------- per-tool narration
# One entry per agent tool (tests assert the table covers fabos_agentd.TOOLS). Templates are callables so
# each can look at the step input; keep them to a single natural sentence.
NARRATION = {
    "generate_image": lambda i: "Generating the image now.",
    "run_shell": lambda i: ("Running a system command as administrator now." if i.get("as_root")
                            else "Running a command for you now."),
    "read_file": lambda i: "Reading %s now." % _name(i.get("path")),
    "write_file": lambda i: ("Adding to %s now." if i.get("append") else "Writing %s now.") % _name(i.get("path")),
    "list_dir": lambda i: "Having a look inside %s." % _name(i.get("path")),
    "open_app": lambda i: "Opening %s for you now." % app_name(i.get("app")),
    "type_text": lambda i: "Typing that in for you.",
    "send_email": lambda i: "Sending the email to %s now." % (i.get("to") or "them"),
    "check_email": lambda i: "Checking your inbox now.",
    "schedule_watch": lambda i: "I will keep a watch on this in the background and tell you when it happens.",
    "web_fetch": lambda i: "Fetching %s now." % _host(i.get("url")),
    "notify_user": lambda i: "Sending you a quick notification.",
    "ask_user": lambda i: "I need to ask you something.",
    "list_apps": lambda i: "Checking which applications are installed.",
}

NARRATION_DONE = {
    "generate_image": lambda i: "Done, the image is saved in your Pictures folder.",
    "run_shell": lambda i: "Done, the command has finished.",
    "read_file": lambda i: "Done, I have read %s." % _name(i.get("path")),
    "write_file": lambda i: "Done, %s is saved." % _name(i.get("path")),
    "list_dir": lambda i: "Done, I have the list.",
    "open_app": lambda i: "Done, I have opened %s for you." % app_name(i.get("app")),
    "type_text": lambda i: "Done, that is typed in.",
    "send_email": lambda i: "Done, the email is on its way to %s." % (i.get("to") or "them"),
    "check_email": lambda i: "Done, I have checked your inbox.",
    "schedule_watch": lambda i: "The watch is set.",
    "web_fetch": lambda i: "Done, I have the page.",
    "notify_user": lambda i: "Notification sent.",
    "ask_user": lambda i: "Thanks for the answer.",
    "list_apps": lambda i: "Done, I have the list of applications.",
}

GENERIC_NARRATION = "Working on the next step now."
GENERIC_DONE = "Done with that step."


def _as_dict(inp):
    if isinstance(inp, dict):
        return inp
    try:
        d = json.loads(inp or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def narration(tool, inp=None):
    f = NARRATION.get(tool)
    try:
        return f(_as_dict(inp)) if f else GENERIC_NARRATION
    except Exception:
        return GENERIC_NARRATION


def narration_done(tool, inp=None):
    f = NARRATION_DONE.get(tool)
    try:
        return f(_as_dict(inp)) if f else GENERIC_DONE
    except Exception:
        return GENERIC_DONE


def approval_summary(tool, inp=None, reason="", raw=False):
    """A spoken fragment for PERMISSION: what the agent wants to do, in plain words. The raw command text is spoken only
    when the user has switched on ui.show_raw (owner rule: no raw commands unless asked for)."""
    i = _as_dict(inp)
    if tool == "run_shell":
        s = ("run the command %s" % _short_cmd(i.get("command"))) if raw else "run a command"
        return s + " as administrator" if i.get("as_root") else s
    if tool == "write_file":
        return "write to %s" % _name(i.get("path"))
    if tool == "read_file":
        return "read %s" % _name(i.get("path"))
    if tool == "list_dir":
        return "look inside %s" % _name(i.get("path"))
    if tool == "send_email":
        return "send an email to %s" % (i.get("to") or "someone")
    if tool == "check_email":
        return "read your inbox"
    if tool == "open_app":
        return "open %s" % app_name(i.get("app"))
    if tool == "type_text":
        return "type into the current window"
    if tool == "web_fetch":
        return "fetch a page from %s" % _host(i.get("url"))
    if tool == "schedule_watch":
        return "keep a background watch"
    if tool == "notify_user":
        return "send you a notification"
    if tool == "generate_image":
        return "generate an image"
    if tool == "list_apps":
        return "list your applications"
    if tool == "ask_user":
        return "ask you a question"
    return (reason or ("use %s" % (tool or "a tool").replace("_", " "))).rstrip(".")


# --------------------------------------------------------------------------- yes / no intent
# Spoken answers from Indian users mix English and Hindi; both are understood. Only an ANSWER-SHAPED utterance
# counts: after leading fillers it is at most MAX_ANSWER_TOKENS words, and the decision must lead ("yes please",
# "haan ji kar do", "no, not now"). A sentence that merely contains a yes-word ("the weather is fine today",
# "hmm, that is a fine question, let me think") is None and the daemon waits for the decision on screen. Rules are
# asymmetric because doing nothing is always the safe choice: a refusal that LEADS counts at any length, a refusal
# anywhere in a short answer wins ("yes, wait" -> deny), and an expression of doubt is never a yes.
MAX_ANSWER_TOKENS = 4
FILLERS = {"please", "hmm", "um", "uh", "oh", "well", "so", "hey", "fab", "ji", "bhai", "yaar", "just", "then", "now",
           "it", "that", "this", "the", "abhi", "toh", "to"}
DENY_WORDS = {"no", "nope", "nah", "not", "dont", "don't", "never", "cancel", "stop", "wait", "hold", "nahi", "nahin",
              "mat", "ruko", "rukho", "deny", "denied", "refuse", "abort", "later", "skip", "negative"}
# strong yes-words: enough on their own or up front ("yes", "okay go ahead", "haan karo")
APPROVE_WORDS = {"yes", "yeah", "yep", "yup", "ok", "okay", "sure", "proceed", "approve", "approved", "haan", "han", "haa",
                 "theek", "thik", "karo", "kardo", "chalo", "bilkul", "zaroor", "confirm", "confirmed", "affirmative"}
# weak yes-words: only when the WHOLE answer is made of yes-words and fillers ("fine", "ya ya", "allow it"); never
# when an as_root command is at stake (strict mode) and never inside a longer sentence
APPROVE_WEAK = {"fine", "ya", "continue", "allow", "accept", "ahead", "go", "correct"}
UNSURE_WORDS = {"what", "which", "how", "maybe", "shayad", "explain", "unsure"}
# multi-word idioms folded into one token before the rules run (longest first); "no problem" is a yes, "hold on" a no
IDIOMS = (
    ("go ahead", "yes"), ("do it", "yes"), ("carry on", "yes"), ("theek hai", "yes"), ("thik hai", "yes"), ("kar do", "yes"),
    ("haan ji", "yes"), ("all right", "yes"), ("alright", "yes"), ("that's fine", "yes"), ("that is fine", "yes"),
    ("it's fine", "yes"), ("its fine", "yes"), ("sounds good", "yes"), ("go on", "yes"), ("please do", "yes"),
    ("no problem", "yes"), ("no problems", "yes"), ("no issue", "yes"), ("no issues", "yes"), ("no worries", "yes"),
    ("why not", "yes"), ("koi dikkat nahi", "yes"), ("koi baat nahi", "yes"),
    ("not now", "no"), ("hold on", "no"), ("wait a", "no"), ("no thanks", "no"), ("nahi karo", "no"), ("mat karo", "no"),
    ("ruk jao", "no"), ("do not", "no"), ("never mind", "no"), ("leave it", "no"), ("rehne do", "no"), ("band karo", "no"),
    ("not sure", "unsure"), ("don't know", "unsure"), ("dont know", "unsure"), ("no idea", "unsure"), ("pata nahi", "unsure"),
    ("pata nahin", "unsure"), ("let me think", "unsure"), ("let me see", "unsure"), ("one minute", "unsure"), ("ek minute", "unsure"),
)
# kept for callers/tests that look at the phrase lists
APPROVE_PHRASES = tuple(k for k, v in IDIOMS if v == "yes")
DENY_PHRASES = tuple(k for k, v in IDIOMS if v == "no")
# "stop" said after a wake during a running task: cancel it
STOP_WORDS = {"stop", "cancel", "abort", "ruko", "rukho", "halt", "quit"}
STOP_IDIOMS = ("band karo", "never mind", "leave it", "rehne do", "chhod do", "forget it", "stop it", "stop that", "cancel that", "cancel it")


def _tokens(text):
    return re.findall(r"[a-z']+", (text or "").lower().replace("’", "'"))


def _fold(text):
    """Lower-cased word string with the idioms folded into their one-word meaning."""
    low = " ".join(_tokens(text))
    for idiom, canon in sorted(IDIOMS, key=lambda kv: -len(kv[0])):
        low = re.sub(r"\b%s\b" % re.escape(idiom), canon, low)
    return low


def intent(text, strict=False):
    """'approve' | 'deny' | None from a spoken answer to 'Shall I go ahead?'.
    strict=True (as_root / CRITICAL approvals): only a strong yes-word up front is an approval."""
    toks = _fold(text).split()
    while toks and toks[0] in FILLERS:
        toks.pop(0)
    if not toks:
        return None
    if toks[0] in DENY_WORDS:
        return "deny"                                            # an explicit refusal up front counts at any length
    if len(toks) > MAX_ANSWER_TOKENS or set(toks) & UNSURE_WORDS:
        return None                                              # a sentence or a doubt, not an answer: decide on screen
    if set(toks) & DENY_WORDS:
        return "deny"                                            # "yes, wait" / "okay, stop": the refusal wins
    if toks[0] in APPROVE_WORDS:
        return "approve"
    if strict:
        return None
    rest = [t for t in toks if t not in FILLERS]
    if rest and all(t in APPROVE_WORDS or t in APPROVE_WEAK for t in rest):
        return "approve"                                         # "fine", "ya ya", "allow it", "go ahead please"
    return None


def is_stop(text):
    """True when a short utterance heard after 'Hey Fab' during a running task means 'stop that task'."""
    low = " ".join(_tokens(text))
    for idiom in STOP_IDIOMS:
        low = re.sub(r"\b%s\b" % re.escape(idiom), "stop", low)
    toks = [t for t in low.split() if t not in FILLERS]
    return bool(toks) and len(toks) <= MAX_ANSWER_TOKENS and toks[0] in STOP_WORDS


# --------------------------------------------------------------------------- reply shortening
_SENT = re.compile(r"(?<=[.!?])\s+")


def shorten(text, full=False, sentences=2, max_chars=240):
    t = " ".join((text or "").replace("\r", "").split())
    t = re.sub(r"[*_`#>]+", "", t)                    # markdown noise sounds silly when spoken
    if not t:
        return ""
    if full:
        return t
    parts = _SENT.split(t)
    s = " ".join(parts[:sentences]).strip()
    if len(s) > max_chars:
        s = s[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return s


def error_reason(msg, max_chars=120):
    m = " ".join((msg or "").split())
    m = re.sub(r"^\w*Error:\s*", "", m)
    return (m[: max_chars - 1] + "…") if len(m) > max_chars else (m or "something went wrong")


if __name__ == "__main__":
    for tool in sorted(NARRATION):
        print("%-15s %s" % (tool, narration(tool, {"path": "~/Documents/notes.txt", "app": "dolphin", "to": "priya@example.com", "url": "https://example.org/x"})))
