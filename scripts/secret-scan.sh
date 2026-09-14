#!/usr/bin/env bash
# Fab OS — secret scan gate. Fails if anything that looks like a credential is in the
# working tree (tracked files) or anywhere in git history. Run before every push/release.
#   scripts/secret-scan.sh            # tree + history
#   scripts/secret-scan.sh --tree     # tree only (fast)
set -euo pipefail
cd "$(dirname "$0")/.."
PATTERNS='sk-ant-api[0-9A-Za-z_-]{10,}|sk-proj-[0-9A-Za-z_-]{10,}|sk-[0-9A-Za-z]{32,}|figd_[0-9A-Za-z_-]{10,}|cfat_[0-9A-Za-z_-]{10,}|gh[pousr]_[0-9A-Za-z]{20,}|xkeysib-[0-9a-f]{20,}|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |OPENSSH |EC |PGP |)PRIVATE KEY-----|xox[baprs]-[0-9A-Za-z-]{10,}|eyJhbGciOi[0-9A-Za-z_-]{20,}\.[0-9A-Za-z_-]{20,}'
ALLOW='THIRD_PARTY_LICENSES/|legal/|\.gitignore$|scripts/secret-scan\.sh$'
fail=0
echo "== tracked files"
if git ls-files -z | grep -zvE "$ALLOW" | xargs -0 grep -nIE "$PATTERNS" 2>/dev/null; then fail=1; fi
echo "== sensitive paths that must not be tracked"
# .env files are allowed only as commented/default templates under packages/*/etc/; public keyrings under usr/share/keyrings are fine.
if git ls-files | grep -E '^(build/secrets/|deploy/|website/admin/|community/)|\.pem$|id_rsa|id_ed25519|\.key$' ; then fail=1; fi
if git ls-files | grep -E '\.env$' | grep -vE '^packages/[^/]+/etc/' ; then fail=1; fi
if git ls-files | grep -E '\.gpg$' | grep -vE 'usr/share/keyrings/' ; then fail=1; fi
for k in $(git ls-files | grep -E 'usr/share/keyrings/.*\.gpg$'); do
  if gpg --show-keys --with-colons "$k" 2>/dev/null | grep -q '^sec'; then echo "PRIVATE KEY MATERIAL IN $k"; fail=1; fi
done
# template .env files must not assign anything that looks like a real value
if git ls-files | grep -E '^packages/[^/]+/etc/.*\.env$' | xargs grep -nE '^[A-Z_]*(KEY|TOKEN|PASS|SECRET)[A-Z_]*=.+' 2>/dev/null; then fail=1; fi
if [ "${1:-}" != "--tree" ]; then
  echo "== git history (all refs)"
  if git rev-list --all | xargs -n 50 git grep -nIE "$PATTERNS" -- ':!THIRD_PARTY_LICENSES' ':!legal' 2>/dev/null | head -20 | grep .; then fail=1; fi
  echo "== history: sensitive paths ever committed"
  if git log --all --name-only --format= | sort -u | grep -E '^(build/secrets/|deploy/|website/admin/)|\.pem$|id_rsa|id_ed25519' ; then fail=1; fi
  if git log --all --name-only --format= | sort -u | grep -E '\.env$' | grep -vE '^packages/[^/]+/etc/' ; then fail=1; fi
fi
if [ "$fail" = 1 ]; then echo "SECRET SCAN: FAIL"; exit 1; fi
echo "SECRET SCAN: PASS (no credential patterns, no sensitive paths, tree${1:+ only}${1:-+history})"
