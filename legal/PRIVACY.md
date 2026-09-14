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
  from the Patience AI repository; Brave Browser updates from Brave's own
  repository. All are standard apt transactions with no identifying payload.
- No wallet (ADR-0015): KWallet is disabled and no wallet application is
  installed, so nothing asks for a wallet password. Wi-Fi and VPN secrets are
  kept by NetworkManager in root-only files under
  `/etc/NetworkManager/system-connections/` (readable by administrators of
  the machine, not by other ordinary users); the agent's keys stay in
  `systemd-creds` as described above; browser passwords are protected by the
  browser's own store.
- Brave Browser is a third-party application shipped exactly as Brave
  publishes it. Its own settings govern what it sends (for example Brave
  Rewards, Brave News and its product analytics); Fab OS does not change or
  pre-answer them.
- Applicable law: Digital Personal Data Protection Act 2023 (India) and GDPR
  where users are in the EU. Because nothing is collected, no consent flow is
  required for the OS itself.
