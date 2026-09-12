// Fab OS default layout: top bar (global menu feel) + centred floating dock at the bottom.
var top = new Panel
top.location = "top"; top.height = Math.round(gridUnit * 1.9); top.floating = false; top.hiding = "none"
var kickoff = top.addWidget("org.kde.plasma.kickoff")
kickoff.currentConfigGroup = ["General"]
kickoff.writeConfig("icon", "fabos")   // Fab OS mark instead of the KDE logo
kickoff.writeConfig("showActionButtonCaptions", false)
kickoff.writeConfig("primaryActions", 0)
top.addWidget("org.kde.plasma.appmenu")
top.addWidget("org.kde.plasma.panelspacer")
top.addWidget("org.kde.plasma.digitalclock")
top.addWidget("org.kde.plasma.panelspacer")
top.addWidget("org.kde.plasma.systemtray")
top.addWidget("org.kde.plasma.showdesktop")

var dock = new Panel
dock.location = "bottom"; dock.height = Math.round(gridUnit * 3.2); dock.floating = true; dock.hiding = "dodgewindows"
dock.lengthMode = "fit"; dock.alignment = "center"
var tasks = dock.addWidget("org.kde.plasma.icontasks")
tasks.currentConfigGroup = ["General"]
tasks.writeConfig("launchers", ["applications:fabos-command-center.desktop","applications:org.kde.dolphin.desktop","applications:org.kde.konsole.desktop","applications:org.kde.kate.desktop","applications:firefox.desktop","applications:systemsettings.desktop","applications:org.kde.discover.desktop"])
tasks.writeConfig("iconSpacing", 1)
tasks.writeConfig("highlightWindows", true)
tasks.writeConfig("indicateAudioPlaying", true)
tasks.writeConfig("fill", false)

// desktop containments: wallpaper + the "Ask me to do…" bar near the top of the home screen
var desktops = desktopsForActivity(currentActivity())
for (var j = 0; j < desktops.length; j++) {
  var d = desktops[j]
  d.wallpaperPlugin = "org.kde.image"
  d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"]
  d.writeConfig("Image", "/usr/share/wallpapers/FabOS/")
  d.addWidget("in.patienceai.fabos.askbar", 260, 90, 760, 100)
}
