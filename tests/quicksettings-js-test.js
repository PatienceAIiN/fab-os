#!/usr/bin/env node
// Unit tests for the quick-settings applet's pure helpers (packages/fabos-desktop/.../in.patienceai.fabos.quicksettings/contents/ui/status.js):
// status.sh output -> state, /proc/net parsing and rates, glyph names, text, and the shell-scripting sync command.
//   node tests/quicksettings-js-test.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents/ui/status.js"), "utf8");
const S = {};
vm.runInNewContext(src.replace(/^\.pragma library\s*$/m, ""), S);
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

t("parseStatus reads every field", () => {
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
t("wifi glyphs follow signal and lock", () => {
  const s = S.parseStatus(STATUS);
  assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-good-locked");
  s.wifiSignal = 90; s.wifiLocked = false; assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-excellent");
  s.wifiSignal = 40; assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-ok");
  s.wifiSignal = 10; assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-weak");
  s.wifiSignal = 0; assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-none");
  s.wifiRadio = false; assert.strictEqual(S.wifiIcon(s), "network-wireless-off"); assert.strictEqual(S.wifiLine(s), "Wi-Fi off");
  s.wifiRadio = true; s.connType = ""; assert.strictEqual(S.wifiIcon(s), "network-wireless-disconnected"); assert.strictEqual(S.wifiLine(s), "Not connected");
});
t("battery glyph names exist in the FabOS mono set (000..100 step 10, -charging)", () => {
  assert.strictEqual(S.batteryIcon(87, "Discharging"), "battery-090");
  assert.strictEqual(S.batteryIcon(4, "Discharging"), "battery-000");
  assert.strictEqual(S.batteryIcon(55, "Charging"), "battery-060-charging");
  assert.strictEqual(S.batteryIcon(-1, ""), "battery-missing");
});
t("volume + bluetooth glyphs", () => {
  assert.strictEqual(S.volumeIcon(45, false), "audio-volume-medium");
  assert.strictEqual(S.volumeIcon(80, false), "audio-volume-high");
  assert.strictEqual(S.volumeIcon(10, false), "audio-volume-low");
  assert.strictEqual(S.volumeIcon(80, true), "audio-volume-muted");
  assert.strictEqual(S.bluetoothIcon({ btPresent: true, btPowered: true, btConnected: 2 }), "network-bluetooth-activated");
  assert.strictEqual(S.bluetoothIcon({ btPresent: true, btPowered: true, btConnected: 0 }), "bluetooth");
  assert.strictEqual(S.bluetoothIcon({ btPresent: true, btPowered: false, btConnected: 0 }), "bluetooth-disabled");
  assert.strictEqual(S.bluetoothLine({ btPresent: true, btPowered: true, btConnected: 2 }), "2 devices connected");
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
t("fmtRate units", () => {
  assert.strictEqual(S.fmtRate(0), "0 B/s"); assert.strictEqual(S.fmtRate(999), "999 B/s"); assert.strictEqual(S.fmtRate(2500), "2.5 kB/s");
  assert.strictEqual(S.fmtRate(80000), "80 kB/s"); assert.strictEqual(S.fmtRate(1.2e6), "1.2 MB/s"); assert.strictEqual(S.fmtRate(2.5e9), "2.50 GB/s");
});
t("bar size -> clock px and the shell-scripting sync command", () => {
  assert.strictEqual(S.clockSizeFor("small"), 12); assert.strictEqual(S.clockSizeFor("medium"), 13); assert.strictEqual(S.clockSizeFor("large"), 15);
  const cmd = S.syncCommand("large", false);
  assert.ok(cmd.startsWith("qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '"));
  assert.ok(cmd.includes('writeConfig("fontSize", 15)')); assert.ok(cmd.includes('writeConfig("magnify", false)'));
  assert.ok(!cmd.includes("magnification"), "the dock's magnification strength is the dock's own setting: never written from the bar");
  assert.ok(cmd.includes('widgets("org.kde.plasma.digitalclock")') && cmd.includes('widgets("in.patienceai.fabos.dock")'));
  assert.strictEqual((cmd.match(/'/g) || []).length, 2, "the script itself contains no single quotes");
  // the script is valid JS against a fake shell API
  const api = { panels: () => [{ widgets: (type) => [{ writeConfig(k, v) { api.written.push(type + ":" + k + "=" + v) }, currentConfigGroup: [] }] }], written: [] };
  vm.runInNewContext(S.syncScript("small", true), api);
  assert.deepStrictEqual(api.written, ["org.kde.plasma.digitalclock:fontSize=12", "in.patienceai.fabos.dock:magnify=true"]);
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
  wifi: "*:Home\\:Net:78:WPA2", ip4: "10.0.0.5", bt: { present: true, powered: true, connected: 1 }, volume: "Volume: 0.45", profile: "balanced" });
t("JSON probe: every field lands where the key=value form put it", () => {
  const s = S.parseStatus(FULL + "\n");
  assert.strictEqual(s.light, false);
  assert.strictEqual(s.wifiRadio, true); assert.strictEqual(s.connType, "wifi"); assert.strictEqual(s.connDev, "wlp2s0"); assert.strictEqual(s.connName, "Home:Net");
  assert.strictEqual(s.wifiSsid, "Home:Net"); assert.strictEqual(s.wifiSignal, 78); assert.strictEqual(s.wifiLocked, true);
  assert.strictEqual(s.iface, "wlp2s0"); assert.strictEqual(s.rx, 284); assert.strictEqual(s.tx, 746); assert.strictEqual(s.ip4, "10.0.0.5");
  assert.strictEqual(s.btPresent, true); assert.strictEqual(s.btPowered, true); assert.strictEqual(s.btConnected, 1);
  assert.strictEqual(s.volume, 45); assert.strictEqual(s.muted, false); assert.strictEqual(s.hasAudio, true);
  assert.strictEqual(s.batPct, 87); assert.strictEqual(s.batStatus, "Discharging"); assert.strictEqual(s.batTime, "3.2 hours"); assert.strictEqual(s.hasBattery, true);
  assert.strictEqual(s.profile, "balanced"); assert.strictEqual(s.blCur, 45528); assert.strictEqual(s.blMax, 64764); assert.strictEqual(s.hasBacklight, true);
  assert.strictEqual(S.wifiIcon(s), "network-wireless-signal-good-locked"); assert.strictEqual(S.wifiLine(s), "Home:Net · 78%");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(S.counters(s))), { iface: "wlp2s0", rx: 284, tx: 746 });
});
t("JSON probe: radio off = every Wi-Fi device unavailable; wired; no Wi-Fi device = radio unknown; daemons down", () => {
  const off = S.parseStatus(JSON.stringify({ light: false, net: { iface: "enp3s0", rx: 1, tx: 1 }, wifi_quality: null, battery: null, backlight: null,
    devs: ["wlp2s0:wifi:unavailable:", "enp3s0:ethernet:connected:Wired connection 1"], wifi: "", ip4: "", bt: { present: true, powered: false, connected: 0 }, volume: "Volume: 1.00 [MUTED]", profile: "" }));
  assert.strictEqual(off.wifiRadio, false); assert.strictEqual(off.connType, "wired"); assert.strictEqual(S.wifiIcon(off), "network-wireless-off");
  assert.strictEqual(off.muted, true); assert.strictEqual(off.volume, 100); assert.strictEqual(off.hasBattery, false); assert.strictEqual(S.bluetoothIcon(off), "bluetooth-disabled");
  const nowifi = S.parseStatus(JSON.stringify({ light: false, net: { iface: "", rx: 0, tx: 0 }, devs: ["lo:loopback:connected (externally):lo"], bt: { present: false, powered: null, connected: 0 }, volume: "", profile: "" }));
  assert.strictEqual(nowifi.wifiRadio, null); assert.strictEqual(nowifi.connType, ""); assert.strictEqual(nowifi.hasAudio, false); assert.strictEqual(S.wifiLine(nowifi), "Network unavailable");
  const down = S.parseStatus(JSON.stringify({ light: false, net: { iface: "eth0", rx: 5, tx: 5 }, wifi_quality: null, battery: null, backlight: null, devs: [], wifi: "", ip4: "", bt: { present: false, powered: null, connected: 0 }, volume: "", profile: "" }));
  assert.strictEqual(down.wifiRadio, null); assert.strictEqual(down.btPresent, false); assert.strictEqual(S.batteryLine(down), "No battery");
  assert.strictEqual(S.parseStatus("{not json").hasAudio, false, "junk -> empty state, no throw");
});
t("JSON probe: a --light line refreshes only the kernel readings inside the last full state", () => {
  const full = S.parseStatus(FULL);
  const light = S.parseStatus(JSON.stringify({ light: true, net: { iface: "wlp2s0", rx: 284 + 2400000, tx: 746 + 160000 }, wifi_quality: 57,
    battery: { pct: 86, status: "Discharging", time: "3.1 hours" }, backlight: { cur: 40000, max: 64764 } }));
  assert.strictEqual(light.light, true); assert.strictEqual(light.connType, "", "a light line knows nothing about connections");
  const m = S.mergeLight(full, light);
  assert.strictEqual(m.wifiSsid, "Home:Net"); assert.strictEqual(m.connType, "wifi"); assert.strictEqual(m.btConnected, 1); assert.strictEqual(m.volume, 45); assert.strictEqual(m.profile, "balanced");
  assert.strictEqual(m.batPct, 86); assert.strictEqual(m.batTime, "3.1 hours"); assert.strictEqual(m.blCur, 40000);
  assert.strictEqual(m.wifiSignal, 57, "the signal follows the kernel's link quality between full probes");
  assert.strictEqual(m.rx, 284 + 2400000); assert.strictEqual(m.light, true);
  const r = S.rates(S.counters(full), S.counters(m), 2000);
  assert.strictEqual(S.speedText(r.down, r.up), "↓ 1.2 MB/s  ↑ 80 kB/s");
  const wired = S.mergeLight(S.parseStatus(JSON.stringify({ light: false, net: { iface: "enp3s0", rx: 0, tx: 0 }, devs: ["enp3s0:ethernet:connected:Wired"], bt: {}, volume: "", profile: "" })), light);
  assert.strictEqual(wired.wifiSignal, -1, "link quality is not applied to a wired connection");
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
    const s = S.parseStatus(lines[0]);
    assert.strictEqual(s.light, j.light); assert.strictEqual(s.iface, j.net.iface);
  }
});
console.log("quicksettings-js-test: " + n + " groups passed");
