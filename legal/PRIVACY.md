# Privacy posture

- Fab OS sends no telemetry, crash reports or usage data anywhere by
  default. `ubuntu-report`, `apport` auto-upload, `motd-news`, `popularity-
  contest` and `ubuntu-pro-client` are not installed.
- AI is local-first. Cloud AI providers (for example Claude) are off until the
  user adds their own API key in Settings and enables them. Keys are encrypted
  with systemd credentials (`systemd-creds --user`) in the user's config
  directory, mode 0600; if that facility is unavailable the key is kept in a
  0600 file owned by the user instead. Keys never appear in logs, the task
  history or the activity log.
- The built-in offline model (Qwen2.5 1.5B, shipped inside the image, no
  account, no download) runs entirely on the machine: llama-server is bound
  to 127.0.0.1 only, loads on demand and is unloaded after ten idle minutes.
  With it selected, prompts, files and tool results never leave the computer.
- What leaves the machine is only the request and tool results sent to the
  one provider the user selected. The System-Wide AI switch turns the agent
  off entirely; the permission mode (Ask / Auto / Bypass) governs which actions
  need the user's approval before they run.
- One connectivity probe, only when a task needs the web (ADR-0020). When the
  built-in model runs a task whose request names a web page or URL, the agent
  first checks whether the internet is reachable so the model is told
  "online" or "offline" instead of guessing: a single HTTPS `HEAD /` to the
  configured cloud provider's API host, or — with the local model, whose
  endpoint is the loopback — to `1.1.1.1:443` (Cloudflare's public resolver),
  2-second timeout, no payload, no cookies, no identifier beyond the plain
  `FabOS-agent/1.0` user agent; the answer is cached for 60 seconds. Tasks that
  name no web page or URL (a copy, a count, a note) make no probe at all, and
  the status the desktop widgets poll only reports the last result, never
  triggering a new one. On a managed computer the administrator's
  `hosts_allowed` list binds the probe exactly as it binds `web_fetch`: a target
  not on the list is not probed. The System-Wide AI switch turns it off with
  the rest of the agent.
- Voice ("Hey Fab"): wake-word detection is fully offline — PocketSphinx
  listens on this computer only, and the microphone stream is never stored
  or sent anywhere while it waits for the phrase (only the last 6 s are held
  in memory so the wake clip can be double-checked offline). After "Hey Fab" (or a
  microphone press) the short recording of your request goes one of two ways:
  if you configured a cloud provider (OpenAI or Gemini) in Fab AI Controls and
  left `voice.offline_only` off, the recording — and the text to be spoken
  back — is sent to that provider; otherwise it is transcribed offline with the
  whisper.cpp model shipped in the ISO and never leaves the computer. Nothing is stored beyond
  the current utterance: the WAV lives in the session runtime directory for
  the duration of the transcription call and is deleted straight after. The
  listener is off with `fabos-voice wake off` (or the Voice switch in Fab AI
  Controls), and it stays silent when no microphone exists. A spoken "yes"
  is accepted only as a short, answer-shaped reply, never from a longer
  sentence, and a command run as administrator needs a clear "yes".
- Ubuntu package updates fetch metadata from Ubuntu mirrors; Fab OS updates
  from the Patience AI repository; Firefox updates from Mozilla's own
  repository. All are standard apt transactions with no identifying payload.
- No wallet (ADR-0015): KWallet is disabled and no wallet application is
  installed, so nothing asks for a wallet password. Wi-Fi and VPN secrets are
  kept by NetworkManager in root-only files under
  `/etc/NetworkManager/system-connections/` (readable by administrators of
  the machine, not by other ordinary users); the agent's keys stay in
  `systemd-creds` as described above; browser passwords are protected by the
  browser's own store.
- Firefox is a third-party application shipped exactly as Mozilla publishes
  it. Fab OS adds only Mozilla's documented enterprise-policy file
  (`/usr/lib/firefox/distribution/policies.json`, ADR-0018), which turns off
  Firefox telemetry and studies, the Terms of Use / Privacy Notice startup
  screen (`SkipTermsOfUse` — Patience AI accepts Mozilla's Firefox Terms of Use
  on behalf of Fab OS users; legal/OPEN-SOURCE-RELEASE-CHECKLIST.md C6; Mozilla's
  Privacy Notice still applies to what Firefox itself does), the first-run
  tour, the default-browser prompt and sponsored tiles and sets the home page;
  nothing is locked, and Firefox's own settings govern everything else it sends.
- Applicable law: Digital Personal Data Protection Act 2023 (India) and GDPR
  where users are in the EU. Because nothing is collected, no consent flow is
  required for the OS itself.
- Audit log (ADR-0017). The agent keeps an activity log on this computer only
  (`~/.local/share/fabos/agent.db`): which tasks ran, which steps were approved,
  denied or refused, setting changes, the names (never the values) of secrets
  that were set, root requests and their exit codes, and policy reloads. Commands
  are cut to 300 characters and tool outputs are not stored there; keys, passwords
  and mail bodies never appear. Each row is sealed with an HMAC chain so that later
  edits are detectable (`fabos audit verify`). Nothing in it leaves the machine
  unless the user, or an organisation that manages the computer through
  `/etc/fabos/policy.json`, exports it with `fabos audit export` into a directory
  the organisation collects. A separate root-only log (`/var/log/fabos/rootexec.log`)
  records every request to run a step as administrator; it stays on the machine too.
  On a managed computer the administrator's policy is visible to the user at any time
  (`fabos policy`, "Managed by your organisation" in Fab AI Controls).
