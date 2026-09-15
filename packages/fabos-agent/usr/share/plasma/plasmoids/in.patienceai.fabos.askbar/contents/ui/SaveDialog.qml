import QtQuick
import QtQuick.Dialogs

// The viewer's "Save as" file dialog, kept in its own file so ImageViewer.qml can load it through a Loader: on a machine
// without the QtQuick.Dialogs module the Loader fails, nothing else breaks, and Save as falls back to a copy in ~/Pictures.
FileDialog {
    id: dlg
    property string suggestedName: ""
    signal chosen(string localPath)
    title: "Save image as"
    fileMode: FileDialog.SaveFile
    nameFilters: ["Images (*.png *.jpg *.jpeg)", "All files (*)"]
    onAccepted: {
        var u = String(dlg.selectedFile)
        if (u.indexOf("file://") === 0) u = u.slice(7)
        try { u = decodeURIComponent(u) } catch (e) { }
        if (u.length) dlg.chosen(u)
    }
}
