// Fab OS default layout.
//   Top bar: global menu · clock · Fab OS quick settings (network glyph + live ↓/↑ speed, Bluetooth, volume, battery with
//            percentage, bell) · system tray for third-party status icons only. No start button, no peek button up here;
//            every status glyph is a FabOS monochrome icon (brand/gen/make_assets.py MONO_MAP) recoloured to the scheme.
//            One "bar size" (BAR_SIZE below) sets the quick-settings glyph/text sizes AND the clock's Inter size at first
//            login; later changes go through the quick-settings config page, which re-applies both via plasmashell scripting.
//   Bottom dock (floating, centred, Windows-style placement): Fab OS start button first, then the Fab OS dock (pinned/running
//            apps with macOS-like hover magnify), and "peek at the desktop" as the last item (the bottom-right hot corner does
//            the same: kwinrc ElectricBorders).
//   Home screen: the "Ask me to do anything…" bar centred on the desktop.

var BAR_SIZE = "medium"                                       // small | medium | large  (quick settings + clock)
var CLOCK_PX = { small: 12, medium: 13, large: 15 }            // Inter size of the stock clock for each bar size
var MAGNIFY = true                                            // hover magnify: one switch for the bar's glyphs and the dock (quick settings + dock, kept in sync)
var MAGNIFICATION = "normal"                                  // how much the dock magnifies (subtle | normal | strong) — the dock's own setting

var top = new Panel
top.location = "top"; top.height = Math.round(gridUnit * 2.0); top.floating = false; top.hiding = "none"
top.addWidget("org.kde.plasma.appmenu")
top.addWidget("org.kde.plasma.panelspacer")
var clock = top.addWidget("org.kde.plasma.digitalclock")
clock.currentConfigGroup = ["Appearance"]          // "Sat 13 Sep · 12:47 AM" on one bold line
clock.writeConfig("showDate", true); clock.writeConfig("dateDisplayFormat", "BesideTime")
clock.writeConfig("dateFormat", "custom"); clock.writeConfig("customDateFormat", "ddd d MMM")
clock.writeConfig("autoFontAndSize", false); clock.writeConfig("fontFamily", "Inter"); clock.writeConfig("fontWeight", 700); clock.writeConfig("boldText", true); clock.writeConfig("fontSize", 13)   // = CLOCK_PX.medium (literal: tests/branding-check.sh greps it)
if (CLOCK_PX[BAR_SIZE] !== 13) clock.writeConfig("fontSize", CLOCK_PX[BAR_SIZE])   // any other BAR_SIZE: the table wins
top.addWidget("org.kde.plasma.panelspacer")
top.addWidget("org.kde.plasma.systemtray")   // third-party status icons only: battery, network, volume, bluetooth and the
                                             // stock bell are hidden there by /usr/lib/fabos/tray-defaults at login (no duplicates)
var quick = top.addWidget("in.patienceai.fabos.quicksettings")
quick.currentConfigGroup = ["General"]
quick.writeConfig("barSize", BAR_SIZE)
quick.writeConfig("magnify", MAGNIFY)

var dock = new Panel
dock.location = "bottom"; dock.height = Math.round(gridUnit * 4.0); dock.floating = true; dock.hiding = "dodgewindows"
dock.lengthMode = "fit"; dock.alignment = "center"
var kickoff = dock.addWidget("org.kde.plasma.kickoff")
kickoff.currentConfigGroup = ["General"]
kickoff.writeConfig("icon", "fabos")               // Fab OS mark; the launcher opens from the dock like a Windows start button
kickoff.writeConfig("showActionButtonCaptions", false)
kickoff.writeConfig("primaryActions", 0)
var tasks = dock.addWidget("in.patienceai.fabos.dock")   // TasksModel backend, macOS-like magnify (replaces org.kde.plasma.icontasks)
tasks.currentConfigGroup = ["General"]
tasks.writeConfig("launchers", ["applications:fabos-overview.desktop", "applications:fabos-command-center.desktop", "applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop",
  "applications:org.kde.kate.desktop", "applications:brave-browser.desktop", "applications:systemsettings.desktop", "applications:org.kde.discover.desktop"])
tasks.writeConfig("magnify", MAGNIFY)
tasks.writeConfig("magnification", MAGNIFICATION)
tasks.writeConfig("groupApps", true)
dock.addWidget("org.kde.plasma.showdesktop")   // peek at the desktop: last dock item (icon user-desktop -> FabOS mono desktop_windows glyph)

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
