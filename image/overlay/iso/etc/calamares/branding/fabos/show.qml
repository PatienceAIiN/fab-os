import QtQuick 2.0;
import calamares.slideshow 1.0;
Presentation {
    id: presentation
    Timer { interval: 12000; running: presentation.activatedInCalamares; repeat: true; onTriggered: presentation.goToNextSlide() }
    function onActivate() {} function onLeave() {}
    Slide { anchors.fill: parent; Rectangle { anchors.fill: parent; color: "#0E1116" }
        Image { source: "slide.png"; anchors.fill: parent; fillMode: Image.PreserveAspectCrop; opacity: 0.55 }
        Column { anchors.centerIn: parent; spacing: 14; width: parent.width * 0.8
            Text { text: "Fab OS does the work."; color: "white"; font.pixelSize: 34; font.bold: true; font.family: "Inter"; wrapMode: Text.WordWrap; width: parent.width }
            Text { text: "Ask the built-in agent to open apps, write code, send mail and track replies. Every step is checked by a policy you control and kept in history."; color: "#C9D1DC"; font.pixelSize: 18; font.family: "Inter"; wrapMode: Text.WordWrap; width: parent.width } } }
    Slide { anchors.fill: parent; Rectangle { anchors.fill: parent; color: "#0E1116" }
        Column { anchors.centerIn: parent; spacing: 14; width: parent.width * 0.8
            Text { text: "Ubuntu underneath. Yours on top."; color: "white"; font.pixelSize: 34; font.bold: true; font.family: "Inter"; wrapMode: Text.WordWrap; width: parent.width }
            Text { text: "Every Ubuntu package and command works. Security updates come from Ubuntu; Fab OS updates come from Patience AI, signed. Standard or Beta channel, your choice."; color: "#C9D1DC"; font.pixelSize: 18; font.family: "Inter"; wrapMode: Text.WordWrap; width: parent.width } } }
    Slide { anchors.fill: parent; Rectangle { anchors.fill: parent; color: "#0E1116" }
        Column { anchors.centerIn: parent; spacing: 14; width: parent.width * 0.8
            Text { text: "After the first boot"; color: "white"; font.pixelSize: 34; font.bold: true; font.family: "Inter"; wrapMode: Text.WordWrap; width: parent.width }
            Text { text: "Once online, Fab OS installs updates, hardware drivers and firmware in the background and tells you when it is done. Add an AI provider key in Command Center → Settings, or use a local model offline."; color: "#C9D1DC"; font.pixelSize: 18; font.family: "Inter"; wrapMode: Text.WordWrap; width: parent.width } } }
}
