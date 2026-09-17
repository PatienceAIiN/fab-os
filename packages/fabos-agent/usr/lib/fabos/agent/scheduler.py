#!/usr/bin/env python3
"""scheduler.py — the Fab OS schedule: reminders for work, calendar, mail and the day's tasks, set in plain language.

Used by fabos_agentd.py (the `schedule` tool, the /schedule/* endpoints, the reminder loop) and by the Fab AI Controls
Schedule tab. Everything here is deterministic and works offline: the parser below understands English and common
Indian-English phrasings ("tomorrow at 9am call the bank", "every weekday 6.30 pm gym", "meeting with Rohan on 20 Sep 3pm",
"pay rent on the 1st every month", "in 45 minutes", "next Monday morning", "day after tomorrow", "6 baje") against the
user's local time zone. Anything ambiguous (a bare "at 3", "5/6", "next week", a date without a time) is parsed with a
guess AND flagged needs_confirm, so callers create the item only after the user has seen and confirmed the reading;
anything unsupported or impossible (no time at all, "30 Feb", "25:00", "every other day", two times, a time in the past)
fails with a sentence that says why — nothing is guessed silently.

  Items      SQLite table `schedule` in the agent's store: title, when (UTC epoch) + the user's tz name, repeat
             none|daily|weekdays|weekly|monthly, source chat|mail|ui|tool, status pending|done|dismissed|missed,
             remind_before (minutes), notes. Repeating items roll forward after they fire or are done.
  Reminders  SchedulerLoop (a daemon thread) fires a desktop notification (Done · Snooze 10 min · Open) when an item is
             due; items that fell due while the computer was off are marked missed and listed at the next login.
  Login      send_login_summary(): ONE notification "Today: N items" with up to 5 lines and "and N more in Schedule";
             clicking it opens the Schedule tab. fabos-schedule-summary.service (WantedBy=graphical-session.target) runs
             `scheduler.py --login-summary`, which asks the daemon over HTTP; the daemon de-duplicates per login.
  Mail       MailIntake: every 10 minutes, unseen mail in the user's OWN inbox that comes FROM the user's own address
             with a subject starting Remind / Reminder / Schedule is parsed like a chat request. Mail from anyone else is
             never acted on. A reply with the interpretation goes back only when the mail asks for one. Setting
             scheduler.mail_intake (default on when mail is configured).
  Settings   scheduler.timezone (IANA name; empty = the system zone), scheduler.clock "12" | "24" (display + a hint for
             bare "10:30"), scheduler.mail_intake true|false.

CLI:  scheduler.py --parse "text" [--now ISO] [--tz Zone] [--clock 12|24]     scheduler.py --login-summary
      scheduler.py --today      scheduler.py add "text"      scheduler.py list [today|upcoming|done|all]
"""
import calendar, datetime as dt, email.utils, html, json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error

try:
    from zoneinfo import ZoneInfo
except ImportError:                                   # Python < 3.9: not the case on Fab OS, kept so the module imports anywhere
    ZoneInfo = None

APP = "Fab OS"
DESKTOP_ID = "fabos-command-center"
REPEATS = ("none", "daily", "weekdays", "weekly", "monthly")
STATUSES = ("pending", "done", "dismissed", "missed")
SOURCES = ("chat", "mail", "ui", "tool")
DEFAULT_HOUR = 9                                      # a date without a time reads as 9:00 (flagged needs_confirm)
SNOOZE_MIN = 10
MISSED_GRACE_S = 15 * 60                              # due more than this long ago without having fired = missed while the computer was off
ROLL_AFTER_S = 3 * 3600                               # a repeating item that fired and got no answer rolls forward after this
MAIL_INTAKE_EVERY_S = 600
SUMMARY_LINES = 5
SUMMARY_TIMEOUT_MS = 20000                            # the login summary stays 20 s (then it is in the notification history)
DAY_PARTS = {"early morning": (7, 0), "morning": (9, 0), "before work": (8, 0), "first thing": (9, 0), "noon": (12, 0), "midday": (12, 0), "mid-day": (12, 0),
             "lunch": (13, 0), "lunchtime": (13, 0), "lunch time": (13, 0), "after lunch": (14, 0), "afternoon": (15, 0), "evening": (18, 0), "after work": (18, 0),
             "end of day": (18, 0), "end of the day": (18, 0), "eod": (18, 0), "dinner": (20, 0), "dinner time": (20, 0), "dinnertime": (20, 0), "after dinner": (21, 0),
             "night": (21, 0), "tonight": (21, 0), "bedtime": (22, 0), "bed time": (22, 0), "midnight": (0, 0)}
PM_PARTS = {"afternoon", "evening", "night", "tonight", "after lunch", "after work", "end of day", "end of the day", "eod", "dinner", "dinner time", "dinnertime", "after dinner", "bedtime", "bed time", "lunch", "lunchtime", "lunch time", "noon", "midday", "mid-day"}
NIGHT_PARTS = ("night", "tonight", "after dinner", "bedtime", "bed time")
WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1, "wednesday": 2, "wed": 2, "thursday": 3, "thurs": 3, "thur": 3, "thu": 3,
            "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
          "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
             "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "forty five": 45, "sixty": 60, "ninety": 90, "couple of": 2, "couple": 2, "half an": 0.5, "half": 0.5}
WD_RE = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues|tue|wed|thurs|thur|thu|fri|sat|sun)"
MON_RE = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)"
AMPM = r"(a\.?m\.?|p\.?m\.?)"
HOURW = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
FILLER = re.compile(r"\b(?:sometime|some\s+time|someday|maybe|probably|roughly|approximately|approx\.?|ideally|preferably)\b")
CONNECTORS = {"at", "on", "in", "by", "for", "to", "the", "a", "an", "about", "of", "that", "and", "then", "please", "pls", "kindly", "is", "it", "this", "from",
              "around", "@", "-", "—", "–", ":", ",", ";", ".", "sharp", "o'clock", "itself", "also", "again", "till", "until", "with", "regarding", "re", "&", "so",
              "i", "should", "need", "must", "have", "want", "would", "like", "be", "there", "onwards", "yes", "ok", "okay", "na", "yaar", "ya", "hai", "ko", "ke", "ki", "ka",
              "baje", "bajey"}
TRIGGER = re.compile(r"^\s*(?:(?:hey|hi|ok|okay|yo)[,\s]+fab[,!\s]*)?(?:please\s+|pls\s+|kindly\s+|just\s+)?(?:(?:can|could|will|would)\s+you\s+(?:please\s+|pls\s+)?)?"
                     r"(?:remind\s+me(?:\s+(?:to|about|of|that|for|on|regarding)\b)?|set\s+(?:a\s+|an\s+)?(?:reminder|alarm)(?:\s+(?:to|for|about|that|on)\b)?"
                     r"|add\s+(?:a\s+|an\s+)?(?:reminder|task|todo|to-do|event|item|entry|appointment|meeting)(?:\s+(?:to\s+my\s+|on\s+my\s+|in\s+my\s+)(?:schedule|calendar|list))?(?:\s+(?:to|for|about|that)\b|\s*:)?"
                     r"|put\s+(?:this\s+|it\s+|that\s+)?(?:on|in)\s+my\s+(?:schedule|calendar|diary|list)(?:\s+(?:to|for|that)\b|\s*:)?"
                     r"|schedule(?:d)?(?:\s+(?:a|an|the|my|this|for)\b|\s*:)?|reminder(?:\s+(?:to|for|about|on|that)\b|\s*:)?|remind(?:\s+to\b)?|note\s+to\s+self:?|note:?|todo:?|to-do:?|task:?"
                     r"|remember\s+to|don'?t\s+(?:let\s+me\s+)?forget\s+to|i\s+(?:have|need|want)\s+to|i\s+must|i\s+should)\s*[:\-,]?\s*", re.I)
TRAILER = re.compile(r"[\s,]*\b(?:please|pls|plz|thanks|thank you|thx|na|yaar|ok|okay)\b[\s.!?]*$", re.I)


# ----------------------------------------------------------------------------- time zone + formatting
def system_tz_name():
    """The computer's zone name: /etc/timezone, else the /etc/localtime symlink target, else 'UTC'."""
    try:
        with open("/etc/timezone") as f:
            name = f.read().strip()
        if name and ZoneInfo is not None:
            ZoneInfo(name)
            return name
    except Exception:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        m = re.search(r"zoneinfo/(.+)$", target)
        if m and ZoneInfo is not None:
            ZoneInfo(m.group(1))
            return m.group(1)
    except Exception:
        pass
    return "UTC"


def resolve_tz(name):
    """ZoneInfo for a setting value; None when the name is unknown (callers fall back to the system zone)."""
    name = (name or "").strip()
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def user_tz(store=None):
    """The user's zone: setting scheduler.timezone when valid, else the system zone."""
    z = resolve_tz(store.setting("scheduler.timezone", "") if store is not None else "")
    return z or resolve_tz(system_tz_name()) or dt.timezone.utc


def tz_name(tz):
    return getattr(tz, "key", None) or str(tz)


def user_clock(store=None):
    v = (store.setting("scheduler.clock", "") if store is not None else "") or ""
    return "12" if str(v).strip() == "12" else "24"


def fmt_time(d, clock="24"):
    if clock == "12":
        h = d.hour % 12 or 12
        return "%d:%02d %s" % (h, d.minute, "AM" if d.hour < 12 else "PM")
    return "%02d:%02d" % (d.hour, d.minute)


def fmt_day(d, now=None):
    """'Today', 'Tomorrow', 'Fri 18 Sep' or 'Mon 1 Feb 2027' — relative to now (same zone)."""
    if now is not None:
        delta = (d.date() - now.date()).days
        if delta == 0:
            return "Today"
        if delta == 1:
            return "Tomorrow"
        if delta == -1:
            return "Yesterday"
    s = d.strftime("%a %d %b").replace(" 0", " ")
    if now is None or d.year != now.year:
        s += " " + str(d.year)
    return s


def fmt_when(d, now=None, clock="24"):
    return "%s, %s" % (fmt_day(d, now), fmt_time(d, clock))


def repeat_text(repeat):
    return {"daily": "every day", "weekdays": "every weekday", "weekly": "every week", "monthly": "every month"}.get(repeat, "")


def local(epoch, tz):
    return dt.datetime.fromtimestamp(float(epoch), tz)


# ----------------------------------------------------------------------------- the parser
def _normalise(text):
    s = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-").replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _words_num(tok):
    tok = tok.strip().lower()
    if tok.isdigit():
        return int(tok)
    return NUM_WORDS.get(tok)


class _Work:
    """The text under analysis: `low` is matched by the regexes, matched spans are blanked (\\x00) in `chars` (original
    casing, for the title) and replaced by spaces in `low` so later patterns do not see them again."""

    def __init__(self, text):
        self.norm = _normalise(text)
        self.low = self.norm.lower()
        if len(self.low) != len(self.norm):          # a rare non-ASCII casing change: build the title from the lower-cased copy
            self.norm = self.low
        self.chars = list(self.norm)

    def blank(self, start, end):
        for i in range(start, end):
            self.chars[i] = "\x00"
        self.low = self.low[:start] + " " * (end - start) + self.low[end:]

    def take(self, pattern, flags=0):
        """First match of pattern in the remaining text: blank it and return the match (or None)."""
        m = re.search(pattern, self.low, flags)
        if m:
            self.blank(m.start(), m.end())
        return m

    def take_all(self, pattern, flags=0):
        out = []
        while True:
            m = re.search(pattern, self.low, flags)
            if not m:
                return out
            self.blank(m.start(), m.end())
            out.append(m)

    def title(self):
        pieces = [p for p in "".join(self.chars).split("\x00")]
        words_out = []
        for p in pieces:
            words = [w for w in re.split(r"\s+", p.strip()) if w]
            while words and words[0].lower().strip("',.;:!-") in CONNECTORS:
                words.pop(0)
            while words and words[-1].lower().strip("',.;:!-") in CONNECTORS:
                words.pop()
            if words:
                words_out.append(" ".join(words))
        t = " ".join(words_out)
        t = re.sub(r"\s+([,.;:!?])", r"\1", t)
        t = t.strip(" ,.;:!-\"'")
        t = re.sub(r"\s{2,}", " ", t)
        if t:
            t = t[0].upper() + t[1:]
        return t


def _fail(res, why):
    res.update(ok=False, error=why, needs_confirm=False, confidence="none", when=None, when_epoch=None)
    res["interpretation"] = why
    return res


def _valid_time(h, m):
    return 0 <= h <= 23 and 0 <= m <= 59


def _add_months(d, n):
    y, mth = d.year, d.month + n
    while mth > 12:
        y, mth = y + 1, mth - 12
    while mth < 1:
        y, mth = y - 1, mth + 12
    return dt.date(y, mth, min(d.day, calendar.monthrange(y, mth)[1]))


def next_occurrence(when, repeat, after, weekday=None, dom=None):
    """The first occurrence of a repeating item strictly after `after` (both aware datetimes in the user's zone), keeping
    the wall-clock time of `when`. weekdays skips Saturday/Sunday; weekly keeps `weekday` (default: when's); monthly
    keeps `dom` (default: when's day; months without that day use their last day)."""
    tz = when.tzinfo
    t = when.timetz().replace(tzinfo=None)
    if repeat == "none":
        return when

    def at(date):
        return dt.datetime.combine(date, t, tzinfo=tz)
    d = after.date()
    if repeat == "daily":
        c = at(d)
        return c if c > after else at(d + dt.timedelta(days=1))
    if repeat == "weekdays":
        c = at(d)
        if c <= after:
            d += dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        return at(d)
    if repeat == "weekly":
        wd = when.weekday() if weekday is None else weekday
        d = d + dt.timedelta(days=(wd - d.weekday()) % 7)
        c = at(d)
        return c if c > after else at(d + dt.timedelta(days=7))
    if repeat == "monthly":
        day = when.day if dom is None else dom
        y, m = d.year, d.month
        for _ in range(3):
            c = at(dt.date(y, m, min(day, calendar.monthrange(y, m)[1])))
            if c > after:
                return c
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return when


def parse(text, now=None, tz=None, clock="24"):
    """Read one reminder from plain language. Returns a dict:
       ok, title, when (aware datetime, user zone) / when_epoch, tz, repeat, confidence exact|assumed|ambiguous|none,
       needs_confirm, assumptions [sentences], error, interpretation (one line for the user), text (the input)."""
    if tz is None:
        tz = now.tzinfo if (now is not None and now.tzinfo) else (resolve_tz(system_tz_name()) or dt.timezone.utc)
    now = (now or dt.datetime.now(tz)).astimezone(tz)
    res = {"ok": False, "text": str(text or ""), "title": "", "when": None, "when_epoch": None, "tz": tz_name(tz), "repeat": "none", "confidence": "none",
           "needs_confirm": False, "assumptions": [], "error": None, "interpretation": "", "remind_before": 0}
    w = _Work(text)
    if not w.low.strip():
        return _fail(res, "Tell me what to remind you of and when.")
    m = TRIGGER.match(w.low)
    if m and m.end() > 0:
        w.blank(0, m.end())
    m = TRAILER.search(w.low)
    if m:
        w.blank(m.start(), m.end())
    assumptions = []
    w.take_all(FILLER.pattern)
    # ---- "10 minutes before" -> remind_before
    remind_before = 0
    m = w.take(r"\b(\d+|a|an|one|two|five|ten|fifteen|twenty|thirty|forty[- ]five|sixty|half\s+an)\s*(minutes?|mins?|hours?|hrs?)\s+(?:before|earlier|in\s+advance|prior|ahead|beforehand)\b")
    if m:
        q = _words_num(m.group(1)) or 0
        remind_before = int(q * (60 if m.group(2).startswith("h") else 1))
    # ---- repeat -----------------------------------------------------------------------------------------------------
    bad = w.take(r"\b(?:every\s+(?:other|alternate|second|2nd|third|two|three|four|five|\d+)\s+(?:days?|weeks?|months?|years?|hours?|minutes?|mins?|" + WD_RE + r"s?)"
                 r"|every\s+(?:hour|weekend|year|sat(?:urday)?\s*(?:and|&)\s*sun(?:day)?|" + WD_RE + r"s?\s*(?:,|and|&)\s*" + WD_RE + r"s?)"
                 r"|hourly|yearly|annually|fortnightly|every\s+fortnight|twice\s+(?:a|every)\s+(?:day|week|month)|(?:on\s+)?alternate\s+days|every\s+few\s+\w+)\b")
    if bad:
        return _fail(res, "Repeating '%s' is not supported yet. I can repeat every day, every weekday, every week or every month." % w.norm[bad.start():bad.end()].strip())
    repeat, rep_weekday, rep_dom, rep_part = "none", None, None, None
    m = w.take(r"\b(?:every\s?day|everyday|daily|each\s+day|every\s+single\s+day|all\s+days|once\s+a\s+day|(?:on\s+)?a\s+daily\s+basis)\b")
    if m:
        repeat = "daily"
    m = w.take(r"\bevery\s+(early\s+morning|morning|afternoon|evening|night)\b")
    if m:
        repeat, rep_part = "daily", m.group(1)
    m = w.take(r"\b(?:every\s+weekday|on\s+weekdays|weekdays|every\s+working\s+day|(?:on\s+)?working\s+days|all\s+weekdays|mon(?:day)?\s*(?:to|-|through|till)\s*fri(?:day)?)\b")
    if m:
        repeat = "weekdays"
    m = w.take(r"\b(?:every|each)\s+" + WD_RE + r"s?\b")
    if m:
        repeat, rep_weekday = "weekly", WEEKDAYS[m.group(1)]
    m = w.take(r"\b(?:weekly|every\s+week|each\s+week|once\s+a\s+week|(?:on\s+)?a\s+weekly\s+basis)\b")
    if m:
        repeat = "weekly" if repeat == "none" else repeat
    m = w.take(r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?:every|each)\s+month\b|\bevery\s+(?:month\s+on\s+the\s+)?(\d{1,2})(?:st|nd|rd|th)\b(?:\s+of\s+(?:the|every|each)\s+month)?")
    if m:
        repeat, rep_dom = "monthly", int(m.group(1) or m.group(2))
    m = w.take(r"\b(?:every\s+month\s+end|(?:at\s+)?(?:the\s+)?end\s+of\s+(?:every|each)\s+month|(?:on\s+)?(?:the\s+)?last\s+day\s+of\s+(?:every|each|the)\s+month)\b")
    if m:
        repeat, rep_dom = "monthly", 31
    m = w.take(r"\b(?:every\s+month|each\s+month|monthly|once\s+a\s+month|(?:on\s+)?a\s+monthly\s+basis)\b")
    if m:
        repeat = "monthly" if repeat == "none" else repeat
    if rep_dom is not None and not 1 <= rep_dom <= 31:
        return _fail(res, "There is no %dth day in a month." % rep_dom)
    # ---- relative ("in 45 minutes", "after 2 hours", "3 days from now") -----------------------------------------------
    rel = None
    m = w.take(r"\b(?:in|after|within)\s+(?:the\s+next\s+|another\s+)?(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|forty|forty[- ]five|sixty|ninety|half\s+an|half|couple\s+of|couple|few|some)"
               r"\s*(minutes?|mins?|min|m|hours?|hrs?|hr|h|days?|weeks?|wks?|wk|months?)\b(?:\s+(?:from\s+now|later))?"
               r"|\b(\d+)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|months?)\s+(?:from\s+now|later)\b")
    if m:
        qty_s, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        qty = _words_num(qty_s)
        if qty is None:
            return _fail(res, "How many %s? Say a number, like 'in 20 minutes'." % ("minutes" if unit.startswith("m") and not unit.startswith("mo") else unit.rstrip("s") + "s"))
        if qty <= 0:
            return _fail(res, "'in %s %s' is not a time in the future." % (qty_s, unit))
        u = unit.rstrip("s")
        if u in ("minute", "min", "m"):
            rel = ("seconds", int(qty * 60))
        elif u in ("hour", "hr", "h"):
            rel = ("seconds", int(qty * 3600))
        elif u == "day":
            rel = ("days", int(qty))
        elif u in ("week", "wk"):
            rel = ("days", int(qty) * 7)
        else:
            rel = ("months", int(qty))
        if qty != int(qty) and rel[0] != "seconds":
            return _fail(res, "'%s %s' is not a whole number of days." % (qty_s, unit))
    # ---- dates ----------------------------------------------------------------------------------------------------------
    date, date_kind, weekday, wd_qual, dom, part_from_date = None, None, None, None, None, None
    date_confirm = False
    m = w.take(r"\b(\d{4})-(\d{2})-(\d{2})\b")
    if m:
        try:
            date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return _fail(res, "%s is not a real date." % m.group(0))
        date_kind = "explicit"
    if date is None:
        m = w.take(r"\b(\d{1,2})([/-])(\d{1,2})(?:\2(\d{2,4}))?\b|\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")
        if m:
            a, b, y = (m.group(1), m.group(3), m.group(4)) if m.group(1) else (m.group(5), m.group(6), m.group(7))
            a, b = int(a), int(b)
            year = int(y) if y else None
            if year is not None and year < 100:
                year += 2000
            if a > 12 and b <= 12:
                d_, mo = a, b
            elif b > 12 and a <= 12:
                d_, mo = b, a
                assumptions.append("%d/%d read as month/day" % (a, b))
            elif a <= 12 and b <= 12:
                d_, mo = a, b
                if a != b:
                    assumptions.append("%d/%d read as day/month (%d %s)" % (a, b, a, calendar.month_abbr[b]))
                    date_confirm = True
            else:
                return _fail(res, "%s is not a real date." % m.group(0))
            yy = year or now.year
            try:
                date = dt.date(yy, mo, d_)
            except ValueError:
                return _fail(res, "%d %s is not a real date." % (d_, calendar.month_name[mo] if 1 <= mo <= 12 else "month " + str(mo)))
            if year is None and date < now.date():
                date = dt.date(yy + 1, mo, d_)
                assumptions.append("%d %s has passed this year, so %d" % (d_, calendar.month_abbr[mo], yy + 1))
            date_kind = "explicit"
    if date is None:
        m = w.take(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?" + MON_RE + r"\b\.?(?:,?\s*(\d{4}))?|\b" + MON_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?")
        if m:
            if m.group(1):
                d_, mon, y = int(m.group(1)), MONTHS[m.group(2)], m.group(3)
            else:
                d_, mon, y = int(m.group(5)), MONTHS[m.group(4)], m.group(6)
            yy = int(y) if y else now.year
            try:
                date = dt.date(yy, mon, d_)
            except ValueError:
                return _fail(res, "%d %s is not a real date." % (d_, calendar.month_name[mon]))
            if not y and date < now.date():
                date = dt.date(yy + 1, mon, d_)
                assumptions.append("%d %s has passed this year, so %d" % (d_, calendar.month_abbr[mon], yy + 1))
            date_kind = "explicit"
    if w.take(r"\byesterday\b|\blast\s+(?:week|month|night|" + WD_RE + r")\b"):
        return _fail(res, "That is in the past — I can only remind you about times still to come.")
    m = w.take(r"\b(day\s*[- ]?after\s*[- ]?(?:tomorrow|tmrw|tmr|tom)|tomorrow|tmrw|tmr|today)\s+(early\s+morning|morning|afternoon|evening|night)\b")
    if m:                                                # "today evening 7 pm", "tomorrow morning": the day part belongs to the date
        if date is None:
            date = now.date() + dt.timedelta(days=2 if m.group(1).startswith("day") else (0 if m.group(1) == "today" else 1))
            date_kind = "relative"
        part_from_date = re.sub(r"\s+", " ", m.group(2))
    m = w.take(r"\bday\s*[- ]?after\s*[- ]?(?:tomorrow|tmrw|tmr|tom)\b|\bday\s+after\b")
    if m and date is None:
        date, date_kind = now.date() + dt.timedelta(days=2), "relative"
    m = w.take(r"\b(?:tomorrow|tmrw|tmr|tommorow|tommorrow|tomorow|tom)\b")
    if m and date is None:
        date, date_kind = now.date() + dt.timedelta(days=1), "relative"
    m = w.take(r"\btonight\b|\bthis\s+(early\s+morning|morning|afternoon|evening|noon|night)\b|\btoday\s+(?:itself|only)\b|\btoday\b")
    if m:
        if date is None:
            date, date_kind = now.date(), "relative"
        part_from_date = "night" if m.group(0).startswith("tonight") else (m.group(1) or part_from_date)
    m = w.take(r"\b(?:(next|this|coming|upcoming|on)\s+)?" + WD_RE + r"(?:\s+(early\s+morning|morning|afternoon|evening|night))?\b")
    if m and date is None:
        weekday, wd_qual, date_kind = WEEKDAYS[m.group(2)], m.group(1) or "", "weekday"
        part_from_date = re.sub(r"\s+", " ", m.group(3)) if m.group(3) else part_from_date
    elif m and repeat == "weekly" and rep_weekday is None:
        rep_weekday = WEEKDAYS[m.group(2)]
    m = w.take(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b|\bon\s+(\d{1,2})(?:st|nd|rd|th)\b")
    if m and date is None and weekday is None:
        dom = int(m.group(1) or m.group(2))
        if not 1 <= dom <= 31:
            return _fail(res, "There is no %dth day in a month." % dom)
        date_kind = "dom"
    if date is None and weekday is None and dom is None:
        m = w.take(r"\bnext\s+week\b")
        if m:
            date = now.date() + dt.timedelta(days=7 - now.weekday())
            date_kind, date_confirm = "vague", True
            assumptions.append("'next week' read as Monday %s" % date.strftime("%d %b").lstrip("0"))
        m = w.take(r"\bnext\s+month\b")
        if m and date is None:
            date = _add_months(now.date().replace(day=1), 1)
            date_kind, date_confirm = "vague", True
            assumptions.append("'next month' read as the 1st")
        m = w.take(r"\b(?:this|next|coming|the)\s+weekend\b|\bweekend\b")
        if m and date is None:
            d_ = now.date() + dt.timedelta(days=(5 - now.weekday()) % 7 or 7)
            if "next" in m.group(0) and (5 - now.weekday()) % 7 in (0, 1, 2):
                d_ += dt.timedelta(days=7)
            date, date_kind, date_confirm = d_, "vague", True
            assumptions.append("'weekend' read as Saturday %s" % d_.strftime("%d %b").lstrip("0"))
        m = w.take(r"\b(?:at\s+|by\s+)?(?:the\s+)?end\s+of\s+(?:the\s+|this\s+)?month\b|\bmonth\s+end\b")
        if m and date is None:
            date = dt.date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])
            date_kind = "explicit"
        m = w.take(r"\b(?:at\s+|by\s+)?(?:the\s+)?end\s+of\s+(?:the\s+|this\s+)?week\b")
        if m and date is None:
            weekday, wd_qual, date_kind, date_confirm = 4, "", "weekday", True
            assumptions.append("'end of the week' read as Friday")
    # ---- times ----------------------------------------------------------------------------------------------------------
    times = []                                       # (hour, minute, kind exact|bare, raw)
    for m in w.take_all(r"\b(\d{1,2})[:.](\d{2})\s*(?:" + AMPM + r"|hrs\b|hours\b|h\b)?(?:\s+sharp)?"):
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
        raw = m.group(0).strip()
        if ap:
            if not 1 <= h <= 12:
                return _fail(res, "%s is not a valid time." % raw)
            h = h % 12 + (12 if ap.startswith("p") else 0)
            times.append((h, mi, "exact", raw))
        else:
            if not _valid_time(h, mi):
                return _fail(res, "%s is not a valid time." % raw)
            padded = m.group(1).startswith("0") or h == 0 or h > 12 or re.search(r"hrs|hours|h$", raw)
            times.append((h, mi, "exact" if (padded or (clock == "24" and h >= 7)) else "bare", raw))
    for m in w.take_all(r"\b(\d{1,2})\s*" + AMPM + r"\b(?:\s+sharp)?"):
        h, ap = int(m.group(1)), m.group(2)
        if not 1 <= h <= 12:
            return _fail(res, "%s is not a valid time." % m.group(0).strip())
        times.append((h % 12 + (12 if ap.startswith("p") else 0), 0, "exact", m.group(0).strip()))
    for m in w.take_all(r"\b(half|quarter)\s+past\s+" + HOURW + r"\b(?:\s*" + AMPM + r")?|\bquarter\s+to\s+" + HOURW + r"\b(?:\s*" + AMPM + r")?"):
        if m.group(1):
            h, mi, ap = _words_num(m.group(2)), 30 if m.group(1) == "half" else 15, m.group(3)
        else:
            h, mi, ap = _words_num(m.group(4)) - 1, 45, m.group(5)
            if h < 0:
                h = 23
        if ap:
            h = (h % 12) + (12 if ap.startswith("p") else 0)
        if not _valid_time(h, mi):
            return _fail(res, "%s is not a valid time." % m.group(0).strip())
        times.append((h, mi, "exact" if ap else "bare", m.group(0).strip()))
    for m in w.take_all(r"\b" + HOURW + r"\s*(?:o'?\s?clock|baje|bajey|bje)\b(?:\s*" + AMPM + r")?"):
        h, ap = _words_num(m.group(1)), m.group(2)
        if not 1 <= h <= 12 and not (ap is None and h <= 23):
            return _fail(res, "%s is not a valid time." % m.group(0).strip())
        times.append(((h % 12 + (12 if ap.startswith("p") else 0)) if ap else h, 0, "exact" if ap else "bare", m.group(0).strip()))
    if w.take(r"\b(?:12\s+)?(?:noon|midday|mid-day)\b"):
        times.append((12, 0, "exact", "noon"))
    if w.take(r"\bmidnight\b"):
        times.append((0, 0, "exact", "midnight"))
    part = None
    m = re.search(r"\b(in\s+the\s+|this\s+|at\s+|by\s+|around\s+|towards\s+|before\s+|the\s+)(early\s+morning|morning|afternoon|evening|night|lunch|dinner|bed\s?time|end\s+of\s+(?:the\s+)?day)\b"
                  r"|\b()(early\s+morning|morning|afternoon|evening|first\s+thing|before\s+work|after\s+work|after\s+lunch|after\s+dinner|lunch\s?time|dinner\s?time|bed\s?time|end\s+of\s+(?:the\s+)?day|eod)\b", w.low)
    if m:
        prefixed = bool(m.group(1))
        part = re.sub(r"\s+", " ", m.group(2) or m.group(4))
        has_exact = any(k == "exact" for _h, _mi, k, _r in times)
        if prefixed or not has_exact:              # "team lunch at 1pm" keeps its title; "at lunch" / "in the evening" are times
            w.blank(m.start(), m.end())
    part = part or part_from_date or rep_part
    for m in w.take_all(r"\b(?:at|@|by|around|about|for|till|until|before)\s+(\d{1,2})\b(?!\s*(?:[:./-]\d|" + AMPM + r"|o'?clock|min|hour|hr|day|week|month|year|st|nd|rd|th|%|km|kg|people|persons|items|of|times|pages|lakh|crore|rs|₹|\$))(?:\s+sharp)?"):
        h = int(m.group(1))
        if not 0 <= h <= 23:
            return _fail(res, "%s is not a valid time." % m.group(0).strip())
        times.append((h, 0, "bare", m.group(0).strip()))
    if len(times) > 1:
        dedup = {(h, mi) for h, mi, _k, _r in times}
        if len(dedup) > 1:
            return _fail(res, "I found two times (%s and %s). One reminder has one time — which one?" % (times[0][3], times[1][3]))
        times = [times[0]]
    hour = minute = None
    time_kind = None
    if times:
        hour, minute, time_kind, raw = times[0]
        if time_kind == "bare":
            if hour > 12:
                time_kind = "exact"
            elif part:
                if part in PM_PARTS and hour < 12 and not (part in ("night", "tonight", "after dinner", "bedtime", "bed time") and hour == 12):
                    hour += 12
                elif part in ("night", "tonight", "after dinner", "bedtime", "bed time") and hour == 12:
                    hour = 0
                time_kind = "exact"
            else:
                guess_pm = hour in (1, 2, 3, 4, 5, 6) or hour == 12
                if hour == 12:
                    pass
                elif guess_pm:
                    hour += 12
                assumptions.append("'%s' read as %s" % (raw, fmt_time(dt.datetime(2000, 1, 1, hour, minute), clock)))
    elif part:
        hour, minute = DAY_PARTS.get(part, (DEFAULT_HOUR, 0))
        time_kind = "part"
        if part not in ("noon", "midday", "mid-day", "midnight"):
            assumptions.append("'%s' read as %s" % (part, fmt_time(dt.datetime(2000, 1, 1, hour, minute), clock)))
    # ---- combine --------------------------------------------------------------------------------------------------------
    needs_confirm = False
    when = None
    if rel and rel[0] == "seconds":
        when = now + dt.timedelta(seconds=rel[1])
        when = when.replace(second=0, microsecond=0) if rel[1] >= 300 else when.replace(microsecond=0)
        confidence = "exact"
    else:
        if rel and rel[0] == "days":
            date, date_kind = now.date() + dt.timedelta(days=rel[1]), "relative"
        elif rel and rel[0] == "months":
            date, date_kind = _add_months(now.date(), rel[1]), "relative"
        if hour is None:
            if rel and rel[0] == "days" and rel[1] % 7 == 0 and "week" in w.norm.lower() and date_kind == "relative":
                hour, minute = now.hour, now.minute
                time_kind = "part"
                assumptions.append("same time of day as now")
            elif date is not None or weekday is not None or dom is not None or repeat != "none":
                hour, minute, time_kind = DEFAULT_HOUR, 0, "default"
                assumptions.append("no time given, so %s" % fmt_time(dt.datetime(2000, 1, 1, DEFAULT_HOUR), clock))
                needs_confirm = True
            else:
                return _fail(res, "When should I remind you? Give a time such as 'tomorrow at 9am', 'on 20 Sep 3pm' or 'in 45 minutes'.")
        t = dt.time(hour, minute)
        if date is None and weekday is not None:
            d = (weekday - now.weekday()) % 7
            if d == 0 and (wd_qual == "next" or dt.datetime.combine(now.date(), t, tzinfo=tz) <= now):
                d = 7
            date = now.date() + dt.timedelta(days=d)
        if date is None and dom is not None:
            y, mo = now.year, now.month
            for _ in range(13):
                if dom <= calendar.monthrange(y, mo)[1]:
                    c = dt.datetime.combine(dt.date(y, mo, dom), t, tzinfo=tz)
                    if c > now or repeat == "monthly":
                        date = c.date()
                        break
                y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
            if date is None:
                return _fail(res, "There is no %dth day in a month." % dom)
        if date is None:
            date = now.date()
        if hour == 0 and minute == 0 and part in NIGHT_PARTS:   # "at 12 tonight" / "tomorrow night at midnight": the midnight that ENDS that night
            date += dt.timedelta(days=1)
            assumptions.append("midnight at the end of that night")
        when = dt.datetime.combine(date, t, tzinfo=tz)
        if when <= now:
            if date_kind is None and repeat == "none":
                when = when + dt.timedelta(days=1)
                assumptions.append("%s has passed today, so tomorrow" % fmt_time(when, clock))
            elif repeat == "none":
                return _fail(res, "%s has already passed." % fmt_when(when, now, clock))
        confidence = "exact" if time_kind == "exact" and not assumptions else "assumed"
    if repeat != "none":
        anchor = when
        if repeat == "monthly" and rep_dom is not None:
            anchor = dt.datetime.combine(dt.date(now.year, now.month, min(rep_dom, calendar.monthrange(now.year, now.month)[1])), when.timetz().replace(tzinfo=None), tzinfo=tz)
        when = next_occurrence(anchor, repeat, max(now, when - dt.timedelta(seconds=1)) if when > now else now,
                               weekday=rep_weekday if rep_weekday is not None else (weekday if weekday is not None else None), dom=rep_dom)
    if time_kind == "bare" or date_confirm:
        needs_confirm = True
    if needs_confirm:
        confidence = "ambiguous"
    title = w.title() or "Reminder"
    res.update(ok=True, title=title, when=when, when_epoch=when.timestamp(), repeat=repeat, confidence=confidence, needs_confirm=needs_confirm, assumptions=assumptions,
               rep_weekday=rep_weekday, rep_dom=rep_dom, remind_before=remind_before)
    res["interpretation"] = interpretation(res, now, clock)
    return res


def interpretation(p, now=None, clock="24"):
    """'Fri 18 Sep, 09:00 — Call the bank · every weekday · no time given, so 09:00'."""
    when = p.get("when")
    if when is None and p.get("when_epoch"):
        when = local(p["when_epoch"], resolve_tz(p.get("tz")) or dt.timezone.utc)
    if when is None:
        return p.get("error") or ""
    s = "%s — %s" % (fmt_when(when, now, clock), p.get("title") or "Reminder")
    if p.get("repeat", "none") != "none":
        s += " · " + repeat_text(p["repeat"])
    if p.get("remind_before"):
        s += " · reminder %d min before" % int(p["remind_before"])
    if p.get("assumptions"):
        s += " · " + "; ".join(p["assumptions"])
    return s


def is_confirmation(text):
    return bool(re.match(r"^\s*(?:yes|yeah|yep|ya|yup|y|ok|okay|sure|correct|right|confirm|confirmed|go ahead|do it|add it|fine|haan|ha|han|theek hai|thik hai|sahi hai|please do|yes please|that's right|thats right)\b[\s.!]*$", (text or "").strip(), re.I))


def is_rejection(text):
    return bool(re.match(r"^\s*(?:no|nope|nah|cancel|don'?t|do not|stop|never mind|nevermind|forget it|nahi|na)\b[\s.!]*$", (text or "").strip(), re.I))


def intent(text):
    """Does a chat request want the schedule? -> ('add', text) | ('list', scope) | None. Only clear phrasings: everything
    else goes to the model (which has the `schedule` tool)."""
    t = (text or "").strip()
    low = t.lower()
    if not low:
        return None
    if re.match(r"^\s*(?:(?:hey|hi|ok|okay)[,\s]+fab[,!\s]*)?(?:please\s+|pls\s+)?(?:(?:what|whats|what's|what is|show|list|tell me|read)\b.*\b(?:on\s+)?(?:my\s+|the\s+|today'?s\s+)?(?:schedule|agenda|plan|reminders|calendar|to-?do(?:s|\s+list)?|tasks)\b"
                r"|(?:what|whats|what's)\s+(?:do\s+i\s+have|is\s+there|have\s+i\s+got)\s+(?:on\s+)?(?:today|tomorrow|this week)|my\s+(?:schedule|agenda|day|reminders)|today'?s\s+(?:schedule|agenda|plan|reminders)|(?:show|open)\s+(?:the\s+)?schedule)"
                r"(?:\s+(?:for\s+|of\s+)?(?:today|tomorrow|this\s+week|the\s+week|now|please))*\s*\??\s*$", low):
        scope = "tomorrow" if "tomorrow" in low else ("upcoming" if ("week" in low or "upcoming" in low) else "today")
        return ("list", scope)
    if re.match(r"^\s*(?:(?:hey|hi|ok|okay)[,\s]+fab[,!\s]*)?(?:please\s+|pls\s+|kindly\s+)?(?:(?:can|could|will|would)\s+you\s+(?:please\s+)?)?(?:remind\s+me\b|set\s+(?:a\s+|an\s+)?(?:reminder|alarm)\b|add\s+(?:a\s+|an\s+)?(?:reminder|task|todo|to-do|event|appointment|meeting)\b|"
                r"put\s+(?:this\s+|it\s+|that\s+)?(?:on|in)\s+my\s+(?:schedule|calendar|diary)|schedule\s+(?:a|an|the|my)\b|schedule:|reminder:|reminder\s+(?:to|for)\b|note\s+to\s+self|remember\s+to\b|don'?t\s+(?:let\s+me\s+)?forget\s+to\b)", low):
        if re.match(r"^\s*(?:(?:hey|hi|ok|okay)[,\s]+fab[,!\s]*)?(?:please\s+)?remind\s+me\s+(?:what|how|why|which|who|where|whether|if)\b", low):
            return None                                  # "remind me what the capital of France is": a question for the model
        return ("add", t)
    return None


# ----------------------------------------------------------------------------- the store
class ScheduleStore:
    """Items in the agent's SQLite store (duck-typed: .q .one .all .setting .set_setting .activity, as fabos_agentd.Store)."""

    def __init__(self, store):
        self.s = store
        self.s.q("""CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, when_utc REAL NOT NULL, tz TEXT NOT NULL,
                    repeat TEXT NOT NULL DEFAULT 'none', source TEXT NOT NULL DEFAULT 'ui', status TEXT NOT NULL DEFAULT 'pending', remind_before INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL, notes TEXT, fired REAL, snoozed_until REAL, done_at REAL, parent_id INTEGER, origin TEXT, rep_weekday INTEGER, rep_dom INTEGER)""")
        self.s.q("CREATE TABLE IF NOT EXISTS schedule_mail(message_id TEXT PRIMARY KEY, uid TEXT, seen REAL, item_id INTEGER, result TEXT)")

    # -- settings
    def tz(self):
        return user_tz(self.s)

    def clock(self):
        return user_clock(self.s)

    def now(self):
        return dt.datetime.now(self.tz())

    # -- CRUD
    def add(self, title, when, repeat="none", source="ui", remind_before=0, notes="", origin="", rep_weekday=None, rep_dom=None, status="pending"):
        if repeat not in REPEATS:
            raise ValueError("repeat must be one of " + ", ".join(REPEATS))
        if source not in SOURCES:
            source = "ui"
        if isinstance(when, dt.datetime):
            tz = when.tzinfo or self.tz()
            epoch = when.timestamp()
        else:
            tz = self.tz()
            epoch = float(when)
        title = (title or "").strip()[:200] or "Reminder"
        now = time.time()
        rid = self.s.q("INSERT INTO schedule(title,when_utc,tz,repeat,source,status,remind_before,created,updated,notes,origin,rep_weekday,rep_dom) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       title, epoch, tz_name(tz), repeat, source, status, int(remind_before or 0), now, now, (notes or "")[:2000], (origin or "")[:500], rep_weekday, rep_dom).lastrowid
        self.s.activity("schedule", "item_added", None, "#%d %s at %s (%s, %s)" % (rid, title, dt.datetime.fromtimestamp(epoch, tz).isoformat(timespec="minutes"), repeat, source))
        return self.get(rid)

    def get(self, item_id):
        r = self.s.one("SELECT * FROM schedule WHERE id=?", int(item_id))
        return self.decorate(r) if r else None

    def decorate(self, r, now=None, clock=None):
        """Add display fields: when_local (ISO), when_text, time_text, day_text, repeat_text, overdue."""
        tz = self.tz()
        now = now or dt.datetime.now(tz)
        clock = clock or self.clock()
        d = dt.datetime.fromtimestamp(r["when_utc"], tz)
        r = dict(r)
        r["when_local"] = d.isoformat(timespec="minutes")
        r["date"] = d.date().isoformat()
        r["time_text"] = fmt_time(d, clock)
        r["day_text"] = fmt_day(d, now)
        r["when_text"] = fmt_when(d, now, clock)
        r["repeat_text"] = repeat_text(r["repeat"])
        r["overdue"] = r["status"] == "pending" and r["when_utc"] < now.timestamp()
        return r

    def update(self, item_id, **fields):
        item = self.get(item_id)
        if not item:
            return None
        sets, vals = [], []
        for k, v in fields.items():
            if k == "when":
                if isinstance(v, dt.datetime):
                    v = v.timestamp()
                sets.append("when_utc=?")
                vals.append(float(v))
                sets.append("fired=NULL")
                sets.append("snoozed_until=NULL")
                if item["status"] == "missed":
                    sets.append("status='pending'")
            elif k == "repeat":
                if v not in REPEATS:
                    raise ValueError("repeat must be one of " + ", ".join(REPEATS))
                sets.append("repeat=?")
                vals.append(v)
            elif k == "status":
                if v not in STATUSES:
                    raise ValueError("status must be one of " + ", ".join(STATUSES))
                sets.append("status=?")
                vals.append(v)
                if v == "done":
                    sets.append("done_at=?")
                    vals.append(time.time())
            elif k in ("title", "notes", "tz", "origin"):
                sets.append("%s=?" % k)
                vals.append((v or "")[:2000] if k == "notes" else str(v or "")[:500])
            elif k in ("remind_before", "rep_weekday", "rep_dom"):
                sets.append("%s=?" % k)
                vals.append(None if v is None else int(v))
        if not sets:
            return item
        sets.append("updated=?")
        vals.append(time.time())
        vals.append(int(item_id))
        self.s.q("UPDATE schedule SET %s WHERE id=?" % ", ".join(sets), *vals)
        self.s.activity("schedule", "item_edited", None, "#%d %s" % (item_id, ", ".join(k for k in fields)))
        return self.get(item_id)

    def remove(self, item_id):
        item = self.get(item_id)
        if not item:
            return False
        self.s.q("DELETE FROM schedule WHERE id=?", int(item_id))
        self.s.activity("schedule", "item_removed", None, "#%d %s" % (item_id, item["title"]))
        return True

    def roll_forward(self, item, after=None):
        """Move a repeating item to its next occurrence after `after` (default now) — and always past its current time, so Done on
        this week's review (still ahead) means next week's. Returns the updated item."""
        tz = self.tz()
        after = after or dt.datetime.now(tz)
        when = dt.datetime.fromtimestamp(item["when_utc"], tz)
        nxt = next_occurrence(when, item["repeat"], max(after, when), weekday=item.get("rep_weekday"), dom=item.get("rep_dom"))
        self.s.q("UPDATE schedule SET when_utc=?, fired=NULL, snoozed_until=NULL, status='pending', updated=? WHERE id=?", nxt.timestamp(), time.time(), item["id"])
        return self.get(item["id"])

    def complete(self, item_id, now=None):
        """Done: a one-off item is marked done; a repeating item records a done copy of this occurrence and rolls forward."""
        item = self.get(item_id)
        if not item:
            return None
        ts = time.time()
        if item["repeat"] != "none":
            self.s.q("INSERT INTO schedule(title,when_utc,tz,repeat,source,status,remind_before,created,updated,notes,origin,parent_id,done_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     item["title"], item["when_utc"], item["tz"], "none", item["source"], "done", item["remind_before"], ts, ts, item["notes"], item["origin"], item["id"], ts)
            self.s.activity("schedule", "item_done", None, "#%d %s (occurrence)" % (item_id, item["title"]))
            return self.roll_forward(item, now)
        self.s.q("UPDATE schedule SET status='done', done_at=?, updated=? WHERE id=?", ts, ts, item["id"])
        self.s.activity("schedule", "item_done", None, "#%d %s" % (item_id, item["title"]))
        return self.get(item_id)

    def dismiss(self, item_id, now=None):
        item = self.get(item_id)
        if not item:
            return None
        if item["repeat"] != "none":
            self.s.activity("schedule", "item_dismissed", None, "#%d %s (occurrence)" % (item_id, item["title"]))
            return self.roll_forward(item, now)
        self.s.q("UPDATE schedule SET status='dismissed', updated=? WHERE id=?", time.time(), item["id"])
        self.s.activity("schedule", "item_dismissed", None, "#%d %s" % (item_id, item["title"]))
        return self.get(item_id)

    def snooze(self, item_id, minutes=SNOOZE_MIN, now=None):
        item = self.get(item_id)
        if not item:
            return None
        base = (now or dt.datetime.now(dt.timezone.utc)).timestamp()
        self.s.q("UPDATE schedule SET snoozed_until=?, fired=NULL, status='pending', updated=? WHERE id=?", base + int(minutes) * 60, time.time(), item["id"])
        self.s.activity("schedule", "item_snoozed", None, "#%d %s +%d min" % (item_id, item["title"], minutes))
        return self.get(item_id)

    def reopen(self, item_id):
        item = self.get(item_id)
        if not item:
            return None
        self.s.q("UPDATE schedule SET status='pending', fired=NULL, snoozed_until=NULL, done_at=NULL, updated=? WHERE id=?", time.time(), item["id"])
        return self.get(item_id)

    # -- queries
    def list(self, scope="all", now=None, limit=200):
        """scope today (pending/missed items due today, plus overdue pending ones) · tomorrow · upcoming (after today) · done (done/dismissed, newest first)
        · missed · pending (everything still to come) · all."""
        tz = self.tz()
        now = now or dt.datetime.now(tz)
        clock = self.clock()
        start = dt.datetime.combine(now.date(), dt.time(0, 0), tzinfo=tz)
        end = start + dt.timedelta(days=1)
        if scope == "today":
            rows = self.s.all("SELECT * FROM schedule WHERE status IN ('pending','missed') AND when_utc<? ORDER BY when_utc LIMIT ?", end.timestamp(), limit)
        elif scope == "tomorrow":
            rows = self.s.all("SELECT * FROM schedule WHERE status='pending' AND when_utc>=? AND when_utc<? ORDER BY when_utc LIMIT ?", end.timestamp(), (end + dt.timedelta(days=1)).timestamp(), limit)
        elif scope == "upcoming":
            rows = self.s.all("SELECT * FROM schedule WHERE status='pending' AND when_utc>=? ORDER BY when_utc LIMIT ?", end.timestamp(), limit)
        elif scope == "pending":
            rows = self.s.all("SELECT * FROM schedule WHERE status IN ('pending','missed') ORDER BY when_utc LIMIT ?", limit)
        elif scope == "done":
            rows = self.s.all("SELECT * FROM schedule WHERE status IN ('done','dismissed') ORDER BY COALESCE(done_at, updated) DESC LIMIT ?", limit)
        elif scope == "missed":
            rows = self.s.all("SELECT * FROM schedule WHERE status='missed' ORDER BY when_utc LIMIT ?", limit)
        else:
            rows = self.s.all("SELECT * FROM schedule ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'missed' THEN 0 ELSE 1 END, when_utc LIMIT ?", limit)
        return [self.decorate(r, now, clock) for r in rows]

    def due(self, now=None):
        """Pending items whose reminder moment has come (when - remind_before, or the snooze end) and that have not fired."""
        ts = (now or dt.datetime.now(dt.timezone.utc)).timestamp()
        return [self.decorate(r) for r in self.s.all("SELECT * FROM schedule WHERE status='pending' AND fired IS NULL AND COALESCE(snoozed_until, when_utc - remind_before*60) <= ? ORDER BY when_utc", ts)]

    def sweep(self, now=None, grace=MISSED_GRACE_S):
        """Items that fell due while the daemon was not running: pending, never fired, due more than `grace` ago -> one-off
        items become 'missed' (listed at login); repeating ones roll forward (the skipped occurrence is written as a
        missed copy so the Today list shows it). Repeating items that fired and got no answer roll forward after
        ROLL_AFTER_S. Returns (missed_items, rolled_items)."""
        tz = self.tz()
        now = now or dt.datetime.now(tz)
        ts = now.timestamp()
        missed, rolled = [], []
        for r in self.s.all("SELECT * FROM schedule WHERE status='pending' AND fired IS NULL AND COALESCE(snoozed_until, when_utc) < ?", ts - grace):
            if r["repeat"] == "none":
                self.s.q("UPDATE schedule SET status='missed', updated=? WHERE id=?", time.time(), r["id"])
                missed.append(self.get(r["id"]))
            else:
                self.s.q("INSERT INTO schedule(title,when_utc,tz,repeat,source,status,remind_before,created,updated,notes,origin,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         r["title"], r["when_utc"], r["tz"], "none", r["source"], "missed", r["remind_before"], time.time(), time.time(), r["notes"], r["origin"], r["id"])
                missed.append(self.get(self.s.one("SELECT MAX(id) id FROM schedule")["id"]))
                rolled.append(self.roll_forward(r, now))
            self.s.activity("schedule", "item_missed", None, "#%d %s (due %s)" % (r["id"], r["title"], dt.datetime.fromtimestamp(r["when_utc"], tz).isoformat(timespec="minutes")))
        for r in self.s.all("SELECT * FROM schedule WHERE status='pending' AND repeat!='none' AND fired IS NOT NULL AND when_utc < ?", ts - ROLL_AFTER_S):
            rolled.append(self.roll_forward(r, now))
        return missed, rolled

    def mark_fired(self, item_id, now=None):
        self.s.q("UPDATE schedule SET fired=?, snoozed_until=NULL, updated=? WHERE id=?", (now or dt.datetime.now(dt.timezone.utc)).timestamp(), time.time(), int(item_id))

    # -- the login summary text
    def summary(self, now=None, limit=SUMMARY_LINES):
        """('Today: 3 items', 'Missed while you were away: …\\n09:00 Call the bank\\n…\\nand 2 more in Schedule', items)."""
        tz = self.tz()
        now = now or dt.datetime.now(tz)
        clock = self.clock()
        items = self.list("today", now)
        missed = [i for i in items if i["status"] == "missed" or (i["status"] == "pending" and i["when_utc"] < now.timestamp() - MISSED_GRACE_S)]
        ahead = [i for i in items if i not in missed]
        n = len(ahead)
        title = "Today: %d item%s" % (n, "" if n == 1 else "s") if n else "Today: nothing scheduled"
        lines = []
        for i in ahead[:limit]:
            lines.append("%s  %s%s" % (i["time_text"], i["title"], (" (" + i["repeat_text"] + ")") if i["repeat"] != "none" else ""))
        if n > limit:
            lines.append("and %d more in Schedule" % (n - limit))
        if missed:
            lines.insert(0, "Missed while you were away: " + " · ".join("%s (%s)" % (i["title"], fmt_when(local(i["when_utc"], tz), now, clock)) for i in missed[:3]) + (" and %d more" % (len(missed) - 3) if len(missed) > 3 else ""))
        if not lines:
            lines.append("Nothing planned for today. Say 'remind me …' to add something.")
        return title, "\n".join(lines), items


# ----------------------------------------------------------------------------- desktop notifications with buttons
class Notifier:
    """org.freedesktop.Notifications with action buttons, from a daemon thread that owns a GLib main loop (libnotify's
    notify-send drops the buttons on Plasma 6, so D-Bus is spoken directly — the same approach as fabos-updates).
    on_action(tag, action_id) is called for a pressed button or the body click ('default'). Backend FABOS_SCHED_NOTIFY:
    dbus (default) | notify-send (no buttons; tests shim the binary and read its arguments)."""

    def __init__(self, on_action=None, app=APP, icon=DESKTOP_ID, desktop_entry=DESKTOP_ID, backend=None, log=None):
        self.on_action = on_action or (lambda tag, action: None)
        self.app, self.icon, self.desktop_entry = app, icon, desktop_entry
        self.backend = backend or os.environ.get("FABOS_SCHED_NOTIFY", "dbus")
        self.log = log or (lambda *a: None)
        self.tags = {}                      # notification id -> tag
        self._loop = self._iface = self._glib = self._dbus = self._bus = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self.sent = []                      # (title, body, actions) — for tests and the activity log

    def start(self):
        """Nothing runs until the first send(): a daemon under test never touches the developer's session bus."""
        return self

    def _ensure(self):
        if self.backend != "dbus":
            return
        with self._lock:
            if self._loop is None and not self._ready.is_set():
                threading.Thread(target=self._run_loop, daemon=True, name="sched-notify").start()
        self._ready.wait(5)

    def _run_loop(self):
        try:
            import dbus, dbus.mainloop.glib
            from gi.repository import GLib
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self._glib, self._dbus = GLib, dbus
            self._bus = dbus.SessionBus()
            self._connect()
            self._bus.add_signal_receiver(self._on_action, "ActionInvoked", "org.freedesktop.Notifications")
            self._bus.add_signal_receiver(self._on_closed, "NotificationClosed", "org.freedesktop.Notifications")
            self._loop = GLib.MainLoop()
            self._ready.set()
            self._loop.run()
        except Exception as e:
            self.log("scheduler notifier: D-Bus path unavailable (%s); using notify-send" % str(e)[:160])
            self.backend = "notify-send"
            self._ready.set()

    def _connect(self):
        """A proxy that FOLLOWS the owner of org.freedesktop.Notifications: plasmashell restarts at every login (and on a
        crash), and a proxy bound to its old unique name would fail with ServiceUnknown — seen in the VM proof, where the
        first 'Today' summary after a re-login was lost that way."""
        self._iface = self._dbus.Interface(self._bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications", follow_name_owner_changes=True),
                                           "org.freedesktop.Notifications")

    def _on_action(self, nid, key):
        tag = self.tags.pop(int(nid), None)
        if tag is not None:
            try:
                self.on_action(tag, str(key))
            except Exception as e:
                self.log("scheduler notifier: action handler failed: %s" % e)

    def _on_closed(self, nid, _reason):
        self.tags.pop(int(nid), None)

    def send(self, title, body, actions=(), urgency="normal", tag=None, timeout_ms=-1):
        """Show a notification; returns its id (0 when only the fallback ran). Actions: [(id, label), ...]."""
        self.sent.append((title, body, tuple(actions)))
        self._ensure()
        if self.backend == "dbus" and self._iface is not None:
            result = {"id": 0}
            done = threading.Event()

            def do():
                acts = []
                for aid, label in actions:
                    acts += [aid, label]
                hints = {"urgency": self._dbus.Byte({"low": 0, "normal": 1, "critical": 2}.get(urgency, 1)), "desktop-entry": self.desktop_entry, "category": "im"}
                for attempt in (1, 2):                  # a failed call (the server went away) gets ONE fresh proxy and a second try
                    try:
                        nid = int(self._iface.Notify(self.app, self._dbus.UInt32(0), self.icon, title, body, acts, hints, self._dbus.Int32(timeout_ms)))
                        result["id"] = nid
                        if tag is not None:
                            with self._lock:
                                self.tags[nid] = tag
                        break
                    except Exception as e:
                        self.log("scheduler notifier: Notify failed (%s)%s" % (str(e)[:160], "; reconnecting" if attempt == 1 else "; using notify-send"))
                        result["error"] = str(e)
                        try:
                            self._connect()
                        except Exception as e2:
                            self.log("scheduler notifier: reconnect failed (%s)" % str(e2)[:120])
                done.set()
                return False
            self._glib.idle_add(do)
            done.wait(5)
            if result.get("id"):
                return result["id"]
        cmd = ["notify-send", "-a", self.app, "-i", self.icon, "-u", urgency, "-h", "string:desktop-entry:" + self.desktop_entry]
        if timeout_ms and timeout_ms > 0:
            cmd += ["-t", str(timeout_ms)]
        cmd += [title, body]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            self.log("scheduler notifier: notify-send not found")
        return 0


def open_schedule_tab(env=None, log=None):
    """Open Fab AI Controls on the Schedule tab in the user's session (the notification's Open / body click)."""
    try:
        subprocess.Popen([DESKTOP_ID, "--schedule"], env=env or None, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError as e:
        if log:
            log("scheduler: cannot open %s: %s" % (DESKTOP_ID, e))
        return False


# ----------------------------------------------------------------------------- mail intake
SUBJECT_RE = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)*(remind(?:er)?|schedule)\b\s*[:\-–—]?\s*(.*)$", re.I | re.S)
WANTS_REPLY_RE = re.compile(r"\b(?:reply|confirm|confirmation|let me know|acknowledge|ack|write back|revert)\b", re.I)
# The confirmation goes to the user's OWN address, i.e. into the very inbox the intake reads. Its subject must therefore never
# start with Remind / Reminder / Schedule (SUBJECT_RE also strips "Re:"), and its body carries a marker the intake skips on —
# otherwise every pass would read the previous reply as a new request: a duplicate item, another mail and a popup every 10 min.
REPLY_SUBJECT = "Your schedule"
REPLY_MARK = "(automatic reply from the Fab OS schedule)"
REPLY_ASK_TAIL = re.compile(r"[\s,;\-]*(?:and\s+)?(?:please\s+|pls\s+|kindly\s+)?(?:confirm|reply|revert|acknowledge|ack|let\s+me\s+know|write\s+back)(?:\s+(?:me|back|please|pls|asap))?[\s.!?]*$", re.I)


class MailIntake:
    """Turns mail the user sends to their own inbox into schedule items. search() -> [{message_id, uid, from, subject, body, date}]
    (the daemon passes its IMAP search, tests a stub); send(to, subject, body) sends the optional confirmation. Only mail
    FROM own_address (the signed-in account) is considered; the subject must start with Remind / Reminder / Schedule."""

    def __init__(self, sched, own_address, search, send=None, log=None):
        self.sched, self.own, self.search, self.send, self.log = sched, (own_address or "").strip().lower(), search, send, (log or (lambda *a: None))

    @staticmethod
    def sender(msg):
        return (email.utils.parseaddr(msg.get("from") or "")[1] or "").strip().lower()

    @staticmethod
    def wants_reply(msg):
        return bool(WANTS_REPLY_RE.search((msg.get("subject") or "") + "\n" + (msg.get("body") or "")[:2000]))

    @staticmethod
    def is_own_reply(msg):
        """The intake's own confirmation coming back into the inbox (or the user's answer to it, which quotes the marker):
        skipped, never parsed, never answered — the loop guard."""
        subj = re.sub(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)*", "", msg.get("subject") or "", flags=re.I)
        return subj.lower().startswith(REPLY_SUBJECT.lower()) or REPLY_MARK in (msg.get("body") or "")

    def candidate_texts(self, msg):
        """The texts to try, in order: the subject after the keyword, then the first body lines (unquoted, non-empty).
        A trailing 'please confirm' / 'let me know' is the request for a reply, not part of the reminder."""
        m = SUBJECT_RE.match(msg.get("subject") or "")
        if not m:
            return []
        out = []
        rest = REPLY_ASK_TAIL.sub("", (m.group(2) or "").strip()).strip()
        body_lines = [REPLY_ASK_TAIL.sub("", ln.strip()).strip() for ln in (msg.get("body") or "").splitlines()]
        body_lines = [ln for ln in body_lines if ln and not ln.startswith(">") and not re.match(r"^(on .* wrote:|--\s*$|sent from)", ln, re.I)][:6]
        if rest:
            out.append(rest)
            if body_lines:
                out.append(rest + " " + body_lines[0])
        out += body_lines
        return out

    def run_once(self, now=None):
        """One pass. Returns {'checked': n, 'created': [items], 'skipped': [(message_id, why)], 'replied': n}."""
        now = now or self.sched.now()
        report = {"checked": 0, "created": [], "skipped": [], "replied": 0, "error": None}
        if not self.own:
            report["error"] = "no mail address"
            return report
        try:
            msgs = self.search() or []
        except Exception as e:
            report["error"] = str(e)[:300]
            self.log("scheduler mail intake: %s" % report["error"])
            return report
        for msg in msgs:
            report["checked"] += 1
            mid = (msg.get("message_id") or "").strip() or ("uid:" + str(msg.get("uid") or ""))
            if not mid or mid == "uid:":
                continue
            if self.sched.s.one("SELECT 1 FROM schedule_mail WHERE message_id=?", mid):
                continue
            why, item = None, None
            if self.is_own_reply(msg):
                why = "the schedule's own reply"
            elif self.sender(msg) != self.own:
                why = "not from the user's own address"
            elif not SUBJECT_RE.match(msg.get("subject") or ""):
                why = "subject does not start with Remind / Reminder / Schedule"
            else:
                texts = self.candidate_texts(msg)
                best = None
                for t in texts:
                    p = parse(t, now, self.sched.tz(), self.sched.clock())
                    if p["ok"] and not p["needs_confirm"]:
                        best = p
                        break
                    if p["ok"] and best is None:
                        best = p
                if best is None:
                    why = "no time found"
                    for t in texts:
                        p = parse(t, now, self.sched.tz(), self.sched.clock())
                        if p.get("error"):
                            why = p["error"]
                            break
                elif best["needs_confirm"]:
                    why = "ambiguous: " + best["interpretation"]
                else:
                    item = self.sched.add(best["title"], best["when"], best["repeat"], "mail", 0, notes="from mail: " + (msg.get("subject") or "")[:200], origin=mid,
                                          rep_weekday=best.get("rep_weekday"), rep_dom=best.get("rep_dom"))
                    item["interpretation"] = best["interpretation"]
                    report["created"].append(item)
            self.sched.s.q("INSERT OR REPLACE INTO schedule_mail(message_id,uid,seen,item_id,result) VALUES(?,?,?,?,?)", mid, str(msg.get("uid") or ""), time.time(), item["id"] if item else None, (why or "added")[:300])
            self.sched.s.activity("schedule", "mail_intake", None, "%s: %s" % ((msg.get("subject") or "")[:80], (item["interpretation"] if item else why)[:200]))
            if why:
                report["skipped"].append((mid, why))
            if self.send and self.sender(msg) == self.own and not self.is_own_reply(msg) and SUBJECT_RE.match(msg.get("subject") or "") and self.wants_reply(msg):
                try:
                    body = ("Added to your schedule: %s\n\nYou will get a reminder on this computer at that time; it is also in Fab AI Controls › Schedule." % item["interpretation"]) if item else \
                           ("I could not add this to your schedule: %s\n\nWrite the time plainly, for example 'Reminder: call the bank tomorrow 9am'." % why)
                    body += "\n\n" + REPLY_MARK
                    self.send(self.own, "%s: %s" % (REPLY_SUBJECT, ("added — " + item["title"]) if item else "not added"), body)
                    report["replied"] += 1
                except Exception as e:
                    self.log("scheduler mail intake: reply failed: %s" % e)
        return report


# ----------------------------------------------------------------------------- the loop
class SchedulerLoop(threading.Thread):
    """Ticks every `tick` seconds: sweeps items missed while the computer was off, fires due reminders as notifications
    with Done · Snooze 10 min · Open, rolls repeating items forward, and runs the mail intake every 10 minutes when the
    mail account is signed in and scheduler.mail_intake is not 'false'. send_login_summary() is called by the
    /schedule/login-summary endpoint (fabos-schedule-summary.service at graphical-session.target)."""

    def __init__(self, store, notifier=None, session_env=None, mail_factory=None, tick=None, log=None, enabled=None):
        super().__init__(daemon=True, name="scheduler")
        self.store = store
        self.sched = ScheduleStore(store)
        self.log = log or (lambda *a: None)
        self.session_env = session_env or (lambda: None)
        self.notifier = notifier or Notifier(self.on_action, log=self.log).start()
        self.notifier.on_action = self.on_action
        self.mail_factory = mail_factory          # -> MailIntake or None (the daemon builds it from its mail config)
        self.tick = tick or int(os.environ.get("FABOS_SCHED_TICK", "30"))
        self.enabled = enabled or (lambda: True)
        self.stop = threading.Event()
        self._tick_lock = threading.Lock()        # the loop thread and POST /schedule/tick never sweep/fire at the same time
        self.summary_sent = False
        self.last_mail = 0.0
        self.mail_report = None

    def run(self):
        while not self.stop.wait(self.tick):
            try:
                self.tick_once()
            except Exception as e:
                self.log("scheduler loop error: %s" % e)

    def tick_once(self, now=None):
        with self._tick_lock:
            return self._tick(now)

    def _tick(self, now=None):
        tz = self.sched.tz()
        now = now or dt.datetime.now(tz)
        clock = self.sched.clock()
        missed, rolled = self.sched.sweep(now)
        if missed and self.summary_sent:           # the computer slept with the session open: say so now, once
            self.notifier.send("Missed while the computer was asleep", "\n".join("%s  %s" % (fmt_when(local(i["when_utc"], tz), now, clock), i["title"]) for i in missed[:5]),
                               actions=(("default", "Open"), ("open", "Open Schedule")), tag=("missed", 0))
        for item in self.sched.due(now):
            self.fire(item, now)
        if self.mail_factory and time.time() - self.last_mail >= MAIL_INTAKE_EVERY_S:
            self.last_mail = time.time()
            self.run_mail_intake(now)
        return missed, rolled

    def fire(self, item, now=None):
        tz = self.sched.tz()
        now = now or dt.datetime.now(tz)
        clock = self.sched.clock()
        when = local(item["when_utc"], tz)
        if item["remind_before"] and when > now:
            mins = int(round((when - now).total_seconds() / 60))
            body = "in %d min — %s" % (mins, fmt_when(when, now, clock))
        elif when.date() == now.date():
            body = fmt_time(when, clock) + (" · " + item["repeat_text"] if item["repeat"] != "none" else "")
        else:
            body = fmt_when(when, now, clock)
        if item.get("notes") and not item["notes"].startswith("from mail:"):
            body += "\n" + item["notes"][:160]
        self.sched.mark_fired(item["id"], now)
        self.store.activity("schedule", "reminder_shown", None, "#%d %s" % (item["id"], item["title"]))
        # timeout 0 = the popup stays until the user answers it (Done / Snooze / close): a reminder that vanished after six seconds is no reminder
        self.notifier.send(item["title"], html.escape(body, quote=False), actions=(("default", "Open"), ("done", "Done"), ("snooze", "Snooze %d min" % SNOOZE_MIN), ("open", "Open")),
                           urgency="normal", tag=("item", item["id"]), timeout_ms=0)

    def on_action(self, tag, action):
        kind, item_id = tag if isinstance(tag, tuple) else ("item", tag)
        if action == "done" and kind == "item":
            self.sched.complete(item_id)
        elif action == "snooze" and kind == "item":
            self.sched.snooze(item_id, SNOOZE_MIN)
        elif action in ("open", "default"):
            open_schedule_tab(self.session_env(), self.log)

    def run_mail_intake(self, now=None):
        try:
            intake = self.mail_factory()
        except Exception as e:
            self.mail_report = {"error": str(e)[:200]}
            return self.mail_report
        if not intake:
            self.mail_report = {"skipped": "mail intake off or mail not configured"}
            return self.mail_report
        self.mail_report = intake.run_once(now)
        created = self.mail_report.get("created") or []
        if len(created) == 1:
            it = created[0]
            self.notifier.send("Added from your mail", html.escape(it["interpretation"], quote=False), actions=(("default", "Open"), ("open", "Open Schedule")), tag=("mail", it["id"]))
        elif created:                                # ONE notification per pass, never one per mail (a pass reads up to 20)
            body = "\n".join(html.escape(it["interpretation"], quote=False) for it in created[:SUMMARY_LINES])
            if len(created) > SUMMARY_LINES:
                body += "\nand %d more in Schedule" % (len(created) - SUMMARY_LINES)
            self.notifier.send("Added %d items from your mail" % len(created), body, actions=(("default", "Open"), ("open", "Open Schedule")), tag=("mail", created[0]["id"]))
        return self.mail_report

    def brief(self):
        """For /status: {'today': n, 'missed': n, 'next': {'title', 'when_text'} | None}."""
        try:
            now = self.sched.now()
            items = self.sched.list("today", now)
            ahead = [i for i in items if i["status"] == "pending" and i["when_utc"] >= now.timestamp()]
            nxt = self.sched.s.one("SELECT * FROM schedule WHERE status='pending' AND when_utc>=? ORDER BY when_utc LIMIT 1", now.timestamp())
            return {"today": len(items), "missed": len([i for i in items if i["status"] == "missed"]), "ahead_today": len(ahead),
                    "next": ({"id": nxt["id"], "title": nxt["title"], "when_text": self.sched.decorate(nxt, now)["when_text"]} if nxt else None)}
        except Exception as e:
            return {"error": str(e)[:200]}

    def session_key(self, session=None):
        """One summary per login: date + boot id + the graphical session's id. The caller (scheduler.py --login-summary, run
        by the user unit at graphical-session.target) passes the session it found with loginctl — the daemon's own
        environment holds the FIRST session's id for the life of the user manager, which would hide the summary from a
        second login on the same day."""
        sid = session or os.environ.get("XDG_SESSION_ID") or ""
        try:
            with open("/proc/sys/kernel/random/boot_id") as f:
                boot = f.read().strip()
        except OSError:
            boot = "noboot"
        return "%s:%s:%s" % (dt.date.today().isoformat(), boot, sid)

    def send_login_summary(self, force=False, now=None, session=None):
        """The 'Today: N items' notification (clicking opens the Schedule tab). Returns {sent, title, body, count, deduped}."""
        key = self.session_key(session)
        last = self.store.setting("scheduler.summary_sent", "")
        tz = self.sched.tz()
        now = now or dt.datetime.now(tz)
        self.sched.sweep(now)
        title, body, items = self.sched.summary(now)
        if last == key and not force:
            return {"sent": False, "deduped": True, "title": title, "body": body, "count": len(items), "key": key}
        self.notifier.send(title, html.escape(body, quote=False), actions=(("default", "Open"), ("open", "Open Schedule")), tag=("summary", 0), timeout_ms=SUMMARY_TIMEOUT_MS)
        self.store.set_setting("scheduler.summary_sent", key)
        self.store.activity("schedule", "login_summary", None, "%s | %s" % (title, body.replace("\n", " · ")[:300]))
        self.summary_sent = True
        return {"sent": True, "deduped": False, "title": title, "body": body, "count": len(items), "key": key}


# ----------------------------------------------------------------------------- requests -> items (shared by the tool, the API and the UI)
def parsed_json(p):
    """A parse result as JSON-safe dict (when -> ISO in the user's zone)."""
    out = {k: v for k, v in p.items() if k != "when"}
    out["when"] = p["when"].isoformat(timespec="minutes") if p.get("when") else None
    return out


def parse_when_value(v, tz, default_hour=DEFAULT_HOUR):
    """A UI / tool 'when': epoch seconds, or ISO 'YYYY-MM-DDTHH:MM' (naive = the user's zone), or 'YYYY-MM-DD' (-> default_hour).
    Returns an aware datetime or raises ValueError."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return dt.datetime.fromtimestamp(float(v), tz)
    s = str(v or "").strip()
    if not s:
        raise ValueError("when is empty")
    if re.match(r"^\d+(\.\d+)?$", s):
        return dt.datetime.fromtimestamp(float(s), tz)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        d = dt.date.fromisoformat(s)
        return dt.datetime.combine(d, dt.time(default_hour, 0), tzinfo=tz)
    try:
        d = dt.datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        raise ValueError("'%s' is not a date and time (use YYYY-MM-DDTHH:MM)" % s)
    return d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz)


def add_from_request(sched, inp, source="ui"):
    """Create an item from {text} (natural language) or {title, when, repeat, remind_before, notes}. Returns
    {added, item, interpretation, text} · {needs_confirm, parsed, interpretation, text} (until confirm=true) · {error, text}."""
    now, tz, clock = sched.now(), sched.tz(), sched.clock()
    text = str(inp.get("text") or "").strip()
    title = str(inp.get("title") or "").strip()
    repeat = str(inp.get("repeat") or "").strip().lower() or None
    when_in = inp.get("when")
    confirm = bool(inp.get("confirm"))
    notes = str(inp.get("notes") or "")
    try:
        remind_before = int(inp.get("remind_before") or 0)
    except (TypeError, ValueError):
        return {"error": "remind_before must be a number of minutes", "text": "remind_before must be a number of minutes"}
    if repeat is not None and repeat not in REPEATS:
        return {"error": "repeat must be one of " + ", ".join(REPEATS), "text": "repeat must be one of " + ", ".join(REPEATS)}
    p = None
    rep_weekday = rep_dom = None
    if when_in not in (None, ""):
        try:
            when = parse_when_value(when_in, tz)
        except ValueError as e:
            return {"error": str(e), "text": str(e)}
        if isinstance(when_in, str) and "T" in when_in:
            when = when.replace(second=0, microsecond=0)
        repeat = repeat or "none"
        if when < now - dt.timedelta(seconds=60):
            if repeat == "none":
                why = "%s has already passed." % fmt_when(when, now, clock)
                return {"error": why, "text": why}
            when = next_occurrence(when, repeat, now)
        if not title and text:
            p0 = parse(text, now, tz, clock)
            title = p0["title"] if p0["ok"] else text
        title = title or "Reminder"
        p = {"title": title, "when": when, "repeat": repeat, "assumptions": [], "remind_before": remind_before}
    else:
        if not text:
            why = "Say what to remind you of and when, for example 'tomorrow at 9am call the bank'."
            return {"error": why, "text": why}
        p = parse(text, now, tz, clock)
        if not p["ok"]:
            return {"error": p["error"], "text": p["error"], "parsed": parsed_json(p)}
        if p["needs_confirm"] and not confirm:
            return {"needs_confirm": True, "parsed": parsed_json(p), "interpretation": p["interpretation"],
                    "text": "I read that as %s. Is that right? Say yes, or give the time again." % p["interpretation"]}
        when, title = p["when"], title or p["title"]
        repeat = repeat or p["repeat"]
        remind_before = remind_before or p.get("remind_before") or 0
        rep_weekday, rep_dom = p.get("rep_weekday"), p.get("rep_dom")
    interp = interpretation({"title": title, "when": when, "repeat": repeat, "assumptions": [], "remind_before": remind_before}, now, clock)
    item = sched.add(title, when, repeat, source, remind_before, notes=notes, origin=text, rep_weekday=rep_weekday, rep_dom=rep_dom)
    item["interpretation"] = interp
    return {"added": True, "item": item, "interpretation": interp, "text": "Added: %s. I will remind you then." % interp}


def list_text(items, scope, now, clock, limit=12):
    """A short spoken/typed rendering of a list: 'Today: 2 items — 09:00 Call the bank · 15:00 Meeting with Rohan'."""
    label = {"today": "Today", "tomorrow": "Tomorrow", "upcoming": "Coming up", "done": "Done", "missed": "Missed", "pending": "Still to do", "all": "Your schedule"}.get(scope, scope.title())
    if not items:
        return "%s: nothing scheduled." % label if scope in ("today", "tomorrow") else "%s: nothing here yet." % label
    parts = []
    for i in items[:limit]:
        when = local(i["when_utc"], now.tzinfo)
        t = fmt_time(when, clock) if scope in ("today", "tomorrow") else fmt_when(when, now, clock)
        parts.append("%s %s%s%s" % (t, i["title"], (" (%s)" % i["repeat_text"]) if i["repeat"] != "none" else "", " — missed" if i["status"] == "missed" else ""))
    more = " · and %d more" % (len(items) - limit) if len(items) > limit else ""
    return "%s: %d item%s — %s%s" % (label, len(items), "" if len(items) == 1 else "s", " · ".join(parts), more)


# ----------------------------------------------------------------------------- CLI (the user unit + hand tests)
def _daemon():
    run = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabos-agent")
    try:
        with open(os.path.join(run, "token")) as f:
            tok = f.read().strip()
        with open(os.path.join(run, "port")) as f:
            port = f.read().strip()
    except OSError:
        return None, None
    return "http://127.0.0.1:%s" % port, tok


def _call(method, path, body=None, timeout=10):
    base, tok = _daemon()
    if not base:
        raise RuntimeError("the agent service is not running (no token in $XDG_RUNTIME_DIR/fabos-agent)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except Exception:
            return {"error": "HTTP %s" % e.code}


def _wait_for(pred, timeout, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def notifications_server_up():
    """Is a notification server (plasmashell) on the session bus yet?"""
    try:
        import dbus
        return bool(dbus.SessionBus().name_has_owner("org.freedesktop.Notifications"))
    except Exception:
        return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


def graphical_session_id():
    """The id of this user's newest seat session (loginctl), else $XDG_SESSION_ID — the once-per-login key."""
    try:
        out = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True, timeout=5).stdout
        me = str(os.getuid())
        ids = [ln.split()[0] for ln in out.splitlines() if len(ln.split()) >= 4 and ln.split()[1] == me and ln.split()[3].startswith("seat")]
        if ids:
            return max(ids, key=lambda s: int(s) if s.isdigit() else 0)
    except Exception:
        pass
    return os.environ.get("XDG_SESSION_ID") or ""


def desktop_ready():
    """Popups need more than the bus name: plasmashell registers org.freedesktop.Notifications while its panel is still
    loading, and a notification sent then lands in the history without a popup (seen in the VM proof, 14 s after login).
    Ready = the server is there and not inhibited (its `Inhibited` property; do-not-disturb counts too)."""
    try:
        import dbus
        bus = dbus.SessionBus()
        if not bus.name_has_owner("org.freedesktop.Notifications"):
            return False
        try:
            obj = bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
            if bool(dbus.Interface(obj, "org.freedesktop.DBus.Properties").Get("org.freedesktop.Notifications", "Inhibited")):
                return False
        except Exception:
            pass                                    # a server without the property: nothing more to learn
        return True
    except Exception:
        return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


def login_summary_cli(args):
    """fabos-schedule-summary.service: wait for the agent (≤ 90 s), for the notification server to be up and not inhibited
    (≤ 90 s) and then FABOS_SUMMARY_DELAY seconds (default 15) for the desktop to settle, then ask the daemon to send today's
    summary for THIS login; prints what it waited for and what it sent. Exit 0 even when there was nothing to do."""
    t0 = time.time()
    if not _wait_for(lambda: _daemon()[0] is not None and _call("GET", "/health", timeout=3).get("ok"), 90):
        print("fabos-schedule-summary: the agent service did not come up; no summary", file=sys.stderr)
        return 0
    t_agent = time.time() - t0
    ready = _wait_for(desktop_ready, 90, 1.0)
    t_ready = time.time() - t0
    delay = float(os.environ.get("FABOS_SUMMARY_DELAY", "15"))
    time.sleep(delay)
    print("fabos-schedule-summary: agent after %.0fs, desktop %s after %.0fs, settled %.0fs, session %s" % (t_agent, "ready" if ready else "NOT ready (sending anyway)", t_ready, delay, graphical_session_id()), flush=True)
    r = _call("POST", "/schedule/login-summary", {"force": "--force" in args, "session": graphical_session_id()})
    print(json.dumps(r, ensure_ascii=False))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--login-summary":
        return login_summary_cli(argv[1:])
    if argv[0] == "--parse":
        text = " ".join(a for a in argv[1:] if not a.startswith("--") and a not in ("12", "24") and not re.match(r"^\d{4}-\d{2}-\d{2}", a) and "/" not in a)
        now = tz = None
        clock = "24"
        for i, a in enumerate(argv):
            if a == "--now" and i + 1 < len(argv):
                now = dt.datetime.fromisoformat(argv[i + 1])
            if a == "--tz" and i + 1 < len(argv):
                tz = resolve_tz(argv[i + 1])
            if a == "--clock" and i + 1 < len(argv):
                clock = argv[i + 1]
        if now is not None and now.tzinfo is None:
            now = now.replace(tzinfo=tz or resolve_tz(system_tz_name()) or dt.timezone.utc)
        p = parse(text, now, tz, clock)
        p = dict(p, when=p["when"].isoformat() if p["when"] else None)
        print(json.dumps(p, ensure_ascii=False, indent=1))
        return 0 if p["ok"] else 1
    if argv[0] == "--today":
        print(json.dumps(_call("GET", "/schedule/today"), ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "add":
        r = _call("POST", "/schedule", {"text": " ".join(argv[1:]), "source": "ui", "confirm": "--confirm" in argv})
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r.get("item") else 1
    if argv[0] == "list":
        r = _call("GET", "/schedule?scope=" + (argv[1] if len(argv) > 1 else "all"))
        for it in r.get("items", []):
            print("#%-3d %-8s %-22s %s%s" % (it["id"], it["status"], it["when_text"], it["title"], (" · " + it["repeat_text"]) if it["repeat"] != "none" else ""))
        return 0
    print("unknown command %r; see --help" % argv[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
