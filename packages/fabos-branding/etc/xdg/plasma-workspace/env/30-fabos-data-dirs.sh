#!/bin/sh
# @DISTRO_NAME@: /usr/share/fabos carries the Fab-named copies of upstream XDG data files (desktop entries, shortcut
# components, web-shortcut stubs — written by /usr/lib/fabos/rebrand-overrides from /usr/lib/fabos/overrides.d/*.rules).
# Put first in XDG_DATA_DIRS for the whole session (startplasma sources this before the compositor starts and imports the
# result into the systemd user manager and the session bus), so KService, the launcher, Fab Search, notifications and
# Fab Settings resolve a basename to the Fab OS copy before /usr/local/share (the generic rebrand pass) and /usr/share.
case ":${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:" in
  *:/usr/share/fabos:*) ;;
  *) export XDG_DATA_DIRS="/usr/share/fabos:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" ;;
esac
