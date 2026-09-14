#!/usr/bin/env bash
# Publish the Fab OS ISO for a one-click download from https://fabos.patienceai.in/download/ (no size limit, no split).
# The web docroot is root-owned, so this stages the file in the operator's home on the server and prints the ONE
# command to move it into place. GitHub gets only the checksum + manifest + notes (a single-file ISO exceeds GitHub's
# 2 GiB asset limit, which is user-hostile to reassemble).
#   scripts/publish-iso.sh [--iso build/fabos-1.0-desktop-amd64.iso] [--server user@host] [--key ~/.ssh/id_ed25519]
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
ISO=$(ls build/fabos-*-desktop-amd64.iso 2>/dev/null | head -1); SERVER=${FABOS_DEPLOY_HOST:-}; KEY=${FABOS_DEPLOY_KEY:-$HOME/.ssh/id_ed25519}
while [ $# -gt 0 ]; do case $1 in --iso) ISO=$2; shift;; --server) SERVER=$2; shift;; --key) KEY=$2; shift;; esac; shift; done
[ -f "$ISO" ] || { echo "no ISO at $ISO"; exit 1; }
[ -n "$SERVER" ] || { echo "set the target with --server user@host (or FABOS_DEPLOY_HOST)"; exit 1; }
base=$(basename "$ISO")
echo "== checksum"; (cd "$(dirname "$ISO")" && sha256sum "$base") | tee "build/$base.sha256"
echo "== upload to $SERVER:~/fab-os-download/ ($(du -h "$ISO" | cut -f1))"
ssh -o BatchMode=yes -i "$KEY" "$SERVER" 'mkdir -p ~/fab-os-download'
rsync -az --progress -e "ssh -o BatchMode=yes -i $KEY" "$ISO" "build/$base.sha256" "$SERVER:fab-os-download/"
cat <<MSG

== staged on the server. Run this ONE command to publish the download (needs your sudo password):

  ssh -i $KEY $SERVER 'sudo install -d /var/www/fab-os/download && sudo rsync -a ~/fab-os-download/ /var/www/fab-os/download/'

Then the one-click link works:  https://fabos.patienceai.in/download/$base
Checksum page:                 https://fabos.patienceai.in/download/$base.sha256
MSG
