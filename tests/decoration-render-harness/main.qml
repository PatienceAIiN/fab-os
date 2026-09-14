// Renders /theme/decoration.svg (the Fab OS Aurorae frame) as KSvg.FrameSvgItem frames — the same FrameSvg engine Aurorae v2
// paints with — and grabs both the active and the inactive frame to /out/*.png for tests/decoration-render-harness/probe.py.
import QtQuick
import org.kde.plasma.plasmoid
import org.kde.ksvg as KSvg
PlasmoidItem {
    id: root
    width: 400; height: 300
    property string theme: "/theme/decoration.svg"
    KSvg.FrameSvgItem { id: fa; x: 0; y: 0; width: 400; height: 300; imagePath: root.theme; prefix: "decoration"; enabledBorders: KSvg.FrameSvg.AllBorders; visible: true }
    KSvg.FrameSvgItem { id: fi; x: 0; y: 0; width: 400; height: 300; imagePath: root.theme; prefix: "decoration-inactive"; enabledBorders: KSvg.FrameSvg.AllBorders; visible: false }
    KSvg.FrameSvg { id: probe; imagePath: root.theme
        Component.onCompleted: console.log("PREFIXES decoration=" + hasElementPrefix("decoration") + " inactive=" + hasElementPrefix("decoration-inactive") + " mask=" + hasElementPrefix("mask") + " maximized=" + hasElementPrefix("decoration-maximized")) }
    Timer { interval: 2500; running: true; onTriggered: {
        console.log("MARGINS active L=" + fa.margins.left + " T=" + fa.margins.top + " R=" + fa.margins.right + " B=" + fa.margins.bottom)
        fa.grabToImage(function(r){ r.saveToFile("/out/decoration-active.png"); console.log("SAVED active"); fa.visible = false; fi.visible = true;
            fi.grabToImage(function(r2){ r2.saveToFile("/out/decoration-inactive.png"); console.log("SAVED inactive"); Qt.quit() }) }) } }
}
