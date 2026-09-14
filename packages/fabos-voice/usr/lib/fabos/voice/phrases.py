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
  intent(text)                      -> "approve" | "deny" | None from a spoken yes/no answer
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
TIMED_OUT = "This is taking a while. I will keep working on it; you can see the progress in Fab AI Controls."
AI_OFF = "System-Wide AI is off right now. Turn it on in Fab AI Controls and try again."
AGENT_DOWN = "Sorry, the Fab agent is not running right now."
FIRST_RUN_TITLE = "Talk to Fab"
FIRST_RUN_BODY = "Say 'Hey Fab' to talk to me. You can turn this off in Fab AI Controls › Voice."
VOICE_UNAVAILABLE = "Voice is not available on this machine"
NO_MIC = "No microphone found on this computer."
NO_STT = "Speech recognition is not available: no offline model and no cloud provider key."
NO_TTS = "Text to speech is not available on this machine."
LOW_MEMORY = "Not enough free memory for offline speech recognition right now (it needs about 600 MB)."
WAKE_ON = "Okay, I am listening for 'Hey Fab'."
WAKE_OFF = "Okay, I will stop listening for 'Hey Fab'. You can turn it back on any time."
WAKE_UNSURE = "I thought I heard 'Hey Fab', but I am not sure. Say it once more if you need me."

# --------------------------------------------------------------------------- display names for system apps
# The agent launches programs by their command name; the user hears the Fab OS product name instead.
APP_NAMES = {
    "dolphin": "Fab Files", "konsole": "Fab Terminal", "kate": "Fab Editor", "kwrite": "Fab Editor",
    "plasma-discover": "Fab Software", "discover": "Fab Software", "gwenview": "Fab Photos", "okular": "Fab Documents",
    "kcalc": "Fab Calculator", "spectacle": "Fab Screenshot", "plasma-systemmonitor": "Fab Monitor",
    "kinfocenter": "Fab System Info", "systemsettings": "System Settings", "kwalletmanager6": "Fab Wallet",
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


def approval_summary(tool, inp=None, reason=""):
    """A spoken fragment for PERMISSION: what the agent wants to do, in plain words."""
    i = _as_dict(inp)
    if tool == "run_shell":
        s = "run the command %s" % _short_cmd(i.get("command"))
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
    if tool == "list_apps":
        return "list your applications"
    if tool == "ask_user":
        return "ask you a question"
    return (reason or ("use %s" % (tool or "a tool").replace("_", " "))).rstrip(".")


# --------------------------------------------------------------------------- yes / no intent
# Spoken answers from Indian users mix English and Hindi; both are understood. A refusal anywhere in the
# answer wins ("yes, wait" -> deny), because doing nothing is always the safe choice.
DENY_WORDS = {"no", "nope", "nah", "not", "dont", "don't", "never", "cancel", "stop", "wait", "hold", "nahi", "nahin",
              "mat", "ruko", "rukho", "deny", "denied", "refuse", "abort", "later", "skip", "negative"}
APPROVE_WORDS = {"yes", "yeah", "yep", "ya", "yup", "ok", "okay", "sure", "ahead", "proceed", "approve", "approved",
                 "allow", "fine", "haan", "han", "haa", "theek", "thik", "karo", "kardo", "chalo", "confirm",
                 "continue", "affirmative", "accept"}
APPROVE_PHRASES = ("go ahead", "do it", "carry on", "theek hai", "thik hai", "kar do", "haan ji", "ok go", "all right", "alright")
DENY_PHRASES = ("not now", "hold on", "wait a", "no thanks", "nahi karo", "mat karo", "ruk jao", "don't", "do not")


def _tokens(text):
    return re.findall(r"[a-z']+", (text or "").lower().replace("’", "'"))


def intent(text):
    low = " ".join(_tokens(text))
    if not low:
        return None
    words = set(low.split())
    if any(p in low for p in DENY_PHRASES) or words & DENY_WORDS:
        return "deny"
    if any(p in low for p in APPROVE_PHRASES) or words & APPROVE_WORDS:
        return "approve"
    return None


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
