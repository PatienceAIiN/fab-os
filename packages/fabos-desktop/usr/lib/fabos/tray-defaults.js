// Fab OS: run through plasmashell's scripting API at login. Pins battery (with percentage), network, volume and
// bluetooth as always-visible tray icons so they align with the other status icons in the top bar.
var cs = containments()
for (var i = 0; i < cs.length; i++) {
  var c = cs[i]
  if (c.type !== "org.kde.plasma.private.systemtray") continue
  c.currentConfigGroup = ["General"]
  c.writeConfig("shownItems", ["org.kde.plasma.battery", "org.kde.plasma.networkmanagement", "org.kde.plasma.volume", "org.kde.plasma.bluetooth"])
  var ws = c.widgets("org.kde.plasma.battery")
  for (var j = 0; j < ws.length; j++) { ws[j].currentConfigGroup = ["General"]; ws[j].writeConfig("showPercentage", true) }
}
