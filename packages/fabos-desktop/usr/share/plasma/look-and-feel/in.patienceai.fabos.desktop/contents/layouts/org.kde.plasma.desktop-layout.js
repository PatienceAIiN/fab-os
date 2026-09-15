// Fab OS default layout.
//   Top bar: global menu · Fab OS clock (one bold Inter line, "Mon 15 Sep · 10:47") · system tray for third-party status
//            icons only · Fab OS quick settings (network glyph + live ↓/↑ speed, Bluetooth, volume, battery with
//            percentage, bell). No start button, no peek button up here; every status glyph is a FabOS monochrome icon
//            (brand/gen/make_assets.py MONO_MAP) recoloured to the scheme. ONE size (BAR_SIZE below) sets the
//            quick-settings glyph/text sizes AND the clock's Inter size at first login; later changes go through the
//            quick-settings config page, which re-applies both via plasmashell scripting (status.js syncScript).
//   Bottom dock (floating, centred, Windows-style placement): ONE applet, the Fab OS dock, draws everything at one
//            size with macOS-like hover magnify: the Fab OS start button first, the pinned/running apps, "peek at the
//            desktop" last (the bottom-right hot corner does the same: kwinrc ElectricBorders). The stock launcher
//            (kickoff) stays in the panel as a ZERO-WIDTH applet — with an empty icon and label its compact
//            representation has no size (plasma-desktop 6.6 CompactRepresentation: impWidth 0) — so the Meta key and
//            plasmashell's activateLauncherMenu() still open it, anchored at the dock's left end where the Fab button
//            sits; the Fab button itself calls activateLauncherMenu.
//   Home screen: the "Ask me to do anything…" bar centred on the desktop.

var BAR_SIZE = "medium"                                       // small | medium | large  (quick settings + clock: Inter 12 / 13 / 15)
var MAGNIFY = true                                            // hover magnify: one switch for the bar's glyphs and the dock (quick settings + dock, kept in sync)
var MAGNIFICATION = "normal"                                  // how much the dock magnifies (subtle | normal | strong) — the dock's own setting

// System tray, Plasma 6.6: the tray applet is its own containment and its settings live under the widget (appletsrc
// [Containments][panel][Applets][tray][General]). At start its PlasmoidRegistry adds every EnabledByDefault status
// applet it does not yet KNOW (knownItems) to extraItems — so a plugin is kept out for good only by listing it in
// knownItems and leaving it out of extraItems. The Fab OS quick settings replace the network, Bluetooth, volume,
// battery and brightness applets (volume keys and their OSD live in plasma-pa's kded module audioshortcutsservice,
// not in the applet; brightness keys in powerdevil), so those five are dropped from extraItems here. The notifications
// applet stays loaded (it draws the popups) but is forced into the tray's hidden popup. `hiddenItems` alone was not
// enough before: the login script that wrote it used a global (a containment list) the 6.6 scripting API does not have,
// and hidden items are only folded into the tray's popup anyway — Plasma re-shows an "active" one on the bar.
// Verified by reading the appletsrc a real plasmashell wrote from this script (tests/desktop-applets-qml-test.sh, step tray).
var TRAY_REPLACED = ["org.kde.plasma.networkmanagement", "org.kde.plasma.bluetooth", "org.kde.plasma.volume", "org.kde.plasma.battery", "org.kde.plasma.brightness"]
var TRAY_HIDDEN = ["org.kde.plasma.notifications"]
// the 6.6 image's EnabledByDefault status applets (recorded from a generated appletsrc): the fallback when the registry
// has not filled extraItems by the time this runs
var TRAY_DEFAULT = ["org.kde.kupapplet", "org.kde.plasma.vault", "org.kde.kscreen", "org.kde.plasma.battery", "org.kde.plasma.bluetooth", "org.kde.plasma.brightness",
  "org.kde.plasma.cameraindicator", "org.kde.plasma.clipboard", "org.kde.plasma.devicenotifier", "org.kde.plasma.keyboardlayout", "org.kde.plasma.manage-inputmethod",
  "org.kde.plasma.mediacontroller", "org.kde.plasma.networkmanagement", "org.kde.plasma.notifications", "org.kde.plasma.volume", "org.kde.plasma.printmanager",
  "org.kde.plasma.keyboardindicator", "org.kde.plasma.weather"]
function toList(v) { if (v === undefined || v === null) return []; if (typeof v === "string") return v.length ? v.split(",") : []; var out = []; for (var i = 0; i < v.length; i++) out.push(String(v[i])); return out }
function union(a, b) { var out = a.slice(); for (var i = 0; i < b.length; i++) if (out.indexOf(b[i]) < 0) out.push(b[i]); return out }
function without(a, b) { var out = []; for (var i = 0; i < a.length; i++) if (b.indexOf(a[i]) < 0) out.push(a[i]); return out }
function configureTray(tray) {
  tray.currentConfigGroup = ["General"]
  var extra = toList(tray.readConfig("extraItems", [])), known = toList(tray.readConfig("knownItems", []))
  if (extra.length === 0) extra = TRAY_DEFAULT.slice()
  known = union(union(known, extra), TRAY_REPLACED)
  tray.writeConfig("knownItems", known)
  tray.writeConfig("extraItems", without(extra, TRAY_REPLACED))
  tray.writeConfig("hiddenItems", TRAY_HIDDEN)
  tray.writeConfig("shownItems", [])
}

var top = new Panel
top.location = "top"; top.height = Math.round(gridUnit * 2.0); top.floating = false; top.hiding = "none"
top.addWidget("org.kde.plasma.appmenu")
top.addWidget("org.kde.plasma.panelspacer")
var clock = top.addWidget("in.patienceai.fabos.clock")   // replaces the stock digital clock: Inter 700, "ddd d MMM · time" on one line, size = BAR_SIZE
clock.currentConfigGroup = ["General"]
clock.writeConfig("barSize", BAR_SIZE)
clock.writeConfig("showDate", true)
clock.writeConfig("dateFormat", "ddd d MMM")
top.addWidget("org.kde.plasma.panelspacer")
var tray = top.addWidget("org.kde.plasma.systemtray")   // third-party status icons only (see configureTray)
configureTray(tray)
var quick = top.addWidget("in.patienceai.fabos.quicksettings")
quick.currentConfigGroup = ["General"]
quick.writeConfig("barSize", BAR_SIZE)
quick.writeConfig("magnify", MAGNIFY)
quick.writeConfig("showSpeed", true)   // the live rate stays on the bar whenever a link is up (0 kB/s when idle)

var dock = new Panel
dock.location = "bottom"; dock.height = Math.round(gridUnit * 4.0); dock.floating = true; dock.hiding = "dodgewindows"
dock.lengthMode = "fit"; dock.alignment = "center"
var kickoff = dock.addWidget("org.kde.plasma.kickoff")   // the launcher menu: zero-width (no icon, no label); the Fab OS dock draws the start button
kickoff.currentConfigGroup = ["General"]
kickoff.writeConfig("icon", "")
kickoff.writeConfig("menuLabel", "")
kickoff.writeConfig("showActionButtonCaptions", false)
kickoff.writeConfig("primaryActions", 0)
var tasks = dock.addWidget("in.patienceai.fabos.dock")   // TasksModel backend, macOS-like magnify (replaces org.kde.plasma.icontasks), start + peek items built in
tasks.currentConfigGroup = ["General"]
tasks.writeConfig("launchers", ["applications:fabos-overview.desktop", "applications:fabos-command-center.desktop", "applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop",
  "applications:org.kde.kate.desktop", "applications:firefox.desktop", "applications:systemsettings.desktop", "applications:org.kde.discover.desktop"])
tasks.writeConfig("magnify", MAGNIFY)
tasks.writeConfig("magnification", MAGNIFICATION)
tasks.writeConfig("groupApps", true)
tasks.writeConfig("showStart", true)   // Fab OS start button as the first dock item (activateLauncherMenu)
tasks.writeConfig("showPeek", true)    // peek at the desktop as the last dock item (KWin's Show Desktop)

// Desktop containments: wallpaper + the ask bar centred on the home screen: a tall transparent strip from 24% down to the dock; the card sits at its top and the response panel unfolds inside it (desktop layer, behind every window).
var desktops = desktopsForActivity(currentActivity())
for (var j = 0; j < desktops.length; j++) {
  var d = desktops[j]
  d.wallpaperPlugin = "org.kde.image"
  d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"]
  d.writeConfig("Image", "/usr/share/wallpapers/FabOS/")
  d.writeConfig("FillMode", 2)   // PreserveAspectCrop: the best-matching render is cropped to the screen, never stretched
  // The ask bar is a full-width transparent strip; the card inside centres itself from the real screen width at runtime.
  var sw = 3840, sh = 1080
  try { var g = screenGeometry(d.screen >= 0 ? d.screen : 0); if (g && g.width > 0) { sw = g.width; sh = g.height } } catch (e) {}
  d.addWidget("in.patienceai.fabos.askbar", 0, Math.round(sh * 0.24), sw, Math.round(sh * 0.66))   // tall strip: card at its top, the response panel unfolds inside it (desktop layer, behind every window), down to the dock
}
