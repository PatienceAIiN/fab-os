#!/usr/bin/env node
// Unit tests for the ask bar's pure-JS helpers (packages/.../in.patienceai.fabos.askbar/contents/ui/agent.js).
// The QML engine is not available headless, so the JSON -> conversation logic is exercised here under node.
//   node tests/askbar-js-test.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-agent/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents/ui/agent.js"), "utf8");
const A = {};
vm.runInNewContext(src.replace(/^\.pragma library\s*$/m, ""), A);   // .pragma is a QML directive, not JS

let n = 0;
function t(name, fn) { fn(); n++; }
// objects come from another vm realm (different Object prototype), so compare by value, not by prototype
function eq(a, b) { assert.strictEqual(JSON.stringify(a), JSON.stringify(b)); }

t("shellQuote escapes single quotes", () => {
  assert.strictEqual(A.shellQuote("it's"), "'it'\\''s'");
});
t("apiCommand builds a curl call with token/port files and a JSON body", () => {
  const c = A.apiCommand("POST", "/tasks", { request: "open kate", parent_id: 7 });
  assert.ok(c.includes('curl -sS -m 12 -X POST'));
  assert.ok(c.includes('Authorization: Bearer $(cat "$R/token"'));
  assert.ok(c.includes(`--data-binary '{"request":"open kate","parent_id":7}'`));
  assert.ok(c.endsWith('"http://127.0.0.1:$P/tasks" 2>/dev/null'));
  assert.ok(!A.apiCommand("GET", "/status").includes("--data-binary"));
});
t("tagged/parseTag round-trip", () => {
  const s = A.tagged("poll", 12, 99, A.apiCommand("GET", "/tasks/12"));
  assert.ok(s.startsWith(": poll.12.99; R="));
  eq(A.parseTag(s), { kind: "poll", ref: 12 });
  eq(A.parseTag("setsid -f fabos-command-center"), { kind: "", ref: 0 });
});
t("parseJson tolerates junk", () => {
  assert.strictEqual(A.parseJson(""), null); assert.strictEqual(A.parseJson("not json"), null);
  eq(A.parseJson(' {"a":1} '), { a: 1 });
});
t("friendlyApp maps bundled apps to Fab names and icons", () => {
  eq(A.friendlyApp("kate", ["/home/u/Documents/fabos-note.txt"]), { label: "Fab Editor", icon: "kate", appIcon: true, detail: "fabos-note.txt" });
  assert.strictEqual(A.friendlyApp("org.kde.dolphin", []).label, "Fab Files");
  assert.strictEqual(A.friendlyApp("konsole").icon, "utilities-terminal");
  eq(A.friendlyApp("xdg-open", ["https://fabos.patienceai.in/docs/"]), { label: "fabos.patienceai.in", icon: "globe", appIcon: false, detail: "" });
  eq(A.friendlyApp("xdg-open", ["/tmp/report.pdf"]), { label: "report.pdf", icon: "document-open", appIcon: false, detail: "" });
  eq(A.friendlyApp("gimp"), { label: "Gimp", icon: "gimp", appIcon: true, detail: "" });
});
t("describeStep: open_app / type_text / run_shell (raw only when showRaw)", () => {
  const o = A.describeStep({ name: "open_app", input: JSON.stringify({ app: "kate", args: ["~/Documents/fabos-note.txt"] }) }, false, "");
  assert.strictEqual(o.running, "Opening Fab Editor"); assert.strictEqual(o.done, "Opened Fab Editor"); assert.strictEqual(o.app, "Fab Editor"); assert.strictEqual(o.icon, "kate");
  const ty = A.describeStep({ name: "type_text", input: JSON.stringify({ text: "hello there", press_enter: true }) }, false, "Fab Editor");
  assert.strictEqual(ty.running, "Typing into Fab Editor"); assert.strictEqual(ty.typed, "hello there"); assert.strictEqual(ty.subtitle, "…then Enter"); assert.strictEqual(ty.icon, "input-keyboard");
  const sh = A.describeStep({ name: "run_shell", input: JSON.stringify({ command: "rm -rf ~/x" }) }, false, "");
  assert.strictEqual(sh.running, "Running a command"); assert.strictEqual(sh.subtitle, "", "raw command must not leak without ui.show_raw");
  assert.strictEqual(A.describeStep({ name: "run_shell", input: JSON.stringify({ command: "uname -a" }) }, true, "").subtitle, "uname -a");
  assert.strictEqual(A.describeStep({ name: "run_shell", input: JSON.stringify({ command: "apt update", as_root: true }) }, false, "").running, "Running a command as administrator");
});
t("describeStep: files / web / mail / notify / ask / watch", () => {
  assert.strictEqual(A.describeStep({ name: "write_file", input: JSON.stringify({ path: "~/Documents/notes.txt", content: "x" }) }, false, "").running, "Writing notes.txt");
  assert.strictEqual(A.describeStep({ name: "write_file", input: JSON.stringify({ path: "~/a.log", content: "x", append: true }) }, false, "").done, "Added to a.log");
  assert.strictEqual(A.describeStep({ name: "read_file", input: JSON.stringify({ path: "/etc/os-release" }) }, false, "").done, "Read os-release");
  assert.strictEqual(A.describeStep({ name: "list_dir", input: JSON.stringify({ path: "/home/u/Pictures/" }) }, false, "").running, "Looking in Pictures");
  assert.strictEqual(A.describeStep({ name: "web_fetch", input: JSON.stringify({ url: "https://example.org/a/b?c" }) }, false, "").running, "Fetching example.org");
  assert.strictEqual(A.describeStep({ name: "send_email", input: JSON.stringify({ to: "someone@example.com", subject: "Hi" }) }, false, "").running, "Emailing someone@example.com");
  assert.strictEqual(A.describeStep({ name: "notify_user", input: JSON.stringify({ message: "All done" }) }, false, "").subtitle, "All done");
  assert.strictEqual(A.describeStep({ name: "ask_user", input: JSON.stringify({ question: "Which folder?" }) }, false, "").icon, "dialog-question");
  assert.strictEqual(A.describeStep({ name: "schedule_watch", input: JSON.stringify({ kind: "email_reply", from_contains: "a@b", notify_message: "x" }) }, false, "").subtitle, "Watching your inbox for a@b");
  assert.strictEqual(A.describeStep({ name: "schedule_watch", input: JSON.stringify({ kind: "email_reply" }) }, false, "").icon, "view-visible");
  assert.strictEqual(A.describeStep({ name: "mystery_tool", input: "{}" }, false, "").running, "Working on mystery tool");
  assert.strictEqual(A.describeStep({ name: "run_shell", input: "not json" }, false, "").running, "Running a command");
});
t("stepStatus follows the daemon's step row lifecycle", () => {
  assert.strictEqual(A.stepStatus({ decision: "", output: "" }), "pending");            // inserted, awaiting approval
  assert.strictEqual(A.stepStatus({ decision: "auto-approved", output: "" }), "running");  // executing
  assert.strictEqual(A.stepStatus({ decision: "approved", output: "" }), "running");
  assert.strictEqual(A.stepStatus({ decision: "auto-approved", output: JSON.stringify({ exit_code: 0, stdout: "ok" }) }), "done");
  assert.strictEqual(A.stepStatus({ decision: "auto-approved", output: JSON.stringify({ exit_code: 3, stdout: "" }) }), "error");
  assert.strictEqual(A.stepStatus({ decision: "auto-approved", output: JSON.stringify({ error: "timeout after 120s" }) }), "error");
  assert.strictEqual(A.stepStatus({ decision: "denied", output: JSON.stringify({ error: "Denied by user/policy (HIGH: x)" }) }), "denied");
  assert.strictEqual(A.stepStatus({ decision: "expired", output: "" }), "denied");
  assert.strictEqual(A.stepStatus({ decision: "auto-approved", output: JSON.stringify({ launched: "kate", pid: 4 }) }), "done");
});
t("stepError is friendly and hides raw output unless showRaw", () => {
  assert.strictEqual(A.stepError({ output: JSON.stringify({ exit_code: 3, stderr: "boom" }) }, false), "The command finished with an error (exit 3)");
  assert.strictEqual(A.stepError({ output: JSON.stringify({ exit_code: 3, stderr: "boom" }) }, true), "The command finished with an error (exit 3): boom");
  assert.strictEqual(A.stepError({ output: JSON.stringify({ error: "Denied by user/policy (HIGH: external)" }) }, false), "Not allowed — skipped");
  assert.strictEqual(A.stepError({ output: JSON.stringify({ error: "timeout after 120s (process killed)" }) }, false), "Took too long and was stopped");
  assert.strictEqual(A.stepError({ output: JSON.stringify({ error: "wtype failed: no virtual keyboard" }) }, false), "Could not type into the app (no virtual keyboard in this session)");
  assert.strictEqual(A.stepError({ output: "" }, false), "This step did not work");
  assert.strictEqual(A.stepError({ output: JSON.stringify({ error: "RuntimeError: Mail is not configured. Ask the user\nmore" }) }, false), "Mail is not configured. Ask the user");
});
t("statusLabel / isActive / riskLabel", () => {
  assert.strictEqual(A.statusLabel("running"), "Working on it…"); assert.strictEqual(A.statusLabel("waiting_approval"), "Needs your permission");
  assert.strictEqual(A.statusLabel("weird"), "");
  assert.ok(A.isActive("queued") && A.isActive("waiting_user") && !A.isActive("done") && !A.isActive("cancelled"));
  assert.strictEqual(A.riskLabel("CRITICAL"), "Critical"); assert.strictEqual(A.riskLabel("low"), "Low risk");
});
t("Markdown-lite: bold, italics, inline code, links, lists, headings, paragraphs", () => {
  const h = A.paragraphs("# Title\nSome **bold** and *it* and `code` and [doc](https://fabos.patienceai.in/docs/)\nsecond line\n\n- one\n- two\n1. first\n2) second\nPlain <b>escaped</b>", "#14000000");
  assert.ok(h.startsWith("<p><b>Title</b></p>"));
  assert.ok(h.includes("<b>bold</b>")); assert.ok(h.includes("<i>it</i>"));
  assert.ok(h.includes(`<code style="font-family:'JetBrains Mono',monospace;background-color:#14000000">code</code>`));
  assert.ok(h.includes('<a href="https://fabos.patienceai.in/docs/">doc</a>'));
  assert.ok(h.includes("second line</p>"), "consecutive lines merge into one paragraph");
  assert.ok(h.includes("<ul><li>one</li><li>two</li></ul>")); assert.ok(h.includes("<ol><li>first</li><li>second</li></ol>"));
  assert.ok(h.includes("&lt;b&gt;escaped&lt;/b&gt;"), "HTML in model output is escaped");
});
t("mdBlocks splits fenced code into monospace cards", () => {
  const b = A.mdBlocks("Here:\n```bash\nls -la\necho hi\n```\nDone **ok**", "#14000000");
  assert.strictEqual(b.length, 3);
  eq(b[1], { type: "code", text: "ls -la\necho hi", lang: "bash" });
  assert.strictEqual(b[0].type, "text"); assert.ok(b[2].html.includes("<b>ok</b>"));
  eq(A.mdBlocks("", "#0"), []);
  eq(A.mdBlocks("```\n```", "#0"), []);
});
t("plainSummary strips markup and truncates", () => {
  assert.strictEqual(A.plainSummary("**Hi** there ```code``` `x`", 100), "Hi there x");
  assert.strictEqual(A.plainSummary("abcdefghij", 5), "abcd…");
});
t("voiceInfo: available only with exit 0 and a real STT backend", () => {
  eq(A.voiceInfo(0, '{"wake": false, "listening": false, "stt": "whisper.cpp", "tts": "espeak-ng", "mic": true}'), { available: true, stt: "whisper.cpp", tts: "espeak-ng", mic: true });
  assert.strictEqual(A.voiceInfo(0, '{"stt":"none","tts":"none","mic":false}').available, false);
  assert.strictEqual(A.voiceInfo(127, "").available, false);   // binary missing
  assert.strictEqual(A.voiceInfo(4, "").available, false);
});
t("banned words never appear in UI strings produced by the helpers", () => {
  // third-party product names that must never surface in Fab OS UI strings (spelled in halves so this file does not contain them either)
  const banned = new RegExp(["Chat" + "GPT", "Open" + "AI", "G" + "PT", "Snow" + "UI", "So" + "ra", "DA" + "LL", "Upgrade " + "plan", "can make " + "mistakes",
                             "Anth" + "ropic", "Cla" + "ude", "Gem" + "ini", "Goo" + "gle", "Deep" + "Seek"].join("|"));
  for (const s of ["queued", "running", "waiting_approval", "waiting_user", "done", "failed", "cancelled"]) assert.ok(!banned.test(A.statusLabel(s)));
  for (const k of Object.keys(A.APPS)) assert.ok(!banned.test(A.APPS[k][0]), k);
  assert.ok(!banned.test(src.replace(/\/\/.*$/gm, "")), "agent.js code has no banned product names");
});
console.log("askbar-js-test: " + n + " test groups passed");
