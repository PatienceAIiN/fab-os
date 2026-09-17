#!/bin/sh
# Fab OS quick settings: ONE probe -> ONE JSON line (parsed by ui/status.js). Run by the applet through the Plasma5Support
# executable engine; the applet wraps the call in `timeout 8` so a stuck daemon never leaves a probe behind.
#
#   status.sh           full probe: kernel readings + NetworkManager, BlueZ, WirePlumber, power-profiles-daemon, night light
#   status.sh --light   kernel readings (net counters, battery, backlight) + the active Wi-Fi signal from NetworkManager
#   status.sh --pane    also the IPv4 address (shown only inside the pane)
#
# Task budget (docs/LOW-RAM.md "Idle budget"; counted with /proc/stat `processes`, which counts forks AND threads, in
# the image): the previous key=value script created 166 tasks per run (a subshell per `$(...)`, `timeout` around every
# tool, grep/awk/head/sed pipelines, powerprofilesctl = a Python interpreter, bluetoothctl = 10 tasks per call). This one:
# --light = sh + one awk = 2, plus ONE nmcli (5 tasks; 13–21 ms measured) only while a wireless interface is up, so the
# bar's Wi-Fi glyph follows NetworkManager's own signal reading every probe instead of the kernel's link quality (which
# some drivers do not expose and which does not match what nmcli / the network applet show); full = sh + nmcli (1–2 calls)
# + busctl (3–4 calls, 2 each) + wpctl (4) [+ ip (2)] + one awk that reads the /proc files and prints the JSON (escaping
# included) ≈ 18–27. Everything else is shell builtins: read, printf, case, for over lines, ${var%...}. No root: nmcli,
# busctl, wpctl and sysfs reads all work as the logged-in user. Every `dev wifi list` says --rescan no: a probe must
# never trigger a scan (NetworkManager refreshes the active access point's strength by itself).
LIGHT=0; PANE=0
for a in "$@"; do case "$a" in --light) LIGHT=1;; --pane) PANE=1;; esac; done
NL='
'

# ---- battery from sysfs (read is a builtin: no processes); time left / to full from the energy or charge counters
BAT_PCT=""; BAT_STATUS=""; BAT_TIME=""; BAT_PRESENT=0
for b in /sys/class/power_supply/BAT*; do
  [ -r "$b/capacity" ] || continue
  BAT_PRESENT=1
  read -r BAT_PCT < "$b/capacity" 2>/dev/null || BAT_PCT=""
  read -r BAT_STATUS < "$b/status" 2>/dev/null || BAT_STATUS=""
  now=""; rate=""; full=0
  if [ -r "$b/energy_now" ] && [ -r "$b/power_now" ]; then
    read -r now < "$b/energy_now"; read -r rate < "$b/power_now"; [ -r "$b/energy_full" ] && read -r full < "$b/energy_full"
  elif [ -r "$b/charge_now" ] && [ -r "$b/current_now" ]; then
    read -r now < "$b/charge_now"; read -r rate < "$b/current_now"; [ -r "$b/charge_full" ] && read -r full < "$b/charge_full"
  fi
  case "$now$rate$full" in *[!0-9]*) rate=0;; esac
  mins=0
  if [ "${rate:-0}" -gt 0 ] 2>/dev/null; then
    case "$BAT_STATUS" in
      Discharging) mins=$(( now * 60 / rate ));;
      Charging) [ "$full" -gt "$now" ] && mins=$(( (full - now) * 60 / rate ));;
    esac
  fi
  if [ "$mins" -ge 60 ]; then BAT_TIME="$(( mins / 60 )).$(( (mins % 60) * 10 / 60 )) hours"; elif [ "$mins" -gt 0 ]; then BAT_TIME="$mins minutes"; fi
  break
done
# ---- backlight from sysfs
BL_CUR=""; BL_MAX=""
for d in /sys/class/backlight/*; do
  [ -r "$d/brightness" ] || continue
  read -r BL_CUR < "$d/brightness"; read -r BL_MAX < "$d/max_brightness" 2>/dev/null || BL_MAX=""
  break
done
# ---- is any wireless interface up? (sysfs, no process) — decides whether the light probe spends its one nmcli call
WLAN_UP=0
for w in /sys/class/net/*/wireless; do
  [ -d "$w" ] || continue
  d=${w%/wireless}; st=""; read -r st < "$d/operstate" 2>/dev/null || st=""
  [ "$st" = up ] && WLAN_UP=1
done
export LIGHT BAT_PRESENT BAT_PCT BAT_STATUS BAT_TIME BL_CUR BL_MAX
set -f   # no pathname expansion from here on: nmcli lines are split into words below

WIFI=""
if [ "$LIGHT" = 1 ]; then
  # ACTIVE:SIGNAL of every known access point; the "yes" line is the one in use (nmcli's own reading, 0-100)
  if [ "$WLAN_UP" = 1 ]; then
    lines=$(nmcli -w 2 -t -f ACTIVE,SIGNAL dev wifi list --rescan no 2>/dev/null)
    IFS=$NL; for line in $lines; do case "$line" in yes:*) WIFI=$line; break;; esac; done; unset IFS
  fi
  export WIFI
else
  # ---- daemons. One nmcli for every device (DEVICE:TYPE:STATE:CONNECTION; a Wi-Fi device reads "unavailable" while the
  # radio is off), the Wi-Fi list only when a Wi-Fi device is connected, BlueZ, power-profiles-daemon and KWin's night
  # light through busctl (2 tasks each; bluetoothctl costs 10 tasks per call, powerprofilesctl ~110 ms of CPU), wpctl
  # for the sink volume.
  DEVS=$(nmcli -w 3 -t -f DEVICE,TYPE,STATE,CONNECTION dev status 2>/dev/null)
  IFS=$NL; for line in $DEVS; do case "$line" in *:wifi:connected:*) WIFI=connected;; esac; done; unset IFS
  if [ "$WIFI" = connected ]; then
    # ACTIVE:SIGNAL:SSID:SECURITY of the network in use (nmcli escapes ':' inside values as '\:'; status.js splits on the unescaped ones)
    WIFI=""; lines=$(nmcli -w 3 -t -f ACTIVE,SIGNAL,SSID,SECURITY dev wifi list --rescan no 2>/dev/null)
    IFS=$NL; for line in $lines; do case "$line" in yes:*) WIFI=$line; break;; esac; done; unset IFS
  fi
  IP4=""
  if [ "$PANE" = 1 ]; then
    # default-route interface without a process (the awk below finds it too); the address itself needs `ip` (2 tasks)
    while read -r dev dst rest; do case "$dst" in 00000000) set -- $(ip -4 -o addr show dev "$dev" scope global 2>/dev/null); IP4=${4%/*}; break;; esac; done < /proc/net/route
  fi
  # BlueZ: the default adapter is hci0 on every machine with one Bluetooth controller (the case this bar is for)
  BT_PRESENT=false; BT_POWERED=null; BT_OBJECTS=""
  case "$(busctl --timeout=2 --system get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered 2>/dev/null)" in
    "b true") BT_PRESENT=true; BT_POWERED=true; BT_OBJECTS=$(busctl --timeout=2 --system call org.bluez / org.freedesktop.DBus.ObjectManager GetManagedObjects 2>/dev/null);;
    "b false") BT_PRESENT=true; BT_POWERED=false;;
  esac
  VOLUME=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null)                    # "Volume: 0.45" or "Volume: 0.45 [MUTED]"
  MIC=$(wpctl get-volume @DEFAULT_AUDIO_SOURCE@ 2>/dev/null)                     # the default microphone, same form ("" without one)
  # Privacy: is anything recording or filming right now? PipeWire knows every capture stream — an app recording the
  # microphone is a running "Stream/Input/Audio" node, an app using the camera through PipeWire / the camera portal a
  # running "Stream/Input/Video" node. pw-dump prints one key per line, so one awk pass counts running nodes per class
  # (2 tasks). Apps that open /dev/video* directly (V4L2) do not appear in PipeWire: those are found by the open file
  # handles of THIS user's processes (fuser, 1 task; other users' handles are not readable without root).
  MIC_USED=0; CAM_USED=0
  if command -v pw-dump >/dev/null 2>&1; then
    counts=$(pw-dump 2>/dev/null | LC_ALL=C awk '
      /^  \{/ { cls = ""; st = "" }
      /"media.class":/ { cls = $0 }
      /"state":/ { st = $0 }
      /^  \}/ { if (st ~ /"running"/) { if (cls ~ /Stream\/Input\/Audio/) a++; else if (cls ~ /Stream\/Input\/Video/) v++ } }
      END { printf "%d %d", a + 0, v + 0 }')
    MIC_USED=${counts% *}; CAM_USED=${counts#* }
  fi
  CAM_PRESENT=0
  set +f   # the one place a glob is wanted
  for v in /dev/video*; do
    [ -c "$v" ] || continue
    CAM_PRESENT=1
    if [ "$CAM_USED" = 0 ] && command -v fuser >/dev/null 2>&1 && fuser "$v" >/dev/null 2>&1; then CAM_USED=1; fi
  done
  set -f
  case "$MIC_USED$CAM_USED" in *[!0-9]*) MIC_USED=0; CAM_USED=0;; esac
  PROFILE=$(busctl --timeout=2 --system get-property net.hadess.PowerProfiles /net/hadess/PowerProfiles net.hadess.PowerProfiles ActiveProfile 2>/dev/null)
  PROFILE=${PROFILE#s \"}; PROFILE=${PROFILE%\"}
  # KWin night light: one GetAll (enabled = set up in Settings, running = tinting right now)
  NIGHT=$(busctl --user --timeout=2 call org.kde.KWin /org/kde/KWin/NightLight org.freedesktop.DBus.Properties GetAll s org.kde.KWin.NightLight 2>/dev/null)
  NIGHT_ENABLED=null; NIGHT_RUNNING=false
  case "$NIGHT" in *'"enabled" b true'*) NIGHT_ENABLED=true;; *'"enabled" b false'*) NIGHT_ENABLED=false;; esac
  case "$NIGHT" in *'"running" b true'*) NIGHT_RUNNING=true;; esac
  export DEVS WIFI IP4 BT_PRESENT BT_POWERED BT_OBJECTS VOLUME MIC MIC_USED CAM_USED CAM_PRESENT PROFILE NIGHT_ENABLED NIGHT_RUNNING
fi

# ---- one awk: default-route interface + its rx/tx bytes, the kernel's Wi-Fi link quality (0-70 -> %) as a fallback
# when /proc/net/wireless exists (cfg80211 wext; absent on many kernels), then the JSON line from the values gathered
# above (strings escaped here, numbers validated here).
W=""; [ -r /proc/net/wireless ] && W=/proc/net/wireless
LC_ALL=C exec awk '
  # JSON string: escape backslash and quote, tab -> \t, drop other control characters. A per-character loop, not gsub
  # with backslashes in the replacement: mawk and gawk disagree on those (mawk keeps "\\" literally).
  function jstr(s,   out, i, c) { out = ""; for (i = 1; i <= length(s); i++) { c = substr(s, i, 1); if (c == "\\" || c == "\"") out = out "\\" c; else if (c == "\t") out = out "\\t"; else if (c < " ") continue; else out = out c } return "\"" out "\"" }
  function jnum(s) { return (s ~ /^[0-9]+$/) ? s : "null" }
  function jlines(s,   n, i, out, parts) { n = split(s, parts, "\n"); out = ""; for (i = 1; i <= n; i++) if (parts[i] != "") out = out (out == "" ? "" : ",") jstr(parts[i]); return "[" out "]" }
  # connected BlueZ devices: in GetManagedObjects, count "Connected" b true inside org.bluez.Device1 blocks only
  # (org.bluez.MediaControl1 carries a Connected property too)
  function btcount(s,   n, i, tok, iface, c) { n = split(s, tok, " "); iface = ""; c = 0; for (i = 1; i <= n; i++) { if (tok[i] ~ /^"org\.bluez\.[A-Za-z0-9]+1"$/) iface = tok[i]; else if (tok[i] == "\"Connected\"" && tok[i+1] == "b" && tok[i+2] == "true" && iface == "\"org.bluez.Device1\"") c++ } return c }
  FILENAME == "/proc/net/route" { if ($2 == "00000000" && dev == "") dev = $1; next }
  FILENAME == "/proc/net/dev" { n = index($0, ":"); if (n < 1) next; name = substr($0, 1, n - 1); gsub(/[ \t]/, "", name); $0 = substr($0, n + 1); rx[name] = $1; tx[name] = $9; next }
  FILENAME == "/proc/net/wireless" { n = index($0, ":"); if (n < 1) next; name = substr($0, 1, n - 1); gsub(/[ \t]/, "", name); $0 = substr($0, n + 1); q[name] = int(($2 + 0) * 100 / 70 + 0.5); next }
  END {
    r = (dev in rx) ? rx[dev] : 0; t = (dev in tx) ? tx[dev] : 0; ql = (dev in q) ? q[dev] : -1; if (ql > 100) ql = 100
    bat = (ENVIRON["BAT_PRESENT"] == "1") ? "{\"pct\":" jnum(ENVIRON["BAT_PCT"]) ",\"status\":" jstr(ENVIRON["BAT_STATUS"]) ",\"time\":" jstr(ENVIRON["BAT_TIME"]) "}" : "null"
    bl = (ENVIRON["BL_MAX"] != "") ? "{\"cur\":" jnum(ENVIRON["BL_CUR"]) ",\"max\":" jnum(ENVIRON["BL_MAX"]) "}" : "null"
    head = "\"net\":{\"iface\":" jstr(dev) ",\"rx\":" jnum(r) ",\"tx\":" jnum(t) "},\"wifi_quality\":" (ql < 0 ? "null" : ql) ",\"wifi\":" jstr(ENVIRON["WIFI"]) ",\"battery\":" bat ",\"backlight\":" bl
    if (ENVIRON["LIGHT"] == "1") { print "{\"light\":true," head "}"; exit }
    print "{\"light\":false," head ",\"devs\":" jlines(ENVIRON["DEVS"]) ",\"ip4\":" jstr(ENVIRON["IP4"]) \
          ",\"bt\":{\"present\":" ENVIRON["BT_PRESENT"] ",\"powered\":" ENVIRON["BT_POWERED"] ",\"connected\":" btcount(ENVIRON["BT_OBJECTS"]) "}" \
          ",\"volume\":" jstr(ENVIRON["VOLUME"]) ",\"mic\":" jstr(ENVIRON["MIC"]) \
          ",\"privacy\":{\"mic_used\":" jnum(ENVIRON["MIC_USED"]) ",\"cam_used\":" jnum(ENVIRON["CAM_USED"]) ",\"cam_present\":" jnum(ENVIRON["CAM_PRESENT"]) "}" \
          ",\"profile\":" jstr(ENVIRON["PROFILE"]) \
          ",\"night\":{\"enabled\":" ENVIRON["NIGHT_ENABLED"] ",\"running\":" ENVIRON["NIGHT_RUNNING"] "}}"
  }
' /proc/net/route /proc/net/dev $W
