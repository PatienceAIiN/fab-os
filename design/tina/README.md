# UI revamp source: "TinaDesktop — An Operating System design you will love" (Figma Community)

Reference: https://www.figma.com/design/zSOuPXkqig1VIJJf86uPkt/ (node 40-228)

## Intake (blocked on access)
Figma files are not readable without credentials. Two ways to unblock, either works:
1. Figma personal access token → `export FIGMA_TOKEN=figd_...` then `python3 design/tina/pull.py` pulls frame PNGs,
   colours, type styles and component names into `design/tina/export/` via the Figma REST API.
2. Manual export: in Figma select the main frames (desktop, dock, launcher, settings, notifications, lock/login),
   Export → PNG @2x (+ SVG for icons) into `design/tina/export/`.

## License check (required before shipping)
Figma Community files are usually CC BY 4.0. Record the exact license and author here, add attribution to
legal/ARTWORK.md, and confirm the file contains no third-party proprietary assets (Apple/Microsoft icons, stock photos)
before reproducing them. Fab OS reproduces the *layout, spacing, colour and motion language* with its own assets.

## Mapping Figma → Plasma 6 (what "whole UI" means technically)
| Figma element            | Plasma mechanism (package fabric-desktop)                                   |
|--------------------------|------------------------------------------------------------------------------|
| Colours / typography     | `FabOS.colors` KDE colour scheme + kdeglobals fonts (Inter)                  |
| Top bar / dock / widgets | look-and-feel `layout.js` + custom plasmoids (askbar, dock) + Plasma theme  |
| Window chrome            | Aurorae window decoration theme (SVG) or Breeze settings                     |
| Panel/popup surfaces     | Plasma desktop theme (`plasma/desktoptheme/FabOS`, SVG + colors)             |
| Launcher                 | Kickoff config or custom launcher plasmoid                                   |
| Login / lock             | SDDM theme QML + kscreenlocker Look-and-Feel                                 |
| Boot / splash            | Plymouth script theme + KSplash QML                                          |
| Icons                    | FabOS icon theme (Material Symbols tiles) → extend per design                |
| Motion                   | KWin effects config + Plasma AnimationDurationFactor + QML transitions       |
