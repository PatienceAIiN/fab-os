#!/usr/bin/env bash
# Copy static musl aios/aiosd from the ai-native-os checkout into build/bin (building them if needed).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); SRC=${FABRIC_AI_SRC:-$HOME/ai-native-os}
T=$SRC/target/x86_64-unknown-linux-musl/release; mkdir -p "$HERE/build/bin"
if [ ! -x "$T/aios" ] || [ ! -x "$T/aiosd" ]; then
  echo "building aios/aiosd (musl) under resource guard..."; ( cd "$SRC" && . ~/.cargo/env && tools/rg -- cargo build --release --target x86_64-unknown-linux-musl -p aios -p aiosd )
fi
cp "$T/aios" "$T/aiosd" "$HERE/build/bin/"; file "$HERE"/build/bin/* | sed 's/,.*//'; ls -la "$HERE/build/bin"
