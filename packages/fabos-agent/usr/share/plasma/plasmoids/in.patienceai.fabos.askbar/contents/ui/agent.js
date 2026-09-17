.pragma library
// Pure helpers for the Fab OS ask bar: shell/curl command building, task JSON -> friendly step descriptions,
// Markdown-lite -> Qt rich text. No QML types in here so the logic can be unit-tested with plain JavaScript
// (tests/askbar-js-test.js runs this file under node).

function shellQuote(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'" }

function parseJson(text) {
    if (text === undefined || text === null) return null
    var t = String(text).trim()
    if (!t.length || (t[0] !== "{" && t[0] !== "[")) return null
    try { return JSON.parse(t) } catch (e) { return null }
}

// curl against fabos-agentd on 127.0.0.1 using the same token/port files the fabos CLI reads (POSIX sh syntax: the
// executable DataSource runs commands through /bin/sh -c). The bearer token never reaches any argv: the shell's builtin
// printf writes one curl config line ("header = ...") down a pipe and curl reads it with -K - (config from stdin), so
// /proc/<pid>/cmdline of curl shows only the method, content type, body and URL (the token file is 0600 for a reason).
// The caller prefixes the routing tag.
function apiCommand(method, path, body) {
    var c = "R=\"${XDG_RUNTIME_DIR:-/tmp}/fabos-agent\"; P=$(cat \"$R/port\" 2>/dev/null || echo 8790); "
          + "printf 'header = \"Authorization: Bearer %s\"\\n' \"$(cat \"$R/token\" 2>/dev/null)\" | "
          + "curl -sS -m 12 -K - -X " + method + " -H 'Content-Type: application/json'"
    if (body !== undefined && body !== null) c += " --data-binary " + shellQuote(JSON.stringify(body))
    return c + " \"http://127.0.0.1:$P" + path + "\" 2>/dev/null"
}

// The bar's ONE periodic call: GET /status and, while a task is followed, GET /tasks/{id} — both in a single curl.
// Same token discipline as apiCommand (config line on stdin, never argv), but no cat: `read` is a shell builtin, so the
// port and token files cost no process, and curl fetches both URLs in one run with -w '\n' ending each body with a
// newline — the reply is one JSON object per line (parseSnapshot). Measured in the image with /proc/stat `processes`:
// 4 tasks per snapshot (sh, the printf subshell, curl and its resolver thread) against 7 per apiCommand call, i.e. 14 for
// the status + task pair the bar used to make every 1.5 s (docs/LOW-RAM.md "Idle budget").
function snapshotCommand(taskId) {
    // (read returns 1 on a file without a trailing newline although the variable IS set, so the fallback tests $P, not $?)
    var c = "R=\"${XDG_RUNTIME_DIR:-/tmp}/fabos-agent\"; read -r P < \"$R/port\" 2>/dev/null; [ -n \"$P\" ] || P=8790; read -r T < \"$R/token\" 2>/dev/null; "
          + "printf 'header = \"Authorization: Bearer %s\"\\n' \"$T\" | curl -s -m 12 -K - -w '\\n' \"http://127.0.0.1:$P/status\""
    if (taskId > 0) c += " \"http://127.0.0.1:$P/tasks/" + taskId + "\""
    return c + " 2>/dev/null"
}
// snapshot stdout -> {status, task}: line 1 = /status, line 2 = /tasks/{id} (null when not asked for or not JSON)
function parseSnapshot(out) {
    var lines = String(out || "").split("\n")
    return { status: parseJson(lines[0]), task: lines.length > 1 ? parseJson(lines[1]) : null }
}

// ": kind.ref.serial; ..." -> {kind, ref}  (the ': …;' prefix is a shell no-op that makes each source unique and routable)
function tagged(kind, ref, serial, cmd) { return ": " + kind + "." + ref + "." + serial + "; " + cmd }
function parseTag(source) {
    var m = /^: ([a-z]+)\.(\d+)\.(\d+);/.exec(String(source || ""))
    return m ? { kind: m[1], ref: parseInt(m[2], 10) } : { kind: "", ref: 0 }
}

function basename(p) {
    var s = String(p || "").replace(/\/+$/, "")
    var parts = s.split("/")
    return parts[parts.length - 1] || s || ""
}

function hostOf(url) {
    var m = /^[a-z][a-z0-9+.-]*:\/\/([^\/:?#]+)/i.exec(String(url || ""))
    return m ? m[1] : String(url || "")
}

// Display names Fab OS gives its bundled apps (desktop-entry overrides, see docs/design/BRANDING.md) and their icons.
var APPS = {
    "kate": ["Fab Editor", "kate"], "kwrite": ["Fab Editor", "kwrite"],
    "dolphin": ["Fab Files", "system-file-manager"],
    "konsole": ["Fab Terminal", "utilities-terminal"],
    "firefox": ["Firefox", "firefox"], "firefox-esr": ["Firefox", "firefox"],
    "systemsettings": ["Fab Settings", "systemsettings"], "systemsettings5": ["Fab Settings", "systemsettings"],
    "plasma-discover": ["Fab Software", "plasmadiscover"], "discover": ["Fab Software", "plasmadiscover"],
    "gwenview": ["Fab Photos", "gwenview"], "okular": ["Fab Documents", "okular"],
    "kcalc": ["Fab Calculator", "accessories-calculator"], "ark": ["Fab Archives", "ark"],
    "spectacle": ["Fab Screenshot", "spectacle"], "kinfocenter": ["Fab System Info", "hwinfo"],
    "plasma-systemmonitor": ["Fab Monitor", "utilities-system-monitor"],
    "kwalletmanager5": ["Fab Wallet", "kwalletmanager"], "kwalletmanager": ["Fab Wallet", "kwalletmanager"],
    "krunner": ["Fab Search", "search"],
    "fabos-command-center": ["Fab AI Controls", "fabos-command-center"], "fabos-updates": ["Fab Updates", "fabos-updates"],
    "fabos-feedback": ["Fab Feedback", "fabos-feedback"], "fabos-welcome": ["Welcome to Fab OS", "fabos"],
    "libreoffice": ["LibreOffice", "libreoffice-startcenter"], "libreoffice-writer": ["LibreOffice Writer", "libreoffice-writer"],
    "libreoffice-calc": ["LibreOffice Calc", "libreoffice-calc"], "libreoffice-impress": ["LibreOffice Impress", "libreoffice-impress"]
}

function friendlyApp(app, args) {
    var id = String(app || "").trim(), a = args || []
    var base = basename(id).toLowerCase().replace(/\.desktop$/, "")
    var detail = a.length ? basename(String(a[0])) : ""
    if ((base === "xdg-open" || base === "open") && a.length) {
        var target = String(a[0])
        if (/^https?:\/\//i.test(target)) return { label: hostOf(target), icon: "globe", appIcon: false, detail: "" }
        return { label: basename(target), icon: "document-open", appIcon: false, detail: "" }
    }
    var k = APPS[base] || APPS[base.replace(/^org\.kde\./, "")]
    if (k) return { label: k[0], icon: k[1], appIcon: true, detail: detail }
    var label = base.replace(/^org\.[a-z0-9]+\./, "").replace(/[-_.]+/g, " ")
    label = label.length ? label.charAt(0).toUpperCase() + label.slice(1) : "an app"
    return { label: label, icon: base.length ? base : "window-new", appIcon: true, detail: detail }
}

var TOOL_ICONS = {
    run_shell: "utilities-terminal", read_file: "text-x-generic", write_file: "document-new", list_dir: "folder",
    web_fetch: "globe", send_email: "mail-message", check_email: "mail-message", notify_user: "notifications",
    ask_user: "dialog-question", schedule_watch: "view-visible", type_text: "input-keyboard", open_app: "window-new",
    list_apps: "view-list-details", generate_image: "image-x-generic"
}

// One tool step -> what the live feed shows. Never raw commands unless showRaw (daemon setting ui.show_raw).
function describeStep(s, showRaw, lastApp) {
    var inp = parseJson(s.input) || {}, name = String(s.name || "")
    var d = { name: name, icon: TOOL_ICONS[name] || "system-run", iconFallback: "system-run", appIcon: false,
              running: "Working", done: "Worked", subtitle: "", typed: "", app: "" }
    switch (name) {
    case "open_app": {
        var a = friendlyApp(inp.app, inp.args)
        d.running = "Opening " + a.label; d.done = "Opened " + a.label
        d.icon = a.icon; d.iconFallback = "window-new"; d.appIcon = a.appIcon; d.app = a.appIcon ? a.label : ""
        d.subtitle = a.detail
        break
    }
    case "type_text":
        d.running = lastApp ? "Typing into " + lastApp : "Typing"
        d.done = lastApp ? "Typed into " + lastApp : "Typed text"
        d.typed = String(inp.text || "")
        if (inp.press_enter) d.subtitle = "…then Enter"
        break
    case "run_shell":
        d.running = inp.as_root ? "Running a command as administrator" : "Running a command"
        d.done = inp.as_root ? "Ran a command as administrator" : "Ran a command"
        if (showRaw) d.subtitle = String(inp.command || "")
        break
    case "write_file": {
        var wb = basename(inp.path)
        d.running = (inp.append ? "Adding to " : "Writing ") + wb; d.done = (inp.append ? "Added to " : "Wrote ") + wb
        if (showRaw) d.subtitle = String(inp.path || "")
        break
    }
    case "read_file":
        d.running = "Reading " + basename(inp.path); d.done = "Read " + basename(inp.path)
        if (showRaw) d.subtitle = String(inp.path || "")
        break
    case "list_dir":
        d.running = "Looking in " + (basename(inp.path) || "a folder"); d.done = "Looked in " + (basename(inp.path) || "a folder")
        if (showRaw) d.subtitle = String(inp.path || "")
        break
    case "web_fetch":
        d.running = "Fetching " + hostOf(inp.url); d.done = "Fetched " + hostOf(inp.url)
        if (showRaw) d.subtitle = String(inp.url || "")
        break
    case "send_email":
        d.running = "Emailing " + (inp.to || "someone"); d.done = "Emailed " + (inp.to || "someone")
        d.subtitle = String(inp.subject || "")
        break
    case "check_email":
        d.running = "Checking your mail"; d.done = "Checked your mail"
        d.subtitle = inp.from_contains ? "from " + inp.from_contains : ""
        break
    case "notify_user":
        d.running = "Notifying you"; d.done = "Notified you"
        d.subtitle = String(inp.message || inp.title || "")
        break
    case "ask_user":
        d.running = "Asking you a question"; d.done = "Asked you a question"
        d.subtitle = String(inp.question || "")
        break
    case "schedule_watch":
        d.running = "Setting up a watch"; d.done = "Set up a watch"
        d.subtitle = inp.kind === "email_reply" ? "Watching your inbox" + (inp.from_contains ? " for " + inp.from_contains : "") : String(inp.notify_message || "Watching in the background")
        break
    case "list_apps":
        d.running = "Looking at installed apps"; d.done = "Looked at installed apps"
        break
    case "generate_image":
        d.running = "Creating an image"; d.done = "Created an image"
        d.subtitle = clip(String(inp.prompt || ""), 120)
        break
    default:
        d.running = "Working on " + name.replace(/_/g, " "); d.done = "Finished " + name.replace(/_/g, " ")
    }
    return d
}

// "pending" (awaiting approval) | "running" | "done" | "error" | "denied"
function stepStatus(s) {
    var dec = String(s.decision || "")
    if (dec === "denied" || dec === "expired") return "denied"
    var out = String(s.output || "")
    if (!out.length) return dec === "" ? "pending" : "running"
    var j = parseJson(out)
    if (j && (j.error !== undefined || (typeof j.exit_code === "number" && j.exit_code !== 0))) return "error"
    return "done"
}

// Short human explanation of a failed step (raw daemon error only when showRaw).
function stepError(s, showRaw) {
    var j = parseJson(s.output) || {}
    if (typeof j.exit_code === "number" && j.exit_code !== 0 && j.error === undefined)
        return "The command finished with an error (exit " + j.exit_code + ")" + (showRaw && j.stderr ? ": " + String(j.stderr).trim().slice(-200) : "")
    var e = String(j.error || "").replace(/^[A-Za-z]+(Error|Exception): /, "")
    if (/^Denied by user\/policy/.test(e)) return "Not allowed — skipped"
    if (/^cancelled/.test(e)) return "Stopped"
    if (/^timeout/.test(e)) return "Took too long and was stopped"
    if (/wtype failed/.test(e)) return "Could not type into the app (no virtual keyboard in this session)"
    return e.length ? (showRaw ? e.slice(0, 300) : e.split("\n")[0].slice(0, 160)) : "This step did not work"
}

function statusLabel(st, queueText) {
    var L = { queued: "Getting ready…", running: "Working on it…", waiting_approval: "Needs your permission", waiting_user: "Waiting for your answer",
              done: "Done", failed: "Something went wrong", cancelled: "Stopped" }
    if (st === "queued" && queueText) return "Queued \u2014 " + queueText    // the daemon says WHY (task.queue.text, 1.0-8)
    return L[st] || ""
}

function isActive(st) { return st === "queued" || st === "running" || st === "waiting_approval" || st === "waiting_user" }

function riskLabel(r) {
    return { LOW: "Low risk", MEDIUM: "Medium risk", HIGH: "High risk", CRITICAL: "Critical" }[String(r || "").toUpperCase()] || String(r || "")
}

// ---- generated images. The daemon's generate_image tool answers {"path": "/home/<user>/Pictures/Fab OS/<name>.png", "width",
// "height", "provider", "prompt"}; the final text may mention the path too. The bar shows ONE image card per file, and
// only after the shell confirmed the file exists (imageCheckCommand) — a path the model merely talked about is no card.
function isImagePath(p) { return /\.(png|jpe?g)$/i.test(String(p || "")) }

// every PNG/JPG path under ~/Pictures/Fab OS/ mentioned in a text (~ / $HOME / /home/<user> / file:// forms), each once.
// (The literal lives inside the function: a fresh RegExp per call, no shared lastIndex.)
function imagePathsInText(text) {
    var out = [], seen = {}, m, s = String(text || "")
    var re = /(?:file:\/\/)?((?:~|\$HOME|\/home\/[^\/\s"'`<>]+)\/Pictures\/Fab(?:%20| )OS\/[^\n"'`<>*|]*?\.(?:png|jpe?g))(?!\w)/gi
    while ((m = re.exec(s)) !== null) {
        var p = m[1].replace(/%20/g, " ")
        if (!seen[p]) { seen[p] = true; out.push(p) }
    }
    return out
}

// a finished generate_image step -> {path, prompt, provider, width, height}; null for any other step or an unfinished one
function imageFromStep(s) {
    if (String(s.name || "") !== "generate_image" || stepStatus(s) !== "done") return null
    var out = parseJson(s.output) || {}, inp = parseJson(s.input) || {}
    var p = String(out.path || "")
    if (!isImagePath(p)) return null
    return { path: p, prompt: String(out.prompt || inp.prompt || ""), provider: String(out.provider || ""),
             width: parseInt(out.width, 10) || 0, height: parseInt(out.height, 10) || 0 }
}

// the small provider label under a card: the built-in model by name, a cloud provider by the id the daemon reported
function imageProviderLabel(p) { p = String(p || ""); return p === "local" ? "Built-in model" : p }

// shell word for a user path: a leading ~/ or $HOME/ becomes "$HOME"/ so the SHELL expands the home (never inside the quotes)
function shellPath(p) {
    var m = /^(?:~|\$HOME)\/(.*)$/.exec(String(p || ""))
    return m ? '"$HOME"/' + shellQuote(m[1]) : shellQuote(p)
}
// prints the absolute path when the file exists (~ expanded by the shell), nothing (exit 1) when it does not
function imageCheckCommand(p) { return "P=" + shellPath(p) + "; [ -f \"$P\" ] && printf '%s\\n' \"$P\"" }

// viewer helpers: which binaries the controls depend on (one sh, one line per binary found), and the actions themselves
var VIEWER_BINS = ["wl-copy", "gwenview", "xdg-open", "plasma-apply-wallpaperimage"]
function binsCommand() { return "for b in " + VIEWER_BINS.join(" ") + "; do command -v \"$b\" >/dev/null 2>&1 && printf '%s\\n' \"$b\"; done; echo -" }
function parseBins(out) {
    var found = {}, lines = String(out || "").split("\n")
    for (var i = 0; i < lines.length; i++) { var l = lines[i].trim(); if (l.length && l !== "-") found[l] = true }
    return found
}
function imageMime(p) { return /\.jpe?g$/i.test(String(p || "")) ? "image/jpeg" : "image/png" }
function copyImageCommand(p) { return "wl-copy --type " + imageMime(p) + " < " + shellQuote(p) + " && echo copied" }
function openImageCommand(p, bins) {
    var app = bins["gwenview"] ? "gwenview" : (bins["xdg-open"] ? "xdg-open" : "")
    return app.length ? "setsid -f " + app + " -- " + shellQuote(p) + " >/dev/null 2>&1; echo " + app : ""
}
function wallpaperCommand(p) { return "plasma-apply-wallpaperimage " + shellQuote(p) + " >/dev/null 2>&1 && echo ok" }
// Save as fallback (no file dialog): a copy in ~/Pictures, never overwriting — prints where it went
function saveCopyCommand(p) {
    var b = basename(p)
    return "D=\"$HOME/Pictures\"; mkdir -p \"$D\"; B=" + shellQuote(b) + "; T=\"$D/$B\"; [ -e \"$T\" ] && T=\"$D/${B%.*}-$(date +%H%M%S).${B##*.}\"; cp -- " + shellQuote(p) + " \"$T\" && printf '%s\\n' \"$T\""
}
function copyToCommand(src, dest) { return "cp -- " + shellQuote(src) + " " + shellQuote(dest) + " && printf '%s\\n' " + shellQuote(dest) }
// a FileDialog's selectedFile (file:///home/u/Pictures/Fab%20OS/x.png) -> a local path
function localPath(url) {
    var u = String(url || "")
    if (u.indexOf("file://") === 0) u = u.slice(7)
    try { return decodeURIComponent(u) } catch (e) { return u }
}
// a local path -> a file URL the Image element accepts (spaces and other reserved characters escaped per segment)
function fileUrl(p) { return "file://" + String(p || "").split("/").map(encodeURIComponent).join("/") }

// ---- Markdown-lite -> Qt rich text (bold, italics, inline code, links, bullet/numbered lists, headings, paragraphs)
function escapeHtml(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }

function inlineMd(s, codeBg) {
    var h = escapeHtml(s)
    h = h.replace(/`([^`\n]+)`/g, function (m, c) { return "<code style=\"font-family:'JetBrains Mono',monospace;background-color:" + codeBg + "\">" + c + "</code>" })
    h = h.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    h = h.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>")
    h = h.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, "<a href=\"$2\">$1</a>")
    h = h.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, "$1<a href=\"$2\">$2</a>")
    return h
}

function paragraphs(s, codeBg) {
    var lines = String(s).split("\n"), html = "", list = null
    function closeList() { if (list) { html += "</" + list + ">"; list = null } }
    for (var i = 0; i < lines.length; i++) {
        var l = lines[i], m
        if ((m = /^\s*[-*•]\s+(.*)$/.exec(l))) { if (list !== "ul") { closeList(); html += "<ul>"; list = "ul" } html += "<li>" + inlineMd(m[1], codeBg) + "</li>"; continue }
        if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(l))) { if (list !== "ol") { closeList(); html += "<ol>"; list = "ol" } html += "<li>" + inlineMd(m[1], codeBg) + "</li>"; continue }
        closeList()
        if ((m = /^\s*#{1,6}\s+(.*)$/.exec(l))) { html += "<p><b>" + inlineMd(m[1], codeBg) + "</b></p>"; continue }
        if (!l.trim().length) continue
        var para = [l]
        while (i + 1 < lines.length && lines[i + 1].trim().length && !/^\s*([-*•]|\d+[.)]|#{1,6})\s/.test(lines[i + 1])) para.push(lines[++i])
        html += "<p>" + inlineMd(para.join(" "), codeBg) + "</p>"
    }
    closeList()
    return html
}

// -> [{type:"text", html}, {type:"code", text, lang}] ; fenced ``` blocks become their own monospace cards
function mdBlocks(text, codeBg) {
    var out = [], parts = String(text || "").split(/```/)
    for (var i = 0; i < parts.length; i++) {
        if (i % 2 === 1) {
            var m = /^([a-zA-Z0-9_+-]*)[ \t]*\n/.exec(parts[i]), lang = m ? m[1] : ""
            var code = (m ? parts[i].slice(m[0].length) : parts[i]).replace(/\n+$/, "")
            if (code.length) out.push({ type: "code", text: code, lang: lang })
        } else {
            var html = paragraphs(parts[i], codeBg)
            if (html.length) out.push({ type: "text", html: html })
        }
    }
    return out
}

function plainSummary(text, max) {
    var t = String(text || "").replace(/```[\s\S]*?```/g, " ").replace(/[*`#_]/g, "").replace(/\s+/g, " ").trim()
    return t.length > max ? t.slice(0, max - 1) + "…" : t
}

// fabos-voice status -> {available, stt, tts, mic, micAllowed, service}; exit 127 = binary missing. micAllowed is the
// microphone permission (Fab AI Controls › Settings › Voice, 1.0-8) and service whether fabos-voiced is alive; an older
// fabos-voice without the keys counts as allowed / alive so a half-upgraded machine never gets a dead microphone.
function voiceInfo(exitCode, stdout) {
    var j = parseJson(stdout) || {}
    var stt = String(j.stt || "none")
    return { available: exitCode === 0 && stt !== "none", stt: stt, tts: String(j.tts || "none"), mic: !!j.mic,
             micAllowed: j.mic_allowed === undefined ? true : !!j.mic_allowed, service: j.service === undefined ? true : !!j.service }
}

function lastLine(s) {
    var lines = String(s || "").split("\n"), i
    for (i = lines.length - 1; i >= 0; i--) { var l = lines[i].trim(); if (l.length) return l }
    return ""
}
function clip(s, max) { s = String(s); return s.length > max ? s.slice(0, max - 1) + "\u2026" : s }

// Why the mic button is dimmed after `fabos-voice status` ("" = voice is ready). The status is cached for 30 s only:
// a microphone can be plugged in later, so the bar asks again when the mic is hovered or clicked.
function voiceReason(exitCode, info) {
    if (exitCode === 127) return "Voice is not installed on this machine"
    if (exitCode !== 0) return "Voice is not available right now"
    if (info.micAllowed === false) return MIC_OFF_TIP
    if (!info.mic) return "No microphone found \u2014 plug one in and tap the mic again"
    if (info.stt === "none") return "Speech recognition is not available on this machine"
    return ""
}

// `fabos-voice listen-once` did not return text: the status-line message, from the CLI's own last stderr line mapped to a
// short inline sentence (voiceReasonText) — exit 4 = no microphone / no speech-to-text / microphone off in Settings, and
// the CLI says which; 3 = nothing heard; 127 = binary missing. Never silent: every failure produces a sentence.
function voiceFailure(exitCode, stdout, stderr) {
    var last = lastLine(stderr)
    if (exitCode === 127 || /fabos-voice: (command )?not found/.test(last)) return "Voice is not installed on this machine (fabos-voice is missing)"
    var known = voiceReasonText(last)
    if (known.length) return known
    if (exitCode === 3 || (exitCode === 0 && !String(stdout || "").trim().length)) return "I did not catch that \u2014 tap the mic and speak after the chime"
    if (exitCode === 4) return last.length ? clip(last, 160) : "Voice is not available on this machine"
    return last.length ? clip("Voice did not work just now \u2014 " + last, 160) : "Voice did not work just now. Tap the mic to try again."
}

// The CLI's reasons (packages/fabos-voice/.../phrases.py) as the short sentences the bar shows inline; "" = not one we know
// (the CLI's own sentence is shown then). The memory line carries the CLI's measured figure, never a hard-coded one.
var MIC_OFF_TIP = "Microphone is off in Settings"
function voiceReasonText(reason) {
    var r = String(reason || "")
    if (/Microphone is off in Settings/i.test(r)) return MIC_OFF_TIP + " \u2014 tap the mic to open them"
    if (/No microphone/i.test(r)) return "No microphone found \u2014 plug one in or check Fab Settings \u203a Sound"
    if (/muted or silent/i.test(r)) return "Microphone is silent \u2014 check the input device and its level in the volume applet"
    if (/is muted/i.test(r)) return "Microphone is muted \u2014 unmute it in the volume applet"
    if (/volume is at zero/i.test(r)) return "Microphone volume is at zero \u2014 raise it in the volume applet"
    if (/Not enough free memory/i.test(r)) { var m = /(\d+)\s*MB/.exec(r); return "Speech recognition needs " + (m ? m[1] : "more") + " MB free \u2014 close some apps" }
    if (/No audio session/i.test(r)) return "No audio session \u2014 PipeWire is not running for this login"
    if (/Speech recognition is not available/i.test(r)) return "Speech recognition is not available \u2014 no offline model and no cloud provider key"
    if (/agent is not running/i.test(r)) return "The Fab OS agent service is not running"
    if (/timed out|did not finish in time/i.test(r)) return "Speech recognition took too long \u2014 tap the mic to try again"
    return ""
}

// ---- the listen run (1.0-8). The Plasma executable engine hands a command's output over only when it ends, so the bar
// runs `fabos-voice listen-once --progress FILE` and polls FILE (`cat`, every 150 ms) for the live state: the state machine
// is idle -> starting -> recording (level meter, "speak now") -> transcribing -> done | error, the last word coming from
// the listen command's exit code and output (onListened), never from the file alone.
var LISTEN_PROGRESS = '"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/fabos-voice/askbar-listen.json"'
function listenCommand(timeoutS) {
    return 'F=' + LISTEN_PROGRESS + '; rm -f "$F"; fabos-voice listen-once --timeout ' + (parseInt(timeoutS) || 10) + ' --progress "$F"'
}
function listenProgressCommand() { return 'F=' + LISTEN_PROGRESS + '; cat "$F" 2>/dev/null' }
function voiceProgress(stdout) {
    var j = parseJson(stdout) || {}
    var phase = String(j.state || "")
    if (["starting", "recording", "transcribing", "done", "error"].indexOf(phase) < 0) phase = ""
    var lv = Number(j.level); if (isNaN(lv)) lv = 0
    return { phase: phase, level: Math.max(0, Math.min(1, lv)), peak: Number(j.peak) || 0, speech: !!j.speech,
             reason: String(j.reason || ""), code: j.code === undefined ? -1 : parseInt(j.code), text: String(j.text || "") }
}
function voicePhaseText(phase, speech) {
    if (phase === "starting") return "Starting the microphone\u2026"
    if (phase === "recording") return speech ? "Listening\u2026" : "Listening\u2026 speak now"
    if (phase === "transcribing") return "Understanding what you said\u2026"
    return "Listening\u2026"
}
// What a tap on the mic does: "settings" while the permission is off in Settings (the tap opens Fab AI Controls › Settings ›
// Voice and never records — permissions only enable), else "listen".
function micTapAction(micAllowed) { return micAllowed === false ? "settings" : "listen" }
// After a FAILED listen the bar re-checks `fabos-voice status`; when the wake-word service is down although voice is on and
// the microphone allowed, it restarts the user unit — once a minute at most, and only on that repair path (never on the
// routine 30 s status refresh, which would otherwise restart a unit that is still starting after login).
function voiceServiceRestart(info, voiceOn, lastRestartMs, nowMs) {
    return !!info && info.service === false && !!voiceOn && info.micAllowed !== false && (nowMs - (lastRestartMs || 0)) > 60000
}
