#!/bin/sh
# fabos-perf-mode self-test (docs/PERFORMANCE.md): runs the mode script from the source tree inside the VM image
# (kwriteconfig6 / kreadconfig6 present, no session bus: KWin, PowerDevil, kscreen-doctor, power-profiles-daemon and
# systemctl calls are skipped or warn), with a user's own choices set first, through
#     server -> gaming -> server -> balanced
# and asserts what ends up in the user's kwinrc / powerdevilrc after every step. The Server -> Gaming direction is the
# one the VM round trip (tests/perf-vm.sh: power-saver, performance, gaming, server, balanced) does not cover, and the
# one that carried Server's "lid closed does nothing, never sleep" keys into Gaming before the 1.0-8 review fix.
#
#     tests/perf/perf-mode-selftest.sh [image]      (default localhost/fabos:vm; re-executes itself inside podman)
set -u
if [ ! -d /tuning ]; then
  HERE=$(cd "$(dirname "$0")/../.." && pwd); IMG=${1:-localhost/fabos:vm}
  exec podman run --rm -v "$HERE/packages/fabos-tuning:/tuning:ro,Z" -v "$HERE/tests/perf/perf-mode-selftest.sh:/tmp/selftest.sh:ro,Z" "$IMG" sh /tmp/selftest.sh
fi
export HOME=/tmp/h; rm -rf "$HOME"; mkdir -p "$HOME/.config"
unset DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR
PM=/tuning/usr/bin/fabos-perf-mode
fails=0
ok() { echo "PASS  $1"; }
ko() { echo "FAIL  $1"; fails=$((fails+1)); }
has() { grep -q -x -- "$2" "$HOME/.config/$1" 2>/dev/null; }           # a line in the USER's own file
eff() { kreadconfig6 --file "$1" --group "$2" --key "$3" --default unset 2>/dev/null; }   # the merged (effective) value
sh -n "$PM" && sh -n /tuning/usr/lib/fabos/perf-mode-root.sh && ok "sh -n" || ko "sh -n"

# the user's own choices, made before any mode
kwriteconfig6 --file kwinrc --group Plugins --key blurEnabled false
kwriteconfig6 --file powerdevilrc --group Battery --group SuspendAndShutdown --key LidAction 2

echo "== set server"; sh "$PM" set server 2>/dev/null
has powerdevilrc "LidAction=0" && has powerdevilrc "AutoSuspendAction=0" && has powerdevilrc "TurnOffDisplayWhenIdle=false" && ok "server writes lid/sleep/display keys" || ko "server keys missing"
# kwriteconfig6 drops a value equal to the /etc/xdg default (AllowTearing=false there), so tearing is read back merged
has kwinrc "slideEnabled=false" && [ "$(eff kwinrc Compositing AllowTearing)" = false ] && ok "server: effects off, tearing off (effective)" || ko "server kwin keys wrong: tearing=$(eff kwinrc Compositing AllowTearing)"
[ -f "$HOME/.config/fabos/performance-mode.snapshot" ] && ok "snapshot taken" || ko "no snapshot"
grep -q '^powerdevilrc|Battery|SuspendAndShutdown|LidAction|2$' "$HOME/.config/fabos/performance-mode.snapshot" && ok "snapshot holds the user's LidAction=2" || ko "snapshot lost the user's value"

echo "== set gaming (from server)"; sh "$PM" set gaming 2>/dev/null
if grep -q "LidAction=0\|AutoSuspendAction=0\|TurnOffDisplayWhenIdle=false\|DimDisplayWhenIdle=false" "$HOME/.config/powerdevilrc"; then ko "Server's lid/sleep/display keys leaked into Gaming"; cat "$HOME/.config/powerdevilrc"; else ok "Gaming carries none of Server's lid/sleep/display keys"; fi
has powerdevilrc "LidAction=2" && ok "the user's LidAction=2 is back in Gaming" || ko "the user's LidAction=2 is gone"
has kwinrc "AllowTearing=true" && has kwinrc "blurEnabled=false" && has kwinrc "magiclampEnabled=false" && ok "gaming: tearing on, its four effects off" || ko "gaming kwin keys wrong"
if has kwinrc "slideEnabled=false" || has kwinrc "fadeEnabled=false" || has kwinrc "kwin4_effect_shapecornersEnabled=false"; then ko "Server's extra effects-off leaked into Gaming"; else ok "Gaming keeps slide/fade/corners as the user had them"; fi
[ -f "$HOME/.config/fabos/performance-mode.snapshot" ] && ok "snapshot kept across the switch" || ko "snapshot dropped on server->gaming"
grep -q '^kwinrc|Plugins|-|blurEnabled|false$' "$HOME/.config/fabos/performance-mode.snapshot" && ok "snapshot still the user's (blur=false), not Server's" || ko "snapshot was retaken with Server's values"

echo "== set server (from gaming)"; sh "$PM" set server 2>/dev/null
has powerdevilrc "LidAction=0" && [ "$(eff kwinrc Compositing AllowTearing)" = false ] && ok "server again: lid 0, tearing off" || ko "server again: lid/tearing wrong"

echo "== set balanced"; sh "$PM" set balanced 2>/dev/null
[ -f "$HOME/.config/fabos/performance-mode.snapshot" ] && ko "snapshot not dropped on balanced" || ok "snapshot dropped"
has kwinrc "blurEnabled=false" && ok "user's blur=false restored" || ko "user's blur=false lost"
if grep -q "AllowTearing\|slideEnabled\|magiclampEnabled\|dimscreenEnabled\|translucencyEnabled" "$HOME/.config/kwinrc"; then ko "mode keys left in kwinrc"; cat "$HOME/.config/kwinrc"; else ok "no mode keys left in kwinrc"; fi
has powerdevilrc "LidAction=2" && ok "user's LidAction=2 restored" || ko "user's LidAction=2 lost"
if grep -q "LidAction=0\|AutoSuspendAction\|TurnOffDisplayWhenIdle\|DimDisplayWhenIdle" "$HOME/.config/powerdevilrc"; then ko "mode keys left in powerdevilrc"; cat "$HOME/.config/powerdevilrc"; else ok "no mode keys left in powerdevilrc"; fi

echo "== status / list"
sh "$PM" status --json 2>/dev/null | python3 -m json.tool >/dev/null && ok "status --json parses" || ko "status --json invalid"
sh "$PM" list | grep -q "lid" && ok "list names the lid for Server" || ko "list does not mention the lid"
echo "== root helper (container: sysfs is read-only, so writes fail; the script must stay quiet and exit 0)"
for m in power-saver gaming balanced; do out=$(sh /tuning/usr/lib/fabos/perf-mode-root.sh $m 2>&1); rc=$?; echo "   $m: $(echo "$out" | tr '\n' ' ')"; [ $rc = 0 ] && ! echo "$out" | grep -q "cannot create" && ok "root helper $m: exit 0, no stray errors" || ko "root helper $m: rc=$rc"; done
echo "== $fails failure(s)"
exit $fails
