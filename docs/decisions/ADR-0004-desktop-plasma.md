# ADR-0004: KDE Plasma 6 on Wayland, no snap, Flatpak for sandboxed apps

## Why Plasma
Mature GPU-composited Wayland session, the most customizable shell (lets us
build the macOS-plus-Windows feel via a look-and-feel package instead of a
bespoke compositor), idle RAM under 1 GB when installed as bare
`plasma-desktop` rather than the Kubuntu metapackage, first-class Inter font
support, KInfoCenter honours os-release/LOGO for About.

## Alternatives
GNOME (heavier, customization via extensions), COSMIC (Rust, promising, young;
kept as an experiment branch), Xfce/LXQt (light but Wayland immature).

## Snap
Not installed and pinned to priority -1. Removes a daemon, mounts, boot time,
and a Canonical-controlled store from the base. Flatpak + Discover cover
sandboxed apps. Firefox therefore comes from Mozilla's apt repo or Flatpak
(documented, not preinstalled yet). *Amended 2026-09-15 (ADR-0016): the
shipped browser is Brave, installed unmodified from Brave's own apt
repository; Firefox and the Mozilla repository were removed.*

## FabOS shell
FabOS AI surfaces (command bar, activity center, settings pane) are delivered
as Plasma widgets/KCMs and layer-shell clients in later milestones; nothing
replaces KWin.
