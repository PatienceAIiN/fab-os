// Fab OS default layout.
//   Top bar: global menu · clock · battery (with percentage) · Wi-Fi · volume · tray. No start button up here.
//   Bottom dock (floating, centred, Windows-style placement): Fab OS start button first, then pinned/running apps.
//   Home screen: the "Ask me to do anything…" bar centred on the desktop.

var top = new Panel
top.location = "top"; top.height = Math.round(gridUnit * 2.0); top.floating = false; top.hiding = "none"
top.addWidget("org.kde.plasma.appmenu")
top.addWidget("org.kde.plasma.panelspacer")
top.addWidget("org.kde.plasma.digitalclock")
top.addWidget("org.kde.plasma.panelspacer")
var battery = top.addWidget("org.kde.plasma.battery")
battery.currentConfigGroup = ["General"]
battery.writeConfig("showPercentage", true)
top.addWidget("org.kde.plasma.networkmanagement")
top.addWidget("org.kde.plasma.volume")
var tray = top.addWidget("org.kde.plasma.systemtray")
// the three indicators above are standalone; keep the tray from loading duplicates of them
tray.currentConfigGroup = ["General"]
tray.writeConfig("extraItems", ["org.kde.plasma.bluetooth", "org.kde.plasma.brightness", "org.kde.plasma.cameraindicator", "org.kde.plasma.clipboard",
  "org.kde.plasma.devicenotifier", "org.kde.plasma.keyboardlayout", "org.kde.plasma.manage-inputmethod", "org.kde.plasma.mediacontroller",
  "org.kde.plasma.notifications", "org.kde.plasma.printmanager", "org.kde.kscreen", "org.kde.plasma.vault"])
tray.writeConfig("knownItems", ["org.kde.plasma.bluetooth", "org.kde.plasma.brightness", "org.kde.plasma.cameraindicator", "org.kde.plasma.clipboard",
  "org.kde.plasma.devicenotifier", "org.kde.plasma.keyboardlayout", "org.kde.plasma.manage-inputmethod", "org.kde.plasma.mediacontroller",
  "org.kde.plasma.notifications", "org.kde.plasma.printmanager", "org.kde.kscreen", "org.kde.plasma.vault"])
top.addWidget("org.kde.plasma.showdesktop")

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
tasks.writeConfig("launchers", ["applications:fabos-command-center.desktop", "applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop",
  "applications:org.kde.kate.desktop", "applications:firefox.desktop", "applications:systemsettings.desktop", "applications:org.kde.discover.desktop"])
tasks.writeConfig("iconSpacing", 1)
tasks.writeConfig("highlightWindows", true)
tasks.writeConfig("indicateAudioPlaying", true)
tasks.writeConfig("fill", false)

// Desktop containments: wallpaper + the ask bar centred on the home screen (about 36% down, 760 px wide).
var desktops = desktopsForActivity(currentActivity())
for (var j = 0; j < desktops.length; j++) {
  var d = desktops[j]
  d.wallpaperPlugin = "org.kde.image"
  d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"]
  d.writeConfig("Image", "/usr/share/wallpapers/FabOS/")
  var sw = 1280, sh = 800
  try { var g = screenGeometry(d.screen); if (g && g.width > 0) { sw = g.width; sh = g.height } } catch (e) {}
  var w = Math.min(760, Math.round(sw * 0.6)), h = 124
  d.addWidget("in.patienceai.fabos.askbar", Math.round((sw - w) / 2), Math.round(sh * 0.36), w, h)
}
