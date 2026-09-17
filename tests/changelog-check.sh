#!/usr/bin/env bash
# Changelog gate — source tree only, no image, no network. Fails when:
#   * a shipped package revision (1..PKG_REVISION, plus every PKG_REVISION value in brand.conf's git history) has no
#     entry in docs/CHANGELOG.md, or an entry older than the current revision is still a draft, or a header breaks the
#     tag formula v<DISTRO_VERSION>.<rev-1>;
#   * docs/CHANGELOG.md or the rendered page's text says anything internal: component / tool / test names, file paths,
#     identifiers, counts, hosts, addresses, or a third-party product name used as ours, or the word "download";
#   * website/changelog/index.html or website/updates.xml is stale against scripts/changelog-render.py (re-rendered to
#     a temp dir and diffed byte for byte);
#   * a public page's footer lacks the Changelog link;  * updates.xml is not well-formed XML;  * the GitHub notes body
#     of a published entry fails to render or says something internal.
# Usage: tests/changelog-check.sh            (exit 0 = green; every line is PASS/FAIL <check>)
set -uo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd); cd "$SRC"; fail=0
chk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
. brand/brand.conf
CL=docs/CHANGELOG.md; PAGE=website/changelog/index.html; FEED=website/updates.xml; RENDER="python3 scripts/changelog-render.py"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# ---- 1. coverage: every shipped revision has an entry -----------------------------------------------------------------
chk "changelog exists and parses"   "test -f $CL && $RENDER --list > $TMP/list 2> $TMP/parse-err"
[ -s "$TMP/parse-err" ] && sed 's/^/      /' "$TMP/parse-err"
# header table: rev tag kind date status (one line per header, straight from the comments so a parse failure still reports)
grep -oE '<!-- release: [^>]* -->' "$CL" | sed -E 's/.*rev=([0-9]+).*tag=(v[0-9.]+).*kind=([a-z]+).*date=([0-9a-z-]+).*/\1 \2 \3 \4/' > "$TMP/heads"
grep -oE '<!-- release: [^>]* -->' "$CL" | grep -c 'status=draft' > /dev/null; grep -oE '<!-- release: [^>]*status=draft[^>]* -->' "$CL" | grep -oE 'rev=[0-9]+' | cut -d= -f2 | sort -u > "$TMP/drafts"
shipped=$(seq 1 "$PKG_REVISION")
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  hist=$(git log --format= -p -- brand/brand.conf 2>/dev/null | grep -oE '^\+PKG_REVISION="[0-9]+"' | grep -oE '[0-9]+' | sort -un || true)
  shipped=$(printf '%s\n%s\n' "$shipped" "$hist" | grep -E '^[0-9]+$' | sort -un)
fi
missing=""; stale_draft=""
for rev in $shipped; do
  grep -qE "^$rev " "$TMP/heads" || missing="$missing $DISTRO_VERSION-$rev"
  if [ "$rev" -lt "$PKG_REVISION" ] && grep -qx "$rev" "$TMP/drafts"; then stale_draft="$stale_draft $DISTRO_VERSION-$rev"; fi
done
chk "every shipped revision (1..$PKG_REVISION and brand history) has an entry${missing:+ — missing:$missing}"  "[ -z '$missing' ]"
chk "no entry older than the current revision $DISTRO_VERSION-$PKG_REVISION is still a draft${stale_draft:+ —$stale_draft}"  "[ -z '$stale_draft' ]"
chk "the current revision $DISTRO_VERSION-$PKG_REVISION has an entry (draft allowed)"  "grep -qE '^$PKG_REVISION ' $TMP/heads"
badtag=$(awk -v v="$DISTRO_VERSION" '{ want = "v" v "." ($1 - 1); if ($2 != want) print $1 ":" $2 "!=" want }' "$TMP/heads" | tr '\n' ' ')
chk "every header follows the tag formula v$DISTRO_VERSION.<rev-1>${badtag:+ — $badtag}"  "[ -z '$badtag' ]"
chk "every header names a kind (image | ota | withheld)"   "! awk '\$3 != \"image\" && \$3 != \"ota\" && \$3 != \"withheld\"' $TMP/heads | grep -q ."
chk "every header dates the release (YYYY-MM-DD; 'unreleased' only for drafts)" "! awk -v d=\"\$(tr '\n' ' ' < $TMP/drafts)\" '\$4 !~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}\$/ && !(\$4 == \"unreleased\" && index(\" \" d, \" \" \$1 \" \"))' $TMP/heads | grep -q ."

# ---- 2. nothing internal in the words users read ----------------------------------------------------------------------
# Internal component/tool names, third-party names we may not use as ours, and jargon that names the parts underneath.
WORDS='daemon|semaphore|QML|dpkg|initramfs|initrd|postinst|preinst|prerm|systemd|polkit|pkexec|sudo|D-Bus|dbus|Calamares|libtaskmanager|TasksModel|KWin|kwinrc|SDDM|Plymouth|apt|apt-get|deb|debs|regex|regexp|endpoint|endpoints|schema|schemas|thread|threads|mutex|stub|stubs|harness|ChatGPT|OpenAI|GPT|SnowUI|LUKS|LUKS2|cryptsetup|bwrap|bubblewrap|AppArmor|sysctl|GRUB|qcow2|QEMU|OVMF|podman|rsync|plasmashell|ksmserver|Aurorae|Kirigami|casper|squashfs|argv|stdin|stdout|stderr|JSON|YAML|HMAC|cgroup|nmcli|busctl|journalctl|ttyS0|udev|PAM|gpg|GNUPGHOME|env|cli|CLI|API|URL|localhost|ADR'
# Heuristics: file paths, file names, identifiers, code spans, URLs, addresses, IPs, test/result vocabulary, counts.
PATTERNS=(
  '(^|[[:space:](])(/[A-Za-z0-9._-]+){2,}'                                        # /usr/share/... style paths
  '~/'                                                                            # home paths
  '\b[A-Za-z0-9_-]+\.(sh|py|js|qml|conf|json|md|txt|xml|iso|img|service|timer|desktop|env|log|deb|asc|gpg)\b'  # file names
  '\b[a-z0-9]+_[a-z0-9_]+\b'                                                      # snake_case identifiers
  '[A-Za-z_]+\(\)'                                                                # function()
  '`'                                                                             # code spans
  'https?://'                                                                     # URLs (the renderer adds the links)
  '[A-Za-z0-9._-]+@[A-Za-z0-9.-]+'                                                # addresses
  '\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b'                                               # IPs
  '\b(PASS|FAIL|SKIP)\b|\btests?/|\bround-?[0-9]+\b|\b[0-9]+ ?/ ?[0-9]+\b|\b[0-9]+ (checks|tests|assertions)\b'  # test names, counts
  '\b[0-9]{8}T[0-9]{6}Z'                                                          # build ids
)
CAMEL='\b[a-z]+[A-Z][A-Za-z]+\b'; CAMEL_OK='iCloud|eSpeak|macOS|iPhone|iPad|iOS'
scan() {  # scan <label> <file-with-plain-text>
  local label=$1 f=$2 hits=0
  grep -n -i -w -E "$WORDS" "$f" > "$TMP/hits" && hits=1
  grep -n -i 'download' "$f" >> "$TMP/hits" && hits=1
  for p in "${PATTERNS[@]}"; do grep -n -E "$p" "$f" >> "$TMP/hits" && hits=1; done
  grep -n -o -E "$CAMEL" "$f" | grep -v -E ":($CAMEL_OK)$" >> "$TMP/hits" && hits=1
  if [ $hits = 1 ]; then echo "FAIL  $label says something internal:"; sort -t: -k1,1n -u "$TMP/hits" | head -40 | sed 's/^/        /'; fail=1; else echo "PASS  $label"; fi
}
python3 - "$CL" > "$TMP/cl.txt" <<'PY'
import re, sys; t = open(sys.argv[1], encoding="utf-8").read()
sys.stdout.write(re.sub(r"<!--.*?-->", "", t, flags=re.S))
PY
scan "docs/CHANGELOG.md uses plain words only (no internal names, paths, identifiers, tests, hosts)" "$TMP/cl.txt"

# ---- 3. the rendered page and feed are current ------------------------------------------------------------------------
chk "renderer runs on this tree"                              "$RENDER --out-dir $TMP/site --quiet"
chk "website/changelog/index.html is current (re-render + diff)"  "test -f $PAGE && cmp -s $TMP/site/changelog/index.html $PAGE"
cmp -s "$TMP/site/changelog/index.html" "$PAGE" 2>/dev/null || diff "$PAGE" "$TMP/site/changelog/index.html" 2>/dev/null | head -12 | sed 's/^/      /'
chk "website/updates.xml is current (re-render + diff)"         "test -f $FEED && cmp -s $TMP/site/updates.xml $FEED"
cmp -s "$TMP/site/updates.xml" "$FEED" 2>/dev/null || diff "$FEED" "$TMP/site/updates.xml" 2>/dev/null | head -12 | sed 's/^/      /'
if command -v xmllint >/dev/null; then chk "updates.xml is well-formed XML (xmllint)" "xmllint --noout $FEED 2>/dev/null"
else chk "updates.xml is well-formed XML (python)" "python3 -c 'import sys,xml.etree.ElementTree as E; E.parse(sys.argv[1])' $FEED 2>/dev/null"; fi
chk "updates.xml keeps its channel header and has one item per published release" \
    "grep -q '<title>Fab OS updates</title>' $FEED && [ \$(grep -c '<item>' $FEED) -eq \$(awk '\$3 != \"withheld\"' $TMP/heads | grep -vc -w -f $TMP/drafts 2>/dev/null || awk '\$3 != \"withheld\"' $TMP/heads | wc -l) ]"
chk "feed items are unique by version"                           "[ \$(grep -o '<guid[^>]*>[^<]*</guid>' $FEED | sort | uniq -d | wc -l) -eq 0 ]"
for rev in $(cat "$TMP/drafts"); do chk "draft $DISTRO_VERSION-$rev is not on the page or in the feed" "! grep -q 'id=\"$DISTRO_VERSION-$rev\"' $PAGE && ! grep -q 'fabos-$DISTRO_VERSION-$rev<' $FEED"; done
sed -n '/<main id="top">/,/<\/main>/p' "$PAGE" | sed 's/<[^>]*>/ /g' | sed 's/&amp;/\&/g' > "$TMP/page.txt"
chk "the rendered page has a body"                               "grep -q 'Fab OS $DISTRO_VERSION-' $TMP/page.txt"
scan "website/changelog/index.html text uses plain words only" "$TMP/page.txt"
for rev in $(awk '$3 != "withheld"' "$TMP/heads" | awk '{print $1}'); do
  grep -qx "$rev" "$TMP/drafts" && continue
  if $RENDER --notes "$rev" > "$TMP/notes-$rev.md" 2> "$TMP/notes-err"; then
    sed -E 's#\((https?://[^)]*)\)##g; s#https?://[A-Za-z0-9./_#-]*##g' "$TMP/notes-$rev.md" > "$TMP/notes-$rev.txt"   # the renderer's own links are allowed
    scan "GitHub notes body for $DISTRO_VERSION-$rev renders and uses plain words only" "$TMP/notes-$rev.txt"
  else echo "FAIL  GitHub notes body for $DISTRO_VERSION-$rev renders"; sed 's/^/      /' "$TMP/notes-err"; fail=1; fi
done

# ---- 4. every public page's footer links to the changelog -----------------------------------------------------------
for page in website/index.html website/docs/index.html website/newsletter/index.html; do
  chk "footer of $page links to the changelog" "sed -n '/<footer/,/<\/footer>/p' $page | grep -q 'changelog/\">Changelog</a>'"
done
chk "the changelog page's own footer links to itself as ./"      "sed -n '/<footer/,/<\/footer>/p' $PAGE | grep -q 'href=\"./\">Changelog</a>'"
chk "sitemap lists the changelog page"                          "grep -q 'fabos.patienceai.in/changelog/' website/sitemap.xml"
chk "the changelog page links the feed and GitHub releases"     "grep -q 'href=\"../updates.xml\"' $PAGE && grep -q 'github.com/PatienceAIiN/fab-os/releases' $PAGE"

exit $fail
