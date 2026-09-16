#!/bin/sh
# @DISTRO_NAME@ leave screen helper. Logout.qml runs it as `sh <this file> all` through a Plasma5Support "executable"
# DataSource (the package installs it 0644, so it is never executed directly). Output is tab-separated, one record per line:
#   countdown <TAB> <seconds>                 ksmserverrc [General] confirmLogoutCountdown (@DISTRO_NAME@ key; default 30, 0 = wait for a click)
#   app <TAB> <desktop-id> <TAB> <name> <TAB> <icon> <TAB> <instances>
#       one per running application, read from the systemd user units Plasma launches applications in
#       (app-<id>@<random>.service, app-<id>-<random>.scope; autostart units have no window and are skipped). Only used
#       when the greeter cannot see the window list itself; see docs/design/SHUTDOWN.md for what each path can and cannot know.
# Every step is best-effort and the script never fails: empty output means "no information", not "no apps".
mode=${1:-all}
cd / 2>/dev/null || true

countdown=30
if command -v kreadconfig6 >/dev/null 2>&1; then
  v=$(kreadconfig6 --file ksmserverrc --group General --key confirmLogoutCountdown --default 30 2>/dev/null)
  case "$v" in ''|*[!0-9]*) ;; *) countdown=$v ;; esac
fi
printf 'countdown\t%s\n' "$countdown"
[ "$mode" = countdown ] && exit 0

command -v systemctl >/dev/null 2>&1 || exit 0
units=$(systemctl --user list-units --type=scope,service --state=running --plain --no-legend 'app-*' 2>/dev/null | awk '{print $1}')
[ -n "$units" ] || exit 0

dirs="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
old_ifs=$IFS; IFS=:
for d in ${XDG_DATA_DIRS:-/usr/local/share:/usr/share}; do dirs="$dirs $d/applications"; done
IFS=$old_ifs
dirs="$dirs /var/lib/flatpak/exports/share/applications $HOME/.local/share/flatpak/exports/share/applications"

# desktop-entry field from the [Desktop Entry] group only, untranslated key, first occurrence
field() { awk -v key="$2" -F= '/^\[Desktop Entry\]/{s=1;next} /^\[/{s=0} s && $1==key {sub(/^[^=]*=/, ""); sub(/[ \t]+$/, ""); print; exit}' "$1" 2>/dev/null; }

ids=""
for u in $units; do
  case "$u" in *@autostart.service) continue ;; esac
  id=${u#app-}
  case "$id" in
    *.service) id=${id%.service}; id=${id%@*} ;;
    *.scope)   id=${id%.scope}; id=$(printf '%s' "$id" | sed -E 's/-[0-9a-fA-F]+$//') ;;
    *) continue ;;
  esac
  case "$id" in flatpak-*) id=${id#flatpak-} ;; esac
  id=$(printf '%s' "$id" | sed 's/\\x2d/-/g')
  [ -n "$id" ] || continue
  ids="$ids
$id"
done

printf '%s\n' "$ids" | sed '/^$/d' | sort | uniq -c | while read -r n id; do
  f=""
  for d in $dirs; do [ -f "$d/$id.desktop" ] && { f="$d/$id.desktop"; break; }; done
  name=$id; icon=application-x-executable
  if [ -n "$f" ]; then
    v=$(field "$f" Name); [ -n "$v" ] && name=$v
    v=$(field "$f" Icon); [ -n "$v" ] && icon=$v
  fi
  printf 'app\t%s\t%s\t%s\t%s\n' "$id" "$name" "$icon" "$n"
done
exit 0
