# ADR-0009: Rename upstream products through an `en@fabos` translation catalog

**Status:** accepted (2026-09-13) · supersedes the bare-word byte rules of ADR-0008

## Context
Window titles ("~ : bash — Konsole", "Home — Dolphin"), About dialogs and in-app sentences carry upstream product
names. Byte patching (ADR-0008) is limited to same-length replacements, cannot produce "Fab Terminal" from "Konsole",
and one bare-word UTF-16 rule corrupted Discover's Qt resource table (UTF-16BE names matched at odd offsets).

## Facts verified in the source and in the image
- ki18n never consults catalogs for the code language: `en` is normalised to `en_US` and `en_US` returns the source
  string. Locale **modifiers are preserved** (`en@fabos` stays `en@fabos`, then falls back to `en` → `en_US`).
- Plasma runs `plasma-workspace/env/*.sh` **before** it applies `plasma-localerc`, so a session env script can set
  `LANGUAGE` for English users without overriding an explicit user choice made later in Region & Language.
- `LANGUAGE=en@fabos:en_US kate --help` prints "Fab Editor - Advanced Text Editor" inside the image (offscreen).

## Decision
1. `rebrand-catalogs` generates `/usr/local/share/locale/en@fabos/LC_MESSAGES/*.mo` from installed catalogs' msgids
   and a rule table; attribution strings are excluded. Alias entries mirror the msgids that byte rules still rewrite.
2. `50-fabos-language.sh` exports `LANGUAGE=en@fabos:<English>` for English/C sessions only.
3. Byte patching is reduced to plugin metadata and non-translated literals; bare UTF-16 words are rejected.
4. Launcher/dock/shortcut names: `/usr/local/share/{applications,kglobalaccel}` overrides; store names: AppStream merge
   components.

## Consequences
- Proper names everywhere ("Fab Terminal", "Fab Files", "Fab Editor", "Fab Software", "Fab OS Desktop 6.6.6").
- Region & Language may list an extra English variant; choosing "American English" explicitly disables the branded
  catalogs for that user (documented limitation).
- Zero risk to binaries from names; upgrades re-generate catalogs via the apt hook (stamped, ~1.5 s).
