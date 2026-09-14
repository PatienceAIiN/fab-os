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
- Ubuntu package updates fetch metadata from Ubuntu mirrors; Fab OS updates
  from the Patience AI repository. Both are standard apt transactions with no
  identifying payload.
- Applicable law: Digital Personal Data Protection Act 2023 (India) and GDPR
  where users are in the EU. Because nothing is collected, no consent flow is
  required for the OS itself.
