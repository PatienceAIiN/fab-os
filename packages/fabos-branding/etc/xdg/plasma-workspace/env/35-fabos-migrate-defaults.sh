#!/bin/sh
# @DISTRO_NAME@: one-time migration of a user's own KDE pins to the @DISTRO_NAME@ values (1.0-8).
#
# Why: /etc/xdg/ksplashrc, /etc/xdg/kdeglobals and the look-and-feel defaults name the Fab OS splash, global theme,
# colour scheme, icons, Plasma style and window frame — but a user's ~/.config wins over /etc/xdg, and a home directory
# written by 1.0-5 / 1.0-6 (the welcome wizard's theme page, the Global Theme / Splash Screen settings pages, or Plasma
# itself when it first ran) can still pin "org.kde.breeze.desktop" and friends. The visible result on such a machine
# after an over-the-air update: the Breeze "Plasma / made by KDE" start-up splash after every login, Breeze names in
# Fab Settings. This runs at every session start (startplasma sources plasma-workspace/env/*.sh BEFORE the compositor and
# the splash start), does its work ONCE per user (stamp file), touches only KDE/Breeze values — a user's own choice of
# another theme, "no splash" (Theme=None) or a third-party colour scheme is left exactly as it is — and never fails the
# session start (every step is best effort; kwriteconfig6 edits KConfig files without disturbing the rest of the file).
_fabos_cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
_fabos_stamp="$_fabos_cfg/fabos/defaults-migrated-v1"
if [ ! -f "$_fabos_stamp" ] && command -v kwriteconfig6 >/dev/null 2>&1 && command -v kreadconfig6 >/dev/null 2>&1; then
  _fabos_read() { kreadconfig6 --file "$1" --group "$2" --key "$3" 2>/dev/null; }
  _fabos_write() { kwriteconfig6 --file "$1" --group "$2" --key "$3" "$4" 2>/dev/null || true; }
  _fabos_log=""
  # dark or light Fab OS? follow the colour scheme the user has now (BreezeDark / FabDark -> dark, anything else -> light)
  _fabos_scheme=$(_fabos_read kdeglobals General ColorScheme)
  case "$_fabos_scheme" in *Dark*|"") _fabos_dark=1 ;; *) _fabos_dark=0 ;; esac
  if [ "$_fabos_dark" = 1 ]; then _fabos_lnf=in.patienceai.fabos.desktop; _fabos_colors=FabDark; _fabos_frame=__aurorae__svg__FabOS
  else _fabos_lnf=in.patienceai.fabos.light.desktop; _fabos_colors=FabLight; _fabos_frame=__aurorae__svg__FabOSLight; fi
  # 1. start-up splash: Breeze -> Fab OS (Theme=None = the user turned the splash off: kept)
  case "$(_fabos_read ksplashrc KSplash Theme)" in
    org.kde.breeze*.desktop)
      _fabos_write ksplashrc KSplash Theme in.patienceai.fabos.desktop; _fabos_write ksplashrc KSplash Engine KSplashQML; _fabos_log="$_fabos_log ksplash" ;;
  esac
  # 2. global theme pin (what Fab Settings > Global Theme highlights; also the lock screen's look-and-feel package)
  case "$(_fabos_read kdeglobals KDE LookAndFeelPackage)" in
    org.kde.breeze*.desktop) _fabos_write kdeglobals KDE LookAndFeelPackage "$_fabos_lnf"; _fabos_log="$_fabos_log lookandfeel" ;;
  esac
  case "$(_fabos_read kscreenlockerrc Greeter Theme)" in
    org.kde.breeze*.desktop) _fabos_write kscreenlockerrc Greeter Theme "$_fabos_lnf"; _fabos_log="$_fabos_log lockscreen-lnf" ;;
  esac
  # 3. colour scheme: Breeze Dark / Breeze Light / Breeze Classic -> the Fab scheme of the same tone (only when it is installed)
  if [ -f "/usr/share/color-schemes/$_fabos_colors.colors" ]; then
    case "$_fabos_scheme" in
      BreezeDark|BreezeLight|BreezeClassic|Breeze) _fabos_write kdeglobals General ColorScheme "$_fabos_colors"; _fabos_log="$_fabos_log colors" ;;
    esac
  fi
  # 4. icons, Plasma style, window frame: Breeze -> FabOS (only when the Fab OS theme is installed)
  if [ -f /usr/share/icons/FabOS/index.theme ]; then
    case "$(_fabos_read kdeglobals Icons Theme)" in breeze|breeze-dark|breeze_cursors) _fabos_write kdeglobals Icons Theme FabOS; _fabos_log="$_fabos_log icons" ;; esac
  fi
  if [ -d /usr/share/plasma/desktoptheme/FabOS ]; then
    case "$(_fabos_read plasmarc Theme name)" in breeze-dark|breeze-light|default) _fabos_write plasmarc Theme name FabOS; _fabos_log="$_fabos_log plasma-style" ;; esac
  fi
  if [ -d /usr/share/aurorae/themes/FabOS ] && [ "$(_fabos_read kwinrc org.kde.kdecoration2 library)" = org.kde.breeze ]; then
    _fabos_write kwinrc org.kde.kdecoration2 library org.kde.kwin.aurorae.v2; _fabos_write kwinrc org.kde.kdecoration2 theme "$_fabos_frame"; _fabos_log="$_fabos_log window-frame"
  fi
  # 5. ~/.config/kdedefaults: Plasma copies the defaults of the global theme named in kdeglobals here at session start
  #    and puts the directory ahead of /etc/xdg (XDG_CONFIG_DIRS) — a Breeze pin there is what actually selects the
  #    Breeze splash. The `package` file records which theme the copy came from: when it names Breeze, drop it so this
  #    start re-copies from the Fab OS theme, and point the splash entry at Fab OS right away in case the copy step
  #    already ran before this script.
  if [ -f "$_fabos_cfg/kdedefaults/package" ]; then
    case "$(cat "$_fabos_cfg/kdedefaults/package" 2>/dev/null)" in
      org.kde.breeze*) rm -f "$_fabos_cfg/kdedefaults/package"; _fabos_log="$_fabos_log kdedefaults" ;;
    esac
  fi
  case "$(_fabos_read "$_fabos_cfg/kdedefaults/ksplashrc" KSplash Theme)" in
    org.kde.breeze*.desktop) _fabos_write "$_fabos_cfg/kdedefaults/ksplashrc" KSplash Theme in.patienceai.fabos.desktop; _fabos_log="$_fabos_log kdedefaults-ksplash" ;;
  esac
  mkdir -p "$_fabos_cfg/fabos" 2>/dev/null && printf '%s migrated:%s\n' "$(date -u +%FT%TZ 2>/dev/null)" "${_fabos_log:- nothing}" > "$_fabos_stamp" 2>/dev/null
  unset -f _fabos_read _fabos_write
  unset _fabos_log _fabos_scheme _fabos_dark _fabos_lnf _fabos_colors _fabos_frame
fi
unset _fabos_cfg _fabos_stamp
