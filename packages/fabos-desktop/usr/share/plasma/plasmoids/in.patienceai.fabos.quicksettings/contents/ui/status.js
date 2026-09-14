// Pure helpers for the Fab OS quick-settings applet: parse the output of contents/code/status.sh and of
// /proc/net/{route,dev}, choose FabOS monochrome glyph names, format rates and times. No Qt, no I/O — the same file
// runs under node in tests/quicksettings-js-test.js.
.pragma library

// ---------------------------------------------------------------- status.sh -> object
function empty() {
    return { wifiRadio: null, wifiSsid: "", wifiSignal: -1, wifiLocked: false, connType: "", connName: "", connDev: "",
             iface: "", ip4: "", btPresent: false, btPowered: null, btConnected: 0,
             volume: -1, muted: false, hasAudio: false,
             batPct: -1, batStatus: "", batTime: "", hasBattery: false, profile: "",
             blCur: -1, blMax: 0, hasBacklight: false }
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

function parseStatus(text) {
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
            var f = splitTerse(v)
            if (f.length >= 3) { s.wifiSsid = f[1]; s.wifiSignal = parseInt(f[2], 10); if (isNaN(s.wifiSignal)) s.wifiSignal = -1; s.wifiLocked = (f[3] || "").trim() !== "" && (f[3] || "").trim() !== "--" }
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

function fmtRate(bps) {
    if (bps < 1000) return Math.round(bps) + " B/s"
    if (bps < 1000 * 1000) return (bps / 1000).toFixed(bps < 10000 ? 1 : 0) + " kB/s"
    if (bps < 1000 * 1000 * 1000) return (bps / 1e6).toFixed(1) + " MB/s"
    return (bps / 1e9).toFixed(2) + " GB/s"
}

function speedText(down, up) { return "↓ " + fmtRate(down) + "  ↑ " + fmtRate(up) }

// ---------------------------------------------------------------- glyph names (FabOS mono icons, brand/gen/make_assets.py MONO_MAP)
function wifiIcon(s) {
    if (s.wifiRadio === false) return "network-wireless-off"
    if (s.connType === "wired") return "network-wired"
    if (s.connType !== "wifi") return "network-wireless-disconnected"
    var lvl = s.wifiSignal
    var name = lvl >= 80 ? "network-wireless-signal-excellent" : lvl >= 55 ? "network-wireless-signal-good" : lvl >= 30 ? "network-wireless-signal-ok"
             : lvl >= 5 ? "network-wireless-signal-weak" : (lvl < 0 ? "network-wireless-signal-excellent" : "network-wireless-signal-none")
    return s.wifiLocked && lvl >= 0 ? name + "-locked" : name
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

// The plasmashell scripting call that re-applies one bar size to the stock clock and pushes the shared "magnify on
// hover" switch to the dock (Plasmoid.configuration of another applet is not writable from QML; the shell's D-Bus
// scripting API is). The dock's magnification strength is the dock's own setting and is not touched here.
function clockSizeFor(barSize) { return barSize === "small" ? 12 : (barSize === "large" ? 15 : 13) }
function syncScript(barSize, magnify) {
    var px = clockSizeFor(barSize)
    return "var ps = panels(); for (var i = 0; i < ps.length; i++) {"
         + " var cs = ps[i].widgets(\"org.kde.plasma.digitalclock\"); for (var j = 0; j < cs.length; j++) { cs[j].currentConfigGroup = [\"Appearance\"]; cs[j].writeConfig(\"fontSize\", " + px + ") }"
         + " var ds = ps[i].widgets(\"in.patienceai.fabos.dock\"); for (var k = 0; k < ds.length; k++) { ds[k].currentConfigGroup = [\"General\"]; ds[k].writeConfig(\"magnify\", " + (magnify ? "true" : "false") + ") } }"
}
function syncCommand(barSize, magnify) {
    // single-quoted for sh; the script contains no single quotes
    return "qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '" + syncScript(barSize, magnify) + "'"
}

// Shell-safe launcher: detach so the executable engine returns at once.
function detach(cmd) { return "nohup " + cmd + " >/dev/null 2>&1 &" }
