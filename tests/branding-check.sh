#!/usr/bin/env bash
# Static checks on the built image: identity, no Canonical branding packages, no snap, legal files present.
set -uo pipefail; PROFILE=${1:-vm}; TAG=fabos:$PROFILE; fail=0
chk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
R() { podman run --rm "$TAG" bash -c "$1"; }
chk "all fabos packages fully installed"   "! R 'dpkg -l fabos-* | grep -v ^ii | grep -E \"^(i|h|r|u)\"' | grep -q ."
chk "os-release ID=fabos"               "R 'grep -q ^ID=fabos /usr/lib/os-release && grep -q ID_LIKE=.ubuntu /usr/lib/os-release'"
chk "os-release keeps UBUNTU_CODENAME"    "R 'grep -q ^UBUNTU_CODENAME=resolute /usr/lib/os-release'"
chk "lsb_release says FabOS"             "R 'lsb_release -ds' | grep -q 'Fab OS'"
chk "/etc/os-release is a symlink"        "R 'test -L /etc/os-release'"
chk "Ubuntu identity diverted, not lost"  "R 'test -f /usr/lib/os-release.ubuntu && grep -q ^ID=ubuntu /usr/lib/os-release.ubuntu'"
chk "no Canonical artwork packages"       "! R 'dpkg -l ubuntu-wallpapers* plymouth-theme-ubuntu* ubuntu-artwork 2>/dev/null | grep ^ii'"
chk "snapd absent"                        "! R 'dpkg -s snapd 2>/dev/null' >/dev/null"
chk "ubuntu-pro-client absent"            "! R 'dpkg -s ubuntu-pro-client 2>/dev/null' >/dev/null"
chk "plymouth default theme = fabos"     "R 'readlink -f /usr/share/plymouth/themes/default.plymouth' | grep -q themes/fabos/fabos.plymouth"
chk "plymouth script plugin present"      "R 'ls /usr/lib/x86_64-linux-gnu/plymouth/script.so'"
chk "initramfs contains fabos theme"     "R 'lsinitramfs /boot/initrd.img-* | grep -q themes/fabos/fabos.script'"
chk "Inter font installed"                "R 'fc-list | grep -qi Inter-Regular'"
chk "SDDM theme configured"               "R 'grep -q Current=fabos /etc/sddm.conf.d/zz-fabos.conf && test -f /usr/share/sddm/themes/fabos/Main.qml'"
chk "look-and-feel package present"       "R 'test -f /usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/defaults'"
chk "wallpaper installed"                 "R 'test -f /usr/share/wallpapers/FabOS/contents/images/1920x1080.png'"
# High-definition assets (2026-09-13): 7 wallpaper sizes light+dark, 4K greeter/splash background, 256 px boot frames,
# bare transparent mark (vector + PNG up to 512 in hicolor, 1024 in pixmaps), scalable/ listed first in the icon theme.
PNGDIM='od -An -tu4 --endian=big -j16 -N8'   # PNG IHDR width/height -> "  W  H" (no python quoting through eval)
chk "wallpapers: 7 sizes, light + dark"     "[ \$(R 'ls /usr/share/wallpapers/FabOS/contents/images/*.png /usr/share/wallpapers/FabOS/contents/images_dark/*.png | wc -l') -eq 14 ]"
chk "wallpaper 4K + 1366x768 present"       "R 'test -f /usr/share/wallpapers/FabOS/contents/images/3840x2160.png && test -f /usr/share/wallpapers/FabOS/contents/images_dark/3840x2160.png && test -f /usr/share/wallpapers/FabOS/contents/images/1366x768.png'"
chk "wallpaper package metadata Fab OS"     "R 'grep -q \"\\\"Name\\\": \\\"Fab OS\\\"\" /usr/share/wallpapers/FabOS/metadata.json && grep -q Apache-2.0 /usr/share/wallpapers/FabOS/metadata.json'"
chk "SDDM background is 3840x2160"          "R \"$PNGDIM /usr/share/sddm/themes/fabos/background.png\" | tr -s ' ' x | grep -q x3840x2160"
chk "SDDM + KSplash use the vector mark"    "R 'test -f /usr/share/sddm/themes/fabos/mark.svg && test -f /usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/splash/images/mark.svg'"
chk "lock screen crops, never stretches"    "R 'grep -q ^FillMode=2 /etc/xdg/kscreenlockerrc'"
chk "plymouth frames are 256 px"            "R \"$PNGDIM /usr/share/plymouth/themes/fabos/spinner-00.png\" | tr -s ' ' x | grep -q x256x256"
chk "plymouth script scales to the screen"  "R 'grep -q \"Window.GetHeight()\" /usr/share/plymouth/themes/fabos/fabos.script && grep -q \"Scale(logo_h\" /usr/share/plymouth/themes/fabos/fabos.script'"
chk "hicolor fabos: svg + 512 png"          "R 'test -f /usr/share/icons/hicolor/scalable/apps/fabos.svg && test -f /usr/share/icons/hicolor/scalable/apps/fabos-symbolic.svg && test -f /usr/share/icons/hicolor/512x512/apps/fabos.png'"
chk "hicolor fabos.svg has no tile"         "! R 'grep -q \"<rect width=\\\"64\\\"\" /usr/share/icons/hicolor/scalable/apps/fabos.svg'"
chk "pixmaps fabos.png is 1024 px"          "R \"$PNGDIM /usr/share/pixmaps/fabos.png\" | tr -s ' ' x | grep -q x1024x1024"
chk "default avatar = fabos-face.png"       "R 'readlink /usr/share/sddm/faces/.face.icon' | grep -q fabos-face.png"
chk "FabOS icon theme: fabos + symbolic"    "R 'test -f /usr/share/icons/FabOS/scalable/apps/fabos.svg && test -f /usr/share/icons/FabOS/scalable/apps/fabos-symbolic.svg && test -f /usr/share/icons/FabOS/512x512/apps/fabos.png'"
chk "FabOS index.theme: scalable first, follows scheme" "R 'grep -qE \"^Directories=scalable/status,.*scalable/apps,16x16/apps\" /usr/share/icons/FabOS/index.theme && grep -q ^FollowsColorScheme=true /usr/share/icons/FabOS/index.theme'"
chk "3D logo shipped"                     "R 'test -s /usr/share/fabos/3d/fabos-mark.glb'"
chk "aios runs"                           "R 'aios settings list' >/dev/null 2>&1"
chk "llama-cli present"                   "R 'test -x /usr/bin/llama-cli'"
chk "copyright files retained (>300)"     "[ \$(R 'find /usr/share/doc -name copyright | wc -l') -gt 300 ]"
chk "source offer shipped on system"      "R 'test -f /usr/share/doc/fabos-branding/SOURCE-OFFER'"
chk "kernel + initrd present"             "R 'ls /boot/vmlinuz-* /boot/initrd.img-*'"
chk "sddm enabled"                        "R 'systemctl is-enabled sddm' | grep -q enabled"
chk "FabOS Plasma theme installed"        "R 'test -f /usr/share/plasma/desktoptheme/FabOS/metadata.json && test -f /usr/share/plasma/desktoptheme/FabOS/dialogs/background.svg'"
chk "FabOS icon theme has Settings icons"  "R 'test -e /usr/share/icons/FabOS/48x48/apps/preferences-system-network.png'"
chk "Fab OS session entry (no Plasma name)" "R 'grep -q ^Name=Fab\ OS /usr/local/share/wayland-sessions/fabos.desktop && grep -q SessionDir=/usr/local/share/wayland-sessions /etc/sddm.conf.d/zz-fabos.conf'"
# 2026-09-15 (ADR-0015): the two wallet checks below used to assert the Fab Wallet KCM binary and menu override; the wallet is
# now removed, so they assert the opposite (rewritten in place — the old expectation is the defect).
chk "no wallet: kwalletmanager + its KCM absent"  "! R 'dpkg -s kwalletmanager 2>/dev/null' >/dev/null && ! R 'test -f /usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings_qwidgets/kcm_kwallet5.so'"
chk "no wallet: no wallet menu entry or override" "[ -z \"\$(R 'ls /usr/share/applications/org.kde.kwalletmanager.desktop /usr/local/share/applications/org.kde.kwalletmanager.desktop 2>/dev/null')\" ]"
chk "no visible KDE names in launcher"     "! R 'grep -lE \"^Name=.*KDE\" /usr/share/applications/*.desktop | while read f; do b=\$(basename \$f); test -f /usr/local/share/applications/\$b || echo \$b; done' | grep -q ."
chk "updates app + polkit + timer"         "R 'test -x /usr/bin/fabos-updates && test -f /usr/share/polkit-1/actions/in.patienceai.fabos.updates.policy && systemctl is-enabled fabos-update-check.timer' | grep -q enabled"
chk "unattended-upgrades allows Fab OS"    "R 'grep -q \"Patience AI:loom\" /etc/apt/apt.conf.d/52fabos-unattended'"
chk "KRunner agent plugin registered"      "R 'test -f /usr/share/krunner/dbusplugins/fabos-runner.desktop && test -f /usr/share/dbus-1/services/in.patienceai.fabos.runner.service'"
chk "file-manager Ask Fab OS action"       "R 'test -f /usr/share/kio/servicemenus/fabos-ask.desktop'"
chk "browser: firefox absent, brave-browser from Brave" "! R 'dpkg -s firefox 2>/dev/null' >/dev/null && R 'dpkg -s brave-browser' | grep -q '^Maintainer: Brave Software'"   # 2026-09-15 (ADR-0016): was the Firefox/Mozilla check
chk "first-boot provisioning shipped"      "R 'test -x /usr/lib/fabos/firstboot.sh && systemctl is-enabled fabos-firstboot.service 2>/dev/null | grep -qE \"enabled|masked\"'"
chk "en@fabos catalogs generated"          "R 'test -s /usr/local/share/locale/en@fabos/LC_MESSAGES/konsole.mo && test -s /usr/local/share/locale/en@fabos/LC_MESSAGES/dolphin.mo'"
chk "session prefers en@fabos"              "R 'test -f /etc/xdg/plasma-workspace/env/50-fabos-language.sh'"
chk "ki18n renames Kate -> Fab Editor"       "R 'HOME=/tmp LANGUAGE=en@fabos:en_US QT_QPA_PLATFORM=offscreen kate --help 2>/dev/null' | grep -q 'Fab Editor - Advanced Text Editor'"
chk "Discover binary never byte-patched"     "R 'test ! -f /usr/bin/plasma-discover.fabos-orig'"
chk "Konsole binaries never byte-patched"      "R 'test ! -f /usr/bin/konsole.fabos-orig && test ! -f /usr/lib/x86_64-linux-gnu/libkonsoleprivate.so.25.12.3.fabos-orig'"
chk "mime override + hidden Software Sources"  "R 'test -f /usr/share/mime/packages/Override.xml && grep -q Hidden=true /usr/local/share/applications/software-properties-qt.desktop'"
chk "light + dark Fab look-and-feel"        "R 'test -f /usr/share/plasma/look-and-feel/in.patienceai.fabos.light.desktop/contents/defaults && grep -q DefaultLightLookAndFeel=in.patienceai.fabos.light.desktop /etc/xdg/kdeglobals'"
chk "KDE donation nag off"                  "R 'grep -q autoload=false /etc/xdg/kded6rc'"
chk "no ubuntu account"                     "! R 'grep -q ^ubuntu: /etc/passwd'"
chk "no unminimize MOTD"                    "R 'test ! -f /etc/update-motd.d/60-unminimize'"
chk "Desktop Actions keep their own names"   "! R 'awk \"/^\\[Desktop Action/{a=1} a && /^Name=Fab Terminal/{print}\" /usr/local/share/applications/org.kde.konsole.desktop' | grep -q ."
chk "shortcut component names overridden"    "R 'grep -q ^Name=Fab\ Terminal /usr/local/share/kglobalaccel/org.kde.konsole.desktop'"
chk "Ubuntu web shortcut hidden"            "R 'grep -q Hidden=true /usr/local/share/kf6/searchproviders/ubuntu.desktop'"
chk "AppStream names for system apps"       "R 'test -f /usr/share/swcatalog/xml/fabos-names.xml'"
chk "KWin runner disabled by real id"       "R 'grep -q krunner_kwinEnabled=false /etc/xdg/krunnerrc'"
chk "wired network managed by NetworkManager" "R 'grep -q renderer:\ NetworkManager /etc/netplan/01-fabos-network-manager.yaml'"
chk "icon theme follows colour scheme"      "R 'grep -q FollowsColorScheme=true /usr/share/icons/FabOS/index.theme'"
# Low-RAM defaults (docs/LOW-RAM.md, ADR-0010) and author metadata (docs/design/BRANDING.md)
chk "zram: generator installed + Fab OS config"  "R 'test -x /usr/lib/systemd/system-generators/zram-generator && test -f /usr/lib/systemd/system/systemd-zram-setup@.service && grep -q \"^\[zram0\]\" /etc/systemd/zram-generator.conf && grep -q \"^zram-size = min(ram / 2, 4096)\" /etc/systemd/zram-generator.conf && grep -q \"^compression-algorithm = zstd\" /etc/systemd/zram-generator.conf'"
chk "zram: swap units generated from config"    "R 'mkdir -p /tmp/zr/etc/systemd /tmp/zr/proc /tmp/zg && cp /etc/systemd/zram-generator.conf /tmp/zr/etc/systemd/ && cp /proc/meminfo /tmp/zr/proc/ && echo > /tmp/zr/proc/cmdline && ZRAM_GENERATOR_ROOT=/tmp/zr /usr/lib/systemd/system-generators/zram-generator /tmp/zg /tmp/zg /tmp/zg >/dev/null 2>&1; test -f /tmp/zg/dev-zram0.swap && test -L /tmp/zg/swap.target.wants/dev-zram0.swap && grep -q ^Priority=100 /tmp/zg/dev-zram0.swap'"
chk "sysctl low-RAM file (swappiness 100)"      "R 'grep -q \"^vm.swappiness = 100\" /etc/sysctl.d/60-fabos-lowram.conf && grep -q \"^vm.vfs_cache_pressure = 50\" /etc/sysctl.d/60-fabos-lowram.conf && grep -q \"^vm.page-cluster = 0\" /etc/sysctl.d/60-fabos-lowram.conf'"
chk "systemd-oomd enabled, 70% pressure limit"  "R 'systemctl is-enabled systemd-oomd.service 2>/dev/null | grep -q ^enabled && grep -q ^DefaultMemoryPressureLimit=70% /etc/systemd/oomd.conf.d/fabos.conf && grep -q ^ManagedOOMMemoryPressureLimit=70% /etc/systemd/system/user@.service.d/90-fabos-oomd.conf'"
chk "baloo file indexing off by default"        "R 'grep -q ^Indexing-Enabled=false /etc/xdg/baloofilerc && grep -q \"^only basic indexing=true\" /etc/xdg/baloofilerc'"
chk "empty session at login"                    "R 'grep -q ^loginMode=emptySession /etc/xdg/ksmserverrc'"
chk "cups on demand (socket+path, no service)"  "R 'systemctl is-enabled cups.socket | grep -q ^enabled && systemctl is-enabled cups.path | grep -q ^enabled && ! systemctl is-enabled cups.service 2>/dev/null | grep -q ^enabled && grep -q ^IdleExitTimeout /etc/cups/cupsd.conf'"
chk "cups-browsed + smartd + motd-news off"     "! R 'systemctl is-enabled cups-browsed.service smartmontools.service motd-news.timer 2>/dev/null' | grep -q ^enabled"
chk "essential services still enabled"          "R 'systemctl is-enabled NetworkManager sddm unattended-upgrades bluetooth avahi-daemon 2>/dev/null' | grep -c ^enabled | grep -q ^5"
chk "aiosd not started at login (opt-in)"       "! R 'systemctl --global is-enabled aiosd.service 2>/dev/null' | grep -q ^enabled"
chk "fabos-agent started at login"              "R 'systemctl --global is-enabled fabos-agent.service' | grep -q ^enabled"
chk "agent daemon imports no SDK/Qt at start"   "! R 'grep -E \"^(import|from) (anthropic|PyQt6|openai|google)\" /usr/lib/fabos/agent/fabos_agentd.py' | grep -q ."
chk "welcome wrapper exits before Python"       "R 'grep -q welcome-done /usr/bin/fabos-welcome && grep -q \"exit 0\" /usr/bin/fabos-welcome'"
chk "SDDM theme Author=Patience AI"             "R 'grep -q \"^Author=Patience AI\" /usr/share/sddm/themes/fabos/metadata.desktop && grep -q ^Email=support@patienceai.in /usr/share/sddm/themes/fabos/metadata.desktop && grep -q ^Website=https://fabos.patienceai.in /usr/share/sddm/themes/fabos/metadata.desktop'"
chk "Plymouth theme names Patience AI"          "R 'grep -q \"by Patience AI\" /usr/share/plymouth/themes/fabos/fabos.plymouth && grep -q ^ModuleName=script /usr/share/plymouth/themes/fabos/fabos.plymouth'"
chk "Patience AI author in every metadata.json" "! R 'grep -L \"\\\"Name\\\": \\\"Patience AI\\\"\" /usr/share/plasma/plasmoids/in.patienceai.fabos.askbar/metadata.json /usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/metadata.json /usr/share/plasma/look-and-feel/in.patienceai.fabos.light.desktop/metadata.json /usr/share/wallpapers/FabOS/metadata.json /usr/share/plasma/desktoptheme/FabOS/metadata.json' | grep -q ."
chk "icon theme Comment names Patience AI"      "R 'grep -q \"^Comment=.*by Patience AI\" /usr/share/icons/FabOS/index.theme'"
chk "Homepage in every fabos package"           "! R 'for p in fabos-agent fabos-ai fabos-branding fabos-desktop fabos-desktop-meta fabos-feedback fabos-firstboot fabos-updates fabos-welcome fabos-voice; do dpkg -s \$p 2>/dev/null | grep -q \"^Homepage: https://fabos.patienceai.in\" || echo \$p; done' | grep -q ."
chk "os-release vendor + URLs"                  "R 'grep -q \"^VENDOR_NAME=.Patience AI\" /usr/lib/os-release && grep -q ^HOME_URL= /usr/lib/os-release && grep -q ^SUPPORT_URL= /usr/lib/os-release && grep -q ^BUG_REPORT_URL= /usr/lib/os-release'"
# NB: negative `grep -L` checks end the container command with `; true` so the pipeline status (pipefail) is that of the
# outer `grep -q .` alone, whatever exit status this grep version gives -L.
chk "about line in Fab OS apps (with ™)"         "! R 'grep -L \"Fab OS™ by Patience AI\" /usr/lib/fabos/welcome/fabos_welcome.py /usr/lib/fabos/updates/fabos_updates.py /usr/lib/fabos/feedback/fabos_feedback.py; true' | grep -q ."
chk "Welcome first page says Fab OS™"           "R 'grep -q \"Page(\\\"Welcome to Fab OS™\\\"\" /usr/lib/fabos/welcome/fabos_welcome.py'"
chk "™ never in machine ids"                    "! R 'grep -l ™ /usr/lib/os-release /etc/lsb-release /var/lib/dpkg/status /usr/share/applications/fabos-*.desktop 2>/dev/null; true' | grep -q ."

# Desktop chrome (2026-09-14): Aurorae window frame, monochrome scheme-following status icons, peek-desktop placement, 11 pt UI.
LAYOUT=/usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js
chk "Aurorae FabOS decoration shipped (+Light)" "R 'test -f /usr/share/aurorae/themes/FabOS/metadata.desktop && test -f /usr/share/aurorae/themes/FabOS/decoration.svg && test -f /usr/share/aurorae/themes/FabOS/close.svg && test -f /usr/share/aurorae/themes/FabOS/FabOSrc && test -f /usr/share/aurorae/themes/FabOSLight/FabOSLightrc && test -f /usr/share/aurorae/themes/FabOSLight/decoration.svg'"
chk "decoration SVGs follow the colour scheme" "R 'grep -q ColorScheme-HeaderBackground /usr/share/aurorae/themes/FabOS/decoration.svg && grep -q current-color-scheme /usr/share/aurorae/themes/FabOS/close.svg'"
chk "Aurorae metadata names the KCM entries Fab OS" "R 'grep -q ^Name=Fab\ OS$ /usr/share/aurorae/themes/FabOS/metadata.desktop && grep -q ^Name=Fab\ OS\ Light$ /usr/share/aurorae/themes/FabOSLight/metadata.desktop'"
chk "kwinrc selects the FabOS decoration via Aurorae v2" "R 'grep -q ^library=org.kde.kwin.aurorae.v2$ /etc/xdg/kwinrc && grep -q ^theme=__aurorae__svg__FabOS$ /etc/xdg/kwinrc && grep -q ^BorderSizeAuto=false /etc/xdg/kwinrc && grep -q ^BorderSize=None /etc/xdg/kwinrc'"
chk "look-and-feel defaults select FabOS/FabOSLight (v2)" "R 'grep -q ^theme=__aurorae__svg__FabOS$ /usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/defaults && grep -q ^theme=__aurorae__svg__FabOSLight$ /usr/share/plasma/look-and-feel/in.patienceai.fabos.light.desktop/contents/defaults && grep -q ^library=org.kde.kwin.aurorae.v2$ /usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/defaults && grep -q ^library=org.kde.kwin.aurorae.v2$ /usr/share/plasma/look-and-feel/in.patienceai.fabos.light.desktop/contents/defaults'"
chk "Aurorae v2 engine + its KCM installed"    "R 'test -f /usr/lib/x86_64-linux-gnu/qt6/plugins/org.kde.kdecoration3/org.kde.kwin.aurorae.v2.so && test -f /usr/lib/x86_64-linux-gnu/qt6/plugins/org.kde.kdecoration3.kcm/kcm_auroraedecoration.so'"
chk "fabos-desktop depends on kwin-style-aurorae" "R 'dpkg -s fabos-desktop' | grep ^Depends | grep -q kwin-style-aurorae"
chk "decoration KCM model lists Fab OS (Aurorae v2)" "R 'HOME=/tmp QT_QPA_PLATFORM=offscreen /usr/lib/x86_64-linux-gnu/libexec/kwin-applywindowdecoration --list-themes 2>/dev/null' | grep -q 'Fab OS (theme name: __aurorae__svg__FabOS'"
chk "bottom-right hot corner shows desktop"   "R 'grep -q ^BottomRight=ShowDesktop /etc/xdg/kwinrc'"
chk "peek-desktop is the last dock item, not in the top bar" "[ \$(R 'grep -c showdesktop $LAYOUT') -eq 1 ] && [ \$(R 'grep -n showdesktop $LAYOUT | cut -d: -f1') -gt \$(R 'grep -n \"^var dock\" $LAYOUT | cut -d: -f1') ]"
chk "FabOS mono status icons follow scheme"   "R 'grep -q ColorScheme-Text /usr/share/icons/FabOS/scalable/status/network-wireless-signal-excellent.svg && grep -q current-color-scheme /usr/share/icons/FabOS/scalable/status/network-wireless-connected-100.svg && test -e /usr/share/icons/FabOS/scalable/status/battery-080-charging.svg && test -e /usr/share/icons/FabOS/scalable/status/audio-volume-high.svg && test -e /usr/share/icons/FabOS/scalable/status/battery-profile-performance-symbolic.svg && test -e /usr/share/icons/FabOS/scalable/status/preferences-system-bluetooth.svg && test -e /usr/share/icons/FabOS/scalable/actions/arrow-down.svg && test -e /usr/share/icons/FabOS/scalable/places/user-desktop.svg'"
chk "mono status icons are SVG only (recolourable)" "! R 'ls /usr/share/icons/FabOS/*/status/*.png 2>/dev/null' | grep -q ."
chk "kdeglobals UI font is 11 pt, icons medium" "R 'grep -q \"^font=Inter,11,\" /etc/xdg/kdeglobals && grep -q \"^menuFont=Inter,11,\" /etc/xdg/kdeglobals && grep -q \"^toolBarFont=Inter,11,\" /etc/xdg/kdeglobals && grep -A1 \"^\\[ToolbarIcons\\]\" /etc/xdg/kdeglobals | grep -q ^Size=24'"
chk "clock is bold Inter 13 on one line"      "R 'grep -q \"\\\"fontSize\\\", 13\" $LAYOUT && grep -q \"\\\"dateDisplayFormat\\\", \\\"BesideTime\\\"\" $LAYOUT'"
chk "tray-defaults is executable"             "R 'test -x /usr/lib/fabos/tray-defaults'"
# Built-in offline AI model (2026-09-14, ADR-0011): the GGUF is inside the image with the right size, its licence + README sit
# next to it, the manifest carries the same hash, the on-demand units are shipped and the socket is enabled for every user.
MODEL=/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf
MODEL_SHA=6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e
chk "built-in model file present, 1117320736 bytes" "[ \$(R 'stat -c %s $MODEL 2>/dev/null') = 1117320736 ]"
chk "model README records the pinned sha256"      "R 'grep -q $MODEL_SHA /usr/share/fabos/models/README'"
chk "model licence: Apache-2.0 + Alibaba notice" "R 'grep -q \"Apache License\" /usr/share/fabos/models/LICENSE-qwen2.5 && grep -q \"Copyright 2024 Alibaba Cloud\" /usr/share/fabos/models/LICENSE-qwen2.5'"
chk "ai-models.json manifest matches the pin"    "R 'grep -q $MODEL_SHA /usr/share/fabos/ai-models.json && grep -q 1117320736 /usr/share/fabos/ai-models.json && grep -q $MODEL /usr/share/fabos/ai-models.json'"
chk "llama-server present"                       "R 'test -x /usr/bin/llama-server'"
chk "fabos-llama units shipped"                  "R 'test -f /usr/lib/systemd/user/fabos-llama.socket && test -f /usr/lib/systemd/user/fabos-llama-proxy.service && test -f /usr/lib/systemd/user/fabos-llama.service && test -x /usr/lib/fabos/ai/llama-start.sh && test -x /usr/bin/fabos-local-model'"
chk "fabos-llama.socket enabled for all users"   "R 'systemctl --global is-enabled fabos-llama.socket' | grep -q ^enabled"
# (test the captured text, not a negated `| grep -q` pipeline: with pipefail, grep -q's early exit SIGPIPEs podman and `!` turns that into a PASS)
chk "fabos-llama units pass systemd-analyze"     "[ -z \"\$(R 'mkdir -p /tmp/xdg && chmod 700 /tmp/xdg && XDG_RUNTIME_DIR=/tmp/xdg systemd-analyze verify --user /usr/lib/systemd/user/fabos-llama.socket /usr/lib/systemd/user/fabos-llama-proxy.service /usr/lib/systemd/user/fabos-llama.service 2>&1 || echo VERIFY-FAILED' | grep -iE 'error|fail|not found|unknown|ignoring')\" ]"
chk "fabos-local-model status: installed + size ok" "R 'fabos-local-model status' | grep -q '\"installed\": true' && R 'fabos-local-model status' | grep -q '\"size_ok\": true'"
chk "ai.env points at the built-in model"        "R 'grep -q ^AIOS_LLAMA_MODEL=$MODEL /etc/fabos/ai.env' && ! R 'grep -qi download /etc/fabos/ai.env'"

# Voice (2026-09-14): "Hey Fab" wake word (pocketsphinx, offline), whisper.cpp tiny.en model shipped in the image, espeak-ng fallback
chk "voice: CLI, daemon and engines installed"     "R 'test -x /usr/bin/fabos-voice && test -x /usr/lib/fabos/voice/fabos_voiced.py && test -x /usr/bin/pocketsphinx && test -x /usr/bin/whisper-cli && test -x /usr/bin/espeak-ng && test -x /usr/bin/pw-record'"
chk "voice: fabos-voiced user unit enabled"        "R 'systemctl --global is-enabled fabos-voiced.service' | grep -q enabled"
chk "voice: whisper tiny.en model shipped (sha256)" "R 'sha256sum /usr/share/fabos/voice/ggml-tiny.en.bin' | grep -q ^921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"
chk "voice: dictionary knows the wake phrase"      "R 'grep -q \"^hey HH EY\" /usr/share/pocketsphinx/model/en-us/cmudict-en-us.dict && grep -q \"^fab F AE B\" /usr/share/pocketsphinx/model/en-us/cmudict-en-us.dict'"
chk "voice: status reports offline whisper.cpp"    "R 'fabos-voice status' | grep -q '\"stt\": \"whisper.cpp\"'"

# Firewall on by default, window snapping + Snap Assist, package revision (2026-09-14; ADR-0013, brand.conf PKG_REVISION)
chk "ufw on: ENABLED=yes, unit enabled, deny in / allow out" "R 'grep -q ^ENABLED=yes$ /etc/ufw/ufw.conf && systemctl is-enabled ufw | grep -q ^enabled && grep -q ^DEFAULT_INPUT_POLICY=.DROP /etc/default/ufw && grep -q ^DEFAULT_OUTPUT_POLICY=.ACCEPT /etc/default/ufw'"
chk "iso profile: no SSH server, no ufw rules"           "[ \"$PROFILE\" != iso ] || ! R 'dpkg -s openssh-server 2>/dev/null | grep ^Package; grep -c \"^-A ufw-user-input\" /etc/ufw/user.rules' | grep -qE '^Package|^[1-9]'"
chk "vm profile: exactly one ufw rule (22/tcp), v4 + v6" "[ \"$PROFILE\" != vm ] || R 'test \$(grep -c \"^-A ufw-user-input\" /etc/ufw/user.rules) -eq 1 && grep -q \"^-A ufw-user-input -p tcp --dport 22 -j ACCEPT\" /etc/ufw/user.rules && test \$(grep -c \"^-A ufw6-user-input\" /etc/ufw/user6.rules) -eq 1'"
chk "snap assist KWin script shipped + enabled"          "R 'test -f /usr/share/kwin/scripts/fabos-snap-assist/contents/code/main.js && grep -q \"\\\"Id\\\": \\\"fabos-snap-assist\\\"\" /usr/share/kwin/scripts/fabos-snap-assist/metadata.json && grep -q \"\\\"Name\\\": \\\"Patience AI\\\"\" /usr/share/kwin/scripts/fabos-snap-assist/metadata.json && grep -q ^fabos-snap-assistEnabled=true /etc/xdg/kwinrc'"
chk "edge tiling: halves, quarter corners, maximise"     "R 'grep -q ^ElectricBorderTiling=true /etc/xdg/kwinrc && grep -q ^ElectricBorderCornerRatio=0.25 /etc/xdg/kwinrc && grep -q ^ElectricBorderMaximize=true /etc/xdg/kwinrc && grep -q ^ElectricBorderDelay=150 /etc/xdg/kwinrc'"
chk "all 10 fabos packages at 1.0-3 in the manifest"     "[ \$(R 'grep -c -P \"^fabos-[a-z-]+\\t1\\.0-3\$\" /usr/share/fabos/manifest.txt') -eq 10 ]"

# Forbidden third-party product names (owner rule): never in UI strings, QML, Python UI, desktop files, website or docs.
# "OpenAI" is allowed only as a provider label: the daemon's PROVIDERS table, Fab AI Controls' provider dropdown/help
# text, the welcome wizard's provider list, the ask bar's "add a provider" hint (main.qml: 'Claude, OpenAI, Gemini or a
# local model' — a provider-label list; owner may drop this entry if the ask-bar track rewords it), README's provider list,
# licence/attribution files.
SRC=$(cd "$(dirname "$0")/.." && pwd)
FORBID='ChatGPT|SnowUI|Sora|DALL.E|Upgrade plan|can make mistakes'
OPENAI_OK='fabos_agentd\.py$|command_center\.py$|fabos_welcome\.py$|in\.patienceai\.fabos\.askbar/contents/ui/main\.qml$|(^|/)README\.md$|ATTRIBUTIONS\.md$|LICENSING\.md$|THIRD_PARTY_LICENSES/|(^|/)legal/|/copyright$'   # copyright files carry upstream MIT notices verbatim (whisper weights)
chk "no forbidden product names in the source tree"     "! grep -rIn -E '$FORBID' $SRC/packages $SRC/website $SRC/brand $SRC/docs | grep -q ."
chk "no forbidden product names in the image"           "! R 'grep -rIn -E \"$FORBID\" /usr/share/fabos /usr/lib/fabos /usr/share/plasma /usr/share/applications 2>/dev/null; true' | grep -q ."
chk "OpenAI only as a provider label (source tree)"     "! grep -rIl OpenAI $SRC/packages $SRC/website $SRC/brand $SRC/docs $SRC/README.md | grep -vE '$OPENAI_OK' | grep -q ."
chk "OpenAI only as a provider label (image)"           "! R 'grep -rIl OpenAI /usr/share/fabos /usr/lib/fabos /usr/share/plasma /usr/share/applications 2>/dev/null; true' | grep -vE '$OPENAI_OK' | grep -q ."
chk "no files named after those products"              "! find $SRC/packages $SRC/website \( -iname '*chatgpt*' -o -iname '*openai*' -o -iname '*snowui*' \) | grep -q ."

# No wallet, rounded window corners, Brave Browser, security posture (2026-09-15; ADR-0015, ADR-0016). The wallet, PAM,
# Brave and mimeapps items only pass on an image built from this Containerfile (the round-3 image still has Firefox + the wallet).
chk "kwalletrc: Enabled=false, Launch Manager=false, no Auto Allow" "R 'grep -q ^Enabled=false$ /etc/xdg/kwalletrc && grep -q \"^Launch Manager=false\" /etc/xdg/kwalletrc' && [ -z \"\$(R 'grep -i \"Auto Allow\" /etc/xdg/kwalletrc')\" ]"
chk "kwalletmanager pinned out (ksshaskpass/libqt6keychain1 Recommend it)" "R 'grep -q kwalletmanager /etc/apt/preferences.d/00-fabos-blocklist'"
chk "pam_kwallet removed from the SDDM PAM stack"        "[ -z \"\$(R 'grep pam_kwallet /etc/pam.d/sddm')\" ]"
chk "decoration: corner notches transparent (L-shaped shadow paths + desc)" "R 'grep -q notch /usr/share/aurorae/themes/FabOS/decoration.svg && grep -q \"d=\\\"M0 0H48V28H28V48H0Z\\\"\" /usr/share/aurorae/themes/FabOS/decoration.svg && grep -q \"d=\\\"M56 0H104V48H76V28H56Z\\\"\" /usr/share/aurorae/themes/FabOS/decoration.svg'"
chk "decoration: corner shadows tapered by a luminance mask (taperTL/TR, not mask-*)" "R 'grep -q \"mask=\\\"url(#taperTL)\\\"\" /usr/share/aurorae/themes/FabOS/decoration.svg && grep -q \"mask=\\\"url(#taperTRi)\\\"\" /usr/share/aurorae/themes/FabOS/decoration.svg'"
chk "decoration: no mask-* elements (Aurorae uses them for blur only)" "[ -z \"\$(R 'grep -o \"id=\\\"mask-\" /usr/share/aurorae/themes/FabOS/decoration.svg')\" ]"
chk "decoration: buttons keep 3 px rounded strokes"     "R 'grep -q \"stroke-width=\\\"3\\\"\" /usr/share/aurorae/themes/FabOS/close.svg && grep -q \"stroke-width=\\\"3\\\"\" /usr/share/aurorae/themes/FabOS/maximize.svg && grep -q stroke-linecap=.round /usr/share/aurorae/themes/FabOS/minimize.svg'"
chk "no world-writable files/dirs under /usr/lib/fabos, /usr/share/fabos" "[ -z \"\$(R 'find /usr/lib/fabos /usr/share/fabos -xdev \( -type f -o -type d \) -perm -0002')\" ]"
# Privileged files: the image's FULL setuid / setgid / file-capability lists must be subsets of these allowlists — Ubuntu's stock set as
# measured on the 2026-09-14 vm + iso images (`find / -xdev -perm -4000 -type f`, `-perm -2000`, `getcap -r /`) plus the ONE non-stock
# entry, Brave's setuid sandbox helper (SECURITY.md, ADR-0016). Any new entry anywhere in the image fails; a missing one does not
# (the vm profile has no newgrp/mount.cifs, the round-3 image has no Brave). Extend the lists only with a SECURITY.md entry.
SUID_OK='^(/usr/bin/(chfn|chsh|fusermount3|gpasswd|mount|newgrp|ntfs-3g|passwd|pkexec|su|sudo\.ws|umount)|/usr/lib/dbus-1\.0/dbus-daemon-launch-helper|/usr/lib/openssh/ssh-keysign|/usr/sbin/mount\.cifs|/opt/brave\.com/brave/chrome-sandbox)$'
SGID_OK='^(/usr/bin/(chage|expiry|ssh-agent)|/usr/sbin/(pam_extrausers_chkpwd|unix_chkpwd))$'
CAPS_OK='^(/usr/bin/kwin_wayland cap_sys_nice=ep|/usr/bin/ping cap_net_raw=ep|/usr/lib/x86_64-linux-gnu/gstreamer1\.0/gstreamer-1\.0/gst-ptp-helper cap_net_bind_service,cap_net_admin,cap_sys_nice=ep|/usr/lib/x86_64-linux-gnu/libexec/ksysguard/ksgrd_network_helper cap_net_raw=ep|/usr/lib/x86_64-linux-gnu/libexec/org_kde_powerdevil cap_wake_alarm=ep)$'
chk "setuid files: Ubuntu stock set + Brave chrome-sandbox only" "[ -z \"\$(R 'find / -xdev -perm -4000 -type f 2>/dev/null' | grep -vE '$SUID_OK')\" ]"
chk "setgid files: Ubuntu stock set only"                       "[ -z \"\$(R 'find / -xdev -perm -2000 -type f 2>/dev/null' | grep -vE '$SGID_OK')\" ]"
chk "file capabilities: Ubuntu stock set only"                  "[ -z \"\$(R 'getcap -r / 2>/dev/null' | grep -vE '$CAPS_OK')\" ]"
chk "no Fab OS file is setuid/setgid or carries a capability"   "[ -z \"\$(R 'find /usr/lib/fabos /usr/share/fabos /usr/bin/fabos* /usr/bin/aios -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null; getcap -r /usr/lib/fabos /usr/share/fabos 2>/dev/null')\" ]"
chk "brave: chrome-sandbox is root:4755 (Chromium sandbox fallback); no cron daemon" "R 'stat -c %U:%a /opt/brave.com/brave/chrome-sandbox' | grep -q ^root:4755$ && ! R 'dpkg -s cron 2>/dev/null; dpkg -s anacron 2>/dev/null' | grep -q ^Package"
chk "browser: Mozilla source, pin and keyring gone"      "[ -z \"\$(R 'ls /etc/apt/sources.list.d/mozilla.sources /etc/apt/preferences.d/mozilla /usr/share/keyrings/packages.mozilla.org.gpg 2>/dev/null')\" ]"
chk "browser: Brave .sources (documented name, Signed-By keyring present)" "R 'grep -q ^URIs:.*brave-browser-apt-release.s3.brave.com /etc/apt/sources.list.d/brave-browser-release.sources && grep -q ^Signed-By:./usr/share/keyrings/brave-browser-archive-keyring.gpg /etc/apt/sources.list.d/brave-browser-release.sources && test -s /usr/share/keyrings/brave-browser-archive-keyring.gpg'"
chk "browser: Brave key not in trusted.gpg.d; no Google source; cron re-add off" "[ -z \"\$(R 'ls /etc/apt/trusted.gpg.d/ | grep -i brave; grep -rl dl.google.com /etc/apt/sources.list.d/ 2>/dev/null')\" ] && R 'grep -q ^repo_add_once=.false /etc/default/brave-browser'"
chk "browser: Brave is the default (mimeapps.list)"      "R 'grep -q ^x-scheme-handler/https=brave-browser.desktop /etc/xdg/mimeapps.list && grep -q ^x-scheme-handler/http=brave-browser.desktop /etc/xdg/mimeapps.list && grep -q ^text/html=brave-browser.desktop /etc/xdg/mimeapps.list && test -f /usr/share/applications/brave-browser.desktop'"
chk "browser: firefox pinned out; no firefox desktop file" "R 'grep -q firefox /etc/apt/preferences.d/00-fabos-blocklist' && [ -z \"\$(R 'ls /usr/share/applications/firefox.desktop 2>/dev/null')\" ]"

# Top bar quick settings + dock (2026-09-15): two Fab OS plasmoids replace the four stock tray icons and icontasks; the
# tray keeps only third-party icons; one bar size drives the indicators and the clock; Brave is the pinned browser.
QS=/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings
DK=/usr/share/plasma/plasmoids/in.patienceai.fabos.dock
chk "quick settings + dock plasmoids installed, Authors Patience AI" "R 'test -f $QS/metadata.json && test -f $DK/metadata.json && grep -q \"\\\"Name\\\": \\\"Patience AI\\\"\" $QS/metadata.json && grep -q \"\\\"Name\\\": \\\"Patience AI\\\"\" $DK/metadata.json && grep -q \"\\\"Id\\\": \\\"in.patienceai.fabos.quicksettings\\\"\" $QS/metadata.json && grep -q \"\\\"Id\\\": \\\"in.patienceai.fabos.dock\\\"\" $DK/metadata.json'"
chk "both applets ship main.qml + config schema + settings page" "R 'test -f $QS/contents/ui/main.qml && test -f $QS/contents/config/main.xml && test -f $QS/contents/config/config.qml && test -f $QS/contents/ui/configGeneral.qml && test -f $QS/contents/code/status.sh && test -f $DK/contents/ui/main.qml && test -f $DK/contents/ui/TaskItem.qml && test -f $DK/contents/config/main.xml && test -f $DK/contents/ui/configGeneral.qml'"
chk "layout: top bar holds quick settings, dock replaces icontasks" "R 'grep -q \"top.addWidget(\\\"in.patienceai.fabos.quicksettings\\\")\" $LAYOUT && grep -q \"dock.addWidget(\\\"in.patienceai.fabos.dock\\\")\" $LAYOUT' && [ -z \"\$(R 'grep \"addWidget(\\\"org.kde.plasma.icontasks\\\")\" $LAYOUT')\" ]"
chk "layout: Brave is the pinned browser, Firefox nowhere" "R 'grep -q applications:brave-browser.desktop $LAYOUT' && ! R 'grep -ci firefox $LAYOUT' | grep -qE '^[1-9]'"
chk "layout: one bar size drives quick settings + clock (12/13/15)" "R 'grep -q \"^var BAR_SIZE = \" $LAYOUT && grep -q \"small: 12, medium: 13, large: 15\" $LAYOUT && grep -q \"quick.writeConfig(\\\"barSize\\\", BAR_SIZE)\" $LAYOUT && grep -q \"tasks.writeConfig(\\\"magnify\\\", MAGNIFY)\" $LAYOUT'"
chk "system tray hides the five duplicated stock icons" "R 'grep -q \"\\\"hiddenItems\\\", \\[\" /usr/lib/fabos/tray-defaults.js && grep hiddenItems /usr/lib/fabos/tray-defaults.js | grep -q org.kde.plasma.battery && grep hiddenItems /usr/lib/fabos/tray-defaults.js | grep -q org.kde.plasma.networkmanagement && grep hiddenItems /usr/lib/fabos/tray-defaults.js | grep -q org.kde.plasma.volume && grep hiddenItems /usr/lib/fabos/tray-defaults.js | grep -q org.kde.plasma.bluetooth && grep hiddenItems /usr/lib/fabos/tray-defaults.js | grep -q org.kde.plasma.notifications && grep -q \"\\\"shownItems\\\", \\[\\]\" /usr/lib/fabos/tray-defaults.js && grep -q tray-defaults-done-v2 /usr/lib/fabos/tray-defaults'"
chk "quick settings: battery percentage beside the glyph in the clock size" "R 'grep -q \"root.st.batPct + \\\"%\\\"\" $QS/contents/ui/main.qml && grep -q \"textPx: root.clockPx\" $QS/contents/ui/main.qml && grep -q \"function clockSizeFor\" $QS/contents/ui/status.js'"
chk "quick settings: slide-down pane, speed text, dnd, stock applets via plasmawindowed" "R 'grep -q \"duration: 240; easing.type: Easing.OutCubic\" $QS/contents/ui/main.qml && grep -q \"function speedText\" $QS/contents/ui/status.js && grep -q toggleDnd $QS/contents/ui/main.qml && grep -q \"plasmawindowed org.kde.plasma.networkmanagement\" $QS/contents/ui/main.qml && grep -q \"plasmawindowed org.kde.plasma.volume\" $QS/contents/ui/main.qml && grep -q \"plasmawindowed org.kde.plasma.battery\" $QS/contents/ui/main.qml && grep -q \"plasmawindowed org.kde.plasma.bluetooth\" $QS/contents/ui/main.qml'"
chk "quick settings: every wired CLI exists in the image" "R 'command -v nmcli && command -v bluetoothctl && command -v wpctl && command -v upower && command -v powerprofilesctl && command -v plasmawindowed && command -v kcmshell6 && command -v qdbus6 && command -v systemsettings' >/dev/null"
chk "dock: TasksModel backend, magnify 1.6/1.3/1.1, Brave default launcher" "R 'grep -q \"import org.kde.taskmanager as TaskManager\" $DK/contents/ui/main.qml && grep -q \"TaskManager.TasksModel\" $DK/contents/ui/main.qml && grep -q \"1.6\" $DK/contents/ui/main.qml && grep -q \"1.3\" $DK/contents/ui/main.qml && grep -q \"1.1\" $DK/contents/ui/main.qml && grep -q applications:brave-browser.desktop $DK/contents/config/main.xml && grep -q \"name=\\\"magnify\\\"\" $DK/contents/config/main.xml'"
chk "dock + quick settings share the magnify setting via the shell scripting API" "R 'grep -q \"in.patienceai.fabos.dock\" $QS/contents/ui/status.js && grep -q evaluateScript $QS/contents/ui/status.js && grep -q \"org.kde.plasma.digitalclock\" $QS/contents/ui/status.js'"
chk "desktop applets carry no hard-coded colours" "! R 'grep -nE \"color: \\\"#|color: \\\"(white|black|red|blue)\\\"\" $QS/contents/ui/*.qml $DK/contents/ui/*.qml' | grep -q ."
# Review fixes (2026-09-15, source tree): the notification cross is reachable (whole-row hover area + the button's own
# hover), the bar magnifies the glyph only, the dock's applet width does not follow a hover, one owner per shared setting.
QSS=$SRC/packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents
DKS=$SRC/packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents
chk "notification row: dismiss cross reachable (no dead strip, button hover counts)" "grep -q 'readonly property bool hovered: ma.containsMouse' $QSS/ui/SmallButton.qml && grep -q 'rowArea.containsMouse || dismissBtn.hovered' $QSS/ui/NotificationRow.qml && ! grep -q 'rightMargin: 36' $QSS/ui/NotificationRow.qml && grep -q 'history: notifList.model' $QSS/ui/main.qml"
chk "bar indicators magnify the glyph only; pane visibility imperative" "grep -c 'scale: hover.hovered' $QSS/ui/Indicator.qml | grep -qx 1 && ! grep -qE '^    scale: hover.hovered' $QSS/ui/Indicator.qml && grep -q 'ReleaseWithinBounds' $QSS/ui/Indicator.qml && ! grep -q 'visible: root.paneMode !== \"closed\"' $QSS/ui/main.qml"
chk "dock: applet width constant on hover (resting + reserve), magnify write-back" "grep -q 'Layout.preferredWidth: dock.appletWidth' $DKS/ui/main.qml && grep -q 'readonly property int reserve' $DKS/ui/main.qml && grep -q 'in.patienceai.fabos.quicksettings' $DKS/ui/main.qml && grep -q evaluateScript $DKS/ui/main.qml"
chk "one owner per shared setting: bar size + magnify switch in quick settings, strength in the dock" "! grep -q 'name=\"magnification\"' $QSS/config/main.xml && ! grep -q 'writeConfig(\\\\\"magnification' $QSS/ui/status.js && ! grep -q cfg_magnification $QSS/ui/configGeneral.qml && grep -q 'name=\"magnification\"' $DKS/config/main.xml && ! grep -q 'quick.writeConfig(\"magnification\"' $SRC/packages/fabos-desktop/usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js"
chk "applet harness renders both Fab OS schemes with the kde platform theme + a real pointer" "grep -q 'QT_QPA_PLATFORMTHEME=kde' $SRC/tests/desktop-applets-qml-test.sh && grep -q FabDark $SRC/tests/desktop-applets-qml-test.sh && grep -q FabLight $SRC/tests/desktop-applets-qml-test.sh && test -f $SRC/tests/quicksettings-qml-harness/fakeinput.py && grep -q org_kde_kwin_fake_input $SRC/tests/dock-qml-harness/kwin-session.sh"
exit $fail
