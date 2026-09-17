#!/usr/bin/env bash
# /usr/lib/fabos/rebrand-binaries after an UPSTREAM upgrade of a patched file (1.0-8 fix). Inside a throw-away container
# of the built image, do to /usr/bin/systemsettings what apt does when Ubuntu ships a new build (the file is replaced,
# dpkg records the new md5sum) and prove that the Post-Invoke run
#   1. recognises the kept <file>.fabos-orig as STALE (dpkg's md5sum no longer matches it), drops it and patches the
#      NEW build — the pre-1.0-8 script wrote the old build's patched copy over the new file (reproduced: 496944 bytes
#      back over a 496945-byte "new" build);
#   2. is a no-op on the second run;
#   3. leaves a file alone, with a WARNING, when neither the installed file nor the kept copy matches dpkg's record
#      (the state a machine is in when the old script already struck; only a reinstall of the package is clean).
# Usage: tests/rebrand-binaries-upgrade-test.sh        Env: IMAGE=localhost/fabos:vm
set -uo pipefail; ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"; IMAGE=${IMAGE:-localhost/fabos:vm}
mkdir -p build; LOG=build/rebrand-binaries-upgrade-test.log
podman run --rm --network none -v "$ROOT:/work:Z" "$IMAGE" bash -c '
set -e
S=/work/packages/fabos-desktop/usr/lib/fabos/rebrand-binaries
f=/usr/bin/systemsettings; pkg=$(dpkg -S "$f" | cut -d: -f1)
python3 "$S" >/dev/null                          # converge on this tree'"'"'s rule set first
test -f "$f.fabos-orig" || { echo "NOTEST: $f is not a patched target in this image"; exit 0; }
cp "$f.fabos-orig" /tmp/new; printf "\0" >> /tmp/new; cp /tmp/new "$f"        # "apt installed a new systemsettings build"
sed -i "s|^[0-9a-f]*  usr/bin/systemsettings\$|$(md5sum /tmp/new | cut -d" " -f1)  usr/bin/systemsettings|" "/var/lib/dpkg/info/$pkg.md5sums"
echo "--- run after the upgrade"; python3 "$S" | grep systemsettings || true
cmp -s "$f.fabos-orig" /tmp/new && echo "ORIG-REFRESHED" || echo "ORIG-STALE"
[ "$(stat -c %s "$f")" = "$(stat -c %s /tmp/new)" ] && echo "SIZE-OF-NEW-BUILD" || echo "SIZE-OF-OLD-BUILD"
echo "FAB-STRINGS=$(grep -a -c "Fab OS Settings" "$f" || true) PRISTINE=$(grep -a -c "Fab OS Settings" /tmp/new || true)"
echo "--- second run"; python3 "$S" | grep systemsettings || echo "NO-OP"
echo "--- damaged state: dpkg record matches neither copy"
sed -i "s|^[0-9a-f]*  usr/bin/systemsettings\$|00000000000000000000000000000000  usr/bin/systemsettings|" "/var/lib/dpkg/info/$pkg.md5sums"
before=$(md5sum "$f" | cut -d" " -f1); python3 "$S" | grep systemsettings || true
[ "$(md5sum "$f" | cut -d" " -f1)" = "$before" ] && echo "DAMAGED-LEFT-ALONE" || echo "DAMAGED-REWRITTEN"
' > "$LOG" 2>&1
sed 's/^/    /' "$LOG"
fail=0; chk() { if grep -q -- "$2" "$LOG"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
if grep -q '^NOTEST' "$LOG"; then echo "SKIP  $(cat "$LOG")"; exit 0; fi
chk "stale kept original recognised through dpkg's md5sums and dropped"      "upstream upgraded, stale original dropped /usr/bin/systemsettings"
chk "the NEW build is what gets patched (not overwritten by the old copy)"   "patched /usr/bin/systemsettings"
chk "installed file keeps the new build's size"                              "^SIZE-OF-NEW-BUILD"
chk "kept original is now the new build"                                     "^ORIG-REFRESHED"
chk "the patch is in the file and not in the pristine copy"                  "^FAB-STRINGS=[1-9][0-9]* PRISTINE=0"
chk "second run is a no-op"                                                  "^NO-OP"
chk "damaged state (neither copy matches dpkg) is left alone with a warning" "^DAMAGED-LEFT-ALONE"
chk "the warning names the file and the way out (reinstall)"                 "WARNING: /usr/bin/systemsettings .*reinstall"
exit $fail
