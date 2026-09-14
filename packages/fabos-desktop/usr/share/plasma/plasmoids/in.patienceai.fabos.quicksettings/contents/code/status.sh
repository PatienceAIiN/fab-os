#!/bin/sh
# Fab OS quick settings: one probe of everything the bar shows, as key=value lines (parsed by ui/status.js).
# Run by the applet through the Plasma5Support executable engine every 10 s and after each action; every daemon call
# is wrapped in `timeout` so a stuck service never stalls the bar. No root: nmcli, bluetoothctl, wpctl, upower,
# powerprofilesctl and sysfs reads all work as the logged-in user. Backlight writes go through powerdevil (QML side).
T="timeout 4"
echo "wifi_radio=$($T nmcli -t -f WIFI radio 2>/dev/null)"
$T nmcli -t -f TYPE,DEVICE,NAME con show --active 2>/dev/null | while IFS= read -r line; do
  type=${line%%:*}; rest=${line#*:}; dev=${rest%%:*}; name=${rest#*:}
  echo "conn=$type|$dev|$name"
done
$T nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list --rescan no 2>/dev/null | grep '^\*' | head -1 | sed 's/^/wifi=/'
dev=$(awk '$2=="00000000"{print $1; exit}' /proc/net/route 2>/dev/null)
echo "iface=$dev"
[ -n "$dev" ] && echo "ip4=$($T nmcli -t -g IP4.ADDRESS dev show "$dev" 2>/dev/null | head -1)"
bt=$($T bluetoothctl show 2>/dev/null)
if printf %s "$bt" | grep -q '^Controller'; then echo "bt_present=yes"; else echo "bt_present=no"; fi
echo "bt_powered=$(printf %s "$bt" | awk -F': ' '/^[[:space:]]*Powered:/{print $2; exit}')"
echo "bt_connected=$($T bluetoothctl devices Connected 2>/dev/null | grep -c '^Device')"
echo "volume=$($T wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null)"
for b in /sys/class/power_supply/BAT*; do
  [ -r "$b/capacity" ] || continue
  echo "bat_pct=$(cat "$b/capacity")"; echo "bat_status=$(cat "$b/status" 2>/dev/null)"; break
done
up=$($T upower -e 2>/dev/null | grep -m1 -i 'BAT')
[ -n "$up" ] && $T upower -i "$up" 2>/dev/null | awk -F': *' '/time to (empty|full)/{print "bat_time=" $2; exit}'
echo "profile=$($T powerprofilesctl get 2>/dev/null)"
for bl in /sys/class/backlight/*; do
  [ -r "$bl/brightness" ] || continue
  echo "bl_cur=$(cat "$bl/brightness")"; echo "bl_max=$(cat "$bl/max_brightness")"; break
done
exit 0
