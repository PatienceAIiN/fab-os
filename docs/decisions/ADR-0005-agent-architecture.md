# ADR-0005: The Fab OS agent — Python daemon, deterministic policy, provider-agnostic

## Problem
Fab OS must let a user say "open editor, write hi, mail it and tell me when they reply" and have the OS do it,
end to end, safely, with history — as an OS capability, not an app.

## Selected
- `fabos-agentd`: per-user systemd service (runs in the graphical session so it can open apps, type, run
  commands as the user). Local HTTP API on 127.0.0.1 with a per-session bearer token (0600 in XDG_RUNTIME_DIR).
- Agentic loop with tools: shell, files, apps (via .desktop inventory), typing (wtype), email (SMTP/IMAP),
  web, notifications, questions, background watches (email reply / command) with follow-up tasks.
- Deterministic policy: every tool call is classified LOW/MEDIUM/HIGH/CRITICAL by regex/heuristics; the user's
  mode (ask/auto/bypass) decides whether an approval is required. Denials are returned to the model as errors.
- Providers behind one interface: Claude (Anthropic SDK, default model claude-opus-5, server-side refusal
  fallbacks) and any OpenAI-compatible endpoint (OpenAI, Gemini, local llama-server). Keys via systemd-creds.
- Surfaces: desktop ask bar (plasmoid), Fab AI Controls chat app (PyQt6; formerly "Command Center"), KRunner D-Bus runner, Dolphin action, CLI.
- Storage: SQLite (tasks, steps, approvals, questions, watches, settings, activity). Everything auditable.

## Why Python (not Rust like the rest of ai-native-os)
Speed of iteration for a large tool surface and GUI; the security boundary is the deterministic policy and the
user's own privileges, not memory safety of the daemon. Port hot paths to Rust once the tool surface stabilises.

## Tradeoffs / risks
The agent runs with the user's full privileges by design; policy + approvals are the containment, plus Linux
permissions (no root without pkexec). Prompt injection via tool output is mitigated by policy (authority never
comes from content) but not eliminated. Tested with a scripted provider (tests/agent-test.py); live-model
evaluation (tests/agent-live-test.py) requires a key.
