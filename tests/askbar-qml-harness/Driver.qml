import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import "../ui" as Ask
import "../ui/agent.js" as Agent

// Headless driver for the ask bar. tests/askbar-qml-test.sh copies the plasmoid into a temp package, appends one
// Loader line to that COPY of main.qml which loads this file and hands over the root item and its ids, then runs the
// package through plasmawindowed with QT_QPA_PLATFORM=offscreen. The driver feeds main.qml the JSON the daemon would
// return for one task and checks the conversation rows it builds, the in-applet response panel (geometry under the
// card, growth / fold animation, no PlasmaCore.Dialog on the desktop, no pointer handler on the transparent strip,
// the containment hit mask = root.contains() is true only over the card + panel stack, which is what plasmashell
// asks to route a right-click), the microphone feedback path (a real `fabos-voice listen-once` inside the image), the
// remembered conversation (incl. the service-not-up-yet retry) and finally the compact form for real: the window is
// shrunk to a panel thickness so `compact` flips, the PlasmaCore.Dialog appears and the mic hint moves into the
// placeholder / tooltip, then grown back. Every ConvoDelegate kind and every AiMark state are instantiated too.
// A 40-row conversation with long lines and one-line code then checks the scroll geometry (see the scroll stage below).
// The polish stages at the end cover the disabled Do it (empty / whitespace field), the cloud hint chip (built-in
// model only, dismissal remembered for the session) and the image cards: a generate_image step pointing at a REAL PNG
// (written by mkpng.py inside the image before plasmawindowed starts, HOME=/tmp) renders one card after the `[ -f ]`
// check, a missing file none, the same file mentioned in the final text no duplicate; a tap opens the 80 % viewer
// (PlasmaCore.Dialog) whose controls follow the binaries found (no wl-copy in the image => Copy image disabled with a
// reason; gwenview + plasma-apply-wallpaperimage present => enabled), Save as falls back to a real copy in ~/Pictures,
// Regenerate posts the follow-up and closes the viewer.
// Prints PASS/FAIL lines and "HARNESS DONE failures=N", renders /out/askbar-{bar,panel,feed,scroll-bottom,scroll-top,
// image-card,image-viewer}.png, then stops plasmawindowed.
Item {
    id: h
    property var bar: null
    property var convo: null
    property var card: null
    property var panel: null
    property var panelMain: null
    property var popup: null
    property var statusText: null
    property var field: null
    property var vbar: null          // the list's overlay ScrollBar (main.qml id vbar)
    property var panelHeader: null   // fixed control row above the list
    property var panelFoot: null     // fixed foot (typing dots) below the list
    property var go: null            // the Do it / Send pill and its MouseArea
    property var goArea: null
    property var micButton: null
    property var cloudHint: null     // the cloud hint chip, its text and its Choose action
    property var cloudHintText: null
    property var cloudChoose: null
    property var viewer: null        // the image viewer's Loader (main.qml id viewerLoader)
    property var list: null
    property int failures: 0
    property int grabsPending: 0
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function kinds() { var k = []; for (var i = 0; i < convo.count; i++) k.push(convo.get(i).kind); return k.join(",") }
    function rowAt(i) { return convo.get(i) }
    // plasmawindowed opens the applet at its Layout.minimum size (396x128 here) while the desktop layout gives the strip
    // sh*0.66: the harness sizes the applet like a small home screen before the checks (700x640; Screen is 800x600 offscreen)
    onListChanged: if (bar && convo && card && panel && panelMain && popup && statusText && field && vbar && panelHeader && panelFoot && go && goArea && micButton && cloudHint && cloudHintText && cloudChoose && viewer && list) {
        bar.Layout.minimumWidth = 700; bar.Layout.minimumHeight = 640     // plasmawindowed sizes its window from these hints
        var w = h.Window.window; if (w) { w.minimumWidth = 700; w.minimumHeight = 640; w.width = 700; w.height = 640 }
        startTimer.start()
    }
    Timer { id: startTimer; interval: 400; onTriggered: h.run() }
    P5Support.DataSource { id: killer; engine: "executable"; connectedSources: [] }

    // static instances of every helper component (compile + instantiate check)
    Row {
        y: 1000; spacing: 8
        Ask.AiMark { width: 32; height: 32; markState: "idle" }
        Ask.AiMark { width: 32; height: 32; markState: "thinking" }
        Ask.AiMark { width: 32; height: 32; markState: "listening" }
        Ask.AiMark { width: 32; height: 32; markState: "done" }
        Ask.AiMark { width: 32; height: 32; markState: "error"; showDot: true }
        Ask.AiMark { width: 32; height: 32; markState: "idle"; awake: false }
        Ask.TypingDots { }
        Ask.Spinner { width: 18; height: 18 }
        Ask.IconButton { icon: "edit-copy"; tip: "Copy" }
        Ask.IconButton { icon: "dialog-cancel"; tip: "Deny"; danger: true; active: false }
        Ask.IconButton { icon: "audio-input-microphone"; tip: "Mic"; dim: true }
    }

    // every ConvoDelegate kind, outside a ListView (required properties set explicitly)
    Component { id: delegateComp; Ask.ConvoDelegate { } }
    function makeRow(o) {
        var base = { index: 0, kind: "note", key: "k", name: "", title: "", running: "", done: "", subtitle: "", narration: "", icon: "", iconFallback: "",
                     appIcon: false, status: "", text: "", typed: "", risk: "", group: -1, shown: true, count: 0, expanded: false, approvalId: 0, questionId: 0, current: false }
        for (var k in o) base[k] = o[k]
        base.width = 600
        return delegateComp.createObject(h, base)
    }

    // the daemon's JSON for one task at successive moments (1 running, 2 waiting_approval, 2.5 waiting_user, 3 done)
    function taskJson(phase) {
        var steps = [
            { id: 101, task_id: 7, kind: "tool_call", name: "open_app", input: JSON.stringify({ app: "kate", args: ["~/Documents/note.txt"] }), output: phase >= 2 ? JSON.stringify({ launched: "kate", pid: 44 }) : "", risk: "LOW", decision: "auto-approved", narration: "Opening Fab Editor for you now." }
        ]
        if (phase >= 2) {
            steps.push({ id: 102, task_id: 7, kind: "tool_call", name: "type_text", input: JSON.stringify({ text: "Hello from Fab OS", press_enter: true }), output: phase >= 3 ? JSON.stringify({ typed: 17 }) : "", risk: "LOW", decision: "auto-approved", narration: "Typing your note into Fab Editor." })
            steps.push({ id: 103, task_id: 7, kind: "tool_call", name: "run_shell", input: JSON.stringify({ command: "rm -rf ~/old-notes" }), output: phase >= 3 ? JSON.stringify({ error: "Denied by user/policy (HIGH: deletes files)" }) : "", risk: "HIGH", decision: phase >= 3 ? "denied" : "" })
        }
        if (phase >= 2.5) steps.push({ id: 104, task_id: 7, kind: "question", name: "ask_user", input: "Which folder should I use instead?", output: "", risk: "", decision: "" })
        if (phase >= 3) {
            steps.push({ id: 105, task_id: 7, kind: "answer", name: "user", input: "Use ~/Notes", output: "", risk: "", decision: "" })   // Store.step(task, "answer", "user", text) => the text is `input`
            steps.push({ id: 106, task_id: 7, kind: "assistant", name: "fake", input: "", output: "Done. I opened **Fab Editor** and typed your note.\n\n- opened `note.txt`\n- typed 17 characters\n\n```bash\nls ~/Notes\n```", risk: "", decision: "" })
            steps.push({ id: 107, task_id: 7, kind: "final", name: "", input: "", output: "Done. I opened **Fab Editor** and typed your note.\n\n- opened `note.txt`\n- typed 17 characters\n\n```bash\nls ~/Notes\n```", risk: "", decision: "" })
        }
        var approvals = phase >= 2 ? [{ id: 9, task_id: 7, step_id: 103, tool: "run_shell", input: JSON.stringify({ command: "rm -rf ~/old-notes" }), risk: "HIGH", reason: "deletes files", status: phase >= 3 ? "denied" : "pending" }] : []
        var questions = phase >= 3 ? [{ id: 5, task_id: 7, question: "Which folder should I use instead?", answer: "Use ~/Notes" }]
                      : (phase >= 2.5 ? [{ id: 5, task_id: 7, question: "Which folder should I use instead?", answer: null }] : [])
        var status = phase >= 3 ? "done" : (phase >= 2.5 ? "waiting_user" : (phase >= 2 ? "waiting_approval" : "running"))
        return { id: 7, title: "Open the editor and type hello", request: "Open the editor and type hello", status: status,
                 result: phase >= 3 ? "Done. I opened **Fab Editor** and typed your note." : null, steps: steps, approvals: approvals, questions: questions, watches: [] }
    }

    // ---- the transparent strip: every MouseArea in the applet must lie inside the card or the panel
    function inside(it, host) {
        var p = it.mapToItem(host, 0, 0)
        return p.x >= -0.5 && p.y >= -0.5 && p.x + it.width <= host.width + 0.5 && p.y + it.height <= host.height + 0.5
    }
    function strayMouseAreas() {
        var bad = []
        function walk(it) {
            for (var i = 0; i < it.children.length; i++) {
                var c = it.children[i]
                if (c === h) continue
                var s = String(c)
                if (s.indexOf("QQuickMouseArea") === 0 && c.width > 0 && c.height > 0 && !(inside(c, card) || inside(c, panel))) bad.push(s)
                walk(c)
            }
        }
        walk(bar)
        return bad
    }
    function rootHandlers() {   // pointer handlers attached to the root item itself would cover the whole strip
        var found = []
        for (var i = 0; i < bar.data.length; i++) { var s = String(bar.data[i]); if (/^QQuick(Hover|Tap|Point|Drag|Pinch|Wheel)Handler/.test(s)) found.push(s) }
        return found
    }

    function run() {
        var w = h.Window.window
        console.log("window " + (w ? Math.round(w.width) + "x" + Math.round(w.height) : "none") + ", applet " + Math.round(bar.width) + "x" + Math.round(bar.height) + ", screen " + Screen.width + "x" + Screen.height
                    + ", card " + Math.round(card.width) + "x" + Math.round(card.height) + " at " + Math.round(card.x) + "," + Math.round(card.y) + ", maxPanelHeight " + bar.maxPanelHeight)
        check(bar.onDesktop === true, "tall strip => desktop mode")
        check(bar.height >= card.height + 8 + 120, "the strip leaves room for the panel under the card (" + Math.round(bar.height - card.height) + " px)")
        check(card.y === 0, "card sits at the top of the strip")
        check(popup.active === false && popup.item === null, "no PlasmaCore.Dialog exists on the desktop (popup loader inactive)")
        check(panel.x === card.x && panel.width === card.width, "panel takes the card's x and width")
        check(panel.y === card.y + card.height + 8, "panel.y == card.y + card.height + 8 (" + panel.y + ")")
        check(panel.height === 0 && !panel.visible, "panel is folded (0 px, hidden) with no conversation")
        check(bar.childAt(2, 2) === null && bar.childAt(2, bar.height - 2) === null, "empty strip corners have no item under the pointer")
        check(rootHandlers().length === 0, "no pointer handler on the strip itself")
        check(bar.containmentMask !== null && bar.containmentMask !== undefined, "root carries a containmentMask (plasmashell routes clicks by root.contains())")
        check(bar.contains(Qt.point(2, 2)) === false && bar.contains(Qt.point(2, bar.height - 2)) === false && bar.contains(Qt.point(bar.width - 2, 2)) === false,
              "hit mask: the transparent strip corners are NOT part of the applet (a right-click there is the desktop's)")
        check(bar.contains(Qt.point(card.x + 5, 5)) === true && bar.contains(Qt.point(card.x + card.width - 5, card.height - 5)) === true, "hit mask: the card IS part of the applet")
        check(bar.contains(Qt.point(card.x - 3, 5)) === false && bar.contains(Qt.point(card.x + card.width + 3, 5)) === false, "hit mask: beside the card is the desktop's")
        check(bar.contains(Qt.point(card.x + 5, card.height + 40)) === false, "hit mask: with the panel folded the space under the card is the desktop's")
        check(bar.markState === "idle" || bar.markState === "error", "mark idle/error at start with no daemon (" + bar.markState + ")")
        var rows = [
            makeRow({ kind: "user", text: "Open the editor and type hello", status: "request" }),
            makeRow({ kind: "assistant", text: "Hi **there** `x`\n\n- a\n- b\n\n```py\nprint(1)\n```", status: "final" }),
            makeRow({ kind: "tools", count: 3, expanded: true, status: "running", group: 0 }),
            makeRow({ kind: "step", name: "open_app", running: "Opening Fab Editor", done: "Opened Fab Editor", icon: "kate", iconFallback: "window-new", appIcon: true, status: "running", narration: "Opening Fab Editor for you now.", current: true, group: 0 }),
            makeRow({ kind: "step", name: "type_text", running: "Typing into Fab Editor", done: "Typed into Fab Editor", icon: "input-keyboard", iconFallback: "system-run", status: "running", typed: "Hello from Fab OS", subtitle: "…then Enter", group: 0 }),
            makeRow({ kind: "step", name: "run_shell", running: "Running a command", done: "Ran a command", icon: "utilities-terminal", iconFallback: "system-run", status: "denied", subtitle: "Not allowed — skipped", group: 0 }),
            makeRow({ kind: "approval", approvalId: 9, title: "Running a command", risk: "HIGH", narration: "deletes files", status: "pending", icon: "utilities-terminal", iconFallback: "system-run" }),
            makeRow({ kind: "question", questionId: 5, text: "Which folder should I use instead?", status: "pending" }),
            makeRow({ kind: "error", text: "The model declined this request (policy)." }),
            makeRow({ kind: "note", text: "Trying again…" }),
            makeRow({ kind: "image", text: h.imgPath, subtitle: "a prompt", name: "local", title: "640 × 400", status: "ready" })
        ]
        var ok = true
        for (var i = 0; i < rows.length; i++) { if (!rows[i]) ok = false; else rows[i].y = 2000 }
        check(ok, "every ConvoDelegate kind instantiates (" + rows.length + " kinds)")
        // the microphone, for real: fabos-voice listen-once runs inside the image (no PipeWire / no capture device there)
        bar.startListening()
        check(bar.listening === true && bar.markState === "listening", "mic tap starts listening: red dot state on the mark")
        check(statusText.visible && statusText.text === "Listening…", "status line says Listening… while recording")
        // a submit with no daemon: curl fails, the request stays in the bar (checked in stage 2)
        field.text = "Open the editor and type hello"
        bar.daemonUp = true; bar.configured = true; bar.aiEnabled = true
        bar.submit()
        check(bar.sending === true && field.text === "", "submit posts and clears the field while sending")
        stage2.start()
    }
    Timer { id: stage2; interval: 2500; onTriggered: {
        check(bar.sending === false && field.text === "Open the editor and type hello", "no daemon: request restored to the bar, status says so (" + bar.status.slice(0, 40) + ")")
        check(bar.panelMode === "closed", "no panel without a task")
        check(bar.listening === false && bar.voiceHint.length > 0, "real fabos-voice listen-once failed here and the reason is in the status line: \"" + bar.voiceHint + "\"")
        check(statusText.visible && statusText.text === bar.voiceHint, "status line shows the voice reason")
        // synthetic CLI outcomes: the CLI's own last stderr line is what the user reads
        bar.handle("listen", 0, 4, "", "No microphone found on this computer.")
        check(bar.voiceHint === "No microphone found on this computer.", "exit 4: stderr line becomes the status (" + bar.voiceHint + ")")
        bar.handle("listen", 0, 4, "", "chime\nSpeech recognition is not available: no offline model and no cloud provider key.")
        check(bar.voiceHint === "Speech recognition is not available: no offline model and no cloud provider key.", "exit 4: the LAST stderr line is used")
        bar.handle("listen", 0, 127, "", "sh: 1: fabos-voice: not found")
        check(bar.voiceHint === "Voice is not installed on this machine (fabos-voice is missing)", "exit 127 => voice not installed")
        bar.handle("listen", 0, 3, "", "Sorry, I did not catch that. Say it once more?")
        check(bar.voiceHint === "I did not catch that. Tap the mic and try again.", "exit 3 => try-again hint")
        bar.handle("listen", 0, 1, "", "Traceback…\nRuntimeError: boom")
        check(bar.voiceHint === "Voice did not work just now — RuntimeError: boom", "other failures quote the last stderr line")
        bar.voiceHint = ""
        // fabos-voice status is cached for 30 s only
        bar.handle("voicestatus", 0, 127, "")
        check(bar.voiceChecked && !bar.voiceAvailable && bar.voiceReason === "Voice is not installed on this machine", "missing fabos-voice => mic dimmed with a reason")
        bar.handle("voicestatus", 0, 0, JSON.stringify({ wake: false, listening: false, stt: "whisper.cpp", tts: "espeak-ng", mic: false }))
        check(bar.voiceAvailable && bar.voiceReason.indexOf("No microphone found") === 0, "STT but no mic => reason names the microphone (" + bar.voiceReason + ")")
        bar.handle("voicestatus", 0, 0, JSON.stringify({ wake: false, listening: false, stt: "whisper.cpp", tts: "espeak-ng", mic: true }))
        check(bar.voiceAvailable && bar.voiceReason === "", "fabos-voice status with STT + mic => mic ready")
        var s0 = bar.serial
        bar.refreshVoice(false)
        check(bar.serial === s0, "a fresh status (< 30 s) is not asked again")
        bar.voiceStatusAt = Date.now() - 31000
        bar.refreshVoice(false)
        check(bar.serial === s0 + 1, "a status older than 30 s is asked again (a mic can be plugged in later)")
        field.text = ""
        // drive the state machine exactly as onNewData would
        bar.pendingRequest = "Open the editor and type hello"
        bar.onCreated(false, 0, { id: 7, status: "queued", parent_id: null })
        check(bar.rootTaskId === 7 && bar.taskId === 7, "task 7 becomes root + polled task")
        check(bar.panelMode === "open", "panel opens on create (" + bar.panelMode + ")")
        check(panel.height < 10, "panel starts at 0 px and grows (height now " + panel.height + ")")
        var saved = bar.savedTask()
        check(saved.root === 7 && saved.task === 7, "conversation remembered in Plasmoid.configuration (" + JSON.stringify(saved) + ")")
        check(kinds() === "user", "one user row after create: " + kinds())
        bar.ingest(taskJson(1))
        check(kinds() === "user,tools,step", "phase 1 rows: " + kinds())
        check(rowAt(2).status === "running" && rowAt(2).running === "Opening Fab Editor" && rowAt(2).icon === "kate" && rowAt(2).current === true, "open_app step is running/current with the app icon")
        check(rowAt(2).narration === "Opening Fab Editor for you now.", "narration carried onto the step row")
        check(bar.markState === "thinking", "mark thinking while running (" + bar.markState + ")")
        bar.ingest(taskJson(1))
        check(kinds() === "user,tools,step", "re-ingesting the same JSON appends nothing: " + kinds())
        bar.ingest(taskJson(2))
        check(kinds() === "user,tools,step,step,step,approval", "phase 2 rows: " + kinds())
        check(rowAt(2).status === "done" && rowAt(2).current === false, "open_app flipped to done, no longer current")
        check(rowAt(3).typed === "Hello from Fab OS" && rowAt(3).running === "Typing into Fab Editor", "type_text step carries the typed text and the target app")
        check(rowAt(4).status === "pending" && rowAt(4).subtitle === "", "run_shell awaiting approval, raw command hidden (ui.show_raw off)")
        check(rowAt(5).kind === "approval" && rowAt(5).risk === "HIGH" && rowAt(5).status === "pending" && rowAt(5).approvalId === 9, "approval card pending with HIGH risk")
        check(rowAt(1).count === 3 && rowAt(1).status === "running", "group chip counts 3 actions while running")
        check(bar.taskStatus === "waiting_approval" && bar.taskActive, "task waiting_approval is active")
        bar.decide(9, "denied")
        check(rowAt(5).status === "denied", "local Deny marks the approval row")
        bar.ingest(taskJson(2.5))
        check(kinds() === "user,tools,step,step,step,approval,question", "waiting_user adds one question row: " + kinds())
        bar.answer("Use ~/Notes")
        check(rowAt(6).status === "answered" && rowAt(7).kind === "user" && rowAt(7).status === "answer", "answer marks the question and appends the user's answer")
        bar.ingest(taskJson(3))
        check(kinds() === "user,tools,step,step,step,approval,question,user,assistant", "phase 3 rows (the daemon's answer step binds to the locally appended answer; no blank duplicate pill): " + kinds())
        check(rowAt(7).key === "t7s105" && rowAt(7).text === "Use ~/Notes", "answer row is now keyed by the daemon's step id")
        check(rowAt(8).status === "final" && rowAt(8).text.indexOf("**Fab Editor**") > 0, "assistant row promoted to final (no duplicate final row)")
        check(rowAt(4).status === "denied" && rowAt(4).subtitle === "Not allowed — skipped", "denied run_shell shows the friendly reason")
        check(rowAt(1).expanded === false && rowAt(2).shown === false, "groups fold when the task finishes")
        check(bar.taskStatus === "done" && !bar.taskActive && bar.resultText.length > 0, "task done, result captured")
        check(bar.markState === "done", "mark shows done (" + bar.markState + ")")
        bar.toggleGroup(0)
        check(rowAt(1).expanded === true && rowAt(2).shown === true, "chip click expands the group")
        bar.ingest(taskJson(3))
        check(rowAt(1).expanded === true, "a trailing poll does not re-fold an expanded group")
        var ext = taskJson(3)   // an answer given elsewhere (Fab AI Controls / CLI) has no local row: it is appended once, from `input`
        ext.steps.push({ id: 108, task_id: 7, kind: "answer", name: "user", input: "Answered from Fab AI Controls", output: "", risk: "", decision: "" })
        bar.ingest(ext)
        check(convo.count === 10 && rowAt(9).kind === "user" && rowAt(9).status === "answer" && rowAt(9).text === "Answered from Fab AI Controls", "external answer appended once from input (" + kinds() + ")")
        // follow-up threads under the root
        bar.pendingRequest = "and now save it"
        bar.onCreated(true, 0, { id: 8, status: "queued", parent_id: 7 })
        check(bar.rootTaskId === 7 && bar.taskId === 8 && kinds().split(",").length === 11, "follow-up keeps root 7, polls 8, appends one user row (" + kinds() + ")")
        saved = bar.savedTask()
        check(saved.root === 7 && saved.task === 8, "remembered ids follow the follow-up (" + JSON.stringify(saved) + ")")
        bar.ingest({ id: 8, status: "failed", result: null, error: "boom", steps: [{ id: 200, task_id: 8, kind: "error", name: "", input: "", output: "The model declined this request (policy)." }], approvals: [], questions: [] })
        check(rowAt(11).kind === "error" && bar.markState === "error", "failed task shows the error row and an amber mark (" + bar.markState + ")")
        check(rowAt(1).expanded === true && rowAt(2).shown === true, "a later task finishing does not fold the earlier group the user opened")
        bar.handle("retry", 0, 0, JSON.stringify({ id: 9, retry_of: 8, parent_id: 7 }))
        check(bar.taskId === 9 && bar.rootTaskId === 7, "retry of a follow-up keeps the root")
        bar.handle("retry", 0, 0, JSON.stringify({ id: 10, retry_of: 7, parent_id: null }))
        check(bar.rootTaskId === 10, "retry of the root re-roots the conversation")
        bar.typeInto("save it as notes")            // the voice transcript path: typewriter into the field, then submit
        bar.handle("status", 0, 0, JSON.stringify({ mode: "auto", provider_ready: false, ai_enabled: true, tasks: {}, pending_approvals: 0 }))
        check(!bar.configured && bar.status.indexOf("No AI provider") === 0, "status without a provider shows the settings hint")
        // Escape / a click on the mark fold the panel to the pill and unfold it again
        bar.toggleCollapse()
        check(bar.panelMode === "min", "mark click / Escape folds the panel to the pill")
        bar.toggleCollapse()
        check(bar.panelMode === "open", "a second click unfolds it")
        bar.panelMode = "min"
        check(bar.panelMode === "min" && panel.contentTarget === 40, "minimized: the panel's target is the 40 px pill (" + panel.contentTarget + ")")
        minTimer.start()
    } }
    Timer { id: minTimer; interval: 600; onTriggered: {
        check(panel.visible && Math.abs(panel.height - 40) <= 1, "panel stays inside the applet and folds to the pill on Minimize (" + panel.height + " px)")
        check(panel.y === card.y + card.height + 8 && panel.width === card.width, "pill keeps the panel's place under the card")
        bar.panelMode = "open"
        bar.taskStatus = "running"; bar.taskId = 10          // show the live state (spinner, typing dots) in the render
        grabTimer.start()
    } }
    Timer { id: grabTimer; interval: 1200; onTriggered: {
        check(field.text === "save it as notes", "typewriter revealed the whole transcript (" + field.text + ")")
        check(panel.height > 100 && Math.abs(panel.height - panel.contentTarget) <= 1 && panel.height <= bar.maxPanelHeight, "panel height animated to its content (" + panel.height + " of max " + bar.maxPanelHeight + " px)")
        check(panel.y + panel.height <= bar.height, "panel ends inside the strip")
        var stray = strayMouseAreas()
        check(stray.length === 0, "every MouseArea lies inside the card or the panel (stray: " + stray.length + ")")
        check(bar.childAt(2, 2) === null, "top-left of the strip is still empty with the panel open")
        check(bar.contains(Qt.point(card.x + 5, panel.y + panel.height - 5)) === true && bar.contains(Qt.point(card.x + card.width - 5, panel.y + 5)) === true, "hit mask: the open panel IS part of the applet")
        check(bar.contains(Qt.point(card.x + 5, card.height + 4)) === true, "hit mask: the 8 px gap between card and panel belongs to the stack")
        check(bar.contains(Qt.point(card.x + 5, Math.min(bar.height - 2, panel.y + panel.height + 4))) === false, "hit mask: under the open panel is the desktop's")
        check(bar.contains(Qt.point(card.x - 3, panel.y + 20)) === false && bar.contains(Qt.point(card.x + card.width + 3, panel.y + 20)) === false && bar.contains(Qt.point(2, 2)) === false,
              "hit mask: beside the open panel and the strip corners stay the desktop's")
        h.grabsPending = 2
        bar.grabToImage(function (r) { r.saveToFile("/out/askbar-bar.png"); console.log("PASS grabbed bar (the whole strip: card + panel stack)"); if (--h.grabsPending === 0) feedTimer.start() })
        panel.grabToImage(function (r) { r.saveToFile("/out/askbar-panel.png"); console.log("PASS grabbed panel (scrolled to the end)"); if (--h.grabsPending === 0) feedTimer.start() })
    } }
    Timer { id: feedTimer; interval: 100; onTriggered: { list.follow = false; list.positionViewAtBeginning(); feedGrab.start() } }
    Timer { id: feedGrab; interval: 400; onTriggered: {
        check(list.atYBeginning, "list scrolled to the top for the feed render")
        panel.grabToImage(function (r) { r.saveToFile("/out/askbar-feed.png"); console.log("PASS grabbed feed (top: request, chip, live step cards)"); scrollStage.start() })
    } }

    // ---- scroll geometry and growth: a fresh conversation grows the panel row by row (no scrollbar, bottom anchored
    // while the height animates), then a 40-row task with long lines and one-line code pushes it to the strip's max:
    // the 6 px overlay bar appears in the 14 px right gutter with every row clear of it, the header row stays fixed at
    // y = 0, the fixed foot stays under the list, the view follows the newest row only while the user is at the end.
    // Renders /out/askbar-scroll-{bottom,top}.png.
    readonly property string longLine: "This is a deliberately long assistant line that must wrap inside the content width minus the gutter and never run under the scrollbar; it keeps going so that it wraps several times — "
    readonly property string codeLine: "for f in $(ls ~/Notes/very/long/path/that/keeps/going/and/going/to/force/horizontal/scrolling/inside/the/card); do printf '%s\\n' \"$f\"; done  # one line, no wrap"
    function longTask(id, turns, firstStep) {
        var steps = [], sid = firstStep
        for (var i = 0; i < turns; i++) {
            steps.push({ id: sid++, task_id: id, kind: "tool_call", name: "run_shell", input: JSON.stringify({ command: "ls ~/Notes" }), output: JSON.stringify({ stdout: "a.txt" }), risk: "LOW", decision: "auto-approved", narration: "Looking at what is in the folder." })
            steps.push({ id: sid++, task_id: id, kind: "assistant", name: "fake", input: "", output: "Turn " + (i + 1) + ": " + h.longLine + h.longLine + "\n\n```bash\n" + h.codeLine + "\n" + h.codeLine + "\n```", risk: "", decision: "" })
        }
        return { id: id, title: "Long one", request: "Tell me everything about my Notes folder, in detail", status: "running", result: null, steps: steps, approvals: [], questions: [] }
    }
    function shortTask(id, n, firstStep) {   // n short assistant rows
        var steps = []
        for (var i = 0; i < n; i++) steps.push({ id: firstStep + i, task_id: id, kind: "assistant", name: "fake", input: "", output: "Short line " + (i + 1) + ".", risk: "", decision: "" })
        return { id: id, title: "Long one", request: "Tell me everything about my Notes folder, in detail", status: "running", result: null, steps: steps, approvals: [], questions: [] }
    }
    function delegates() { var d = []; for (var i = 0; i < list.contentItem.children.length; i++) { var c = list.contentItem.children[i]; if (c.kind !== undefined && c.width > 0) d.push(c) } return d }
    // originY: with 40+ rows the ListView estimates the extent of rows it has not created yet and moves its origin
    function bottomAnchored() { return Math.abs((list.contentY - list.originY) - Math.max(0, list.contentHeight - list.height)) <= 1.5 }
    property real smallHeight: 0
    Timer { id: scrollStage; interval: 100; onTriggered: {
        bar.resetConversation()
        bar.rootTaskId = 30; bar.taskId = 30; bar.taskRequest = "Tell me everything about my Notes folder, in detail"
        convo.append(bar.row({ kind: "user", key: "u30", text: bar.taskRequest, status: "request" }))
        bar.taskStatus = "queued"
        check(list.follow === true, "a fresh conversation follows the end")
        check(panelHeader.y === 0 && panelHeader.parent !== list && panelHeader.parent !== list.contentItem, "header row is outside the scroll area, at y = 0")
        check(panelFoot.parent !== list && panelFoot.parent !== list.contentItem && panelFoot.parent === panelHeader.parent, "foot is outside the scroll area, a sibling of the header")
        scrollSmall.start()
    } }
    Timer { id: scrollSmall; interval: 450; onTriggered: {
        h.smallHeight = panel.height
        check(Math.abs(panel.height - panel.contentTarget) <= 1 && panel.height < bar.maxPanelHeight - 40, "one row: the panel shrank to its content, far below the max (" + Math.round(panel.height) + " of " + bar.maxPanelHeight + " px)")
        check(!panel.overflowing && !vbar.visible, "below the max: no scrollbar")
        check(panelFoot.height === 26 && list.y + list.height <= panelFoot.y + 0.5, "working: the 26 px foot with the typing dots sits under the list (foot.y " + Math.round(panelFoot.y) + ", list bottom " + Math.round(list.y + list.height) + ")")
        bar.ingest(shortTask(30, 3, 400))
        scrollMid.start()
    } }
    Timer { id: scrollMid; interval: 90; onTriggered: {
        check(panel.height > h.smallHeight && panel.height < panel.contentTarget - 1, "three rows in: the panel is mid-growth (" + Math.round(panel.height) + " -> " + Math.round(panel.contentTarget) + " px)")
        check(bottomAnchored(), "mid-growth the newest row stays anchored at the bottom edge (contentY " + (list.contentY - list.originY).toFixed(1) + ", contentHeight - height " + (list.contentHeight - list.height).toFixed(1) + ")")
        check(!vbar.visible, "mid-growth: still no scrollbar")
        scrollBig.start()
    } }
    Timer { id: scrollBig; interval: 400; onTriggered: {
        check(Math.abs(panel.height - panel.contentTarget) <= 1 && panel.height < bar.maxPanelHeight && panel.height > h.smallHeight + 40, "grew to fit the new rows, still under the max (" + Math.round(panel.height) + " px)")
        check(!vbar.visible && list.atYEnd, "fits: no scrollbar, view at the end")
        bar.ingest(longTask(30, 13, 500))
        check(convo.count >= 40, "40-row conversation ingested (" + convo.count + " rows)")
        scrollEnd.start()
    } }
    Timer { id: scrollEnd; interval: 700; onTriggered: {
        var right = list.width - list.gutter
        check(Math.abs(panel.height - bar.maxPanelHeight) <= 1 && panel.overflowing, "at the max height the panel stops growing (" + Math.round(panel.height) + " px) and the list scrolls")
        check(vbar.visible && vbar.size < 1 && vbar.width === 6, "scrollbar appears once at max: overlay, 6 px wide (size " + vbar.size.toFixed(2) + ")")
        check(vbar.x >= right - 0.5 && vbar.x + vbar.width <= list.width + 0.5, "scrollbar x (" + Math.round(vbar.x) + ") >= content right edge (" + Math.round(right) + ") inside the 14 px gutter")
        var ds = delegates(), wideOk = true, underBar = 0, widest = 0
        for (var i = 0; i < ds.length; i++) { widest = Math.max(widest, ds[i].width); if (ds[i].width > right + 0.5) wideOk = false; if (ds[i].x + ds[i].width > vbar.x + 0.5) underBar++ }
        check(ds.length >= 3 && wideOk && underBar === 0, ds.length + " live delegates: none wider than list.width - gutter (widest " + Math.round(widest) + " of " + Math.round(right) + "), none under the bar")
        check(list.atYEnd && bottomAnchored(), "the view followed the newest row to the end (contentY - originY " + (list.contentY - list.originY).toFixed(1) + ", contentHeight - height " + (list.contentHeight - list.height).toFixed(1) + ")")
        check(panelHeader.y === 0 && panelHeader.visible && panelHeader.height > 0 && list.y >= panelHeader.height, "header row still at y = 0 above the list after following to the end")
        check(list.y + list.height <= panelFoot.y + 0.5 && panelFoot.y + panelFoot.height <= panelFoot.parent.height + 0.5, "fixed foot under the list, inside the panel")
        // long text wraps inside the row; code keeps its line and scrolls sideways inside its own card
        var textBlocks = 0, codeCards = 0, wrapped = 0, wideCards = 0, cardsFit = 0
        for (var d = 0; d < ds.length; d++) {
            if (ds[d].kind !== "assistant" || !ds[d].children[0] || !ds[d].children[0].item) continue
            var col = ds[d].children[0].item
            for (var c = 0; c < col.children.length; c++) {
                var it = col.children[c].item
                if (!it) continue
                if (it.wide !== undefined) { codeCards++; if (it.wide && it.overflow > 0) wideCards++; if (it.width <= ds[d].width + 0.5) cardsFit++ }
                else if (it.lineCount !== undefined) { textBlocks++; if (it.contentWidth <= it.width + 0.5 && it.contentHeight > it.font.pixelSize * 2 && it.width <= ds[d].width + 0.5) wrapped++ }   // RichText has no lineCount: judge by the laid-out size
            }
        }
        check(textBlocks > 0 && wrapped === textBlocks, "long assistant text wraps inside the row width (" + wrapped + "/" + textBlocks + " blocks: contentWidth <= width, several lines tall)")
        check(codeCards > 0 && wideCards === codeCards && cardsFit === codeCards, "one-line code overflows sideways inside its card (" + wideCards + "/" + codeCards + " cards), none widens the row")
        panel.grabToImage(function (r) { r.saveToFile("/out/askbar-scroll-bottom.png"); console.log("PASS grabbed scroll-bottom (40 rows, bar in the gutter, header fixed)"); scrollTop.start() })
    } }
    Timer { id: scrollTop; interval: 100; onTriggered: { list.follow = false; list.positionViewAtBeginning(); scrollTopCheck.start() } }
    Timer { id: scrollTopCheck; interval: 300; onTriggered: {
        check(list.atYBeginning && !list.atYEnd, "scrolled to the top")
        check(panelHeader.y === 0 && Math.abs(panel.height - bar.maxPanelHeight) <= 1, "header still at y = 0, panel height unchanged by scrolling")
        var ds = delegates(), underBar = 0
        for (var i = 0; i < ds.length; i++) if (ds[i].x + ds[i].width > vbar.x + 0.5) underBar++
        check(ds.length >= 3 && underBar === 0, "at the top: " + ds.length + " live delegates, none under the bar")
        bar.ingest(shortTask(30, 1, 600))     // a new row while the user reads the top
        scrollKeep.start()
    } }
    Timer { id: scrollKeep; interval: 300; onTriggered: {
        check(list.atYBeginning && !list.atYEnd && !list.follow, "a new row does not yank the view while the user is scrolled up")
        panel.grabToImage(function (r) { r.saveToFile("/out/askbar-scroll-top.png"); console.log("PASS grabbed scroll-top (request pill clear of the gutter, header fixed)"); list.positionViewAtEnd(); list.follow = true; bar.ingest(shortTask(30, 1, 601)); scrollFollow.start() })
    } }
    Timer { id: scrollFollow; interval: 300; onTriggered: {
        check(list.atYEnd && list.follow, "back at the end the view follows new rows again")
        closeTimer.start()
    } }
    Timer { id: closeTimer; interval: 200; onTriggered: {
        // Edit prompt while the panel shrinks: the next submit must be a fresh task, and a task created inside the
        // 300 ms shrink window must cancel the pending close and re-open the panel for the new conversation
        bar.configured = true; bar.aiEnabled = true; bar.taskStatus = "failed"
        check(bar.markState === "error", "failed task tints the mark amber while the panel is open (" + bar.markState + ")")
        bar.editPrompt()
        check(field.text === bar.taskRequest && field.text.length > 0 && bar.closing && bar.panelMode === "open" && !bar.followUp, "edit prompt: request back in the bar, panel shrinking, next submit is not a follow-up")
        bar.pendingRequest = "Open the editor and type hi"
        bar.onCreated(false, 0, { id: 12, status: "queued", parent_id: null })
        check(!bar.closing && bar.panelMode === "open" && bar.rootTaskId === 12 && bar.taskId === 12 && kinds() === "user", "task created during the shrink: close cancelled, conversation re-rooted (" + kinds() + ")")
        raceTimer.start()
    } }
    Timer { id: raceTimer; interval: 500; onTriggered: {
        check(bar.panelMode === "open" && panel.visible && bar.openProgress > 0.99, "panel still open 500 ms later (pending close was cancelled; progress " + bar.openProgress.toFixed(2) + ")")
        bar.taskStatus = "failed"
        bar.closePanel(); finish.start()
    } }
    Timer { id: finish; interval: 700; onTriggered: {
        check(bar.panelMode === "closed" && !panel.visible && panel.height === 0, "closePanel folds the panel away after the shrink animation (" + bar.panelMode + ", " + panel.height + " px)")
        check(bar.taskStatus === "" && bar.markState === "idle", "dismissing the panel forgets the failed task: the mark returns to idle (" + bar.markState + ")")
        var saved = bar.savedTask()
        check(saved.root === 0 && saved.task === 0, "a dismissed conversation is forgotten in Plasmoid.configuration (" + JSON.stringify(saved) + ")")
        // remembered conversation across a re-layout: GET /tasks/{saved} on load. At login the service is usually not up
        // yet: curl fails (exit 7, nothing parsed) -> the ids stay remembered and the first /status answer asks again
        bar.restoreTaskId = 21
        bar.handle("restore", 21, 7, "", "")
        check(bar.restoreTaskId === 21 && bar.panelMode === "closed", "restore with the service down keeps the remembered task for a retry (not forgotten)")
        var sr = bar.serial
        bar.onStatus({ mode: "auto", provider_ready: true, ai_enabled: true, tasks: {}, pending_approvals: 0 })
        check(bar.serial === sr + 1 && bar.restoreTaskId === 0, "the first /status answer re-asks GET /tasks/21 once")
        bar.onStatus({ mode: "auto", provider_ready: true, ai_enabled: true, tasks: {}, pending_approvals: 0 })
        check(bar.serial === sr + 1, "a second /status answer does not ask again while the retry is in flight")
        bar.handle("restore", 21, 0, JSON.stringify({ error: "no such task" }))
        check(bar.panelMode === "closed" && bar.restoreTaskId === 0 && bar.savedTask().task === 0, "the service answering 404 {error} forgets the task (no endless retry)")
        bar.handle("restore", 21, 0, JSON.stringify({ id: 21, status: "done", request: "old one", result: "x", steps: [], approvals: [], questions: [] }))
        check(bar.panelMode === "closed" && bar.savedTask().task === 0, "a remembered task that already finished is forgotten, the panel stays closed")
        var live = taskJson(1); live.id = 21; live.parent_id = null
        bar.handle("restore", 21, 0, JSON.stringify(live))
        check(bar.panelMode === "open" && bar.rootTaskId === 21 && bar.taskId === 21 && kinds() === "user,tools,step", "a remembered task that is still active reopens the panel with its request + live rows (" + kinds() + ")")
        check(bar.savedTask().task === 21 && bar.savedTask().root === 21, "and is remembered again")
        check(bar.restoreTaskId === 0, "an open panel has no pending restore")
        // the desktop-form mic hint lives in the status line, not the placeholder
        bar.handle("listen", 0, 4, "", "No microphone found on this computer.")
        check(statusText.visible && statusText.text === bar.voiceHint && field.placeholderText === "Ask me to do anything…", "desktop form: the voice hint is in the status line, the placeholder stays")
        bar.voiceHint = ""
        // compact (panel) form, for real: shrink the window to a panel thickness so `compact` flips
        bar.Layout.minimumHeight = 36; bar.Layout.preferredHeight = 36
        var w = h.Window.window; if (w) { w.minimumHeight = 36; w.height = 36 }
        compactTimer.start()
    } }
    Timer { id: compactTimer; interval: 700; onTriggered: {
        console.log("compact: applet " + Math.round(bar.width) + "x" + Math.round(bar.height))
        check(bar.compact === true && bar.onDesktop === false, "a 36 px tall applet is the compact (panel) form")
        check(card.x === 0 && card.width === bar.width && card.height === bar.height, "compact: the card fills the applet")
        check(bar.containmentMask === null, "compact: no hit mask (the whole applet is the card)")
        check(popup.active === true && popup.item !== null && popup.item.mainItem !== null && panelMain.parent === popup.item.mainItem, "compact form: PlasmaCore.Dialog created and the conversation moved into it")
        check(popup.item.visible === true, "compact popup is visible while the conversation is open")
        check(panel.height === 0 && !panel.visible, "compact: the in-applet panel is folded away")
        // a failed mic tap must not be silent here either: the status line is hidden, so the placeholder + tooltip carry the reason
        bar.handle("listen", 0, 4, "", "No microphone found on this computer.")
        check(!statusText.visible, "compact: the status line under the field is hidden")
        check(field.placeholderText === "No microphone found on this computer.", "compact: the placeholder carries the voice reason (" + field.placeholderText + ")")
        check(bar.hoverTip === "No microphone found on this computer.", "compact: the card's tooltip carries the voice reason")
        bar.voiceHint = ""
        check(field.placeholderText === "Ask me to do anything…", "compact: placeholder back to the prompt once the hint expires")
        // grow back to the home-screen strip
        bar.Layout.minimumHeight = 640; bar.Layout.preferredHeight = 640
        var w = h.Window.window; if (w) { w.minimumHeight = 640; w.height = 640 }
        uncompactTimer.start()
    } }
    Timer { id: uncompactTimer; interval: 700; onTriggered: {
        check(bar.compact === false && bar.onDesktop === true, "back on the desktop: tall strip again (" + Math.round(bar.height) + " px)")
        check(popup.active === false && popup.item === null && panelMain.parent === panel, "back on the desktop: popup gone, conversation back inside the applet")
        check(bar.containmentMask !== null && bar.contains(Qt.point(2, 2)) === false && bar.contains(Qt.point(card.x + 5, 5)) === true, "back on the desktop: hit mask active again")
        polishStage.start()
    } }

    // ---- polish: Do it disabled on an empty / whitespace field (40 %, no hover, Enter ignored; the mic stays live)
    Timer { id: polishStage; interval: 200; onTriggered: {
        bar.configured = true; bar.aiEnabled = true; bar.daemonUp = true; bar.voiceHint = ""
        field.text = ""
        check(go.canSend === false && goArea.hoverEnabled === false && goArea.cursorShape === Qt.ArrowCursor, "empty field: Do it is not clickable — no hover, arrow cursor")
        field.text = "   "
        var s0 = bar.serial
        bar.submit()
        check(go.canSend === false && bar.sending === false && bar.serial === s0, "whitespace only: still disabled; Enter / submit() posts nothing")
        check(micButton.active === true, "the mic stays enabled while Do it is disabled")
        goOpacity.start()
    } }
    Timer { id: goOpacity; interval: 300; onTriggered: {   // the 160 ms opacity animation has settled
        check(Math.abs(go.opacity - 0.4) < 0.02, "disabled Do it sits at 40 % opacity (" + go.opacity.toFixed(2) + ")")
        field.text = "draw a kite"
        check(go.canSend === true && goArea.hoverEnabled === true && goArea.cursorShape === Qt.PointingHandCursor, "text typed: Do it is live again (hover + hand cursor)")
        goOpacity2.start()
    } }
    Timer { id: goOpacity2; interval: 300; onTriggered: {
        check(Math.abs(go.opacity - 1) < 0.02, "live Do it at 100 % (" + go.opacity.toFixed(2) + ")")
        field.text = ""
        // ---- cloud hint chip: only with the built-in model; dismissal lasts the session; never with a cloud provider
        h.cardBefore = card.height
        var st = { mode: "auto", provider: "local", provider_ready: true, ai_enabled: true, tasks: {}, pending_approvals: 0 }
        bar.onStatus(st)
        check(bar.provider === "local" && bar.cloudHint === true && cloudHint.visible, "local provider: the cloud hint chip shows under the field")
        check(cloudHintText.text === "Using the built-in model. For the best results use a cloud model", "chip wording")
        chipGeometry.start()                                     // the ColumnLayout re-polishes on the next event-loop pass
    } }
    property real cardBefore: 0
    Timer { id: chipGeometry; interval: 150; onTriggered: {
        var cy = cloudHint.mapToItem(card, 0, 0).y, fy = field.mapToItem(card, 0, 0).y
        check(cy >= fy + field.height && cy + cloudHint.height <= card.height, "chip sits under the field, inside the card (card " + Math.round(h.cardBefore) + " -> " + Math.round(card.height) + " px; chip y " + Math.round(cy) + ", field bottom " + Math.round(fy + field.height) + ")")
        check(panel.y === card.y + card.height + 8, "the panel still starts 8 px under the card")
        var st = { mode: "auto", provider: "local", provider_ready: true, ai_enabled: true, tasks: {}, pending_approvals: 0 }
        cloudChoose.clicked()
        check(bar.lastOpenArgs === "--settings provider", "Choose opens Fab AI Controls on Settings › AI provider (" + bar.lastOpenArgs + ")")
        bar.dismissCloudHint()
        check(bar.cloudHint === false && !cloudHint.visible, "dismiss hides the chip")
        bar.onStatus(st)
        check(!cloudHint.visible, "the dismissal is remembered for the session: the next /status does not bring it back")
        bar.cloudHintDismissed = false
        st.provider = "claude"
        bar.onStatus(st)
        check(bar.provider === "claude" && bar.cloudHint === false && !cloudHint.visible, "a cloud provider never shows the chip")
        imageStage.start()
    } }

    // ---- image cards: the generate_image step -> card after the real `[ -f ]` check; missing file -> none; no duplicates
    readonly property string imgPath: "/tmp/Pictures/Fab OS/askbar-test.png"     // HOME=/tmp inside the image; written by mkpng.py
    readonly property string imgPrompt: "a red kite over a green hill at sunrise"
    function imageTask(phase) {
        var steps = [{ id: 700, task_id: 40, kind: "tool_call", name: "generate_image", input: JSON.stringify({ prompt: h.imgPrompt }),
                       output: phase >= 2 ? JSON.stringify({ path: "~/Pictures/Fab OS/askbar-test.png", width: 640, height: 400, provider: "local", prompt: h.imgPrompt }) : "",
                       risk: "LOW", decision: "auto-approved", narration: "Painting your picture now." }]
        if (phase >= 2) {
            steps.push({ id: 701, task_id: 40, kind: "tool_call", name: "generate_image", input: JSON.stringify({ prompt: "a second one" }),
                         output: JSON.stringify({ path: "/tmp/Pictures/Fab OS/never-written.png", width: 640, height: 400, provider: "local", prompt: "a second one" }), risk: "LOW", decision: "auto-approved" })
            steps.push({ id: 702, task_id: 40, kind: "final", name: "", input: "",
                         output: "Here is your kite — saved to $HOME/Pictures/Fab OS/askbar-test.png. The second try (~/Pictures/Fab OS/never-written.png) did not come out.", risk: "", decision: "" })
        }
        return { id: 40, title: "Draw a kite", request: "Draw a red kite over a green hill", status: phase >= 2 ? "done" : "running", result: phase >= 2 ? "Here is your kite" : null, steps: steps, approvals: [], questions: [] }
    }
    function imageDelegate() { var ds = delegates(); for (var i = 0; i < ds.length; i++) if (ds[i].kind === "image") return ds[i]; return null }
    Timer { id: imageStage; interval: 100; onTriggered: {
        bar.resetConversation(); bar.rootTaskId = 40; bar.taskId = 40; bar.taskRequest = "Draw a red kite over a green hill"
        convo.append(bar.row({ kind: "user", key: "u40", text: bar.taskRequest, status: "request" }))
        bar.panelMode = "open"
        bar.ingest(imageTask(1))
        check(kinds() === "user,tools,step" && rowAt(2).running === "Creating an image" && rowAt(2).icon === "image-x-generic" && rowAt(2).subtitle === h.imgPrompt, "generate_image step card while it runs: 'Creating an image' + the prompt (" + kinds() + ")")
        var finalText = imageTask(2).steps[2].output
        var found = Agent.imagePathsInText(finalText)
        check(found.length === 2 && found[0] === "$HOME/Pictures/Fab OS/askbar-test.png" && found[1] === "~/Pictures/Fab OS/never-written.png", "the path regex under the QML engine finds both mentions: " + JSON.stringify(found))
        var s0 = bar.serial
        bar.ingest(imageTask(2))
        check(kinds() === "user,tools,step,step,assistant", "done: the step flips, the final text lands; no card yet — the file check is asked first (" + kinds() + ")")
        check(bar.serial === s0 + 4, "four mentions (two steps, two paths in the text) => four `[ -f ]` checks (" + (bar.serial - s0) + "; offered: " + JSON.stringify(Object.keys(bar.imageSeen)) + ")")
        imageWait.start()
    } }
    Timer { id: imageWait; interval: 1500; onTriggered: {   // four sh processes have answered by now
        var imgs = 0, idx = -1
        for (var i = 0; i < convo.count; i++) if (rowAt(i).kind === "image") { imgs++; idx = i }
        check(imgs === 1 && idx === convo.count - 1, "exactly ONE image card: the real file once (step + text mention), the missing file none (" + kinds() + ")")
        check(idx >= 0 && rowAt(idx).text === h.imgPath && rowAt(idx).subtitle === h.imgPrompt && rowAt(idx).name === "local" && rowAt(idx).title === "640 × 400", "card row: absolute path (~ expanded by the shell), caption = prompt, provider, size")
        check(Object.keys(bar.imagePending).length === 0, "no file check left pending")
        imageRender.start()
    } }
    Timer { id: imageRender; interval: 900; onTriggered: {   // thumbnail decoded + faded in
        var d = imageDelegate(), c = d && d.children[0] ? d.children[0].item : null
        check(c !== null && c.ready === true && c.broken === false, "thumbnail decoded (status Ready)")
        check(c !== null && c.thumbH <= 320 && c.thumbW <= d.width && c.radius === 16, "thumbnail ≤ 320 px tall inside the row (" + (c ? c.thumbW + "x" + c.thumbH : "-") + "), card radius 16")
        check(c !== null && Math.abs(c.thumbW / c.thumbH - 1.6) < 0.02, "aspect ratio kept (640x400)")
        check(c !== null && c.painted === true, "the Canvas painted the rounded thumbnail")
        check(d !== null && d.width <= list.width - list.gutter + 0.5, "card clear of the scrollbar gutter")
        list.positionViewAtEnd()
        thumbRect.start()
    } }
    Timer { id: thumbRect; interval: 150; onTriggered: {   // after positionViewAtEnd settled: where the thumbnail sits in the panel render
        var d = imageDelegate(), c = d && d.children[0] ? d.children[0].item : null
        var p = c.mapToItem(panel, c.children[1].x, c.children[1].y)          // children[1] = the hidden Image; the Canvas shares its box
        console.log("THUMB " + Math.round(p.x) + " " + Math.round(p.y) + " " + Math.round(c.thumbW) + " " + Math.round(c.thumbH))
        panel.grabToImage(function (r) { r.saveToFile("/out/askbar-image-card.png"); console.log("PASS grabbed image-card (panel with the generated-image card)"); viewerStage.start() })
    } }
    Timer { id: viewerStage; interval: 100; onTriggered: {
        var d = imageDelegate()
        check(viewer.active === false && viewer.item === null, "no viewer window before the tap")
        d.openImage(d.text, d.subtitle, d.name)                       // what the card's MouseArea emits on a tap
        check(viewer.active && viewer.item !== null && viewer.item.visible, "tap: the enlarge viewer (PlasmaCore.Dialog) opens")
        var vi = viewer.item.viewer
        check(vi.width === Math.round(Screen.width * 0.8) && vi.height === Math.round(Screen.height * 0.8), "viewer is 80 % of the screen (" + vi.width + "x" + vi.height + " of " + Screen.width + "x" + Screen.height + ")")
        check(vi.path === h.imgPath && vi.prompt === h.imgPrompt && vi.provider === "local", "viewer carries path, prompt and provider")
        viewerWait.start()
    } }
    Timer { id: viewerWait; interval: 1500; onTriggered: {   // the binary probe (one sh) and the big decode have finished
        var vi = viewer.item.viewer
        check(vi.imageReady && vi.big.status === Image.Ready, "the big picture decoded in the viewer (" + vi.sizeLabel + ")")
        check(vi.binsKnown, "the binary probe answered: " + JSON.stringify(vi.bins))
        check(vi.hasWallpaper && vi.wallBtn.active, "plasma-apply-wallpaperimage is in the image: Set as wallpaper enabled")
        check(vi.hasOpen && vi.openBtn.active && vi.bins["gwenview"] === true, "gwenview is in the image: Open in Fab Photos enabled")
        check(!vi.hasCopy && !vi.copyBtn.active && vi.copyBtn.reason.indexOf("wl-clipboard") >= 0 && Math.abs(vi.copyBtn.opacity - 0.4) < 0.02, "no wl-copy in the image: Copy image disabled at 40 % with the reason (" + vi.copyBtn.reason + ")")
        check(vi.saveBtn.active && vi.regenBtn.active && vi.closeBtn !== null, "Save as, Regenerate and Close are present and live")
        check(vi.fileDialogReady === true, "QtQuick.Dialogs FileDialog loaded for Save as")
        check(vi.activeFocus === true, "the viewer holds keyboard focus (Esc reaches its handler)")
        vi.saveCopy()                                                  // the Save as FALLBACK path, for real: a copy into ~/Pictures
        vi.grabToImage(function (r) { r.saveToFile("/out/askbar-image-viewer.png"); console.log("PASS grabbed image-viewer (80 % dialog: picture on the scrim + control row)"); saveWait.start() })
    } }
    Timer { id: saveWait; interval: 900; onTriggered: {
        var vi = viewer.item.viewer
        check(vi.toast.indexOf("Saved to /tmp/Pictures/askbar-test") === 0, "Save as fallback copied the file into ~/Pictures and says where (" + vi.toast + ")")
        bar.daemonUp = true; bar.configured = true; bar.aiEnabled = true
        var s0 = bar.serial
        vi.regenBtn.clicked()
        check(!viewer.active && viewer.item === null && bar.viewerImage === null, "Regenerate closes the viewer")
        check(bar.sending && bar.pendingRequest === "regenerate the image with the same prompt" && bar.serial === s0 + 1, "…and posts the follow-up 'regenerate the image with the same prompt'")
        check(bar.followUp === true && bar.rootTaskId === 40, "the follow-up threads under root task 40 (parent_id)")
        bar.openImage(h.imgPath, h.imgPrompt, "local")
        check(viewer.active && viewer.item.visible, "the viewer opens again from a card")
        viewer.item.viewer.closeRequested()                            // what Close and the Esc handler emit
        check(!viewer.active && viewer.item === null, "Close / Esc closes it")
        regenWait.start()
    } }
    Timer { id: regenWait; interval: 1800; onTriggered: {
        check(bar.sending === false && field.text === "regenerate the image with the same prompt", "no daemon here: the regenerate request came back to the bar like any failed submit")
        field.text = ""
        console.log("HARNESS DONE failures=" + h.failures)
        killer.connectSource("kill -TERM $PPID 2>/dev/null || pkill -x plasmawindowed")
    } }
}
