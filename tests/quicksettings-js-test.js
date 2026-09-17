#!/usr/bin/env node
// Unit tests for the quick-settings applet's pure helpers (packages/fabos-desktop/.../in.patienceai.fabos.quicksettings/contents/ui/status.js):
// status.sh output -> state, recorded nmcli lines -> Wi-Fi glyphs, /proc/net parsing and rates, text, the tile model
// (order / size / enabled / grid geometry) and the shell-scripting sync command.
//   node tests/quicksettings-js-test.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents/ui/status.js"), "utf8");
const S = {};
vm.runInNewContext(src.replace(/^\.pragma library\s*$/m, ""), S);
// objects born in the vm context have another Object prototype: compare structurally through JSON
const _dse = assert.deepStrictEqual; assert.deepStrictEqual = (a, b, m) => _dse(JSON.parse(JSON.stringify(a)), JSON.parse(JSON.stringify(b)), m);
let n = 0;
function t(name, fn) { fn(); n++; }

const STATUS = [
  "wifi_radio=enabled",
  "conn=802-11-wireless|wlp2s0|Home\\:Net",
  "conn=loopback|lo|lo",
  "wifi=*:Home\\:Net:78:WPA2",
  "iface=wlp2s0",
  "ip4=10.0.0.5/24",
  "bt_present=yes", "bt_powered=yes", "bt_connected=1",
  "volume=Volume: 0.45",
  "bat_pct=87", "bat_status=Discharging", "bat_time=3.2 hours",
  "profile=balanced",
  "bl_cur=45528", "bl_max=64764", ""].join("\n");

t("parseStatus reads every field (key=value form)", () => {
  const s = S.parseStatus(STATUS);
  assert.strictEqual(s.wifiRadio, true);
  assert.strictEqual(s.connType, "wifi"); assert.strictEqual(s.connDev, "wlp2s0");
  assert.strictEqual(s.wifiSsid, "Home:Net"); assert.strictEqual(s.wifiSignal, 78); assert.strictEqual(s.wifiLocked, true);
  assert.strictEqual(s.ip4, "10.0.0.5"); assert.strictEqual(s.iface, "wlp2s0");
  assert.strictEqual(s.btPresent, true); assert.strictEqual(s.btPowered, true); assert.strictEqual(s.btConnected, 1);
  assert.strictEqual(s.volume, 45); assert.strictEqual(s.muted, false); assert.strictEqual(s.hasAudio, true);
  assert.strictEqual(s.batPct, 87); assert.strictEqual(s.hasBattery, true); assert.strictEqual(s.batStatus, "Discharging"); assert.strictEqual(s.batTime, "3.2 hours");
  assert.strictEqual(s.profile, "balanced");
  assert.strictEqual(s.blCur, 45528); assert.strictEqual(s.blMax, 64764); assert.strictEqual(s.hasBacklight, true);
});
t("parseStatus: daemons down -> unknowns, no throw", () => {
  const s = S.parseStatus("wifi_radio=\niface=\nbt_present=no\nbt_powered=\nbt_connected=0\nvolume=\nprofile=\n");
  assert.strictEqual(s.wifiRadio, null); assert.strictEqual(s.connType, ""); assert.strictEqual(s.hasAudio, false);
  assert.strictEqual(s.hasBattery, false); assert.strictEqual(s.btPresent, false); assert.strictEqual(s.hasBacklight, false);
  assert.strictEqual(S.wifiLine(s), "Network unavailable"); assert.strictEqual(S.batteryLine(s), "No battery"); assert.strictEqual(S.bluetoothLine(s), "No adapter");
});
t("muted volume, wired connection, charging", () => {
  const s = S.parseStatus("wifi_radio=enabled\nconn=802-3-ethernet|enp3s0|Wired connection 1\nvolume=Volume: 1.00 [MUTED]\nbat_pct=100\nbat_status=Full\n");
  assert.strictEqual(s.volume, 100); assert.strictEqual(s.muted, true);
  assert.strictEqual(s.connType, "wired"); assert.strictEqual(S.wifiIcon(s), "network-wired"); assert.strictEqual(S.wifiLine(s), "Wired · Wired connection 1");
  assert.strictEqual(S.batteryIcon(100, "Full"), "battery-100-charging"); assert.strictEqual(S.batteryLine(s), "100% · full");
});
// ---- Wi-Fi accuracy: recorded nmcli output (Fedora 44 host, 2026-09-15; the image ships nmcli 1.54.3 with the same -t format)
const NMCLI_WIFI = "yes:70:Ramji:WPA2 WPA3\nno:82:RH-2.4G-C526A0:WPA2\nno:79:Sourabh09:WPA2\nno:54:RH-2.4G-C526A0:WPA2\n";   // nmcli -t -f ACTIVE,SIGNAL,SSID,SECURITY dev wifi list --rescan no
const NMCLI_LIGHT = "no:82\nyes:70\nno:79\n";                                                                             // nmcli -t -f ACTIVE,SIGNAL dev wifi list --rescan no
const NMCLI_DEVS = ["wlp2s0:wifi:connected:Ramji", "tailscale0:tun:connected (externally):tailscale0", "lo:loopback:connected (externally):lo"];   // nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status
t("recorded nmcli: the active line is found in any position; signal, SSID and lock parsed; the light form keeps the SSID empty", () => {
  assert.strictEqual(S.activeWifiLine(NMCLI_WIFI), "yes:70:Ramji:WPA2 WPA3");
  const w = S.parseWifiLine(S.activeWifiLine(NMCLI_WIFI));
  assert.deepStrictEqual(w, { active: true, signal: 70, ssid: "Ramji", locked: true });
  assert.deepStrictEqual(S.parseWifiLine(S.activeWifiLine(NMCLI_LIGHT)), { active: true, signal: 70, ssid: "", locked: false });
  assert.deepStrictEqual(S.parseWifiLine("no:82:Other:WPA2"), { active: false, signal: -1, ssid: "", locked: false });
  assert.strictEqual(S.activeWifiLine("no:82\nno:79\n"), "", "no active network -> empty");
  assert.deepStrictEqual(S.parseWifiLine("*:Home\\:Net:78:WPA2"), { active: true, signal: 78, ssid: "Home:Net", locked: true }, "first-release IN-USE form still understood");
  assert.deepStrictEqual(S.parseWifiLine("yes:100:Open\\:Cafe:"), { active: true, signal: 100, ssid: "Open:Cafe", locked: false }, "open network: empty SECURITY = no lock");
  assert.deepStrictEqual(S.parseWifiLine("yes:100:Open\\:Cafe:--"), { active: true, signal: 100, ssid: "Open:Cafe", locked: false });
});
t("signal 0-100 -> the five glyphs at 80 / 55 / 30 / 5", () => {
  const exp = { 100: "excellent", 80: "excellent", 79: "good", 55: "good", 54: "ok", 30: "ok", 29: "weak", 5: "weak", 4: "none", 0: "none" };
  for (const k of Object.keys(exp)) assert.strictEqual(S.wifiLevel(Number(k)), exp[k], "signal " + k);
  const s = S.parseStatus(STATUS);
  s.wifiLocked = false;
  for (const k of Object.keys(exp)) { s.wifiSignal = Number(k); assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-" + exp[k]); }
  s.wifiLocked = true; s.wifiSignal = 70; assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-good-locked");
  s.wifiSignal = -1; assert.strictEqual(S.wifiIcon(s), "network-wireless-connected", "associated, strength unknown: neutral glyph, never 'excellent'");
  s.wifiRadio = false; assert.strictEqual(S.wifiIcon(s), "network-wireless-off"); assert.strictEqual(S.wifiLine(s), "Wi-Fi off");
  s.wifiRadio = true; s.connType = ""; assert.strictEqual(S.wifiIcon(s), "network-wireless-disconnected"); assert.strictEqual(S.wifiLine(s), "Not connected");
});
t("recorded nmcli dev status: wifi wins over tun/loopback; ethernet-only -> wired; radio off -> every wifi device unavailable", () => {
  const s = S.applyDevs(S.empty(), NMCLI_DEVS);
  assert.strictEqual(s.connType, "wifi"); assert.strictEqual(s.connDev, "wlp2s0"); assert.strictEqual(s.connName, "Ramji"); assert.strictEqual(s.wifiRadio, true);
  const wired = S.applyDevs(S.empty(), ["enp3s0:ethernet:connected:Wired connection 1", "wlp2s0:wifi:disconnected:", "lo:loopback:connected (externally):lo"]);
  assert.strictEqual(wired.connType, "wired"); assert.strictEqual(wired.connName, "Wired connection 1"); assert.strictEqual(wired.wifiRadio, true);
  assert.strictEqual(S.wifiIcon(wired), "network-wired");
  const off = S.applyDevs(S.empty(), ["enp3s0:ethernet:connected:Wired connection 1", "wlp2s0:wifi:unavailable:"]);
  assert.strictEqual(off.wifiRadio, false); assert.strictEqual(S.wifiIcon(off), "network-wireless-off");
  const three = S.applyDevs(S.empty(), ["wifi:connected:Ramji", "tun:connected (externally):tailscale0", "loopback:connected (externally):lo"]);   // TYPE,STATE,CONNECTION form
  assert.strictEqual(three.connType, "wifi"); assert.strictEqual(three.connName, "Ramji"); assert.strictEqual(three.connDev, "");
  const both = S.applyDevs(S.empty(), ["enp3s0:ethernet:connected:Wired connection 1", "wlp2s0:wifi:connected:Ramji"]);
  assert.strictEqual(both.connType, "wifi", "Wi-Fi shown when both are up (the glyph is the Wi-Fi glyph)");
});
t("battery glyph names exist in the FabOS mono set (000..100 step 10, -charging)", () => {
  assert.strictEqual(S.batteryIcon(87, "Discharging"), "battery-090");
  assert.strictEqual(S.batteryIcon(4, "Discharging"), "battery-000");
  assert.strictEqual(S.batteryIcon(55, "Charging"), "battery-060-charging");
  assert.strictEqual(S.batteryIcon(-1, ""), "battery-missing");
});
t("volume + bluetooth glyphs, profile helpers, night light line", () => {
  assert.strictEqual(S.volumeIcon(45, false), "audio-volume-medium");
  assert.strictEqual(S.volumeIcon(80, false), "audio-volume-high");
  assert.strictEqual(S.volumeIcon(10, false), "audio-volume-low");
  assert.strictEqual(S.volumeIcon(80, true), "audio-volume-muted");
  assert.strictEqual(S.bluetoothIcon({ btPresent: true, btPowered: true, btConnected: 2 }), "network-bluetooth-activated");
  assert.strictEqual(S.bluetoothIcon({ btPresent: true, btPowered: true, btConnected: 0 }), "bluetooth");
  assert.strictEqual(S.bluetoothIcon({ btPresent: true, btPowered: false, btConnected: 0 }), "bluetooth-disabled");
  assert.strictEqual(S.bluetoothLine({ btPresent: true, btPowered: true, btConnected: 2 }), "2 devices connected");
  assert.strictEqual(S.nextProfile("balanced"), "performance"); assert.strictEqual(S.nextProfile("performance"), "power-saver"); assert.strictEqual(S.nextProfile(""), "power-saver");
  assert.strictEqual(S.profileLabel("power-saver"), "Power saver"); assert.strictEqual(S.profileIcon("performance"), "battery-profile-performance");
  assert.strictEqual(S.nightLine({ nightEnabled: true, nightRunning: true }, false), "On");
  assert.strictEqual(S.nightLine({ nightEnabled: true, nightRunning: false }, true), "Suspended");
  assert.strictEqual(S.nightLine({ nightEnabled: false }, false), "Off");
  assert.strictEqual(S.nightLine({ nightEnabled: null }, false), "Unavailable");
});
const ROUTE = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRRT\nwlp2s0\t00000000\t3B03EC0A\t0003\t0\t0\t600\t00000000\t0\t0\t0\nwlp2s0\t0003EC0A\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n";
const DEV = (rx, tx) => "Inter-|   Receive                                                |  Transmit\n face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n    lo:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0\nwlp2s0: " + rx + "       4    0    0    0     0          0         0      " + tx + "       9    0    0    0     0       0          0\n";
t("parseNet finds the default-route interface and its byte counters", () => {
  const c = S.parseNet(ROUTE + "---\n" + DEV(284, 746));
  assert.strictEqual(c.iface, "wlp2s0"); assert.strictEqual(c.rx, 284); assert.strictEqual(c.tx, 746);
  const none = S.parseNet("Iface\tDestination\n---\n" + DEV(1, 1));
  assert.strictEqual(none.iface, ""); assert.strictEqual(none.rx, 0);
});
t("rates: bytes/s from two samples; interface change -> 0", () => {
  const a = S.parseNet(ROUTE + "---\n" + DEV(1000, 500)), b = S.parseNet(ROUTE + "---\n" + DEV(1000 + 2400000, 500 + 160000));
  const r = S.rates(a, b, 2000);
  assert.strictEqual(Math.round(r.down), 1200000); assert.strictEqual(Math.round(r.up), 80000);
  assert.strictEqual(S.speedText(r.down, r.up), "↓ 1.2 MB/s  ↑ 80 kB/s");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(S.rates({ iface: "eth0", rx: 0, tx: 0 }, b, 2000))), { down: 0, up: 0 });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(S.rates(b, a, 2000))), { down: 0, up: 0 });   // counters went backwards
});
t("fmtRate: kB/s is the floor unit (an idle link reads 0 kB/s, never B/s); speed text at zero is well-formed", () => {
  assert.strictEqual(S.fmtRate(0), "0 kB/s"); assert.strictEqual(S.fmtRate(30), "0 kB/s"); assert.strictEqual(S.fmtRate(500), "0.5 kB/s"); assert.strictEqual(S.fmtRate(999), "1.0 kB/s");
  assert.strictEqual(S.fmtRate(2500), "2.5 kB/s"); assert.strictEqual(S.fmtRate(80000), "80 kB/s"); assert.strictEqual(S.fmtRate(1.2e6), "1.2 MB/s"); assert.strictEqual(S.fmtRate(2.5e9), "2.50 GB/s");
  assert.strictEqual(S.speedText(0, 0), "↓ 0 kB/s  ↑ 0 kB/s");
  assert.ok(!src.includes("zeroSamples"), "status.js has no idle-hide logic (the applet shows the rate whenever a link is up)");
});
t("bar size -> clock px and the shell-scripting sync command (Fab OS clock applet + dock magnify)", () => {
  assert.strictEqual(S.clockSizeFor("small"), 12); assert.strictEqual(S.clockSizeFor("medium"), 13); assert.strictEqual(S.clockSizeFor("large"), 15);
  const cmd = S.syncCommand("large", false);
  assert.ok(cmd.startsWith("qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '"));
  assert.ok(cmd.includes('writeConfig("barSize", "large")')); assert.ok(cmd.includes('writeConfig("magnify", false)'));
  assert.ok(!cmd.includes("magnification"), "the dock's magnification strength is the dock's own setting: never written from the bar");
  assert.ok(cmd.includes('widgets("in.patienceai.fabos.clock")') && cmd.includes('widgets("in.patienceai.fabos.dock")'));
  assert.ok(!cmd.includes("org.kde.plasma.digitalclock"), "the stock clock is gone from the bar");
  assert.strictEqual((cmd.match(/'/g) || []).length, 2, "the script itself contains no single quotes");
  assert.ok(S.syncScript("weird", true).includes('writeConfig("barSize", "medium")'), "an unknown size falls back to medium");
  // the script is valid JS against a fake shell API
  const api = { panels: () => [{ widgets: (type) => [{ writeConfig(k, v) { api.written.push(type + ":" + k + "=" + v) }, currentConfigGroup: [] }] }], written: [] };
  vm.runInNewContext(S.syncScript("small", true), api);
  assert.deepStrictEqual(api.written, ["in.patienceai.fabos.clock:barSize=small", "in.patienceai.fabos.dock:magnify=true"]);
});
t("the dock's write-back script (main.qml syncScript) mirrors the bar's: quicksettings.magnify only", () => {
  // the function lives in the dock's main.qml; lift its body out the same way and run it against the stub shell API
  const dockSrc = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/ui/main.qml"), "utf8");
  const m = dockSrc.match(/function syncScript\(on\) \{([\s\S]*?)\n    \}/); assert.ok(m, "dock main.qml has function syncScript(on)");
  const syncScript = new Function("on", m[1]);
  for (const on of [true, false]) {
    const api = { panels: () => [{ widgets: (type) => [{ writeConfig(k, v) { api.written.push(type + ":" + k + "=" + v) }, currentConfigGroup: [] }] }], written: [] };
    const script = syncScript(on);
    assert.ok(!script.includes("'"), "no single quotes (the command single-quotes it for sh)");
    vm.runInNewContext(script, api);
    assert.deepStrictEqual(api.written, ["in.patienceai.fabos.quicksettings:magnify=" + on]);
  }
  assert.ok(dockSrc.includes("qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '"), "same D-Bus call as the bar");
});
t("detach wraps a launcher so the executable engine returns at once", () => {
  assert.strictEqual(S.detach("systemsettings"), "nohup systemsettings >/dev/null 2>&1 &");
});
// ---- the one-line JSON probe (status.sh since 1.0-3): full and --light, merged the way the applet does it
const FULL = JSON.stringify({ light: false, net: { iface: "wlp2s0", rx: 284, tx: 746 }, wifi_quality: 83,
  battery: { pct: 87, status: "Discharging", time: "3.2 hours" }, backlight: { cur: 45528, max: 64764 },
  devs: ["wlp2s0:wifi:connected:Home\\:Net", "enp3s0:ethernet:unavailable:", "lo:loopback:connected (externally):lo", "p2p-dev-wlp2s0:wifi-p2p:disconnected:"],
  wifi: "yes:78:Home\\:Net:WPA2", ip4: "10.0.0.5", bt: { present: true, powered: true, connected: 1 }, volume: "Volume: 0.45", profile: "balanced", night: { enabled: true, running: false } });
t("JSON probe: every field lands where the key=value form put it", () => {
  const s = S.parseStatus(FULL + "\n");
  assert.strictEqual(s.light, false);
  assert.strictEqual(s.wifiRadio, true); assert.strictEqual(s.connType, "wifi"); assert.strictEqual(s.connDev, "wlp2s0"); assert.strictEqual(s.connName, "Home:Net");
  assert.strictEqual(s.wifiSsid, "Home:Net"); assert.strictEqual(s.wifiSignal, 78, "nmcli SIGNAL wins over the kernel link quality (83)"); assert.strictEqual(s.wifiLocked, true); assert.strictEqual(s.wifiActive, true);
  assert.strictEqual(s.iface, "wlp2s0"); assert.strictEqual(s.rx, 284); assert.strictEqual(s.tx, 746); assert.strictEqual(s.ip4, "10.0.0.5");
  assert.strictEqual(s.btPresent, true); assert.strictEqual(s.btPowered, true); assert.strictEqual(s.btConnected, 1);
  assert.strictEqual(s.volume, 45); assert.strictEqual(s.muted, false); assert.strictEqual(s.hasAudio, true);
  assert.strictEqual(s.batPct, 87); assert.strictEqual(s.batStatus, "Discharging"); assert.strictEqual(s.batTime, "3.2 hours"); assert.strictEqual(s.hasBattery, true);
  assert.strictEqual(s.profile, "balanced"); assert.strictEqual(s.blCur, 45528); assert.strictEqual(s.blMax, 64764); assert.strictEqual(s.hasBacklight, true);
  assert.strictEqual(s.nightEnabled, true); assert.strictEqual(s.nightRunning, false);
  assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-good-locked"); assert.strictEqual(S.wifiLine(s), "Home:Net · 78%");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(S.counters(s))), { iface: "wlp2s0", rx: 284, tx: 746 });
  const old = S.parseStatus(FULL.replace('"yes:78:Home\\\\:Net:WPA2"', '"*:Home\\\\:Net:78:WPA2"'));
  assert.strictEqual(old.wifiSsid, "Home:Net"); assert.strictEqual(old.wifiSignal, 78, "a 1.0-3 status.sh (IN-USE form) still parses");
});
t("JSON probe: radio off = every Wi-Fi device unavailable; wired; no Wi-Fi device = radio unknown; daemons down", () => {
  const off = S.parseStatus(JSON.stringify({ light: false, net: { iface: "enp3s0", rx: 1, tx: 1 }, wifi_quality: null, battery: null, backlight: null,
    devs: ["wlp2s0:wifi:unavailable:", "enp3s0:ethernet:connected:Wired connection 1"], wifi: "", ip4: "", bt: { present: true, powered: false, connected: 0 }, volume: "Volume: 1.00 [MUTED]", profile: "" }));
  assert.strictEqual(off.wifiRadio, false); assert.strictEqual(off.connType, "wired"); assert.strictEqual(S.wifiIcon(off), "network-wireless-off");
  assert.strictEqual(off.muted, true); assert.strictEqual(off.volume, 100); assert.strictEqual(off.hasBattery, false); assert.strictEqual(S.bluetoothIcon(off), "bluetooth-disabled");
  assert.strictEqual(off.nightEnabled, null, "no night key -> unavailable");
  const nowifi = S.parseStatus(JSON.stringify({ light: false, net: { iface: "", rx: 0, tx: 0 }, devs: ["lo:loopback:connected (externally):lo"], bt: { present: false, powered: null, connected: 0 }, volume: "", profile: "" }));
  assert.strictEqual(nowifi.wifiRadio, null); assert.strictEqual(nowifi.connType, ""); assert.strictEqual(nowifi.hasAudio, false); assert.strictEqual(S.wifiLine(nowifi), "Network unavailable");
  const down = S.parseStatus(JSON.stringify({ light: false, net: { iface: "eth0", rx: 5, tx: 5 }, wifi_quality: null, battery: null, backlight: null, devs: [], wifi: "", ip4: "", bt: { present: false, powered: null, connected: 0 }, volume: "", profile: "" }));
  assert.strictEqual(down.wifiRadio, null); assert.strictEqual(down.btPresent, false); assert.strictEqual(S.batteryLine(down), "No battery");
  assert.strictEqual(S.parseStatus("{not json").hasAudio, false, "junk -> empty state, no throw");
});
t("JSON probe: a --light line refreshes the kernel readings AND the active Wi-Fi signal inside the last full state", () => {
  const full = S.parseStatus(FULL);
  const light = S.parseStatus(JSON.stringify({ light: true, net: { iface: "wlp2s0", rx: 284 + 2400000, tx: 746 + 160000 }, wifi_quality: 57, wifi: "yes:61",
    battery: { pct: 86, status: "Discharging", time: "3.1 hours" }, backlight: { cur: 40000, max: 64764 } }));
  assert.strictEqual(light.light, true); assert.strictEqual(light.connType, "", "a light line knows nothing about connections");
  assert.strictEqual(light.wifiActive, true); assert.strictEqual(light.wifiSignal, 61);
  assert.strictEqual(S.linkChanged(full, light), false);
  const m = S.mergeLight(full, light);
  assert.strictEqual(m.wifiSsid, "Home:Net"); assert.strictEqual(m.connType, "wifi"); assert.strictEqual(m.btConnected, 1); assert.strictEqual(m.volume, 45); assert.strictEqual(m.profile, "balanced");
  assert.strictEqual(m.batPct, 86); assert.strictEqual(m.batTime, "3.1 hours"); assert.strictEqual(m.blCur, 40000);
  assert.strictEqual(m.wifiSignal, 61, "the signal follows nmcli's ACTIVE,SIGNAL between full probes (not the kernel's 57)");
  assert.strictEqual(S.wifiIcon(m), "network-wireless-signal-good-locked");
  assert.strictEqual(m.rx, 284 + 2400000); assert.strictEqual(m.light, true);
  const r = S.rates(S.counters(full), S.counters(m), 2000);
  assert.strictEqual(S.speedText(r.down, r.up), "↓ 1.2 MB/s  ↑ 80 kB/s");
  // association dropped: the light line has no active network -> link change -> the applet runs a full probe
  const gone = S.parseStatus(JSON.stringify({ light: true, net: { iface: "wlp2s0", rx: 1, tx: 1 }, wifi_quality: null, wifi: "", battery: null, backlight: null }));
  assert.strictEqual(gone.wifiActive, false); assert.strictEqual(S.linkChanged(full, gone), true);
  assert.strictEqual(S.mergeLight(full, gone).wifiActive, false);
  // a wired machine: the active-line check does not flip anything
  const wiredFull = S.parseStatus(JSON.stringify({ light: false, net: { iface: "enp3s0", rx: 0, tx: 0 }, devs: ["enp3s0:ethernet:connected:Wired"], wifi: "", bt: {}, volume: "", profile: "" }));
  assert.strictEqual(S.linkChanged(wiredFull, gone.iface === "enp3s0" ? gone : S.parseStatus(JSON.stringify({ light: true, net: { iface: "enp3s0", rx: 9, tx: 9 }, wifi: "" }))), false);
  assert.strictEqual(S.mergeLight(wiredFull, light).connType, "wired", "a stray active Wi-Fi reading never rewrites the connection type; the link change runs the full probe instead");
  // the 1.0-3 script (no wifi key in the light line): kernel link quality still applies while on Wi-Fi
  const oldLight = S.parseStatus(JSON.stringify({ light: true, net: { iface: "wlp2s0", rx: 1, tx: 1 }, wifi_quality: 57, battery: null, backlight: null }));
  assert.strictEqual(oldLight.wifiActive, null); assert.strictEqual(S.mergeLight(full, oldLight).wifiSignal, 57); assert.strictEqual(S.linkChanged(full, oldLight), false);
});
// ---- the tile model
t("tiles v3: defaults (three-column spans, three optional extras off), round trip, unknown ids dropped, new ids appended, sizes validated", () => {
  const d = S.defaultTiles();
  assert.strictEqual(d.length, 13); assert.deepStrictEqual(d.map(x => x.id), ["wifi", "bluetooth", "volume", "mic", "brightness", "battery", "dnd", "nightlight", "screenshot", "settings", "notifications", "powerprofile", "netspeed"]);
  assert.deepStrictEqual(d.filter(x => !x.enabled).map(x => x.id), ["notifications", "powerprofile", "netspeed"], "the footer carries the network line, the battery card the power profile");
  assert.strictEqual(d[0].size, "medium"); assert.strictEqual(d[1].size, "small"); assert.strictEqual(d[2].size, "wide"); assert.strictEqual(d[3].size, "wide", "the microphone row is a full-width slider like the volume row"); assert.strictEqual(d[5].size, "medium");
  assert.strictEqual(S.spanOf("small"), 1); assert.strictEqual(S.spanOf("medium"), 2); assert.strictEqual(S.spanOf("wide"), 3); assert.strictEqual(S.COLUMNS, 3);
  assert.deepStrictEqual(S.parseTiles(S.tilesJson(d)), d, "round trip");
  assert.deepStrictEqual(S.parseTiles(""), d); assert.deepStrictEqual(S.parseTiles("{bad"), d); assert.deepStrictEqual(S.parseTiles("[]"), d);
  const p = S.parseTiles(JSON.stringify([{ id: "dnd", size: "wide", enabled: false }, { id: "bogus" }, { id: "wifi", size: "huge" }, { id: "wifi" }, { id: "netspeed", enabled: true }]));
  assert.strictEqual(p[0].id, "dnd"); assert.strictEqual(p[0].size, "wide"); assert.strictEqual(p[0].enabled, false);
  assert.strictEqual(p[1].id, "wifi"); assert.strictEqual(p[1].size, "medium", "invalid size -> the tile's default"); assert.strictEqual(p[1].enabled, true);
  assert.strictEqual(p[2].id, "netspeed"); assert.strictEqual(p[2].enabled, true, "an explicit enabled wins over the default");
  assert.strictEqual(p.length, 13, "every known tile present exactly once"); assert.ok(!p.some(x => x.id === "bogus"));
  assert.strictEqual(p[3].id, "bluetooth", "the rest appended in default order"); assert.strictEqual(p.find(x => x.id === "notifications").enabled, false, "an appended optional tile keeps its default: off");
  // a 1.0-5 layout (two sizes, twelve enabled tiles) still parses: small = one column, wide = the full row
  const old = S.parseTiles(JSON.stringify([{ id: "wifi", size: "small", enabled: true }, { id: "volume", size: "wide", enabled: true }]));
  assert.strictEqual(old[0].size, "small"); assert.strictEqual(old[1].size, "wide"); assert.strictEqual(old.length, 13);
  // a 1.0-7 layout (twelve tiles, no microphone row) gains the Microphone row at the end, enabled (1.0-8)
  const v7 = S.parseTiles(JSON.stringify(["wifi", "bluetooth", "volume", "brightness", "battery", "dnd", "nightlight", "screenshot", "settings", "notifications", "powerprofile", "netspeed"].map(id => ({ id, size: "small", enabled: id !== "netspeed" }))));
  assert.strictEqual(v7.length, 13); assert.strictEqual(v7[12].id, "mic"); assert.strictEqual(v7[12].enabled, true); assert.strictEqual(v7[12].size, "wide");
});
t("tiles v3: move / size cycle / enable are pure and bounded", () => {
  const d = S.defaultTiles();
  const m = S.moveTile(d, 0, 3);
  assert.deepStrictEqual(m.map(x => x.id).slice(0, 4), ["bluetooth", "volume", "mic", "wifi"]); assert.strictEqual(d[0].id, "wifi", "input untouched");
  assert.deepStrictEqual(S.moveTile(d, 5, 5), d); assert.deepStrictEqual(S.moveTile(d, -1, 2), d); assert.deepStrictEqual(S.moveTile(d, 2, 99), d);
  assert.strictEqual(S.nextSize("small"), "medium"); assert.strictEqual(S.nextSize("medium"), "wide"); assert.strictEqual(S.nextSize("wide"), "small");
  assert.strictEqual(S.toggleTileSize(d, "wifi").find(x => x.id === "wifi").size, "wide", "medium -> wide");
  assert.strictEqual(S.toggleTileSize(S.toggleTileSize(d, "wifi"), "wifi").find(x => x.id === "wifi").size, "small", "wide -> small");
  assert.strictEqual(S.toggleTileSize(d, "bluetooth").find(x => x.id === "bluetooth").size, "medium", "small -> medium");
  assert.strictEqual(S.setTileSize(d, "wifi", "huge").find(x => x.id === "wifi").size, "medium", "an unknown size is ignored");
  assert.strictEqual(S.setTileEnabled(d, "screenshot", false).find(x => x.id === "screenshot").enabled, false);
  assert.deepStrictEqual(S.toggleTileSize(d, "nope"), d);
  assert.strictEqual(S.sizeLabel("medium"), "Medium");
});
t("tiles v3: grid geometry — three integer columns, spans 1/2/3, a row wraps when a tile does not fit, every tile in a row takes the row height, the last column ends at the right edge, disabled skipped", () => {
  const tiles = [{ id: "wifi", size: "medium", enabled: true }, { id: "bluetooth", size: "small", enabled: true }, { id: "volume", size: "wide", enabled: true },
                 { id: "battery", size: "medium", enabled: true }, { id: "dnd", size: "small", enabled: true }, { id: "screenshot", size: "small", enabled: false },
                 { id: "settings", size: "small", enabled: true }, { id: "nightlight", size: "medium", enabled: true }, { id: "netspeed", size: "wide", enabled: true }];
  const g = S.layoutTiles(tiles, 608, 12);   // 36 gridUnits at Medium (648) minus 2 x 20 padding; col = floor((608 - 24) / 3) = 194, the last column 196
  const by = {}; g.items.forEach(i => by[i.id] = i);
  assert.deepStrictEqual(by.wifi, { id: "wifi", x: 0, y: 0, w: 400, h: 76, span: 2, n: 0 });
  assert.deepStrictEqual(by.bluetooth, { id: "bluetooth", x: 412, y: 0, w: 196, h: 76, span: 1, n: 1 }, "third column takes the rounding remainder: ends at 608");
  assert.deepStrictEqual(by.volume, { id: "volume", x: 0, y: 88, w: 608, h: 64, span: 3, n: 2 });
  assert.deepStrictEqual(by.battery, { id: "battery", x: 0, y: 164, w: 400, h: 96, span: 2, n: 3 });
  assert.deepStrictEqual(by.dnd, { id: "dnd", x: 412, y: 164, w: 196, h: 96, span: 1, n: 4 }, "a one-column tile beside the battery card is stretched to the row height (96)");
  assert.strictEqual(by.screenshot, undefined, "disabled tile not placed");
  assert.deepStrictEqual(by.settings, { id: "settings", x: 0, y: 272, w: 194, h: 76, span: 1, n: 5 });
  assert.deepStrictEqual(by.nightlight, { id: "nightlight", x: 206, y: 272, w: 402, h: 76, span: 2, n: 6 }, "a two-column tile after a one-column one fills columns 2-3");
  assert.deepStrictEqual(by.netspeed, { id: "netspeed", x: 0, y: 360, w: 608, h: 56, span: 3, n: 7 });
  assert.strictEqual(g.height, 416);
  assert.strictEqual(S.layoutTiles([], 608, 12).height, 0);
  const wrap = S.layoutTiles([{ id: "bluetooth", size: "small" }, { id: "wifi", size: "medium" }, { id: "dnd", size: "small" }, { id: "battery", size: "medium" }], 608, 12);
  assert.strictEqual(wrap.items[2].y, 0 + 76 + 12, "small + medium fill a row; the next small starts a new row"); assert.strictEqual(wrap.items[3].x, 206, "a medium tile after a small one takes columns 2-3 of that row");
  const all = S.layoutTiles(S.defaultTiles(), 608, 12);
  assert.strictEqual(all.items.length, 10, "the three optional tiles are off by default"); assert.strictEqual(all.height, 76 + 12 + 64 + 12 + 64 + 12 + 64 + 12 + 96 + 12 + 76, "six rows: 76 · 64 · 64 (microphone) · 64 · 96 · 76 with 12 px gaps");
  assert.deepStrictEqual(all.items.map(i => i.id), ["wifi", "bluetooth", "volume", "mic", "brightness", "battery", "dnd", "nightlight", "screenshot", "settings"]);
  assert.ok(all.items.every(i => i.x + i.w <= 608), "nothing past the right edge");
  for (const a of all.items) for (const b of all.items) if (a !== b) assert.ok(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y, "no overlap " + a.id + "/" + b.id);
  const everything = S.layoutTiles(S.defaultTiles().map(t => ({ id: t.id, size: t.size, enabled: true })), 608, 12);
  assert.strictEqual(everything.items.length, 13); assert.strictEqual(everything.items.map(i => i.n).join(","), "0,1,2,3,4,5,6,7,8,9,10,11,12", "n is the placement order (the open animation staggers by it)");
});
t("tiles v3: drag helpers — tileAt finds the slot under a point (or the nearest within a tile height), moveTileTo takes the hovered tile's slot; the short Bluetooth line", () => {
  const d = S.defaultTiles(), L = S.layoutTiles(d, 608, 12);
  assert.strictEqual(S.tileAt(L.items, 10, 10), "wifi"); assert.strictEqual(S.tileAt(L.items, 500, 10), "bluetooth");
  assert.strictEqual(S.tileAt(L.items, 300, 100), "volume", "a wide row spans the three columns");
  assert.strictEqual(S.tileAt(L.items, 200, -20), "wifi", "a little above the grid: nearest by centre (within one tile height)");
  assert.strictEqual(S.tileAt(L.items, 500, -80), "", "above the grid, farther than a tile height from every centre");
  assert.strictEqual(S.tileAt(L.items, 150, -400), "", "far away: nothing");
  const m = S.moveTileTo(d, "dnd", "wifi");
  assert.deepStrictEqual(m.map(t => t.id).slice(0, 3), ["dnd", "wifi", "bluetooth"]); assert.strictEqual(d[0].id, "wifi", "input untouched");
  assert.deepStrictEqual(S.moveTileTo(d, "wifi", "wifi"), d); assert.deepStrictEqual(S.moveTileTo(d, "nope", "wifi"), d); assert.deepStrictEqual(S.moveTileTo(d, "wifi", "nope"), d);
  assert.deepStrictEqual(S.rectFor(L, "wifi"), { id: "wifi", x: 0, y: 0, w: 400, h: 76, span: 2, n: 0 }); assert.strictEqual(S.rectFor(L, "nope"), null);
  assert.strictEqual(S.bluetoothShort({ btPresent: true, btPowered: true, btConnected: 1 }), "1 connected"); assert.strictEqual(S.bluetoothShort({ btPresent: true, btPowered: true, btConnected: 0 }), "On"); assert.strictEqual(S.bluetoothShort({ btPresent: false }), "No adapter");
});
t("night light: toggled through kwinrc NightColor/Active + a KWin reconfigure (no bus-lifetime inhibit); nothing when KWin lacks it", () => {
  assert.strictEqual(S.nightCommand({ nightEnabled: true }), "kwriteconfig6 --file kwinrc --group NightColor --key Active false && qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure");
  assert.strictEqual(S.nightCommand({ nightEnabled: false }), "kwriteconfig6 --file kwinrc --group NightColor --key Active true && qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure");
  assert.strictEqual(S.nightCommand({ nightEnabled: null }), ""); assert.strictEqual(S.nightCommand({}), "");
  assert.ok(!S.nightCommand({ nightEnabled: true }).includes("'"), "no single quotes: the executable engine runs it through sh");
});
t("the applet's main.qml: speed always on (no idle-hide), transparent fixed-size dialog with the card animating (no window resize while sliding), v3 geometry markers", () => {
  const base = path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents/ui");
  const qml = fs.readFileSync(path.join(base, "main.qml"), "utf8");
  assert.ok(!qml.includes("zeroSamples"), "no idle counter hides the rate");
  assert.ok(qml.includes('readonly property bool speedVisible: Plasmoid.configuration.showSpeed && st.iface !== ""'), "speed shown whenever a link is up");
  assert.ok(qml.includes("backgroundHints: PlasmaCore.Dialog.NoBackground"), "transparent dialog window");
  assert.ok(qml.includes("y: Math.round(-0.35 * height * (1 - pane.openProgress))") && qml.includes("opacity: pane.openProgress"), "the CARD animates y (from -0.35 x height) and opacity");
  assert.ok(qml.includes("duration: root.closing ? 160 : 220; easing.type: Easing.OutCubic"), "220 ms OutCubic open, 160 ms close");
  const dialogHead = qml.split("PlasmaCore.Dialog {")[1].split("mainItem:")[0];
  assert.ok(!/Behavior on (paneHeight|height|width)/.test(dialogHead), "no animated size on the dialog itself");
  assert.ok(qml.includes("bottomLeftRadius: pane.radius; bottomRightRadius: pane.radius") && qml.includes("readonly property int radius: 24"), "radius 24 at the bottom corners");
  assert.ok(qml.includes('readonly property int paneUnits: root.barSize === "small" ? 32 : (root.barSize === "large" ? 40 : 36)') && qml.includes("Kirigami.Units.gridUnit * root.paneUnits"), "pane width 36 gridUnits at Medium (32 / 40 at Small / Large)");
  assert.ok(qml.includes("readonly property int notifWidth: root.settingsWidth"), "notification pane the same width");
  assert.ok(qml.includes("readonly property int cardPad: 20") && qml.includes("readonly property int tileGap: 12") && qml.includes("readonly property int edge: 12"), "padding 20, 12 px gaps, 12 px right margin");
  assert.ok(qml.includes("PauseAnimation { duration: tw.order * 30 }") && qml.includes('property: "introDy"; to: 0; duration: 160'), "tiles stagger in 30 ms apart over 160 ms");
  assert.ok(qml.includes("DragHandler") && qml.includes("Status.tileAt(") && qml.includes("Status.moveTileTo("), "edit mode drags through the pure helpers");
  assert.ok(qml.includes("Plasmoid.configuration.tilesJson = Status.tilesJson(arr)"), "the tile model is persisted in tilesJson");
  for (const f of ["Tile.qml", "SliderTile.qml", "BatteryCard.qml", "NotificationRow.qml", "main.qml"]) {
    const c = fs.readFileSync(path.join(base, f), "utf8");
    assert.ok(!/color: "#|color: "(white|black|red|blue|grey|gray)"/.test(c), f + " carries no literal colour (Kirigami.Theme only)");
  }
  const tile = fs.readFileSync(path.join(base, "Tile.qml"), "utf8");
  assert.ok(tile.includes("radius: 16") && tile.includes("font.pixelSize: 15; font.weight: Font.DemiBold") && tile.includes("scale: hovered ? 1.02 : 1.0") && tile.includes("duration: 120"), "tile: radius 16, Inter 15/600 title, 1.02 hover lift in 120 ms");
  const slider = fs.readFileSync(path.join(base, "SliderTile.qml"), "utf8");
  assert.ok(slider.includes("width: 36; height: 36") && slider.includes("height: 6; radius: 3"), "slider: 36 px thumb on a 6 px track");
  const battery = fs.readFileSync(path.join(base, "BatteryCard.qml"), "utf8");
  assert.ok(battery.includes("font.pixelSize: 28; font.weight: Font.Bold"), "battery: percentage in Inter 28/700");
  const row = fs.readFileSync(path.join(base, "NotificationRow.qml"), "utf8");
  assert.ok(row.includes("implicitHeight: Math.max(64, content.implicitHeight + 16)") && row.includes("font.pixelSize: 14; wrapMode: Text.Wrap"), "notification rows 64 px, body 14 px");
});
t("the shipped status.sh prints one JSON line in both modes (syntax + shape; tools absent here are simply empty)", () => {
  const { spawnSync } = require("child_process");
  const script = path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents/code/status.sh");
  if (spawnSync("sh", ["-c", "command -v awk"]).status !== 0) { console.log("  (status.sh run skipped: no awk)"); return; }
  for (const args of [["--light"], [], ["--pane"]]) {
    const r = spawnSync("sh", [script, ...args], { encoding: "utf8", timeout: 10000, env: { ...process.env, PATH: "/usr/bin:/bin" } });
    assert.strictEqual(r.status, 0, "status.sh " + args.join(" ") + " exit " + r.status + " " + r.stderr);
    const lines = r.stdout.split("\n").filter((l) => l.length);
    assert.strictEqual(lines.length, 1, "exactly one line: " + JSON.stringify(r.stdout));
    const j = JSON.parse(lines[0]);
    assert.strictEqual(j.light, args[0] === "--light"); assert.ok(j.net && typeof j.net.rx === "number" && typeof j.net.iface === "string");
    assert.strictEqual(typeof j.wifi, "string", "both modes carry the nmcli active line (empty when none)");
    const s = S.parseStatus(lines[0]);
    assert.strictEqual(s.light, j.light); assert.strictEqual(s.iface, j.net.iface);
  }
  const sh = fs.readFileSync(script, "utf8");
  assert.ok(sh.includes("--rescan no"), "every nmcli wifi list call says --rescan no: the 5 s probe must never trigger a scan");
  assert.ok(!sh.includes("/proc/net/wireless") || sh.includes("wifi_quality"), "kernel link quality, if read, is only the fallback");
});
// ---- microphone row + privacy indicators (1.0-8)
t("microphone: the default source parses like the sink (JSON and key=value), glyphs by level, lines; privacy counts -> tooltip line", () => {
  const j = S.parseStatus(JSON.stringify({ net: { iface: "", rx: 0, tx: 0 }, wifi: "", devs: [], volume: "Volume: 0.45", mic: "Volume: 0.80 [MUTED]", privacy: { mic_used: 0, cam_used: 0, cam_present: 1 } }));
  assert.strictEqual(j.hasMic, true); assert.strictEqual(j.micVolume, 80); assert.strictEqual(j.micMuted, true); assert.strictEqual(j.camPresent, true); assert.strictEqual(j.micUsed, 0);
  assert.strictEqual(S.micIcon(j.micVolume, j.micMuted), "microphone-sensitivity-muted"); assert.strictEqual(S.micLine(j), "Muted");
  const k = S.parseStatus("volume=Volume: 0.45\nmic=Volume: 0.20\n");
  assert.strictEqual(k.hasMic, true); assert.strictEqual(k.micVolume, 20); assert.strictEqual(k.micMuted, false); assert.strictEqual(S.micIcon(20, false), "microphone-sensitivity-low");
  assert.strictEqual(S.micIcon(50, false), "microphone-sensitivity-medium"); assert.strictEqual(S.micIcon(90, false), "microphone-sensitivity-high"); assert.strictEqual(S.micIcon(0, false), "microphone-sensitivity-muted");
  const none = S.parseStatus(JSON.stringify({ net: { iface: "", rx: 0, tx: 0 }, wifi: "", devs: [], volume: "Volume: 0.45", mic: "" }));
  assert.strictEqual(none.hasMic, false); assert.strictEqual(none.micVolume, -1); assert.strictEqual(S.micLine(none), "No microphone"); assert.strictEqual(S.privacyLine(none), "");
  const busy = S.parseStatus(JSON.stringify({ net: { iface: "", rx: 0, tx: 0 }, wifi: "", devs: [], mic: "Volume: 1.00", privacy: { mic_used: 1, cam_used: 2, cam_present: 1 } }));
  assert.strictEqual(S.privacyLine(busy), "1 app is using the microphone · 2 apps are using the camera"); assert.strictEqual(S.micLine(busy), "100% · in use");
  assert.strictEqual(S.privacyLine(S.parseStatus(JSON.stringify({ net: {}, wifi: "", devs: [], privacy: { mic_used: 3, cam_used: 0 } }))), "3 apps are using the microphone");
  // the probe's shell side names the same fields and both wpctl targets; the tile's actions hit the default SOURCE only
  const sh = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents/code/status.sh"), "utf8");
  assert.ok(sh.includes("wpctl get-volume @DEFAULT_AUDIO_SOURCE@") && sh.includes('\\"mic\\":') && sh.includes('\\"mic_used\\":') && sh.includes('\\"cam_used\\":') && sh.includes("Stream/Input/Audio") && sh.includes("Stream/Input/Video"), "status.sh probes the default source and the PipeWire capture streams");
  const qml = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents/ui/main.qml"), "utf8");
  assert.ok(qml.includes('root.run("wpctl set-volume @DEFAULT_AUDIO_SOURCE@ "') && qml.includes('root.run("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle")') && qml.includes('id: micRow') && qml.includes('id: micInd') && qml.includes('id: camInd'), "main.qml: microphone row + bar glyphs for microphone and camera use");
  const xml = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents/config/main.xml"), "utf8");
  assert.ok(xml.includes('{"id":"volume","size":"wide","enabled":true},{"id":"mic","size":"wide","enabled":true}'), "main.xml default layout: the microphone row under Volume");
});
console.log("quicksettings-js-test: " + n + " groups passed");
