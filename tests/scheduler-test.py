#!/usr/bin/env python3
"""Unit tests for packages/fabos-agent/usr/lib/fabos/agent/scheduler.py — no daemon, no network, no GUI.

  parser corpus   60+ phrasings against a fixed clock (Thu 17 Sep 2026 10:00 Asia/Kolkata): the expected local date/time,
                  title, repeat; ambiguous readings must come back needs_confirm with the guess; unsupported / impossible
                  ones must FAIL with a reason (never a silent guess)
  time zones      the same words give different instants in Asia/Kolkata and Europe/London; the 12 h / 24 h preference
  roll-forward    daily / weekdays (skips the weekend) / weekly / monthly (31st clamps) / DST-safe wall-clock time
  store           add · list scopes · edit · done (a repeating item leaves a done copy and rolls forward) · dismiss · snooze · remove
  missed-while-off  sweep(): one-off items become missed and appear in the login summary; repeating ones roll forward
  loop            due reminders fire once with Done / Snooze / Open buttons; the buttons act; the login summary is sent
                  once per login (force resends); "and N more in Schedule" after 5 lines
  mail intake     a stub IMAP message set: own-address "Reminder:" mail becomes an item, other senders / other subjects /
                  ambiguous readings are skipped with the reason, the confirmation goes out only when asked, no duplicates
  intent + API helpers  chat routing phrases; add_from_request (needs_confirm gate, explicit when, past time refused)
Run:  python3 tests/scheduler-test.py
"""
import datetime as dt, os, sys, tempfile, time, unittest
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.join(ROOT, "packages/fabos-agent/usr/lib/fabos/agent")
os.environ.setdefault("FABOS_POLICY_FILE", os.path.join(tempfile.gettempdir(), "fabos-test-no-policy-%d.json" % os.getpid()))
os.environ["FABOS_SCHED_NOTIFY"] = "notify-send"        # never the developer's session bus
sys.path.insert(0, AGENT_DIR)
import scheduler as S            # noqa: E402
import fabos_agentd as fa        # noqa: E402  (its Store is the SQLite layer the scheduler sits on)

IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
NOW = dt.datetime(2026, 9, 17, 10, 0, tzinfo=IST)       # Thursday


def L(y, m, d, hh, mm=0, tz=IST):
    return dt.datetime(y, m, d, hh, mm, tzinfo=tz)


# (text, expected local datetime | None for a failure, title (None = don't care), repeat, needs_confirm)
CORPUS = [
    # ---- the owner's examples
    ("remind me tomorrow at 9am to call the bank", L(2026, 9, 18, 9), "Call the bank", "none", False),
    ("every weekday 6:30 pm gym", L(2026, 9, 17, 18, 30), "Gym", "weekdays", False),
    ("meeting with Rohan on 20 Sep 3pm", L(2026, 9, 20, 15), "Meeting with Rohan", "none", False),
    ("pay rent on the 1st every month", L(2026, 10, 1, 9), "Pay rent", "monthly", True),          # no time given -> 09:00, confirm
    ("in 45 minutes", L(2026, 9, 17, 10, 45), "Reminder", "none", False),
    ("next Monday morning", L(2026, 9, 21, 9), "Reminder", "none", False),                        # 'morning' = 09:00 by convention (said in the echo)
    # ---- exact readings
    ("Reminder: dentist appointment day after tomorrow 4.30 pm", L(2026, 9, 19, 16, 30), "Dentist appointment", "none", False),
    ("Hey Fab, please remind me to water the plants every morning", L(2026, 9, 18, 9), "Water the plants", "daily", False),
    ("call mom tonight", L(2026, 9, 17, 21), "Call mom", "none", False),
    ("in 2 hours check the oven", L(2026, 9, 17, 12), "Check the oven", "none", False),
    ("standup every Monday 10am", L(2026, 9, 21, 10), "Standup", "weekly", False),
    ("take medicine daily at 8pm", L(2026, 9, 17, 20), "Take medicine", "daily", False),
    ("team lunch on September 25th at 1pm", L(2026, 9, 25, 13), "Team lunch", "none", False),
    ("submit the report by 5pm today", L(2026, 9, 17, 17), "Submit the report", "none", False),
    ("remind me at half past six in the evening to leave office", L(2026, 9, 17, 18, 30), "Leave office", "none", False),
    ("remind me at 15:00 to join the review", L(2026, 9, 17, 15), "Join the review", "none", False),
    ("remind me on 2026-10-02 at 10:00 to renew the domain", L(2026, 10, 2, 10), "Renew the domain", "none", False),
    ("doctor appointment 20/09/2026 4pm", L(2026, 9, 20, 16), "Doctor appointment", "none", False),
    ("weekly review every Friday 4 pm", L(2026, 9, 18, 16), None, "weekly", False),
    ("remind me in a week to follow up with the vendor", L(2026, 9, 24, 10), "Follow up with the vendor", "none", False),
    ("tomorrow noon lunch with Aman", L(2026, 9, 18, 12), "Lunch with Aman", "none", False),
    ("remind me at midnight to switch off the router", L(2026, 9, 18, 0), "Switch off the router", "none", False),
    ("remind me on Sunday evening to prepare for the week", L(2026, 9, 20, 18), "Prepare for the week", "none", False),
    ("remind me tmrw 7am yoga", L(2026, 9, 18, 7), "Yoga", "none", False),
    ("remind me to pay the credit card bill on the 5th of every month at 10am", L(2026, 10, 5, 10), "Pay the credit card bill", "monthly", False),
    ("remind me at 9 in the morning to book tickets", L(2026, 9, 18, 9), "Book tickets", "none", False),
    ("remind me in 90 minutes to take a break", L(2026, 9, 17, 11, 30), "Take a break", "none", False),
    ("remind me at 7 pm sharp to call dad", L(2026, 9, 17, 19), "Call dad", "none", False),
    ("morning walk at 6am tomorrow", L(2026, 9, 18, 6), "Morning walk", "none", False),
    ("remind me 10 minutes before the meeting at 3pm", L(2026, 9, 17, 15), "Meeting", "none", False),
    ("remind me coming Monday 11 am for the review", L(2026, 9, 21, 11), "Review", "none", False),
    ("today evening 7 pm dinner with parents", L(2026, 9, 17, 19), "Dinner with parents", "none", False),
    ("remind me at lunch to call HR", L(2026, 9, 17, 13), "Call HR", "none", False),
    ("remind me on 20th Sep at 6 in the evening to pick up the cake", L(2026, 9, 20, 18), "Pick up the cake", "none", False),
    ("schedule: dentist on 3rd October 11:30 am", L(2026, 10, 3, 11, 30), "Dentist", "none", False),
    ("Schedule a meeting with the design team next Tuesday 2pm", L(2026, 9, 22, 14), "Meeting with the design team", "none", False),
    ("remind me an hour before my flight at 6pm tomorrow", L(2026, 9, 18, 18), "My flight", "none", False),
    ("gym at 6 today itself", L(2026, 9, 17, 18), "Gym", "none", True),
    ("call Priya at 4 pm on the 25th", L(2026, 9, 25, 16), "Call Priya", "none", False),
    ("electricity bill on the 10th of every month 9am", L(2026, 10, 10, 9), "Electricity bill", "monthly", False),
    # ---- ambiguous: parsed with a guess, created only after the user confirms
    ("meeting at 3", L(2026, 9, 17, 15), "Meeting", "none", True),
    ("remind me on 5/6 about the dentist", L(2027, 6, 5, 9), "Dentist", "none", True),
    ("remind me sometime next week to renew insurance", L(2026, 9, 21, 9), "Renew insurance", "none", True),
    ("schedule a call with Priya on Friday at 11", L(2026, 9, 18, 11), "Call with Priya", "none", True),
    ("remind me on the 15th to pay the electricity bill", L(2026, 10, 15, 9), "Pay the electricity bill", "none", True),
    ("remind me in 3 days to renew the passport", L(2026, 9, 20, 9), "Renew the passport", "none", True),
    ("pick up kids at 3.30", L(2026, 9, 17, 15, 30), "Pick up kids", "none", True),
    ("call the plumber at 4 o'clock", L(2026, 9, 17, 16), "Call the plumber", "none", True),
    ("remind me on Monday to send the invoice", L(2026, 9, 21, 9), "Send the invoice", "none", True),
    ("remind me to call Rohan at 9", L(2026, 9, 18, 9), "Call Rohan", "none", True),
    ("wake me up at 6 baje tomorrow", L(2026, 9, 18, 18), "Wake me up", "none", True),
    ("end of the month submit invoices", L(2026, 9, 30, 9), "Submit invoices", "none", True),
    ("this weekend clean the garage", L(2026, 9, 19, 9), "Clean the garage", "none", True),
    # ---- must FAIL with a reason (never a silent guess)
    ("remind me to call the bank", None, None, None, None),
    ("remind me on 30 Feb to file taxes", None, None, None, None),
    ("every other day at 9am gym", None, None, None, None),
    ("remind me at 25:00", None, None, None, None),
    ("remind me later", None, None, None, None),
    ("remind me yesterday at 9am", None, None, None, None),
    ("remind me at 9am and 5pm to take medicine", None, None, None, None),
    ("remind me in a few minutes", None, None, None, None),
    ("remind me on 17 Sep at 9am to submit the report", None, None, None, None),        # today, already passed
    ("remind me every 2 weeks to backup the laptop", None, None, None, None),
    ("every weekend wash the car", None, None, None, None),
    ("remind me", None, None, None, None),
    ("", None, None, None, None),
]


class Parser(unittest.TestCase):
    def test_corpus(self):
        failures = []
        for text, when, title, repeat, confirm in CORPUS:
            p = S.parse(text, NOW, IST, "24")
            try:
                if when is None:
                    self.assertFalse(p["ok"], "should have failed: %r -> %s" % (text, p["interpretation"]))
                    self.assertTrue(p["error"], text); self.assertIsNone(p["when"]); self.assertFalse(p["needs_confirm"])
                    continue
                self.assertTrue(p["ok"], "%r: %s" % (text, p["error"]))
                self.assertEqual(p["when"], when, "%r -> %s (%s)" % (text, p["when"], p["interpretation"]))
                if title is not None:
                    self.assertEqual(p["title"], title, text)
                self.assertEqual(p["repeat"], repeat, text)
                self.assertEqual(p["needs_confirm"], confirm, "%r needs_confirm=%s: %s" % (text, p["needs_confirm"], p["interpretation"]))
                self.assertIn("—", p["interpretation"])                # the echo always names the time and the title
                if confirm:
                    self.assertEqual(p["confidence"], "ambiguous")
            except AssertionError as e:
                failures.append(str(e))
        self.assertGreaterEqual(len(CORPUS), 60)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_details(self):
        p = S.parse("remind me 10 minutes before the meeting at 3pm", NOW, IST)
        self.assertEqual(p["remind_before"], 10); self.assertIn("reminder 10 min before", p["interpretation"])
        self.assertEqual(S.parse("remind me an hour before my flight at 6pm tomorrow", NOW, IST)["remind_before"], 60)
        p = S.parse("pay rent on the 1st every month", NOW, IST); self.assertEqual(p["rep_dom"], 1)
        p = S.parse("standup every Monday 10am", NOW, IST); self.assertEqual(p["rep_weekday"], 0)
        # a bare hour is guessed and SAID: 1-6 -> pm, 7-11 -> am, 12 -> noon
        p8 = S.parse("call at 8", NOW, IST)                                              # 7-11 read as am; 08:00 has passed -> tomorrow 08:00, flagged
        self.assertEqual((p8["when"].hour, p8["when"].date(), p8["needs_confirm"]), (8, NOW.date() + dt.timedelta(days=1), True))
        self.assertEqual(S.parse("call at 11", NOW, IST)["when"].hour, 11)
        self.assertEqual(S.parse("call at 5", NOW, IST)["when"].hour, 17)
        self.assertEqual(S.parse("call at 12", NOW, IST)["when"].hour, 12)
        self.assertIn("read as", S.parse("call at 12", NOW, IST)["interpretation"])
        # the failure sentences say why
        self.assertIn("When should I remind you", S.parse("remind me to call the bank", NOW, IST)["error"])
        self.assertIn("not supported", S.parse("every other day at 9am gym", NOW, IST)["error"])
        self.assertIn("two times", S.parse("remind me at 9am and 5pm to take medicine", NOW, IST)["error"])
        self.assertIn("already passed", S.parse("remind me on 17 Sep at 9am to submit the report", NOW, IST)["error"])
        self.assertIn("not a real date", S.parse("remind me on 30 Feb to file taxes", NOW, IST)["error"])
        self.assertIn("not a valid time", S.parse("remind me at 25:00", NOW, IST)["error"])
        # a weekday that is today: later today when the time is still ahead, else next week; "next <today>" is always next week
        self.assertEqual(S.parse("call at 5pm on Thursday", NOW, IST)["when"], L(2026, 9, 17, 17))
        self.assertEqual(S.parse("call at 9am on Thursday", NOW, IST)["when"], L(2026, 9, 24, 9))
        self.assertEqual(S.parse("call next Thursday 5pm", NOW, IST)["when"], L(2026, 9, 24, 17))

    def test_clock_preference_and_zone(self):
        # "10:30" is exact under a 24 h clock, a confirmable guess under 12 h; "3.30" is a guess under both
        self.assertFalse(S.parse("remind me at 10:30 to join the call", NOW, IST, "24")["needs_confirm"])
        self.assertTrue(S.parse("remind me at 10:30 to join the call", NOW, IST, "12")["needs_confirm"])
        self.assertTrue(S.parse("pick up kids at 3.30", NOW, IST, "24")["needs_confirm"])
        self.assertFalse(S.parse("pick up kids at 03:30", NOW, IST, "24")["needs_confirm"])
        p12 = S.parse("meeting with Rohan on 20 Sep 3pm", NOW, IST, "12")
        self.assertIn("3:00 PM", p12["interpretation"]); self.assertIn("15:00", S.parse("meeting with Rohan on 20 Sep 3pm", NOW, IST, "24")["interpretation"])
        # the same words in London are a different instant (and the item keeps its zone name)
        a = S.parse("remind me tomorrow at 9am to call the bank", NOW, IST); b = S.parse("remind me tomorrow at 9am to call the bank", NOW.astimezone(LONDON), LONDON)
        self.assertEqual(a["when"].hour, 9); self.assertEqual(b["when"].hour, 9)
        self.assertEqual(a["when_epoch"] - b["when_epoch"], -(5.5 - 1) * 3600)            # IST is UTC+5:30, London BST UTC+1 in September
        self.assertEqual(a["tz"], "Asia/Kolkata"); self.assertEqual(b["tz"], "Europe/London")
        self.assertEqual(S.fmt_when(L(2026, 9, 18, 9), NOW, "12"), "Tomorrow, 9:00 AM"); self.assertEqual(S.fmt_when(L(2026, 9, 25, 13), NOW, "24"), "Fri 25 Sep, 13:00")
        self.assertEqual(S.fmt_when(L(2027, 2, 1, 9), NOW, "24"), "Mon 1 Feb 2027, 09:00")

    def test_intent(self):
        self.assertEqual(S.intent("remind me tomorrow 9am to call the bank"), ("add", "remind me tomorrow 9am to call the bank"))
        self.assertEqual(S.intent("Schedule a meeting with Rohan on 20 Sep 3pm")[0], "add")
        self.assertEqual(S.intent("what's on my schedule today?"), ("list", "today"))
        self.assertEqual(S.intent("my reminders for tomorrow"), ("list", "tomorrow"))
        self.assertEqual(S.intent("Hey Fab, what is on my agenda this week?"), ("list", "upcoming"))
        self.assertIsNone(S.intent("remind me what the capital of France is"))      # a question for the model
        self.assertIsNone(S.intent("open the editor and write hello"))
        self.assertEqual(S.intent("schedule the system update tonight")[0], "add")   # a reminder at 21:00 (the echo says so); the agent has no deferred tasks
        self.assertTrue(S.is_confirmation("yes")); self.assertTrue(S.is_confirmation("haan")); self.assertTrue(S.is_rejection("no thanks") is False or S.is_rejection("no"))
        self.assertFalse(S.is_confirmation("no")); self.assertTrue(S.is_rejection("cancel"))


class RollForward(unittest.TestCase):
    def test_rules(self):
        fri = L(2026, 9, 18, 18, 30)
        self.assertEqual(S.next_occurrence(fri, "daily", fri), L(2026, 9, 19, 18, 30))
        self.assertEqual(S.next_occurrence(fri, "weekdays", fri), L(2026, 9, 21, 18, 30))            # skips Sat/Sun
        self.assertEqual(S.next_occurrence(fri, "weekly", fri), L(2026, 9, 25, 18, 30))
        self.assertEqual(S.next_occurrence(L(2026, 1, 31, 9), "monthly", L(2026, 1, 31, 9)), L(2026, 2, 28, 9))   # 31st clamps to the month's last day
        self.assertEqual(S.next_occurrence(L(2026, 1, 31, 9), "monthly", L(2026, 2, 28, 9), dom=31), L(2026, 3, 31, 9))
        self.assertEqual(S.next_occurrence(L(2026, 9, 17, 10), "weekly", L(2026, 9, 17, 10), weekday=0), L(2026, 9, 21, 10))
        # DST-safe: a daily 09:00 in London stays 09:00 wall clock across the October change (25 Oct 2026)
        d = dt.datetime(2026, 10, 24, 9, 0, tzinfo=LONDON)
        n = S.next_occurrence(d, "daily", d)
        self.assertEqual((n.hour, n.minute, n.date()), (9, 0, dt.date(2026, 10, 25)))
        self.assertEqual(n.timestamp() - d.timestamp(), 25 * 3600)                                  # the real gap is 25 hours


class FakeNotifier:
    def __init__(self):
        self.sent, self.on_action, self.backend = [], (lambda t, a: None), "fake"

    def start(self):
        return self

    def send(self, title, body, actions=(), urgency="normal", tag=None, timeout_ms=-1):
        self.sent.append({"title": title, "body": body, "actions": tuple(actions), "tag": tag, "urgency": urgency, "timeout_ms": timeout_ms})
        return len(self.sent)


class StoreAndLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched-store-")
        self.store = fa.Store(os.path.join(self.tmp, "agent.db"))
        self.store.set_setting("scheduler.timezone", "Asia/Kolkata")
        self.notifier = FakeNotifier()
        self.opened = []
        self.loop = S.SchedulerLoop(self.store, notifier=self.notifier, session_env=lambda: None, tick=3600)
        S.open_schedule_tab = lambda env=None, log=None: self.opened.append(env) or True
        self.sched = self.loop.sched

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_store_crud_and_scopes(self):
        s = self.sched
        a = s.add("Call the bank", L(2026, 9, 17, 11), source="chat")
        b = s.add("Gym", L(2026, 9, 17, 18, 30), repeat="weekdays", source="ui")
        c = s.add("Dentist", L(2026, 9, 20, 15), source="mail", remind_before=30, notes="bring the file")
        d = s.add("Old one", L(2026, 9, 16, 9))
        self.assertEqual([i["id"] for i in s.list("today", NOW)], [d["id"], a["id"], b["id"]])       # overdue pending shows under today
        self.assertEqual([i["title"] for i in s.list("upcoming", NOW)], ["Dentist"])
        self.assertEqual(s.list("done", NOW), [])
        self.assertEqual(s.get(c["id"])["remind_before"], 30); self.assertEqual(s.get(c["id"])["source"], "mail")
        self.assertEqual(s.get(a["id"])["when_text"], "Today, 11:00"); self.assertEqual(s.get(a["id"])["tz"], "Asia/Kolkata")
        e = s.update(a["id"], title="Call the bank manager", when=L(2026, 9, 18, 9, 15), repeat="none", remind_before=5)
        self.assertEqual((e["title"], e["when_local"][:16], e["remind_before"]), ("Call the bank manager", "2026-09-18T09:15", 5))
        with self.assertRaises(ValueError):
            s.update(a["id"], repeat="fortnightly")
        # done: one-off -> done; repeating -> a done copy for the occurrence + the item rolls forward (Thu 18:30 -> Fri 18:30)
        self.assertEqual(s.complete(a["id"], NOW)["status"], "done")
        rolled = s.complete(b["id"], L(2026, 9, 17, 18, 35))
        self.assertEqual((rolled["status"], rolled["when_local"][:16]), ("pending", "2026-09-18T18:30"))
        done = s.list("done", NOW)
        self.assertEqual(sorted(i["title"] for i in done), ["Call the bank manager", "Gym"]); self.assertEqual([i for i in done if i["title"] == "Gym"][0]["parent_id"], b["id"])
        # snooze / dismiss / remove
        sn = s.snooze(c["id"], 10, L(2026, 9, 20, 14, 30)); self.assertEqual(sn["snoozed_until"], L(2026, 9, 20, 14, 40).timestamp())
        self.assertEqual(s.dismiss(d["id"])["status"], "dismissed")
        self.assertTrue(s.remove(c["id"])); self.assertIsNone(s.get(c["id"])); self.assertFalse(s.remove(c["id"]))
        kinds = [r["kind"] for r in self.store.all("SELECT kind FROM activity")]
        for k in ("item_added", "item_edited", "item_done", "item_snoozed", "item_dismissed", "item_removed"):
            self.assertIn(k, kinds)

    def test_due_fire_actions_and_roll(self):
        s, loop = self.sched, self.loop
        a = s.add("Call the bank", L(2026, 9, 17, 10, 5), remind_before=5)
        b = s.add("Later", L(2026, 9, 17, 12))
        g = s.add("Gym", L(2026, 9, 17, 10), repeat="weekdays")
        self.assertEqual([i["id"] for i in s.due(NOW)], [g["id"], a["id"]])          # a: 10:05 - 5 min = 10:00 -> due now
        loop.tick_once(NOW)
        self.assertEqual([n["title"] for n in self.notifier.sent], ["Gym", "Call the bank"])
        n = self.notifier.sent[1]
        self.assertEqual([x[0] for x in n["actions"]], ["default", "done", "snooze", "open"]); self.assertEqual(n["actions"][2][1], "Snooze 10 min")
        self.assertEqual(n["tag"], ("item", a["id"])); self.assertIn("in 5 min", n["body"]); self.assertEqual(n["timeout_ms"], 0, "a reminder stays until answered")
        loop.tick_once(NOW + dt.timedelta(minutes=1))
        self.assertEqual(len(self.notifier.sent), 2, "a fired reminder does not fire again")
        # the buttons: Done completes (the repeating Gym rolls to Friday 10:00), Snooze re-arms 10 minutes later, Open opens the tab
        loop.on_action(("item", g["id"]), "done")                                  # the button uses the real clock: rolled past its time, a done copy left
        self.assertGreater(s.get(g["id"])["when_utc"], g["when_utc"]); self.assertEqual([i["title"] for i in s.list("done")], ["Gym"])
        self.assertEqual(s.complete(s.add("Gym 2", L(2026, 9, 17, 10), repeat="weekdays")["id"], NOW)["when_local"][:16], "2026-09-18T10:00")
        loop.on_action(("item", a["id"]), "snooze")
        self.assertIsNone(s.get(a["id"])["fired"]); self.assertGreater(s.get(a["id"])["snoozed_until"], time.time() + 9 * 60)
        loop.on_action(("item", a["id"]), "open"); self.assertEqual(len(self.opened), 1)
        loop.on_action(("summary", 0), "default"); self.assertEqual(len(self.opened), 2)
        # a repeating item that fired and was ignored rolls forward after ROLL_AFTER_S
        loop.tick_once(L(2026, 9, 17, 12, 0)); self.assertEqual(self.notifier.sent[-1]["title"], "Later")
        h = s.add("Standup", L(2026, 9, 18, 9, 30), repeat="daily"); loop.tick_once(L(2026, 9, 18, 9, 30)); self.assertEqual(self.notifier.sent[-1]["title"], "Standup")
        loop.tick_once(L(2026, 9, 18, 13, 0)); self.assertEqual(s.get(h["id"])["when_local"][:16], "2026-09-19T09:30")

    def test_missed_while_off_and_login_summary(self):
        s, loop = self.sched, self.loop
        s.add("Pay rent", L(2026, 9, 16, 9))                                      # yesterday — the computer was off
        s.add("Yoga", L(2026, 9, 17, 7), repeat="daily")                         # this morning, missed: rolls to tomorrow, a missed copy stays
        for i, t in enumerate(("Call the bank", "Meeting with Rohan", "Send invoice", "Review PR", "Pick up kids", "Groceries", "Gym")):
            s.add(t, L(2026, 9, 17, 11 + i))
        s.add("Dentist", L(2026, 9, 20, 15))
        r = loop.send_login_summary(now=NOW)
        self.assertTrue(r["sent"]); self.assertEqual(r["title"], "Today: 7 items")
        lines = r["body"].split("\n")
        self.assertTrue(lines[0].startswith("Missed while you were away: Pay rent (Yesterday, 09:00) · Yoga (Today, 07:00)"), lines[0])
        self.assertEqual(lines[1:6], ["11:00  Call the bank", "12:00  Meeting with Rohan", "13:00  Send invoice", "14:00  Review PR", "15:00  Pick up kids"])
        self.assertEqual(lines[6], "and 2 more in Schedule"); self.assertEqual(len(lines), 7)
        n = self.notifier.sent[-1]; self.assertEqual(n["title"], "Today: 7 items"); self.assertEqual(n["actions"][0], ("default", "Open")); self.assertEqual(n["tag"], ("summary", 0))
        self.assertEqual(s.get(1)["status"], "missed"); self.assertEqual(s.get(2)["when_local"][:16], "2026-09-18T07:00")   # one-off missed; repeating rolled
        self.assertEqual([i["title"] for i in s.list("missed", NOW)], ["Pay rent", "Yoga"])
        # once per login: the second call is de-duplicated; a NEW graphical session (a second login the same day) gets it again; force resends
        r2 = loop.send_login_summary(now=NOW); self.assertFalse(r2["sent"]); self.assertTrue(r2["deduped"]); self.assertEqual(len(self.notifier.sent), 1)
        self.assertTrue(loop.send_login_summary(now=NOW, session="9")["sent"]); self.assertEqual(len(self.notifier.sent), 2)
        self.assertTrue(loop.send_login_summary(now=NOW, session="9")["deduped"])
        self.assertTrue(loop.send_login_summary(force=True, now=NOW, session="9")["sent"]); self.assertEqual(len(self.notifier.sent), 3)
        self.assertEqual(self.notifier.sent[-1]["timeout_ms"], S.SUMMARY_TIMEOUT_MS)
        # nothing planned: still one line, and the loop tells about items missed during a sleep only after the summary went out
        empty = S.SchedulerLoop(fa.Store(os.path.join(self.tmp, "empty.db")), notifier=FakeNotifier(), tick=3600)
        e = empty.send_login_summary(now=NOW); self.assertEqual(e["title"], "Today: nothing scheduled"); self.assertIn("Nothing planned", e["body"])
        s.add("Slept through", L(2026, 9, 17, 16))
        missed, _ = loop.tick_once(L(2026, 9, 17, 18))                        # the lid was closed 10:00 -> 18:00: everything due in between is missed
        self.assertIn("Slept through", [m["title"] for m in missed]); self.assertEqual(len(missed), 8)
        self.assertEqual(self.notifier.sent[-1]["title"], "Missed while the computer was asleep"); self.assertEqual(self.notifier.sent[-1]["body"].count("\n"), 4)   # 5 lines

    def test_add_from_request_and_list_text(self):
        s = self.sched
        s.now = lambda: NOW                                                        # the request helpers read the store's clock
        r = S.add_from_request(s, {"text": "meeting at 3"}, "chat")
        self.assertTrue(r["needs_confirm"]); self.assertNotIn("item", r); self.assertEqual(s.list("all"), [])
        r = S.add_from_request(s, {"text": "meeting at 3", "confirm": True}, "chat")
        self.assertTrue(r["added"]); self.assertEqual(r["item"]["source"], "chat"); self.assertEqual(r["item"]["when_local"][:16], "2026-09-17T15:00")
        r = S.add_from_request(s, {"title": "Dentist", "when": "2026-09-20T15:00", "repeat": "none", "remind_before": 30}, "ui")
        self.assertEqual((r["item"]["title"], r["item"]["remind_before"], r["interpretation"]), ("Dentist", 30, "Sun 20 Sep, 15:00 — Dentist · reminder 30 min before"))
        self.assertIn("already passed", S.add_from_request(s, {"title": "Old", "when": "2026-09-17T09:00"}, "ui")["error"])
        self.assertIn("When should I remind you", S.add_from_request(s, {"text": "call the bank"}, "ui")["error"])
        self.assertIn("repeat must be", S.add_from_request(s, {"text": "gym at 6pm", "repeat": "fortnightly"}, "ui")["error"])
        r = S.add_from_request(s, {"title": "Standup", "when": "2026-09-17T09:00", "repeat": "weekdays"}, "ui")
        self.assertEqual(r["item"]["when_local"][:16], "2026-09-18T09:00", "a repeating item whose first time passed starts at the next occurrence")
        txt = S.list_text(s.list("today", NOW), "today", NOW, "24")
        self.assertTrue(txt.startswith("Today: 1 item — 15:00 Meeting"), txt)
        self.assertEqual(S.list_text([], "tomorrow", NOW, "24"), "Tomorrow: nothing scheduled.")
        self.assertEqual(S.parse_when_value("2026-09-20", IST), L(2026, 9, 20, 9))
        self.assertEqual(S.parse_when_value(L(2026, 9, 20, 9).timestamp(), IST), L(2026, 9, 20, 9))
        with self.assertRaises(ValueError):
            S.parse_when_value("next week", IST)


class MailIntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched-mail-")
        self.store = fa.Store(os.path.join(self.tmp, "agent.db")); self.store.set_setting("scheduler.timezone", "Asia/Kolkata")
        self.sched = S.ScheduleStore(self.store); self.sched.now = lambda: NOW
        self.replies = []
        self.msgs = [
            {"message_id": "<1@x>", "uid": "1", "from": "Harsh <me@example.com>", "subject": "Reminder: call the bank tomorrow at 9am", "body": "", "date": ""},
            {"message_id": "<2@x>", "uid": "2", "from": "me@example.com", "subject": "Schedule", "body": "Meeting with Rohan on 20 Sep 3pm\n\nPlease confirm.\n> quoted junk at 4am", "date": ""},
            {"message_id": "<3@x>", "uid": "3", "from": "Boss <boss@example.com>", "subject": "Reminder: pay me tomorrow 9am", "body": "", "date": ""},
            {"message_id": "<4@x>", "uid": "4", "from": "me@example.com", "subject": "Fwd: your invoice", "body": "remind me tomorrow 9am", "date": ""},
            {"message_id": "<5@x>", "uid": "5", "from": "me@example.com", "subject": "Remind - dentist at 3", "body": "let me know", "date": ""},
            {"message_id": "<6@x>", "uid": "6", "from": "ME@Example.com", "subject": "Re: Reminder gym every weekday 6.30 pm", "body": "", "date": ""},
        ]
        self.intake = S.MailIntake(self.sched, "me@example.com", lambda: list(self.msgs), lambda to, subj, body: self.replies.append((to, subj, body)))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_intake(self):
        r = self.intake.run_once(NOW)
        self.assertEqual(r["checked"], 6); self.assertIsNone(r["error"])
        created = {i["title"]: i for i in r["created"]}
        self.assertEqual(sorted(created), ["Call the bank", "Gym", "Meeting with Rohan"])
        self.assertEqual(created["Call the bank"]["when_local"][:16], "2026-09-18T09:00"); self.assertEqual(created["Call the bank"]["source"], "mail")
        self.assertEqual(created["Meeting with Rohan"]["when_local"][:16], "2026-09-20T15:00")            # the body's first line; the quoted line is ignored
        self.assertEqual(created["Gym"]["repeat"], "weekdays")
        skipped = dict(r["skipped"])
        self.assertEqual(skipped["<3@x>"], "not from the user's own address")                              # never another sender, whatever the subject
        self.assertEqual(skipped["<4@x>"], "subject does not start with Remind / Reminder / Schedule")
        self.assertTrue(skipped["<5@x>"].startswith("ambiguous: Today, 15:00 — Dentist"), skipped["<5@x>"])  # a bare "at 3" is never guessed from mail
        # replies only where the mail asked for one: #2 ("Please confirm") and #5 ("let me know"); never to other senders
        self.assertEqual(r["replied"], 2); self.assertEqual([x[0] for x in self.replies], ["me@example.com", "me@example.com"])
        self.assertIn("Added to your schedule: Sun 20 Sep, 15:00 — Meeting with Rohan", self.replies[0][2]); self.assertEqual(self.replies[0][1], "Re: Schedule")
        self.assertIn("could not add", self.replies[1][2]); self.assertIn("Today, 15:00 — Dentist", self.replies[1][2])
        # the second pass sees the same (still unseen) mail and does nothing
        r2 = self.intake.run_once(NOW); self.assertEqual((r2["created"], r2["skipped"], r2["replied"]), ([], [], 0))
        self.assertEqual(len(self.sched.list("all")), 3)
        self.assertTrue(any(a["kind"] == "mail_intake" for a in self.store.all("SELECT kind FROM activity")))
        # a search failure is reported, not raised
        broken = S.MailIntake(self.sched, "me@example.com", lambda: (_ for _ in ()).throw(RuntimeError("IMAP down")))
        self.assertIn("IMAP down", broken.run_once(NOW)["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
