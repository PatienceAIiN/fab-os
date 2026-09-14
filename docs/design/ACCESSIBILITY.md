# Accessibility

Inherited from the platform: Plasma and Qt provide keyboard navigation, focus rings, AT-SPI/screen-reader
semantics (Orca), high-contrast colour schemes, large-text scaling (Settings → Text & Fonts), and Reduce Motion.

Fab OS specifics:
- All Fab OS apps use standard Qt widgets (accessible by default) with logical tab order; every action has a
  keyboard path (Enter submits the ask bar; Meta+Space opens Fab AI Controls; Alt+F2 "do …" reaches the agent).
- Status is never colour-only: the ask bar's state dot is accompanied by text; approvals show risk as text.
- Contrast: Fab Dark text `#E6EAF0` on `#0E1116` ≈ 15:1; muted `#9AA4B2` on `#161B22` ≈ 6.8:1; accent
  `#6E9BFF` on ink ≈ 7.4:1. Fab Light `#1B1F27` on `#F6F7F9` ≈ 15:1. Minimum target 4.5:1 for text.
- Touch: controls are at least 32 px; the dock and ask bar accept touch.

Open items (tracked in GitHub issues): a full keyboard-only walkthrough of every Fab OS app, screen-reader
labelling audit of the ask bar plasmoid, and a formal contrast audit of the icon tiles' glyphs.
