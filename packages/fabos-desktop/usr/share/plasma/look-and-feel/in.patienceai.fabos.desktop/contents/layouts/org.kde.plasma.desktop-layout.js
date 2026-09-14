// Fab OS default layout.
//   Top bar: global menu · clock · battery (with percentage) · Wi-Fi · volume · tray. No start button, no peek button up here;
//            every status glyph is a FabOS monochrome icon (brand/gen/make_assets.py MONO_MAP) recoloured to the scheme.
//   Bottom dock (floating, centred, Windows-style placement): Fab OS start button first, then pinned/running apps,
//            and "peek at the desktop" as the last item (the bottom-right hot corner does the same: kwinrc ElectricBorders).
//   Home screen: the "Ask me to do anything…" bar centred on the desktop.

var top = new Panel
top.location = "top"; top.height = Math.round(gridUnit * 2.0); top.floating = false; top.hiding = "none"
top.addWidget("org.kde.plasma.appmenu")
top.addWidget("org.kde.plasma.panelspacer")
var clock = top.addWidget("org.kde.plasma.digitalclock")
clock.currentConfigGroup = ["Appearance"]          // "Sat 13 Sep · 12:47 AM" on one bold line
clock.writeConfig("showDate", true); clock.writeConfig("dateDisplayFormat", "BesideTime")
clock.writeConfig("dateFormat", "custom"); clock.writeConfig("customDateFormat", "ddd d MMM")
clock.writeConfig("autoFontAndSize", false); clock.writeConfig("fontFamily", "Inter"); clock.writeConfig("fontWeight", 700); clock.writeConfig("boldText", true); clock.writeConfig("fontSize", 13)
top.addWidget("org.kde.plasma.panelspacer")
top.addWidget("org.kde.plasma.systemtray")   // battery (with %), Wi-Fi, volume, bluetooth are pinned visible by /usr/lib/fabos/tray-defaults at login

var dock = new Panel
dock.location = "bottom"; dock.height = Math.round(gridUnit * 3.2); dock.floating = true; dock.hiding = "dodgewindows"
dock.lengthMode = "fit"; dock.alignment = "center"
var kickoff = dock.addWidget("org.kde.plasma.kickoff")
kickoff.currentConfigGroup = ["General"]
kickoff.writeConfig("icon", "fabos")               // Fab OS mark; the launcher opens from the dock like a Windows start button
kickoff.writeConfig("showActionButtonCaptions", false)
kickoff.writeConfig("primaryActions", 0)
var tasks = dock.addWidget("org.kde.plasma.icontasks")
tasks.currentConfigGroup = ["General"]
tasks.writeConfig("launchers", ["applications:fabos-overview.desktop", "applications:fabos-command-center.desktop", "applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop",
  "applications:org.kde.kate.desktop", "applications:firefox.desktop", "applications:systemsettings.desktop", "applications:org.kde.discover.desktop"])
tasks.writeConfig("iconSpacing", 1)
tasks.writeConfig("highlightWindows", true)
tasks.writeConfig("indicateAudioPlaying", true)
tasks.writeConfig("fill", false)
dock.addWidget("org.kde.plasma.showdesktop")   // peek at the desktop: last dock item (icon user-desktop -> FabOS mono desktop_windows glyph)

// Desktop containments: wallpaper + the ask bar centred on the home screen (about 36% down, 760 px wide).
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
  d.addWidget("in.patienceai.fabos.askbar", 0, Math.round(sh * 0.30), sw, 150)
}
