# Renamed upstream strings: translation catalogs first, byte patches last

Fab OS hides upstream vendor and product names from the user interface. Two mechanisms are used, both shipped in
source form in the `fabos-desktop` package and this repository, both re-applied by the apt hook
`/etc/apt/apt.conf.d/90fabos-rebrand` after every package operation.

## 1. Branded English translation catalogs (`/usr/lib/fabos/rebrand-catalogs`)

KDE applications translate every visible string through ki18n/gettext. `rebrand-catalogs` generates catalogs for the
language variant **`en@fabos`** in `/usr/local/share/locale/en@fabos/LC_MESSAGES/<domain>.mo`, built from the message
ids of the catalogs already installed for other languages and a rule table ("Konsole" → "Fab Terminal", "Dolphin" →
"Fab Files", "KDE Wallet" → "Fab Wallet", "Plasma" → "Fab OS", …). English sessions set
`LANGUAGE=en@fabos:<user's English>` (`/etc/xdg/plasma-workspace/env/50-fabos-language.sh`), so branded strings are
used first and everything else falls through to the normal English text. Other languages are not touched.

- **No package file is modified**; the catalogs are ordinary translation data, the same mechanism any language pack
  uses. Removing the directory or the env script restores upstream wording instantly.
- Attribution is preserved by construction: strings containing copyright notices, author/maintainer/translator
  credits, licence texts, e-mail addresses or links are never rewritten (see `SKIP` in the script).
- The "About KDE" dialog is reachable as "About Desktop Platform"; its content (KDE community credits) is unchanged.

## 2. Length-preserving byte patches (`/usr/lib/fabos/rebrand-binaries`)

Used only where no catalog can reach: System Settings module names live in JSON/CBOR plugin metadata inside the
`.so`, and a few literals are not translated. Replacements are **byte-length-preserving** (for example
`KDE Wallet` → `Fab Wallet`, `Plasma Style` → `Fab OS Style`, `\0Konsole\0` → `\0Console\0`, PackageKit origin label
`Ubuntu ` → `Fab OS `). Rules and the exact file groups are in the script.

Facts and obligations:

- The affected packages are free software (LGPL-2.1+/GPL-2.0+: KWallet, KWalletManager, KInfoCenter, KXmlGui,
  Konsole, Plasma KCMs/applets/kded modules/runners, PackageKit apt backend). Modifying and redistributing them is
  permitted; the licences are unchanged and the copyright notices in `/usr/share/doc/*/copyright` are untouched.
- Every patched file keeps its original next to it as `<file>.fabos-orig`; patching always restarts from the
  original, so a changed rule set converges, and files that leave the target list are restored automatically.
  `dpkg --verify` reports patched files as modified, which is expected.
- Corresponding source for the unmodified packages is offered under `legal/SOURCE-OFFER.md`; the modification
  itself is this script.
- Only display strings are changed. No functional identifiers are touched: UTF-8 rules must contain a space or be a
  whole NUL-terminated word, UTF-16 rules must contain a space. Bare UTF-16 words are forbidden because Qt resource
  name tables are UTF-16BE and a bare UTF-16LE word matches them at odd offsets (this broke Discover once and is now
  rejected by the script's self-check).
- Upstream projects (KDE e.V., Konsole authors, PackageKit) are credited in `legal/THIRD-PARTY.md`; Fab OS does not
  claim authorship of these programs.

## 3. Text-file wording (`/usr/lib/fabos/rebrand-desktop-entries`)

Desktop entries, global-shortcut component names and the Ubuntu web shortcut are overridden with files in
`/usr/local/share` (higher XDG priority, originals untouched). The Software Sources dialog (`software-properties-qt`,
GPL-2.0+) has three wording lines changed in its Python/UI sources with `.fabos-orig` copies kept. AppStream names of
bundled system apps are supplied as merge components in `/usr/share/swcatalog/xml/fabos-names.xml`.
