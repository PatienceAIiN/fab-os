#!/usr/bin/env bash
# OCR every PNG of a directory with the tesseract inside localhost/fabos:iso (the host needs no tesseract); one line per
# frame: "<file>\t<text on one line>". Usage: tests/rebrand-sweep/ocr-frames.sh <dir> [image]
set -uo pipefail
DIR=$(cd "$1" && pwd); IMG=${2:-localhost/fabos:iso}
podman run --rm -i -v "$DIR:/f:z" "$IMG" bash -s <<'EOF'
for p in /f/*.png; do
  [ -f "$p" ] || continue
  printf '%s\t' "$(basename "$p")"
  tesseract "$p" stdout 2>/dev/null | tr '\n' ' ' | tr -s ' '
  echo
done
EOF
