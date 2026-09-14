# Fab OS motion

Motion tokens: instant 0 ms · fast 120 ms · normal 200 ms · slow 320 ms (the ceiling for anything the user waits on);
easings standard / emphasized / accelerate / decelerate (Qt: OutCubic for enter and size changes, InOutSine for the few
loops). Every transition of our own QML and PyQt code sits between **120 and 220 ms**; nothing the user triggers takes
longer than 320 ms to settle.

Applied:
- **Plasma / KWin (`/etc/xdg/kdeglobals`)**: `[KDE] AnimationDurationFactor=0.5` — every stock desktop animation
  (window open/close, minimise, popups, Overview, task switcher, Kirigami transitions) runs at half its default length.
  Settings > Animations moves the same slider. The factor scales **KWin's effects and everything that takes its duration
  from `Kirigami.Units.*Duration` / `PlasmaCore.Units`**; our own plasmoids and Fab AI Controls use literal millisecond
  durations (the 120–220 ms below), which the slider does not scale — they are already at the length the factor gives
  stock Plasma, and they do not become slower when a user moves the slider back to 1. KWin effects stay as before
  (magic lamp, scale, fade, slide, dim screen, sliding popups, translucency, Overview); blur is off on machines under
  3.5 GB (docs/LOW-RAM.md).
- **Ask bar**: hover scale 1.04 / press 0.95 (140 ms), colour transitions 160 ms, the panel unfolds from the card in
  220 ms OutCubic and shrinks back in 220 ms, new conversation rows fade in and rise 12 px in 200 ms, the panel's
  content-height changes take 200 ms. The AI mark breathes (4 s idle, 1.2 s orbit while working, 0.9 s pulse while
  listening, one expanding ring on done) and every loop stops 30 s after the last interaction — an idle bar draws no frames.
- **Quick settings**: the pane slides down and up in 200 ms OutCubic (the close timer equals the slide), tiles and chips
  recolour in 120–160 ms, indicator glyphs magnify in 160 ms. No looping animation anywhere in the bar.
- **Dock**: magnification is a `Behavior on s` (160 ms OutCubic) — the only motion while the pointer moves; the launch
  bounce is 150 + 150 ms; the running-dot width changes in 160 ms; the startup pulse runs **four** times (320 ms halves)
  and then stops, never an endless loop.
- **Fab AI Controls (PyQt)**: new turns fade in over 200 ms, the toast in 200 ms, the result check-mark draws in 320 ms,
  the switch knob moves in 160 ms, a rejected key shakes for 320 ms. Tickers only run while their widget is shown: the
  typing dots repaint every 60 ms (~17 fps for a 48×24 px widget), the busy arc every 50 ms; the mic ring pulses only
  while recording.
- **Website**: reveal-on-scroll and the typewriter demo (both respect `prefers-reduced-motion`).

Rules: motion explains state, cause and continuity; never blocks input; is interruptible (KWin animations are, and our
`Behavior`s retarget mid-flight); collapses to instant for the stock desktop when the user sets Reduce Motion (Settings →
Animations: speed slider to Instant sets the factor to 0, which every KWin effect and every Kirigami/Plasma animation
honours; our own 120–220 ms transitions keep their fixed length — short enough that nothing waits on them). Do not
animate everything: no motion on static labels, no parallax, **no looping decoration** except the boot animation and the
ask bar's mark while it is awake. Anything continuous must stop when its widget is hidden or after a bounded number of loops.

Budget (why the numbers are what they are): a 2 GB machine with an integrated GPU renders a full-screen frame of the
Plasma shell in 6–12 ms; a 200 ms transition is 12 frames at 60 Hz — enough to read as movement, short enough that a
frame drop under load is invisible. Continuous animations cost the same whether or not anyone looks at them, which is
why they are bounded here and why the desktop's idle budget (docs/LOW-RAM.md, "Idle budget") counts frames as well as
processes.
