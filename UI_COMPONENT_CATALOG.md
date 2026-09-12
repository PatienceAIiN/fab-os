# UI component catalogue (what Fab OS uses today)

| Component | Implementation | Themed by |
|-----------|----------------|-----------|
| Panels (top bar, dock, ask bar) | Plasma panels (`layout.js`) | FabOS Plasma theme |
| Launcher | Plasma Kickoff (Fab mark, captions off) | Plasma theme, Fab icons |
| Global search | KRunner + Fab OS agent runner | Plasma theme |
| Notifications, Quick settings, Tray | Plasma applets | Plasma theme |
| Window chrome | KWin + Breeze decoration | Fab Dark/Light scheme |
| Buttons, inputs, switches, tabs, sliders, lists, tables | Qt Widgets / Breeze | colour scheme + palette stylesheet |
| Ask bar | QML plasmoid (Kirigami) | Kirigami.Theme |
| Command Center, Updates, Feedback, Welcome | PyQt6 windows | palette stylesheet |
| Dialogs, context menus, tooltips | Qt/Plasma defaults | Plasma theme / scheme |
| Greeter | SDDM QML theme (Fab) | own |
| Splash, boot | KSplash QML, Plymouth script | own |
| Installer | Calamares (Fab branding) | Qt style + branding.desc |

Duplication rule: Fab OS apps share one stylesheet string (radii, padding) and rely on the scheme for colour.
