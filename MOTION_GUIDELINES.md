# Fab OS motion

Tokens (`fab-ui/design-system/tokens/tokens.json` → motion): instant 0 ms · fast 120 ms · normal 200 ms ·
slow 320 ms · emphasis 480 ms; easings standard / emphasized / accelerate / decelerate.

Applied: KWin effects enabled centrally (`/etc/xdg/kwinrc`): magic lamp (minimise), scale (open/close), fade,
slide (popups), dim screen (auth), sliding popups, translucency; global `AnimationDurationFactor=1`.
Ask bar: hover scale 1.04 / press 0.95 (fast), colour transitions (fast), spinner while sending.
Website: reveal-on-scroll and typewriter demo (respects `prefers-reduced-motion`).

Rules: motion explains state, cause and continuity; never blocks input; is interruptible (KWin animations are);
collapses to instant when the user sets Reduce Motion (Settings → Animations: speed slider to Instant sets the
factor to 0, which every Plasma/Kirigami animation and our plasmoid honour). Do not animate everything: no
motion on static labels, no parallax, no looping decoration except the boot animation.
