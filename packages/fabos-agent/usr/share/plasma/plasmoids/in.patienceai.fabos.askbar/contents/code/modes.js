.pragma library
// Research · Computer use — the two per-chat capability switches of the Fab OS agent (docs/design/MODES.md).
//
// ONE source of numbers and words for both strips: the ask bar (QML) imports this file; Fab AI Controls (Python,
// command_center.py) reads the TOKENS block below out of the same file — it is strict JSON between the two marker
// comments — so the geometry, the timing and the wording of the two strips cannot drift apart. Pure JavaScript, no QML
// types: tests/askbar-js-test.js runs it under node.
//
// Daemon contract (fabos_agentd.py): settings agent.research / agent.computer_use are the user's defaults ("true" |
// "false"); a chat remembers its own choice on its root task (POST /tasks {research, computer_use} for a new chat,
// PATCH /tasks/{root} {research | computer_use} afterwards); GET /tasks/{id} carries the effective values and
// GET /status carries the defaults under "capabilities".

var TOKENS = /* MODES-TOKENS */ {
    "switch": { "width": 40, "height": 22, "knob": 16, "pad": 3, "track_radius": 11, "off_alpha": 0.28, "focus_ring": 1.5, "hover_lift": 0.08 },
    "motion": { "knob_ms": 200, "colour_ms": 200, "reveal_ms": 180, "easing": "OutCubic" },
    "strip": { "height": 30, "radius": 12, "gap": 16, "label_gap": 8, "icon": 14, "font_px": 12, "confirm_ms": 2400 },
    "modes": [
        { "key": "research", "setting": "agent.research", "label": "Research", "icon": "globe", "glyph": "globe", "default": true,
          "tip_on": "Research is on: the agent may read web pages to answer and lists its sources",
          "tip_off": "Research is off: no web pages are read in this chat" },
        { "key": "computer_use", "setting": "agent.computer_use", "label": "Computer use", "icon": "input-keyboard", "glyph": "keyboard", "default": true,
          "tip_on": "Computer use is on: the agent may open apps and type into them",
          "tip_off": "Computer use is off: nothing is opened or typed in this chat — files and commands only" }
    ]
} /* /MODES-TOKENS */

function modes() { return TOKENS.modes }
function modeByKey(key) {
    for (var i = 0; i < TOKENS.modes.length; i++) if (TOKENS.modes[i].key === key) return TOKENS.modes[i]
    return null
}
function defaults() {
    var s = {}
    for (var i = 0; i < TOKENS.modes.length; i++) s[TOKENS.modes[i].key] = TOKENS.modes[i]["default"] === true
    return s
}
function copyState(s) { var o = {}; for (var k in s) o[k] = s[k]; return o }
// "true" / "false" / true / false / 1 / 0 -> boolean (anything unknown keeps dflt)
function parseBool(v, dflt) {
    if (v === true || v === false) return v
    var t = String(v === undefined || v === null ? "" : v).trim().toLowerCase()
    if (t === "true" || t === "1" || t === "on" || t === "yes") return true
    if (t === "false" || t === "0" || t === "off" || t === "no") return false
    return dflt
}

// GET /settings -> the user's defaults (agent.research, agent.computer_use); missing keys keep the built-in default
function fromSettings(j) {
    var s = defaults()
    if (!j) return s
    for (var i = 0; i < TOKENS.modes.length; i++) { var m = TOKENS.modes[i]; if (j[m.setting] !== undefined) s[m.key] = parseBool(j[m.setting], s[m.key]) }
    return s
}
// GET /status -> j.capabilities {research, computer_use} (the defaults, for a bar with no chat open)
function fromStatus(j) {
    var s = defaults()
    var c = j && j.capabilities ? j.capabilities : null
    if (!c) return s
    for (var i = 0; i < TOKENS.modes.length; i++) { var m = TOKENS.modes[i]; if (c[m.key] !== undefined) s[m.key] = parseBool(c[m.key], s[m.key]) }
    return s
}
// GET /tasks/{id} -> the chat's effective values (the daemon resolves task -> root -> defaults); `fallback` fills what it lacks
function fromTask(t, fallback) {
    var s = copyState(fallback || defaults())
    if (!t) return s
    for (var i = 0; i < TOKENS.modes.length; i++) { var m = TOKENS.modes[i]; if (t[m.key] !== undefined && t[m.key] !== null) s[m.key] = parseBool(t[m.key], s[m.key]) }
    return s
}
// which store a toggle writes to: the open chat's root task, or the user's defaults when no chat is open
function scopeOf(threadId) { return threadId > 0 ? "chat" : "defaults" }
// One toggle -> the new state, the ONE request that persists it and the one-line confirmation the UI shows.
//   in a chat:  PATCH /tasks/{root} {"research": false}          "Research off for this chat"
//   no chat:    PUT /settings {"agent.research": "false"}        "Research off for new chats"
function toggle(state, key, on, threadId) {
    var m = modeByKey(key)
    if (!m) return null
    var next = copyState(state); next[key] = !!on
    var req
    if (threadId > 0) { req = { method: "PATCH", path: "/tasks/" + threadId, body: {} }; req.body[key] = !!on }
    else { req = { method: "PUT", path: "/settings", body: {} }; req.body[m.setting] = on ? "true" : "false" }
    return { state: next, request: req, scope: scopeOf(threadId), message: confirmation(key, on, threadId) }
}
function confirmation(key, on, threadId) {
    var m = modeByKey(key)
    return (m ? m.label : key) + (on ? " on" : " off") + (threadId > 0 ? " for this chat" : " for new chats")
}
// the fields a POST /tasks body carries so a NEW chat starts with the strip's state (a follow-up inherits its chat's)
function taskFields(state) {
    var o = {}
    for (var i = 0; i < TOKENS.modes.length; i++) { var k = TOKENS.modes[i].key; o[k] = state && state[k] !== undefined ? !!state[k] : TOKENS.modes[i]["default"] === true }
    return o
}
// tooltip: what the switch does now, and where the choice is kept
function tooltip(key, on, threadId) {
    var m = modeByKey(key)
    if (!m) return ""
    return (on ? m.tip_on : m.tip_off) + (threadId > 0 ? ". Remembered for this chat" : ". The default for new chats") + " (Space toggles)"
}
// the strip is part of the bar's awake state: shown while the bar is awake (interacted with in the last 30 s), the field has
// focus or a conversation is open; never in the compact (panel) card, where the popup's header carries it instead; and it yields
// to a problem notice (`problem`: the agent service is down, System-Wide AI is off, no provider) — a switch could not be kept then
function stripVisible(awake, focused, panelOpen, compact, problem) { return !compact && !problem && (awake || focused || panelOpen) }
// knob x for an animation progress p in [0, 1] (the same formula the Python switch paints with)
function knobX(p) { var s = TOKENS["switch"]; return s.pad + p * (s.width - s.knob - 2 * s.pad) }
// an error from the daemon while persisting a toggle -> the status-line sentence (never silent)
function failureMessage(key, on, j, code) {
    var m = modeByKey(key), what = (m ? m.label : key) + (on ? " on" : " off")
    if (j && j.error) return "Could not turn " + what + ": " + String(j.error).split("\n")[0].slice(0, 120)
    if (code !== 0) return "Could not turn " + what + " — the agent service did not answer"
    return "Could not turn " + what
}
