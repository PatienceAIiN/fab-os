# Releasing Fab OS

The per-release checklist for both kinds of release, the rules the owner set, and where each public artefact comes
from. `docs/QA.md` is the engineering record of what was measured; this file is the procedure. `docs/UPDATES.md`
describes the over-the-air mechanism itself.

## 1. Vocabulary and the two rules that never bend

| Term | Meaning |
|---|---|
| **Package revision `1.0-N`** | `DISTRO_VERSION`-`PKG_REVISION` from `brand/brand.conf`. Every Fab OS package carries it. Bumped by one for every release of any kind. |
| **Image release** | A new ISO (`scripts/build-rootfs.sh iso && scripts/build-iso.sh`) at revision `1.0-N`, served from `https://fabos.patienceai.in/download/`. Installed systems get the same packages over the air. |
| **Over-the-air (OTA) release** | Revision `1.0-N` published only to the signed update channel (`scripts/publish-apt.sh`). No ISO is rebuilt. |
| **Tag** | `v<DISTRO_VERSION>.<PKG_REVISION-1>`: `1.0-6` → `v1.0.5`, `1.0-7` → `v1.0.6`, `1.0-8` → `v1.0.7`. `scripts/release-github.sh` derives the revision from the tag and refuses a tag the changelog does not agree with. |
| **Changelog entry** | One section in `docs/CHANGELOG.md`, the single source of truth for everything users read about a release (website page, feed, GitHub notes). |

**Rule 1 — nothing is published without the owner's explicit go-ahead.** Not the ISO, not the update channel, not a
GitHub release, not the website. Agents and scripts prepare; the owner (or the person the owner names) publishes.
An image goes to the server only after the owner has written it to a USB stick and verified it on real hardware.
An over-the-air push and its GitHub release go out after the verification chain below has passed and the owner has said so.

**Rule 2 — every pushed update gets a changelog entry and a GitHub release.** Over-the-air updates included: no image does
not mean no release. The changelog entry is written first, the GitHub release is made from it, and the website page is
regenerated from it — never the other way round, never by hand.

Two smaller rules: the server keeps only the newest and the previous ISO (versioned file names from the next image
release on); and `build/secrets/` (the archive signing key, deploy credentials) is never copied, printed or committed.

## 2. Editing `docs/CHANGELOG.md`

Entries are newest first. Each one is:

```
<!-- release: rev=7 tag=v1.0.6 kind=ota date=2026-09-16 time=18:16 -->
## 1.0-7 — 16 September 2026 (over-the-air)

One or two sentences of context (optional).

- Four to ten short bullets: what the user sees or gets. One idea per bullet.
- ...

Known limits: what does not work yet or needs a log-out, in one paragraph (optional but usually right).
```

Header fields: `rev` (the package revision), `tag` (must follow the formula), `kind` (`image`, `ota`, or `withheld`
for a revision that was built and never published), `date` (`YYYY-MM-DD`, or `unreleased` while drafting),
`time` (`HH:MM` UTC of publication, for the feed; defaults to 12:00), `file` (the ISO file name under `/download/`,
only on the image release whose file is currently served), `status=draft` (keeps the entry off the page, the feed and GitHub).

Style — enforced by `tests/changelog-check.sh`, so a wrong word fails the gate rather than reaching the site:

* Plain language, present tense, what the user sees. "Login screen", "start-up screen", "installer", "updates",
  "the assistant", "the agent" — never the names of the parts underneath (no component, service, tool or framework
  names, no package-manager vocabulary).
* No file paths, file names, identifiers, code spans, links, addresses, hosts, build ids, test names or counts.
  The renderer adds the links (feed, GitHub release, how to get it).
* No third-party product name used as if it were ours; provider and app names only where the user picks them
  (Firefox, LibreOffice, Ollama, Anthropic Claude, Google Gemini). One provider name is allowed nowhere in `docs/`
  or `website/` except as a label in the product itself — `tests/branding-check.sh` lists the files that may carry it.
* Never the word "download" in anything a user reads: say "get" or "install".
* No secrets, obviously; nothing from `build/secrets/`, no server names, no internal addresses.

While a round is in progress the top entry is a draft (`date=unreleased status=draft`) holding the planned bullets. Finalising
it means: rewrite the bullets from what actually shipped and was verified, write the known limits, set `date` and
`time`, remove `status=draft`, re-render, run the gate, commit.

## 3. Rendering and checking

```
scripts/changelog-render.py            # writes website/changelog/index.html and the <item>s of website/updates.xml
scripts/changelog-render.py --notes 7  # the Markdown "what changed" body for 1.0-7 (what release-github.sh embeds)
scripts/changelog-render.py --list     # every entry: revision, tag, kind, date, status
tests/changelog-check.sh               # the gate: coverage, plain words, page and feed current, footer links, valid XML
tests/branding-check.sh                # the source-tree checks in it must stay green (forbidden names, provider label)
```

The page takes the header, footer and stylesheet from `website/index.html`, so it looks like the rest of the site and
follows it automatically — which also means any edit to `index.html`'s header, footer or `<style>` makes the changelog
page stale until it is re-rendered. The gate says so; re-render and commit both.

Generated files are never edited by hand: `website/changelog/index.html`, and the `<item>` elements of
`website/updates.xml` (its channel header is kept as is). `website/updates/index.html` reads `/api/releases` from the
main-site backend and is not part of this pipeline — do not touch it.

## 4. Checklist — over-the-air release (`1.0-N`, tag `v1.0.(N-1)`)

1. **Revision.** `PKG_REVISION="N"` in `brand/brand.conf`, one commit. Add the draft changelog entry
   (`rev=N tag=v1.0.(N-1) kind=ota date=unreleased status=draft`) with the planned bullets; render; gate green.
2. **Build and prove locally** (`docs/UPDATES.md` §5, `docs/QA.md` for the round's full list):
   `scripts/publish-apt.sh --no-deploy` (builds and signs `build/apt-repo/loom`), the host unit suites, the render tests,
   `tests/branding-check.sh` and `tests/security-check.sh` against the current image, `tests/ota-local-vm.sh --stage`
   then `tests/ota-local-vm.sh` (a 1.0-(N-1) disk upgraded from the local repository through the same path Fab Updates
   uses). Record results and root causes in `docs/QA.md`.
3. **Owner's go-ahead** (Rule 1). Stop here until it is given.
4. **Publish the channel.** `scripts/publish-apt.sh` (stable), and `--channel beta` if the beta suite is to carry the same
   build. Then `tests/update-channel-test.sh --expect 1.0-N` against the public repository from an older installed disk.
5. **Finalise the changelog** (§2): bullets from what shipped, known limits, `date`/`time` of the publication (UTC), no
   `status=draft`. `scripts/changelog-render.py`; `tests/changelog-check.sh`; `tests/branding-check.sh`; commit
   ("Changelog: 1.0-N").
6. **GitHub release.** First `scripts/release-github.sh v1.0.(N-1) --ota --dry-run` and read what it would upload and the
   notes. Then `scripts/release-github.sh v1.0.(N-1) --ota --target <sha>` where `<sha>` is the commit the packages were
   built from (the tag is created on it; without `--target` GitHub tags the head of the default branch). Assets:
   `NOTES.md`, `Packages` (the channel's index at this revision), `InRelease`, `SHA256SUMS` + `SHA256SUMS.gpg` of the
   packages, `fabos-archive-key.asc`. From a worktree, `FABOS_BUILD=/path/to/main/checkout/build`.
7. **Deploy the website** (§6) and purge the CDN. Open `https://fabos.patienceai.in/changelog/` and `/updates.xml`
   and see the entry.
8. **Record and push.** The publication lines in `docs/QA.md` (what, when in UTC, the channel test result), then
   `scripts/secret-scan.sh` and push `main`.

Already shipped without a GitHub release: `1.0-7` (2026-09-16). Its entry is finished; the release is made with
`scripts/release-github.sh v1.0.6 --ota --target ef0c13c` when the owner says so.

## 5. Checklist — image release (`1.0-N`, tag `v1.0.(N-1)`)

1. **Revision and draft entry** as in §4 step 1, with `kind=image` and `file=<the versioned ISO file name>`.
2. **Build.** `scripts/build-rootfs.sh iso && scripts/build-iso.sh`. Prove it: `tests/calamares-jobs-test.sh` (installer
   configuration audit in the image), `tests/branding-check.sh iso`, `tests/security-check.sh iso`, `tests/iso-boot-test.sh`,
   `tests/install-vm.sh plain` and `tests/install-vm.sh luks` (automated installs; the installed disk must boot on its own),
   `scripts/source-offer.sh` (the GPL source record, committed). Record in `docs/QA.md`.
3. **Owner verifies on real hardware** — writes the ISO to a USB stick, boots it live, installs, boots the installed
   system. Nothing is uploaded before this (Rule 1).
4. **Publish the channel first** (`scripts/publish-apt.sh`), so installed systems and the new image agree on `1.0-N`;
   `tests/update-channel-test.sh --expect 1.0-N`.
5. **Upload the ISO.** `scripts/publish-iso.sh --server <user@host>` stages the ISO, its `.sha256`, `SHA256SUMS`,
   `SHA256SUMS.gpg` and the key, then prints the one command that moves them into the served directory. Versioned file
   name; keep only this and the previous ISO on the server. Point the home page's get-it button and checksum link at the
   new file (`website/index.html`, bump its cache-buster), and move the `file=` field in the changelog to this entry.
6. **Finalise the changelog** (§2); render; gate; branding check; commit.
7. **GitHub release.** `scripts/release-github.sh v1.0.(N-1) --dry-run`, then `scripts/release-github.sh v1.0.(N-1)
   --target <sha>`. Assets: `SHA256SUMS` + `SHA256SUMS.gpg`, `<iso>.sha256`, `fabos-archive-key.asc`, `MANIFEST.txt`
   (every package in the image, read from the built image), `NOTES.md`. The ISO itself is not attached: GitHub caps an
   asset at 2 GiB and a split file is user-hostile; the notes link the one-click file on the site.
8. **Deploy the website** (§6), purge, verify the page, the feed and the get-it link.
9. **Record and push** as in §4 step 8.

## 6. Deploying the website

The site is static; `website/` is copied to the web server's document root (the directory whose `download/` folder
`scripts/publish-iso.sh` fills). Exclude `community/` — it is served by its own application, not as static files —
and never copy `build/` or anything under `build/secrets/`:

```
rsync -az --delete --exclude community/ --exclude node_modules/ website/ "$FABOS_DEPLOY_HOST:<document root>/"
scripts/cf-purge.sh          # the CDN caches HTML; without the purge the old page can persist for hours
```

`FABOS_DEPLOY_HOST` and the CDN token come from the operator's environment or `build/secrets/`; they are not written
down here. After the purge, check `https://fabos.patienceai.in/changelog/`, `/updates.xml` (the new `<item>` first)
and, for an image release, the get-it link on the home page.

## 7. What goes where, in one table

| Artefact | Image release | Over-the-air release |
|---|---|---|
| Packages `1.0-N` in the signed channel | yes (`scripts/publish-apt.sh`) | yes |
| ISO on `fabos.patienceai.in/download/` | yes (`scripts/publish-iso.sh`, after hardware verification) | no |
| `docs/CHANGELOG.md` entry | `kind=image`, with `file=` | `kind=ota` |
| `website/changelog/index.html`, `website/updates.xml` | regenerated | regenerated |
| GitHub release `v1.0.(N-1)` | checksums, key, manifest, notes | notes, `Packages`, `InRelease`, checksums of the packages, key |
| `docs/QA.md` | the round's record and publication lines | the same |

## 8. When something goes wrong

* **The gate says an entry is missing or a draft is stale.** Every revision from 1 to `PKG_REVISION` (and every value
  `PKG_REVISION` ever had in the history of `brand/brand.conf`) needs an entry; only the current revision may be a
  draft. A revision that was built and never published gets `kind=withheld` with a short paragraph and no bullets
  (see `1.0-5`).
* **The gate says the page or feed is stale.** Run `scripts/changelog-render.py` and commit the result together with
  the change that caused it.
* **The gate flags a word.** It is right more often than not. Reword for the user; if the flag is a false positive on
  plain English, the allow-lists at the top of `tests/changelog-check.sh` are the place to fix it, in the same commit.
* **`release-github.sh` refuses.** It checks that the tag matches the changelog's tag for that revision, that the entry's
  kind matches the flavour being published, that the entry is not a draft, and (over the air) that the channel's index
  describes exactly this revision. Fix the input; do not bypass the check.
* **A release must be pulled.** Delete the GitHub release and tag, remove the ISO from the server (leave the previous
  one), publish the channel again at the previous revision only if the packages must roll back (and say so in a new
  changelog entry: users need to know what happened), re-render, purge.
