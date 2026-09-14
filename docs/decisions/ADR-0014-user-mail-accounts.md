# ADR-0014: The agent mails through the user's own account (Gmail, Outlook, Yahoo, Zoho, iCloud, any IMAP/SMTP)

Date: 2026-09-15. Status: accepted.

## Context

Until ISO 1.0 rev 2 the agent's `send_email` tool offered two transports: the user's own SMTP/IMAP server (nine raw
fields: host, port, security, user, from, …) or the Brevo transactional API — the same relay Fab Feedback uses to reach
Patience AI. Brevo is right for the feedback relay (one sender, our key, our inbox) and wrong for the agent: a user's
mail must leave from *their* address, through *their* provider, with credentials that never touch Patience AI, and the
setup must be "sign in", not "fill in nine server fields". The owner's request: Gmail and the popular providers with a
Sign-in flow, Brevo only for feedback, and a visibly working "write a hi and send it to X".

## Decision

1. **Presets, not fields.** `mail.provider` ∈ {gmail, outlook, yahoo, zoho, icloud, other} selects a preset in
   `fabos_agentd.MAIL_PROVIDERS`; the user types only `mail.address` (and optionally `mail.from_name`). Server fields
   (`mail.smtp_host/port/security`, `mail.imap_host/port`) stay as *Advanced* overrides and are stored only when they
   differ from the preset, so a later preset change follows through. When no provider is stored the daemon infers it
   from the address domain (`hotmail.com` → outlook, `me.com` → icloud, …; unknown → other). `mail.user` from earlier
   databases is read as `mail.address`; `mail.transport`, `mail.from` and the `mail_api_key` secret are gone from the
   agent (the feedback package keeps its own Brevo configuration untouched). An empty override means "use the preset";
   `mail.imap_host` set to `none` (also `-` / `off`, `MAIL_NO_IMAP`) means "this account has no IMAP — sending only",
   which is the only way to switch reading off for a preset that ships an IMAP host. `PUT /settings` accepts `""` for
   `mail.provider` and `mail.auth` (unset: infer from the address again / password), and the `fabos` CLI exits 1
   whenever the daemon answers 4xx.

   Presets, checked on 2026-09-15 against the providers' public documentation (fetched from the host):

   | Provider | SMTP | IMAP | Credential the provider expects | Source |
   |---|---|---|---|---|
   | Gmail | smtp.gmail.com **587 STARTTLS** (465 SSL also documented) | imap.gmail.com **993 SSL** | app password (2-Step Verification on) or OAuth 2.0 | developers.google.com/workspace/gmail/imap/imap-smtp |
   | Outlook / Hotmail | smtp-mail.outlook.com **587 STARTTLS** | outlook.office365.com **993 SSL/TLS** | SMTP: app password (`AUTH LOGIN` still offered; a wrong one answers 535 5.7.3). IMAP: **no password at all** — the server advertises `LOGINDISABLED` + `AUTH=XOAUTH2` only and answers `LOGIN` with "Basic authentication is disabled" (probed live 2026-09-15) | support.microsoft.com, "POP, IMAP, and SMTP settings for Outlook.com" |
   | Yahoo Mail | smtp.mail.yahoo.com **465 SSL** (587 also documented) | imap.mail.yahoo.com **993 SSL** | generated app password; a wrong one makes the SMTP server **drop the TLS connection at AUTH** instead of answering 535 (probed live 2026-09-15, on 465 and 587); IMAP answers `AUTHENTICATIONFAILED` | help.yahoo.com, "IMAP server settings for Yahoo Mail" (SLN4075) |
   | Zoho Mail | smtp.zoho.com **465 SSL** (587 TLS also documented) | imap.zoho.com **993 SSL** | account password; app password when two-factor is on; IMAP access must be enabled in Zoho Mail settings | zoho.com/mail/help/zoho-smtp.html, …/imap-access.html |
   | iCloud Mail | smtp.mail.me.com **587** (SSL required; TLS/STARTTLS) | imap.mail.me.com **993 SSL** | app-specific password | support.apple.com/en-us/102525 |

   The hosts and ports above were read from those pages. The two failure shapes in the table (Outlook IMAP
   `LOGINDISABLED`, Yahoo SMTP dropping the connection at AUTH) were observed live from the host with a bogus
   credential; successful logins were not exercised (no credentials in the build), so `POST /mail/test` is the user's
   proof.

2. **Two credential kinds.** `mail.auth` = `password` (secret `mail_password`, an app password where the table says so)
   or `oauth` (secret `mail_oauth_refresh`, Google only). Both go through the existing `set_secret` (systemd-creds,
   0600 fallback). SMTP uses `AUTH XOAUTH2` (`smtplib.SMTP.auth`), IMAP `AUTHENTICATE XOAUTH2`
   (`imaplib.IMAP4.authenticate`); the SASL string is `user=<address>\x01auth=Bearer <token>\x01\x01`. Access tokens
   are refreshed from the stored refresh token and cached in memory until they expire. Storing an app password
   switches `mail.auth` back to `password`; removing the refresh token does the same.

3. **"Sign in with Google" is gated on the distributor's OAuth client.** Google's OAuth for installed apps needs a
   registered *Desktop* client. The daemon reads `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` from
   `/etc/fabos/google-oauth.env` (`FABOS_GOOGLE_OAUTH_ENV` overrides the path for tests). When the file or the id is
   missing, `GET /settings` reports `mail_oauth.google=false` with the reason, and Fab AI Controls shows the
   app-password path and says why. When present: `POST /mail/oauth/start` opens a loopback HTTP server on
   `127.0.0.1:<random>`, builds the authorization URL (scope `https://mail.google.com/` — the only scope Google accepts
   for IMAP/SMTP — `access_type=offline`, `prompt=consent`, PKCE S256, random `state`), opens it in the session's
   browser (`xdg-open`) and returns `{flow_id, url}`; `GET /mail/oauth/status?flow_id=` polls `pending → done | error`.
   The redirect handler checks `state`, exchanges the code (with the PKCE verifier) for tokens, reads the address from
   the Gmail profile endpoint, stores the refresh token, sets `mail.provider=gmail`, `mail.auth=oauth`,
   `mail.address`, and shows a small "Signed in — you can close this tab" page. A flow times out after 10 minutes.
   When a flow is decided (done, error or timed out) its loopback server is shut down **and closed** (`server_close`
   — `shutdown` alone leaves the bound socket accepting connections for the daemon's lifetime), the expiry timer is
   cancelled, the result stays pollable for `OAUTH_RESULT_GRACE` (120 s) so the UI's last poll still sees it, and the
   flow is then forgotten.

   **How the owner registers the client (exact steps, Google Cloud Console):**
   1. console.cloud.google.com → create (or pick) a project for Fab OS.
   2. *APIs & Services → Library* → enable **Gmail API**.
   3. *APIs & Services → OAuth consent screen* (Google Auth Platform → Branding / Audience): user type **External**,
      app name "Fab OS", support e-mail support@patienceai.in, homepage https://fabos.patienceai.in, privacy policy
      https://fabos.patienceai.in/privacy (legal/PRIVACY.md is published there); *Data access / Scopes*: add
      `https://mail.google.com/`. This is a **restricted** scope: while the app is in *Testing* only listed test users
      can sign in and refresh tokens expire after 7 days; to ship it to everyone the app must be **published** and
      pass Google's restricted-scope verification (privacy policy, a demo video of the sign-in, and a CASA security
      assessment for the mail scope). Budget weeks, not days.
   4. *Credentials → Create credentials → OAuth client ID* → application type **Desktop app**, name "Fab OS agent".
      Copy the client id and client secret. Loopback redirects (`http://127.0.0.1:<port>`) are implicit for Desktop
      clients; nothing to register there.
   5. Ship them in the image (or the `fabos-agent` package's conffile) as
      ```
      /etc/fabos/google-oauth.env     (mode 0644 — a Desktop client's secret is not confidential by Google's own definition; it only identifies the app)
      GOOGLE_OAUTH_CLIENT_ID=<id>.apps.googleusercontent.com
      GOOGLE_OAUTH_CLIENT_SECRET=<secret>
      ```
      Never commit the real values to this repository; the file is absent from the source tree on purpose.
   6. Verify in a VM: Fab AI Controls → Settings → Mail → provider Gmail → **Sign in with Google** → browser → the
      dialog shows the animated check and "Signed in with Google as …" → `fabos mail-check` reports SMTP and IMAP ok.

4. **`POST /mail/test`** `{provider, address, password?, mail.smtp_host?, …}` → `{ok, detail, smtp:{ok,detail},
   imap:{ok,detail}, latency_ms, provider, address, auth}` really signs in to both servers in parallel, 15 s each
   (`MAIL_TEST_TIMEOUT`), with the typed password when given (so a form can be checked before it is saved) or the
   stored credential otherwise. It distinguishes three classes:
   - **wrong password / app password required** — SMTP 535/534 (`SMTPAuthenticationError`), IMAP `AUTHENTICATIONFAILED`
     / "Invalid credentials", **and** an `SMTPServerDisconnected` raised *after* a successful EHLO/STARTTLS (the
     sign-in phase; Yahoo closes the TLS connection instead of answering 535). Phrased as "wrong password — Gmail needs
     an app password, not your account password" / "wrong password — Yahoo Mail closed the connection at sign-in, which
     it does for a wrong or missing app password" for providers in the table, "the server refused the login" / "the
     server closed the connection at sign-in" for others. A disconnect *before* the sign-in phase stays a
     "mail server error".
   - **password sign-in switched off for IMAP** — the server advertises `LOGINDISABLED` without a PLAIN/LOGIN SASL
     mechanism, or answers `LOGIN` with "Basic authentication is disabled" (Outlook.com). No app password can fix that,
     so it is *not* reported as a wrong password: the IMAP leg comes back `{ok: false, skipped: true, login_disabled:
     true, detail: "Outlook / Hotmail has switched off password sign-in for IMAP (LOGINDISABLED) — sending with the app
     password works, reading the inbox does not"}`, SMTP decides `ok`, and the overall detail is "Signed in (sending
     only)". `check_email` on such an account fails with the same sentence; `send_email` works.
   - **cannot reach host:port (reason)** — DNS, refused, timed out.

   With no IMAP server (provider *other*, or `mail.imap_host=none`) IMAP is skipped and SMTP decides. The password is
   never logged: the activity row records provider, address and the verdict only. `send_email` / `check_email` turn a
   failed sign-in into the same sentences plus "the user fixes it in Fab AI Controls → Settings → Mail" instead of a raw
   smtplib/imaplib repr.

5. **Fab AI Controls → Settings → Mail** is: provider dropdown (Gmail first), address, one **Sign in** button. With
   Google OAuth available it runs the browser flow and shows the animated check; otherwise it reveals, *in reading
   order*, the app-password field, the provider's 3-step hint ("Google Account → Security", "2-Step Verification", "App
   passwords …" — plus the preset's note where one exists, e.g. Outlook's "IMAP password sign-in is switched off") and
   then the **Check connection** row under the field it checks, with the same animated check / shake-on-failure
   pattern as the provider check. A sending-only result shows "Signed in · SMTP ✓ · IMAP off · N ms" and the reason on
   a second line, and still enables Save; once a check has passed the 3-step hint is hidden (it comes back when the
   form changes). Save is disabled until the check passed for exactly the values in the form (or "Require a successful
   check" is unticked under Advanced). Advanced → IMAP server takes `none` for a sending-only account. The welcome
   wizard gained a Mail page whose button opens `fabos-command-center --settings mail`.

6. **Show your work.** The system prompt now tells the model: when the user asks to *write* or *compose* something,
   `open_app` the right app first (kate = Fab Editor for notes/text/code, `libreoffice --writer` for documents; a mail
   body is composed in Fab Editor), `type_text` the content so it appears on screen, save it with `write_file` to the
   same path (and say so, there is no shortcut tool), then do the follow-up (`send_email`, run the code) and narrate.
   Pure file/system operations skip the window. The graded ladder gained L1-f (open_app THEN type_text) and L2-f
   (open_app THEN type_text THEN send_email, mail delivered to the single authorised recipient); the scripted provider
   follows the same order so `tests/agent-test.py` proves the sequence offline against a fake SMTP server.

## Consequences

- Without the distributor's Google client, Gmail works through an app password (2-Step Verification required by
  Google); the UI says so instead of failing silently. Outlook.com has switched password sign-in off for IMAP
  (`LOGINDISABLED`, only `AUTH=XOAUTH2`; verified live): an Outlook account is therefore **sending-only** in Fab OS —
  the check reports the IMAP leg as switched off with that reason (not as a wrong password), SMTP with the app password
  decides, Save is enabled, `send_email` works and `check_email` explains why it cannot. A "Sign in with Microsoft"
  (OAuth 2.0, XOAUTH2 on IMAP) is future work and would need a registered Azure app the same way Google needs a Desktop
  client.
- The ladder and the live-VM script take `MAIL_PROVIDER`, `MAIL_ADDRESS`, `MAIL_APP_PASSWORD` and `MAIL_TO` instead of
  a Brevo key; without them the mail tasks are recorded as *optional* SKIPs that do not fail the core levels.
- Settings removed because nothing reads them any more: `mail.transport`, `mail.from`, secret `mail_api_key`.
