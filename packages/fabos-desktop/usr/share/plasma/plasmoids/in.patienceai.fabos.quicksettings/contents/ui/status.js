// Pure helpers for the Fab OS quick-settings applet: parse the one JSON line of contents/code/status.sh (full or
// --light) and the older key=value / /proc/net forms, choose FabOS monochrome glyph names, format rates and times,
// and the tile model of the pane (order, size, enabled; persisted as JSON in Plasmoid.configuration.tilesJson).
// No Qt, no I/O — the same file runs under node in tests/quicksettings-js-test.js.
.pragma library

// ---------------------------------------------------------------- status.sh -> object
function empty() {
    return { wifiRadio: null, wifiSsid: "", wifiSignal: -1, wifiLocked: false, wifiActive: null, connType: "", connName: "", connDev: "",
             iface: "", ip4: "", btPresent: false, btPowered: null, btConnected: 0,
             volume: -1, muted: false, hasAudio: false,
             micVolume: -1, micMuted: false, hasMic: false, micUsed: 0, camUsed: 0, camPresent: false, micApps: "", camApps: "",
             batPct: -1, batStatus: "", batTime: "", hasBattery: false, profile: "",
             blCur: -1, blMax: 0, hasBacklight: false,
             nightEnabled: null, nightRunning: false,
             light: false, rx: 0, tx: 0, wifiQuality: -1 }
}

// nmcli -t escapes ':' inside values as '\:'; split on unescaped colons only.
function splitTerse(line) {
    var out = [], cur = ""
    for (var i = 0; i < line.length; i++) {
        var c = line[i]
        if (c === "\\" && i + 1 < line.length) { cur += line[i + 1]; i++ }
        else if (c === ":") { out.push(cur); cur = "" }
        else cur += c
    }
    out.push(cur)
    return out
}

// One probe -> state. status.sh prints ONE JSON line (full or --light); the key=value form of the first release is still
// understood (the QML harness feeds it), so an upgraded applet and an old script never disagree.
function parseStatus(text) {
    var t = String(text || "").trim()
    if (t.charAt(0) === "{") {
        var j = null
        try { j = JSON.parse(t.split("\n")[0]) } catch (e) { j = null }
        return j ? fromJson(j) : empty()
    }
    return parseKeyValues(t)
}

// ---- the active line of `nmcli -t -f ACTIVE,SIGNAL,SSID,SECURITY dev wifi list --rescan no` ("yes:70:Home\:Net:WPA2 WPA3")
// or, from the first release's script, the IN-USE form "*:Home\:Net:70:WPA2". Returns { active, signal, ssid, locked }.
// The light probe passes only ACTIVE,SIGNAL ("yes:70"): ssid stays "" and the applet keeps the last full probe's name.
function parseWifiLine(line) {
    var r = { active: false, signal: -1, ssid: "", locked: false }
    var f = splitTerse(String(line || "").trim())
    if (f.length < 2) return r
    var sec = ""
    if (f[0] === "*" || f[0] === " ") {          // IN-USE:SSID:SIGNAL:SECURITY
        if (f[0] !== "*") return r
        r.active = true; r.ssid = f[1] || ""; r.signal = parseInt(f[2], 10); sec = f[3] || ""
    } else {                                     // ACTIVE:SIGNAL[:SSID[:SECURITY]]
        if (f[0] !== "yes") return r
        r.active = true; r.signal = parseInt(f[1], 10); r.ssid = f[2] || ""; sec = f[3] || ""
    }
    if (isNaN(r.signal)) r.signal = -1
    r.locked = f.length > 3 && sec.trim() !== "" && sec.trim() !== "--"
    return r
}

// the first active line out of a whole `dev wifi list` output (any number of lines, any order)
function activeWifiLine(text) {
    var lines = String(text || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
        var l = lines[i].trim()
        if (l.charAt(0) === "*" || l.indexOf("yes:") === 0) return l
    }
    return ""
}

// ---- `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status` lines (a Wi-Fi device reads "unavailable" while the radio
// is off). The three-field form TYPE:STATE:CONNECTION is accepted too. Fills connType / connDev / connName / wifiRadio.
function applyDevs(s, devs) {
    var wifiDevs = 0, wifiUp = 0
    for (var i = 0; i < devs.length; i++) {
        var f = splitTerse(String(devs[i]))
        if (f.length < 3) continue
        var dev, type, state, name
        if (isDevType(f[0]) && !isDevType(f[1])) { dev = ""; type = f[0]; state = f[1]; name = f.slice(2).join(":") }
        else { dev = f[0]; type = f[1]; state = f[2]; name = f.slice(3).join(":") }
        var isWifi = type === "wifi" || type.indexOf("wireless") >= 0, isWired = type === "ethernet" || type.indexOf("ethernet") >= 0
        if (isWifi) { wifiDevs++; if (state.indexOf("unavailable") < 0 && state.indexOf("unmanaged") < 0) wifiUp++ }
        var connected = state.indexOf("connected") === 0
        if (isWifi && connected && s.connType !== "wifi") { s.connType = "wifi"; s.connDev = dev; s.connName = name }
        else if (isWired && connected && !s.connType) { s.connType = "wired"; s.connDev = dev; s.connName = name }
    }
    s.wifiRadio = wifiDevs === 0 ? null : wifiUp > 0
    return s
}
function isDevType(t) { return ["wifi", "ethernet", "loopback", "tun", "bridge", "wifi-p2p", "wireguard", "bond", "vlan", "dummy", "gsm", "bluetooth", "ppp"].indexOf(t) >= 0 }

// ---- the JSON line of status.sh. `light` = kernel readings + the active Wi-Fi signal (net counters, battery,
// backlight, `wifi` = "yes:70" or ""); the applet merges those into the last full state with mergeLight().
// devs = nmcli dev status lines; wifi = the active line of `dev wifi list`; bt from BlueZ over D-Bus; volume = the raw
// wpctl line; night = KWin's night light (enabled in settings / running now).
function fromJson(j) {
    var s = empty()
    s.light = j.light === true
    var net = j.net || {}
    s.iface = String(net.iface || "")
    s.rx = typeof net.rx === "number" ? net.rx : 0
    s.tx = typeof net.tx === "number" ? net.tx : 0
    s.wifiQuality = typeof j.wifi_quality === "number" ? j.wifi_quality : -1
    var b = j.battery
    if (b && typeof b.pct === "number") { s.batPct = b.pct; s.hasBattery = true; s.batStatus = String(b.status || ""); s.batTime = String(b.time || "") }
    var bl = j.backlight
    if (bl && typeof bl.max === "number" && bl.max > 0) { s.blCur = typeof bl.cur === "number" ? bl.cur : 0; s.blMax = bl.max; s.hasBacklight = true }
    var w = parseWifiLine(activeWifiLine(j.wifi))
    if (typeof j.wifi === "string") { s.wifiActive = w.active; if (w.active) { s.wifiSignal = w.signal; s.wifiSsid = w.ssid; s.wifiLocked = w.locked } }
    if (s.light) return s
    applyDevs(s, j.devs || [])
    if (s.connType === "wifi" && s.wifiSignal < 0 && s.wifiQuality >= 0) s.wifiSignal = s.wifiQuality
    s.ip4 = String(j.ip4 || "").replace(/\/\d+$/, "")
    var bt = j.bt || {}
    s.btPresent = bt.present === true
    s.btPowered = bt.powered === true ? true : (bt.powered === false ? false : null)
    s.btConnected = typeof bt.connected === "number" ? bt.connected : 0
    var m = /Volume:\s*([0-9.]+)/.exec(String(j.volume || ""))
    if (m) { s.volume = Math.round(parseFloat(m[1]) * 100); s.hasAudio = true; s.muted = String(j.volume).indexOf("MUTED") >= 0 }
    var mm = /Volume:\s*([0-9.]+)/.exec(String(j.mic || ""))
    if (mm) { s.micVolume = Math.round(parseFloat(mm[1]) * 100); s.hasMic = true; s.micMuted = String(j.mic).indexOf("MUTED") >= 0 }
    var pr = j.privacy || {}
    s.micUsed = typeof pr.mic_used === "number" ? pr.mic_used : 0
    s.camUsed = typeof pr.cam_used === "number" ? pr.cam_used : 0
    s.camPresent = pr.cam_present === 1 || pr.cam_present === true
    s.micApps = String(pr.mic_apps || "")
    s.camApps = String(pr.cam_apps || "")
    s.profile = String(j.profile || "")
    var n = j.night
    if (n && typeof n.enabled === "boolean") { s.nightEnabled = n.enabled; s.nightRunning = n.running === true }
    if (s.connType === "wifi" && !s.wifiSsid) s.wifiSsid = s.connName
    return s
}

// A --light probe carries the kernel readings and the active Wi-Fi signal: keep everything the last full probe knew
// and refresh those. An active Wi-Fi line while the last full probe saw no Wi-Fi (or the reverse) is a link change —
// the applet then runs one full probe (linkChanged()).
function mergeLight(prev, light) {
    var s = {}
    for (var k in prev) s[k] = prev[k]
    s.light = true
    s.iface = light.iface; s.rx = light.rx; s.tx = light.tx; s.wifiQuality = light.wifiQuality
    s.hasBattery = light.hasBattery; s.batPct = light.batPct; s.batStatus = light.batStatus; s.batTime = light.batTime
    s.hasBacklight = light.hasBacklight; s.blCur = light.blCur; s.blMax = light.blMax
    if (light.wifiActive === true && light.wifiSignal >= 0) { s.wifiSignal = light.wifiSignal; s.wifiActive = true }
    else if (light.wifiActive === false) s.wifiActive = false
    else if (s.connType === "wifi" && light.wifiQuality >= 0 && light.wifiActive === null) s.wifiSignal = light.wifiQuality   // older script: kernel link quality only
    return s
}

// did a light probe see the link change since the last state? (interface, or Wi-Fi association up/down)
function linkChanged(prev, light) {
    if (light.iface !== prev.iface) return true
    if (light.wifiActive === null) return (light.wifiQuality >= 0) !== (prev.wifiQuality >= 0)
    return light.wifiActive !== (prev.connType === "wifi")
}

// the counters of a parsed probe, in the shape rates() takes
function counters(s) { return { iface: s.iface || "", rx: s.rx || 0, tx: s.tx || 0 } }

// ---- key=value lines (first-release status.sh; the QML harness still feeds this form)
function parseKeyValues(text) {
    var s = empty()
    var lines = String(text || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i]
        var eq = line.indexOf("=")
        if (eq < 1) continue
        var k = line.slice(0, eq), v = line.slice(eq + 1).trim()
        switch (k) {
        case "wifi_radio": s.wifiRadio = v === "enabled" ? true : (v === "disabled" ? false : null); break
        case "conn": {   // type|device|name, first wireless wins, else first wired
            var p = v.split("|")
            var type = p[0] || "", dev = p[1] || "", name = p.slice(2).join("|")
            var isWifi = type.indexOf("wireless") >= 0, isWired = type.indexOf("ethernet") >= 0
            if (isWifi && s.connType !== "wifi") { s.connType = "wifi"; s.connDev = dev; s.connName = name }
            else if (isWired && !s.connType) { s.connType = "wired"; s.connDev = dev; s.connName = name }
            break
        }
        case "wifi": {   // IN-USE:SSID:SIGNAL:SECURITY
            var w = parseWifiLine(v)
            if (w.active) { s.wifiActive = true; s.wifiSsid = w.ssid; s.wifiSignal = w.signal; s.wifiLocked = w.locked }
            break
        }
        case "iface": s.iface = v; break
        case "ip4": s.ip4 = v.replace(/\/\d+$/, ""); break
        case "bt_present": s.btPresent = v === "yes"; break
        case "bt_powered": s.btPowered = v === "yes" ? true : (v === "no" ? false : null); break
        case "bt_connected": s.btConnected = parseInt(v, 10) || 0; break
        case "volume": {   // "Volume: 0.45" or "Volume: 0.45 [MUTED]"
            var m = /Volume:\s*([0-9.]+)/.exec(v)
            if (m) { s.volume = Math.round(parseFloat(m[1]) * 100); s.hasAudio = true; s.muted = v.indexOf("MUTED") >= 0 }
            break
        }
        case "mic": {      // the default source, same form
            var mm = /Volume:\s*([0-9.]+)/.exec(v)
            if (mm) { s.micVolume = Math.round(parseFloat(mm[1]) * 100); s.hasMic = true; s.micMuted = v.indexOf("MUTED") >= 0 }
            break
        }
        case "bat_pct": s.batPct = parseInt(v, 10); s.hasBattery = !isNaN(s.batPct); if (!s.hasBattery) s.batPct = -1; break
        case "bat_status": s.batStatus = v; break
        case "bat_time": s.batTime = v; break
        case "profile": s.profile = v; break
        case "bl_cur": s.blCur = parseInt(v, 10) || 0; break
        case "bl_max": s.blMax = parseInt(v, 10) || 0; s.hasBacklight = s.blMax > 0; break
        }
    }
    if (s.connType === "wifi" && !s.wifiSsid) s.wifiSsid = s.connName
    return s
}

// ---------------------------------------------------------------- /proc/net/route + /proc/net/dev -> counters
// text = contents of /proc/net/route, a line "---", then /proc/net/dev. Returns { iface, rx, tx } for the default
// route's interface (bytes), or iface "" when there is no default route.
function parseNet(text) {
    var parts = String(text || "").split("\n---\n")
    var route = (parts[0] || "").split("\n"), dev = (parts[1] || "").split("\n")
    var iface = ""
    for (var i = 1; i < route.length; i++) {
        var f = route[i].trim().split(/\s+/)
        if (f.length > 2 && f[1] === "00000000") { iface = f[0]; break }
    }
    var rx = 0, tx = 0
    if (iface) {
        for (var j = 2; j < dev.length; j++) {
            var l = dev[j].trim()
            var c = l.indexOf(":")
            if (c < 0 || l.slice(0, c).trim() !== iface) continue
            var cols = l.slice(c + 1).trim().split(/\s+/)
            rx = parseInt(cols[0], 10) || 0; tx = parseInt(cols[8], 10) || 0
            break
        }
    }
    return { iface: iface, rx: rx, tx: tx }
}

// Two samples -> bytes per second (0 when the interface changed or time went backwards).
function rates(prev, cur, dtMs) {
    if (!prev || !cur || !cur.iface || prev.iface !== cur.iface || dtMs <= 0) return { down: 0, up: 0 }
    var down = Math.max(0, (cur.rx - prev.rx) * 1000 / dtMs), up = Math.max(0, (cur.tx - prev.tx) * 1000 / dtMs)
    return { down: down, up: up }
}

// kB/s is the smallest unit shown: the rate is always on the bar while a link is up, so an idle link reads "0 kB/s"
// (never "0 B/s" jitter), a trickle "0.5 kB/s", then 2.5 kB/s · 80 kB/s · 1.2 MB/s · 2.50 GB/s.
function fmtRate(bps) {
    if (bps < 1000) return (bps < 50 ? "0" : (bps / 1000).toFixed(1)) + " kB/s"
    if (bps < 1000 * 1000) return (bps / 1000).toFixed(bps < 10000 ? 1 : 0) + " kB/s"
    if (bps < 1000 * 1000 * 1000) return (bps / 1e6).toFixed(1) + " MB/s"
    return (bps / 1e9).toFixed(2) + " GB/s"
}

function speedText(down, up) { return "↓ " + fmtRate(down) + "  ↑ " + fmtRate(up) }

// ---------------------------------------------------------------- glyph names (FabOS mono icons, brand/gen/make_assets.py MONO_MAP)
// Signal (nmcli SIGNAL, 0-100) -> five glyphs: excellent >= 80, good >= 55, ok >= 30, weak >= 5, none below.
function wifiLevel(signal) {
    return signal >= 80 ? "excellent" : signal >= 55 ? "good" : signal >= 30 ? "ok" : signal >= 5 ? "weak" : "none"
}
function wifiIcon(s) {
    if (s.wifiRadio === false) return "network-wireless-off"
    if (s.connType === "wired") return "network-wired"
    if (s.connType !== "wifi") return "network-wireless-disconnected"
    var lvl = s.wifiSignal
    if (lvl < 0) return "network-wireless-connected"          // associated, strength not read yet
    var name = "network-wireless-signal-" + wifiLevel(lvl)
    return s.wifiLocked ? name + "-locked" : name
}

function batteryIcon(pct, status) {
    if (pct < 0) return "battery-missing"
    var lvl = Math.max(0, Math.min(100, Math.round(pct / 10) * 10))
    var n = (lvl < 10 ? "00" : (lvl < 100 ? "0" : "")) + lvl
    var charging = status === "Charging" || status === "Full"
    return "battery-" + n + (charging ? "-charging" : "")
}

function volumeIcon(vol, muted) {
    if (muted || vol === 0) return "audio-volume-muted"
    if (vol < 34) return "audio-volume-low"
    if (vol < 67) return "audio-volume-medium"
    return "audio-volume-high"
}

// microphone glyphs (status/ names: microphone-sensitivity-*): muted, low, medium, high
function micIcon(vol, muted) {
    if (muted || vol === 0) return "microphone-sensitivity-muted"
    if (vol < 34) return "microphone-sensitivity-low"
    if (vol < 67) return "microphone-sensitivity-medium"
    return "microphone-sensitivity-high"
}

// Privacy line of the microphone row / bar tooltip: what is recording or filming right now.
// "1 app is using the microphone (Firefox)" — the names come from PipeWire (application.name; Fab Voice's wake-word
// listener records through pw-record and shows as "pw-record"), so the user can tell the always-on listener from a call.
function privacyLine(s) {
    var parts = []
    if (s.micUsed > 0) parts.push((s.micUsed === 1 ? "1 app is using the microphone" : s.micUsed + " apps are using the microphone") + (s.micApps ? " (" + s.micApps + ")" : ""))
    if (s.camUsed > 0) parts.push((s.camUsed === 1 ? "1 app is using the camera" : s.camUsed + " apps are using the camera") + (s.camApps ? " (" + s.camApps + ")" : ""))
    return parts.join(" · ")
}
function micLine(s) {
    if (!s.hasMic) return "No microphone"
    var t = s.micMuted ? "Muted" : s.micVolume + "%"
    if (s.micUsed > 0) t += " · in use"
    return t
}

// status/ names only: "preferences-system-bluetooth*" also exists as a colourful app tile in the FabOS theme and the
// icon loader would pick that over the monochrome glyph.
function bluetoothIcon(s) {
    if (!s.btPresent || s.btPowered === false) return "bluetooth-disabled"
    return s.btConnected > 0 ? "network-bluetooth-activated" : "bluetooth"
}

// ---------------------------------------------------------------- text
function batteryLine(s) {
    if (!s.hasBattery) return "No battery"
    var t = s.batPct + "%"
    if (s.batStatus === "Charging") t += " · charging"
    else if (s.batStatus === "Full") t += " · full"
    if (s.batTime) t += " · " + s.batTime + (s.batStatus === "Charging" ? " to full" : " left")
    return t
}

function wifiLine(s) {
    if (s.wifiRadio === false) return "Wi-Fi off"
    if (s.connType === "wifi") return s.wifiSsid + (s.wifiSignal >= 0 ? " · " + s.wifiSignal + "%" : "")
    if (s.connType === "wired") return "Wired · " + (s.connName || s.connDev)
    return s.wifiRadio === null ? "Network unavailable" : "Not connected"
}

function bluetoothLine(s) {
    if (!s.btPresent) return "No adapter"
    if (s.btPowered === false) return "Off"
    return s.btConnected > 0 ? s.btConnected + (s.btConnected === 1 ? " device connected" : " devices connected") : "On"
}

// one-column tile: "On" / "Off" / "1 connected"
function bluetoothShort(s) {
    if (!s.btPresent) return "No adapter"
    if (s.btPowered === false) return "Off"
    return s.btConnected > 0 ? s.btConnected + " connected" : "On"
}

function profileLabel(p) { return p === "power-saver" ? "Power saver" : p === "performance" ? "Performance" : p === "balanced" ? "Balanced" : "" }
function profileIcon(p) { return p === "power-saver" ? "battery-profile-powersave" : p === "performance" ? "battery-profile-performance" : "battery-profile-balanced" }
function nextProfile(p) { var order = ["power-saver", "balanced", "performance"]; var i = order.indexOf(p); return order[(i + 1) % order.length] }

function nightLine(s, inhibited) {
    if (s.nightEnabled === false) return "Off"
    if (s.nightEnabled === null) return "Unavailable"
    if (inhibited) return "Suspended"
    return s.nightRunning ? "On" : "Scheduled"
}

// ---------------------------------------------------------------- the pane's tiles (Plasmoid.configuration.tilesJson)
// Every tile the pane can show, in the default order, with its default span in the THREE-column grid (small = one
// column, medium = two, wide = the full row), its height and whether it is on the pane by default (the network row lives
// in the pane's footer and the power profile in the battery card, so those two tiles and Notifications are optional
// extras: "+ tile" chips in edit mode / the Tiles page). Persisted as a JSON array of {id, size, enabled}; unknown ids
// are dropped, tiles added in a later version are appended with their defaults, so an old saved layout never loses a
// new tile (the Microphone row, added in 1.0-8, lands at the end of a layout saved by 1.0-7 and in its default slot
// under Volume on a fresh one).
var COLUMNS = 3
var SIZES = ["small", "medium", "wide"]
var TILES = [
    { id: "wifi",          title: "Wi-Fi",          size: "medium", height: 76, enabled: true },
    { id: "bluetooth",     title: "Bluetooth",      size: "small",  height: 76, enabled: true },
    { id: "volume",        title: "Volume",         size: "wide",   height: 64, enabled: true },
    { id: "mic",           title: "Microphone",     size: "wide",   height: 64, enabled: true },
    { id: "brightness",    title: "Brightness",     size: "wide",   height: 64, enabled: true },
    { id: "battery",       title: "Battery",        size: "medium", height: 96, enabled: true },
    { id: "dnd",           title: "Do Not Disturb", size: "small",  height: 76, enabled: true },
    { id: "nightlight",    title: "Night light",    size: "small",  height: 76, enabled: true },
    { id: "screenshot",    title: "Screenshot",     size: "small",  height: 76, enabled: true },
    { id: "settings",      title: "Settings",       size: "small",  height: 76, enabled: true },
    { id: "notifications", title: "Notifications",  size: "small",  height: 76, enabled: false },
    { id: "powerprofile",  title: "Power profile",  size: "small",  height: 76, enabled: false },
    { id: "netspeed",      title: "Network speed",  size: "wide",   height: 56, enabled: false }
]
function tileDef(id) { for (var i = 0; i < TILES.length; i++) if (TILES[i].id === id) return TILES[i]; return null }
function spanOf(size) { return size === "wide" ? COLUMNS : (size === "medium" ? 2 : 1) }
function sizeLabel(size) { return size === "wide" ? "Wide" : (size === "medium" ? "Medium" : "Small") }
function defaultTiles() { return TILES.map(function (t) { return { id: t.id, size: t.size, enabled: t.enabled !== false } }) }
function parseTiles(json) {
    var arr = null
    try { arr = JSON.parse(String(json || "")) } catch (e) { arr = null }
    if (!Array.isArray(arr)) return defaultTiles()
    var out = [], seen = {}
    for (var i = 0; i < arr.length; i++) {
        var t = arr[i]; if (!t || typeof t.id !== "string") continue
        var def = tileDef(t.id); if (!def || seen[t.id]) continue
        seen[t.id] = true
        out.push({ id: t.id, size: SIZES.indexOf(t.size) >= 0 ? t.size : def.size, enabled: typeof t.enabled === "boolean" ? t.enabled : def.enabled !== false })
    }
    for (var k = 0; k < TILES.length; k++) if (!seen[TILES[k].id]) out.push({ id: TILES[k].id, size: TILES[k].size, enabled: TILES[k].enabled !== false })
    return out
}
function tilesJson(tiles) { return JSON.stringify(tiles.map(function (t) { return { id: t.id, size: t.size, enabled: t.enabled !== false } })) }
function moveTile(tiles, from, to) {
    var out = tiles.slice()
    if (from < 0 || from >= out.length || to < 0 || to >= out.length || from === to) return out
    var t = out.splice(from, 1)[0]; out.splice(to, 0, t)
    return out
}
function setTileSize(tiles, id, size) { return tiles.map(function (t) { return t.id === id ? { id: t.id, size: SIZES.indexOf(size) >= 0 ? size : t.size, enabled: t.enabled } : t }) }
// the size toggle cycles small -> medium -> wide -> small
function nextSize(size) { return SIZES[(SIZES.indexOf(size) + 1) % SIZES.length] }
function toggleTileSize(tiles, id) { var t = null; tiles.forEach(function (x) { if (x.id === id) t = x }); return t ? setTileSize(tiles, id, nextSize(t.size)) : tiles }
function setTileEnabled(tiles, id, on) { return tiles.map(function (t) { return t.id === id ? { id: t.id, size: t.size, enabled: !!on } : t }) }

// Grid geometry for the enabled tiles: THREE columns of equal integer width (the last column takes the rounding
// remainder so every row's right edge is the content's right edge); a tile spans 1, 2 or 3 columns and goes into the
// current row when it fits, else starts the next one; every tile in a row is as tall as the row (the tallest tile in
// it), so edges align. `n` is the tile's position among the placed ones (the open animation staggers by it).
// Returns { items: [{id, x, y, w, h, span, n}], height }.
function layoutTiles(tiles, width, gap) {
    gap = gap === undefined ? 12 : gap
    var col = Math.floor((width - (COLUMNS - 1) * gap) / COLUMNS), items = [], row = [], y = 0, used = 0, rowH = 0
    function endRow() {
        if (row.length === 0) return
        for (var k = 0; k < row.length; k++) row[k].h = rowH
        y += rowH + gap; row = []; used = 0; rowH = 0
    }
    for (var i = 0; i < tiles.length; i++) {
        var t = tiles[i]; if (t.enabled === false) continue
        var def = tileDef(t.id) || { height: 76 }
        var span = Math.min(COLUMNS, spanOf(t.size))
        if (used + span > COLUMNS) endRow()
        var x = used * (col + gap)
        var w = used + span === COLUMNS ? width - x : span * col + (span - 1) * gap
        var it = { id: t.id, x: x, y: y, w: w, h: def.height, span: span, n: items.length }
        items.push(it); row.push(it)
        rowH = Math.max(rowH, def.height); used += span
        if (used === COLUMNS) endRow()
    }
    endRow()
    return { items: items, height: Math.max(0, y - gap) }
}

// Drag-to-reorder in the pane's edit mode: the tile whose rect (from layoutTiles) contains the point, else the nearest
// by centre distance within one tile height; "" when the point is far from every tile.
function tileAt(items, px, py) {
    var best = "", bestD = Infinity
    for (var i = 0; i < items.length; i++) {
        var r = items[i]
        if (px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h) return r.id
        var dx = px - (r.x + r.w / 2), dy = py - (r.y + r.h / 2), d = Math.sqrt(dx * dx + dy * dy)
        if (d < bestD && d <= r.h) { bestD = d; best = r.id }
    }
    return best
}
// move tile `id` to the position tile `beforeId` holds (the dragged tile takes the hovered tile's slot; the rest shift)
function moveTileTo(tiles, id, beforeId) {
    var from = -1, to = -1
    for (var i = 0; i < tiles.length; i++) { if (tiles[i].id === id) from = i; if (tiles[i].id === beforeId) to = i }
    return from < 0 || to < 0 ? tiles : moveTile(tiles, from, to)
}
// the rect of one tile in a layoutTiles() result, or null
function rectFor(layout, id) { for (var i = 0; i < layout.items.length; i++) if (layout.items[i].id === id) return layout.items[i]; return null }

// KWin's night light is switched in kwinrc (NightColor/Active) followed by a reconfigure — the D-Bus inhibit() call is
// tied to the caller's bus connection and would end with the one-shot process. "" when KWin does not offer it.
function nightCommand(s) {
    if (s.nightEnabled === null || s.nightEnabled === undefined) return ""
    return "kwriteconfig6 --file kwinrc --group NightColor --key Active " + (s.nightEnabled ? "false" : "true") + " && qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure"
}

// The plasmashell scripting call that pushes one bar size to the Fab OS clock applet and the shared "magnify on
// hover" switch to the dock (Plasmoid.configuration of another applet is not writable from QML; the shell's D-Bus
// scripting API is). The dock's magnification strength is the dock's own setting and is not touched here.
function clockSizeFor(barSize) { return barSize === "small" ? 12 : (barSize === "large" ? 15 : 13) }
function syncScript(barSize, magnify) {
    var size = barSize === "small" || barSize === "large" ? barSize : "medium"
    return "var ps = panels(); for (var i = 0; i < ps.length; i++) {"
         + " var cs = ps[i].widgets(\"in.patienceai.fabos.clock\"); for (var j = 0; j < cs.length; j++) { cs[j].currentConfigGroup = [\"General\"]; cs[j].writeConfig(\"barSize\", \"" + size + "\") }"
         + " var ds = ps[i].widgets(\"in.patienceai.fabos.dock\"); for (var k = 0; k < ds.length; k++) { ds[k].currentConfigGroup = [\"General\"]; ds[k].writeConfig(\"magnify\", " + (magnify ? "true" : "false") + ") } }"
}
function syncCommand(barSize, magnify) {
    // single-quoted for sh; the script contains no single quotes
    return "qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '" + syncScript(barSize, magnify) + "'"
}

// Shell-safe launcher: detach so the executable engine returns at once.
function detach(cmd) { return "nohup " + cmd + " >/dev/null 2>&1 &" }
