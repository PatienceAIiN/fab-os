// Fab OS: run through plasmashell's scripting API at login. The top bar's Fab OS quick settings applet
// (in.patienceai.fabos.quicksettings) shows network, Bluetooth, volume, battery (with percentage) and the bell, so the
// stock tray icons for those five are moved to the tray's hidden list: nothing is duplicated, the applets themselves
// keep running (the notifications applet is the notification server, the volume applet handles the volume keys) and
// stay one click away under the tray's expander. The tray shows third-party status icons only.
var cs = containments()
for (var i = 0; i < cs.length; i++) {
  var c = cs[i]
  if (c.type !== "org.kde.plasma.private.systemtray") continue
  c.currentConfigGroup = ["General"]
  c.writeConfig("hiddenItems", ["org.kde.plasma.battery", "org.kde.plasma.networkmanagement", "org.kde.plasma.volume", "org.kde.plasma.bluetooth", "org.kde.plasma.notifications"])
  c.writeConfig("shownItems", [])
  var ws = c.widgets("org.kde.plasma.battery")
  for (var j = 0; j < ws.length; j++) { ws[j].currentConfigGroup = ["General"]; ws[j].writeConfig("showPercentage", true) }
}
