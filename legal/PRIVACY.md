# Privacy posture

- Fab OS sends no telemetry, crash reports or usage data anywhere by
  default. `ubuntu-report`, `apport` auto-upload, `motd-news`, `popularity-
  contest` and `ubuntu-pro-client` are not installed.
- AI is local-first. Cloud AI providers (for example Claude) are off until the
  user adds their own API key in Settings and enables them. Keys are stored via
  systemd credentials, never in plaintext config, logs or provenance.
- The built-in offline model (Qwen2.5 1.5B, shipped inside the image, no
  account, no download) runs entirely on the machine: llama-server is bound
  to 127.0.0.1 only, loads on demand and is unloaded after ten idle minutes.
  With it selected, prompts, files and tool results never leave the computer.
- Before any data leaves the machine the FabOS AI service checks the user's
  privacy preference (local-only / ask / allow) and shows a cloud indicator.
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
  from the Patience AI repository. Both are standard apt transactions with no
  identifying payload.
- Applicable law: Digital Personal Data Protection Act 2023 (India) and GDPR
  where users are in the EU. Because nothing is collected, no consent flow is
  required for the OS itself.
