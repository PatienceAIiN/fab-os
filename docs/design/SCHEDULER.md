# Schedule — reminders for the day, in your own words

Fab OS 1.0-8 (`fabos-agent`). The owner's ask: *a scheduler which reminds of work, calendar, mails and more — full
computer tasks assigned for a day — settable by the user via a reminder mail and in the OS too; when the user logs in,
show an instant notification of today's schedule which the user set in natural language; time and date set by the user.*

Everything lives in the agent package: `packages/fabos-agent/usr/lib/fabos/agent/scheduler.py` (parser, store, reminder
loop, mail intake, CLI), its wiring in `fabos_agentd.py` (the `schedule` tool, `/schedule/*`, the chat shortcut), the
Schedule tab in `command_center.py`, and one user unit `fabos-schedule-summary.service`. Nothing needs the network.

## What the user sees

| Where | What |
|---|---|
| Any chat (ask bar, Fab AI Controls, `fabos do`) | "remind me tomorrow at 9am to call the bank" → *Added: Tomorrow, 09:00 — Call the bank. I will remind you then.* A reading the parser is not sure about is put back as a question ("I read that as Today, 15:00 — Call Rohan · 'at 3' read as 15:00. Shall I add it?") and the item is created only after *yes* (or a clearer time). "what's on my schedule today?" lists the day. |
| Fab AI Controls › **Schedule** (sidebar, `fabos-command-center --schedule`) | Today / Upcoming / Done lists; an add form whose plain-language field shows the reading live next to an editable date/time, repeat and remind-before; Done · Edit · Remove per row; time zone, 12/24 h and mail intake at the bottom. |
| Desktop notification at the time | Title = the item, body = the time; buttons **Done · Snooze 10 min · Open** (D-Bus `org.freedesktop.Notifications`, the same path fabos-updates uses because `notify-send` drops buttons on Plasma 6). `remind_before` minutes earlier when set. |
| At login | ONE notification "Today: 3 items" with up to 5 lines (`09:00  Call the bank`) and "and N more in Schedule"; items that fell due while the computer was off come first as "Missed while you were away: …". Clicking opens the Schedule tab. Once per login (de-duplicated by date + boot id + session id). |
| Mail | A mail the user sends to their **own** inbox with a subject starting `Remind`, `Reminder` or `Schedule` becomes an item (subject first, then the first body lines). Mail from anyone else is never read for this. A reply with the interpretation goes back only when the mail says reply / confirm / let me know. Every 10 minutes while the account is signed in; setting `scheduler.mail_intake`. |

## The parser (deterministic, offline)

`scheduler.parse(text, now, tz, clock)` → `{ok, title, when, repeat, needs_confirm, assumptions, error, interpretation}`.
English and common Indian-English: `tomorrow / tmrw / day after tomorrow / today itself / tonight`, weekdays with
`next / this / coming`, `20 Sep / September 20th / 20/09/2026 / 2026-09-20 / the 15th`, clock times `9am / 6.30 pm /
15:00 / half past six / 4 o'clock / 6 baje / noon / midnight`, day parts (`morning` 09:00, `afternoon` 15:00, `evening`
18:00, `night` 21:00 — always said back in the echo), relative `in 45 minutes / in 2 hours / in 3 days / in a week`,
repeats `every day / every weekday / every Monday / weekly / on the 1st every month / end of every month`, and
`10 minutes before` → `remind_before`. The rest of the sentence, minus connector words, is the title.

Three outcomes, never a silent guess:

* **exact / assumed** — created at once; every assumption is in the echo (*'morning' read as 09:00*, *09:00 has passed today, so tomorrow*).
* **ambiguous → `needs_confirm`** — a bare hour (*at 3* → 15:00?), `5/6` (day/month?), `next week`, `this weekend`, a date without a time (→ 09:00), `3.30` without am/pm. The guess is shown; chat asks, the UI shows it in the editable date/time, mail intake skips it (and says so in the reply when one was asked for).
* **fail with a reason** — no time at all, `30 Feb`, `25:00`, `every other day` / `every 2 weeks` / `every weekend` (supported: daily, weekdays, weekly, monthly), two times in one sentence, a time already passed, `in a few minutes`.

The clock preference matters: `10:30` is exact under 24 h and a confirmable guess under 12 h; `3.30` is a guess under
both. The time zone is the system's (`/etc/timezone`, `/etc/localtime`) unless `scheduler.timezone` names another
(IANA name, validated). Items store the UTC instant **and** the zone name; repeating items keep their wall-clock time
across DST (`next_occurrence` combines the local date with the local time).

`tests/scheduler-test.py` carries the corpus: 68 cases — 55 phrasings with the expected local date/time, title, repeat
and confirm flag, and 13 that must fail with a reason. A midnight named with a night part (`at 12 tonight`, `tomorrow
night at midnight`) is the midnight that ends that night.

## Store

Table `schedule` in `~/.local/share/fabos/agent.db` (the agent's store): `id, title, when_utc, tz, repeat
none|daily|weekdays|weekly|monthly, source chat|mail|ui|tool, status pending|done|dismissed|missed, remind_before,
created, updated, notes, fired, snoozed_until, done_at, parent_id, origin, rep_weekday, rep_dom`; `schedule_mail`
remembers processed Message-IDs. Every change is an `activity` row (`item_added`, `item_done`, `reminder_shown`,
`login_summary`, `mail_intake`, …) in the HMAC chain like everything else.

* **Done** on a one-off item → `done`. On a repeating item → a `done` copy of that occurrence (parent_id) and the item
  rolls to the next occurrence. **Dismiss** likewise; **Snooze** re-arms it 10 minutes later.
* **Missed while off** — `sweep()`: pending, never fired, due more than 15 minutes ago → one-off items become `missed`
  (they head the login summary and sit in Today with a badge); repeating ones leave a `missed` copy and roll forward. A
  repeating item that fired and got no answer rolls forward after 3 hours. If the sweep finds missed items after the
  login summary already went out (the laptop slept with the session open), one "Missed while the computer was asleep"
  notification is shown.

## Daemon wiring

* Tool `schedule` (`action add|list|done|remove|snooze|dismiss`; LOW risk) for every provider's model. `add` takes the
  user's words (`text`) or `title + when (YYYY-MM-DDTHH:MM local) + repeat`; a `needs_confirm` result tells the model to
  ask before adding with `confirm=true`.
* Chat shortcut (`Agent._schedule_shortcut`): `scheduler.intent()` recognises "remind me …", "set a reminder", "schedule
  a …", "add a reminder", "what's on my schedule …" and handles them without a provider — offline, no key, the same
  tool step + final text as any task. Ambiguity goes through the existing `ask_user` question/answer flow (the UIs show
  the question bubble). "remind me what the capital of France is" and phrases the parser cannot read go to the model.
* Endpoints: `GET /schedule?scope=…`, `GET /schedule/today`, `POST /schedule`, `POST /schedule/parse`,
  `PATCH /schedule/{id}`, `POST /schedule/{id}/done|snooze|dismiss|reopen`, `DELETE /schedule/{id}`,
  `POST /schedule/login-summary`, `POST /schedule/mail-intake/run`, `POST /schedule/tick`; `/status.schedule` carries
  today's count and the next item; `/settings` carries `scheduler.*` and `scheduler_system_timezone`.
* `SchedulerLoop` (daemon thread, 30 s): sweep → fire due → roll → mail intake every 10 min. `Notifier` owns a GLib
  main loop on a thread of its own and maps notification ids to items so `ActionInvoked` (Done / Snooze / Open, or the
  body click) acts on the right one. Its proxy follows the owner of `org.freedesktop.Notifications`
  (`follow_name_owner_changes`) and a failed `Notify` gets one fresh proxy and a retry: plasmashell restarts at every
  login, and the first VM proof lost the "Today" summary to a proxy still bound to the old server's unique name.
  Reminder popups carry timeout 0 (they stay until answered); the summary 20 s. `FABOS_SCHED_NOTIFY=notify-send` forces
  the button-less fallback (tests shim the binary; nothing under test ever reaches the developer's session bus).
* `fabos-schedule-summary.service` (`WantedBy=graphical-session.target`, enabled `--global` in postinst) runs
  `scheduler.py --login-summary`: waits for the agent (≤ 90 s), for the notification server to be up and not inhibited
  (its `Inhibited` property; ≤ 90 s) and then 15 s more for the desktop to settle — plasmashell owns the bus name while
  its panel is still loading, and a notification sent then lands in the history without a popup (VM proof, run 2) —
  then `POST /schedule/login-summary {session}` with the graphical session's id from `loginctl`
  (the user manager's own environment keeps the FIRST session's id all day). The daemon de-duplicates per
  date + boot + session, so a unit restart never shows it twice, a second login the same day does; `{force: true}` always.

## Settings

| key | default | meaning |
|---|---|---|
| `scheduler.timezone` | `""` | IANA zone for reading and showing times; empty = the system zone |
| `scheduler.clock` | `24` | `12` or `24`: how times are shown, and how a bare `10:30` is read |
| `scheduler.mail_intake` | `true` | read the user's own "Reminder:" mails (only when the mail account is signed in with IMAP) |

## Limits (honest)

* One time per reminder; repeats are daily / weekdays / weekly / monthly only (no "every other day", no per-item end date).
* Day-part conventions are fixed (morning 09:00 …) and stated in the echo rather than asked.
* Mail intake requires the sender to be the user's own address **and** the subject keyword; the mail stays unseen
  (IMAP read-only) and is remembered by Message-ID so it is processed once.
* That sender check trusts the **From header** (what the IMAP `FROM` search and `parseaddr` see); it does not verify
  DKIM/SPF, so a forged From that gets past the provider's filters could plant a reminder. That is the whole blast
  radius: an item with the forger's words and its notification — never a command, never a reply to the forger (the
  confirmation goes to the user's own address only, and only when the mail asks for one). Intake reads at most 20
  mails a pass and shows ONE notification per pass however many were added.
* The confirmation's subject is `Your schedule: added — …` / `Your schedule: not added` and its body ends with a
  marker line; the intake skips mail with either (`MailIntake.is_own_reply`). The reply lands in the same inbox from
  the same address, and without this guard every pass would read the previous reply as a new request
  (`SUBJECT_RE` strips `Re:`): a duplicate item, another mail and a popup every 10 minutes.
* "schedule the system update tonight" makes a *reminder* — the agent has no deferred task execution.
* The ask bar shows the chat reply; it has no dedicated Today card (optional in the brief; not done in this round).
* Indian-language words are limited to `baje`, `kal` is deliberately not read (it means both yesterday and tomorrow).

## Tests and proof

* `python3 tests/scheduler-test.py` — parser corpus, zones/clock, roll-forward (DST), store, missed-while-off, loop +
  buttons, login summary (5 lines + "and N more"), mail intake with a stub message set, request helpers.
* `python3 tests/agent-test.py Daemon` — the tool and endpoints through the daemon with the scripted provider; the
  chat shortcut incl. the confirm question; the login summary text; settings validation.
* `podman run … tests/ai-controls-render.py /work/build --schedule` — the Schedule tab in dark + light with sample
  items (`build/ai-controls-schedule-{dark,light}.png`), the live parse label, Add, Done.
* VM evidence under `build/r8-scheduler/` (see the round's report): a real reminder notification and the "Today" login
  notification in the booted OS.
