#!/usr/bin/env bash
# Build the Fab OS .deb packages, assemble a signed apt repository, and publish it to the Patience AI server.
# This is the OS update loop: users' `apt upgrade` pulls Fab OS updates from here, Ubuntu updates from Ubuntu.
#
# Usage: scripts/publish-apt.sh [--no-deploy] [--channel stable|beta]   (beta publishes to suite <codename>-beta)
# Env:   APT_GNUPGHOME (default build/secrets/gpg-home)  APT_KEY_ID ("Fab OS Archive")
#        APT_DEPLOY_TARGET (required for deploy: user@host:/path, no default)  APT_SSH_KEY (default ~/.ssh/id_ed25519)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
. brand/brand.conf
CHANNEL=stable; for a in "$@"; do case $a in --channel=*) CHANNEL=${a#*=};; esac; done; [ "${2:-}" = "--channel" ] && CHANNEL=$3; [ "${1:-}" = "--channel" ] && CHANNEL=$2
SUITE=$DISTRO_CODENAME; [ "$CHANNEL" = beta ] && SUITE="$DISTRO_CODENAME-beta"; COMPONENT=main; ARCHES="amd64 all"
OUT=build/apt-repo/$SUITE; DEBS=build/debs
GNUPGHOME=${APT_GNUPGHOME:-$PWD/build/secrets/gpg-home}; export GNUPGHOME
KEY=${APT_KEY_ID:-"Fab OS Archive"}
TARGET=${APT_DEPLOY_TARGET:-}
SSHKEY=${APT_SSH_KEY:-$HOME/.ssh/id_ed25519}

echo "== 1. build packages (pkgs stage)"
[ -x build/bin/aios ] || scripts/stage-ai-binaries.sh
tools/rg --profile build -- podman build --target pkgs -f image/Containerfile -t fabos:pkgs . > build/podman-build-pkgs.log 2>&1 || { tail -20 build/podman-build-pkgs.log; exit 1; }
rm -rf "$DEBS"; mkdir -p "$DEBS"
c=$(podman create fabos:pkgs); podman cp "$c":/out/. "$DEBS"/; podman rm "$c" >/dev/null
ls -1 "$DEBS"

echo "== 2. assemble repository (apt-ftparchive inside Ubuntu)"
rm -rf "$OUT"; mkdir -p "$OUT/pool/$SUITE" "$OUT/dists/$SUITE/$COMPONENT"   # one dists+pool tree per suite (pool/<suite>), merged by rsync without --delete
cp "$DEBS"/*.deb "$OUT/pool/$SUITE/"
podman run --rm -v "$PWD/$OUT:/repo:Z" -e SUITE="$SUITE" -e COMPONENT="$COMPONENT" -e ORIGIN="$VENDOR_NAME" -e LABEL="$DISTRO_NAME" docker.io/library/ubuntu:26.04 bash -euo pipefail -c '
  apt-get update -qq >/dev/null && apt-get install -y -qq apt-utils dpkg-dev >/dev/null 2>&1
  cd /repo
  for a in amd64 all; do
    mkdir -p dists/$SUITE/$COMPONENT/binary-$a
    apt-ftparchive --arch $a packages pool/$SUITE > dists/$SUITE/$COMPONENT/binary-$a/Packages
    gzip -9kf dists/$SUITE/$COMPONENT/binary-$a/Packages
    printf "Archive: %s\nComponent: %s\nOrigin: %s\nLabel: %s\nArchitecture: %s\n" "$SUITE" "$COMPONENT" "$ORIGIN" "$LABEL" "$a" > dists/$SUITE/$COMPONENT/binary-$a/Release
  done
  # (apt-ftparchive --arch amd64 already includes Architecture: all packages)
  apt-ftparchive -o APT::FTPArchive::Release::Origin="$ORIGIN" -o APT::FTPArchive::Release::Label="$LABEL" -o APT::FTPArchive::Release::Suite="$SUITE" \
     -o APT::FTPArchive::Release::Codename="$SUITE" -o APT::FTPArchive::Release::Architectures="amd64 all" -o APT::FTPArchive::Release::Components="$COMPONENT" \
     -o APT::FTPArchive::Release::Description="$LABEL package updates by $ORIGIN" release dists/$SUITE > dists/$SUITE/Release
  chown -R --reference=/repo/pool /repo/dists 2>/dev/null || true'

echo "== 3. sign Release (key: $KEY)"
gpg --batch --yes --default-key "$KEY" -abs -o "$OUT/dists/$SUITE/Release.gpg" "$OUT/dists/$SUITE/Release"
gpg --batch --yes --default-key "$KEY" --clearsign -o "$OUT/dists/$SUITE/InRelease" "$OUT/dists/$SUITE/Release"
gpg --export --armor "$KEY" > "$OUT/fabos-archive-key.asc"; gpg --export "$KEY" > "$OUT/fabos-archive-keyring.gpg"
cat > "$OUT/index.html" <<HTML
<!doctype html><meta charset=utf-8><title>$DISTRO_NAME apt repository</title>
<style>body{font-family:Inter,system-ui;max-width:720px;margin:48px auto;padding:0 20px;color:#1B1F27}code,pre{background:#EDEFF3;padding:2px 6px;border-radius:6px}</style>
<h1>$DISTRO_NAME package repository</h1><p>Signed updates for $DISTRO_NAME by $VENDOR_NAME. Suite <code>$SUITE</code>, component <code>$COMPONENT</code>.</p>
<p>On $DISTRO_NAME this repository is preconfigured. On stock Ubuntu ${BASE_VERSION_ID}:</p>
<pre>sudo curl -fsSL https://fabos.patienceai.in/apt/fabos-archive-keyring.gpg -o /usr/share/keyrings/fabos-archive-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/fabos-archive-keyring.gpg] https://fabos.patienceai.in/apt $SUITE $COMPONENT' | sudo tee /etc/apt/sources.list.d/fabos.list
sudo apt update &amp;&amp; sudo apt install fabos-desktop-meta</pre>
<p>Packages: $(ls "$DEBS" | sed 's/_.*//' | sort -u | tr '\n' ' ')</p><p>Built $(date -u +%FT%TZ)</p>
HTML
du -sh "$OUT"; find "$OUT/dists" -type f | sed "s|$OUT/||"

if [ "${1:-}" != "--no-deploy" ]; then
  [ -n "$TARGET" ] || { echo "APT_DEPLOY_TARGET is not set (user@host:/path); use --no-deploy to build only" >&2; exit 1; }
  echo "== 4. deploy -> $TARGET"
  host=${TARGET%%:*}; path=${TARGET#*:}
  ssh -o BatchMode=yes -i "$SSHKEY" "$host" "mkdir -p '$path'"
  rsync -az -e "ssh -o BatchMode=yes -i $SSHKEY" "$OUT/" "$TARGET/"
  echo "== published: https://fabos.patienceai.in/apt/  (suite $SUITE, channel $CHANNEL)"
fi
