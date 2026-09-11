// Fab OS default layout: top bar (global menu feel) + centred floating dock at the bottom.
var top = new Panel
top.location = "top"; top.height = Math.round(gridUnit * 1.9); top.floating = false; top.hiding = "none"
top.addWidget("org.kde.plasma.kickoff")
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
tasks.writeConfig("launchers", ["applications:fabric-command-center.desktop","applications:org.kde.dolphin.desktop","applications:org.kde.konsole.desktop","applications:org.kde.kate.desktop","applications:systemsettings.desktop","applications:org.kde.discover.desktop"])
tasks.writeConfig("iconSpacing", 1)

for (var i in screens) {
  var d = new Activity("desktop", screens[i]) // ensure a desktop containment
  d.wallpaperPlugin = "org.kde.image"
  d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"]
  d.writeConfig("Image", "/usr/share/wallpapers/Fabric/")
  // the "Ask me to do…" bar, centred near the top of the home screen
  var sw = screenGeometry(screens[i]).width, sh = screenGeometry(screens[i]).height
  var w = Math.min(760, Math.round(sw * 0.6)), h = Math.round(gridUnit * 5.4)
  d.addWidget("in.patienceai.fabric.askbar", Math.round((sw - w) / 2), Math.round(sh * 0.12), w, h)
}
