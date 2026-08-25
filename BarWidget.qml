import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "." as PlexCore

BarWidget {
  id: root
  moduleName: "io.github.pjgeutjens.omaplex"

  readonly property color plexGold: "#e5a00d"

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    labelVisible: false
    hasVisualContent: true
    fixedWidth: root.vertical ? -1 : plexRow.implicitWidth + button.scaledHorizontalMargin * 2
    active: PlexCore.PlexState.newCount > 0
    activeColor: root.plexGold
    tooltipText: PlexCore.PlexState.tooltipText

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) PlexCore.PlexState.refresh()
      else if (buttonCode === Qt.RightButton) {
        var panel = panelLoader.item
        if (panel && panel.opened && panel.settingsOpen === true)
          panel.close()
        else {
          PlexCore.PlexState.settingsRequested = true
          root.open()
        }
      }
      else root.toggle()
    }

    Row {
      id: plexRow
      anchors.centerIn: parent
      spacing: Style.space(4)

      Text {
        text: "󰚺"
        textFormat: Text.PlainText
        color: button.active ? button.activeColor : button.foreground
        font.family: button.fontFamily
        font.pixelSize: Style.bar.iconFont
        renderType: Text.NativeRendering
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        visible: !root.vertical && PlexCore.PlexState.newCount > 0
        text: String(PlexCore.PlexState.newCount)
        textFormat: Text.PlainText
        color: button.activeColor
        font.family: button.fontFamily
        font.pixelSize: button.fontSize
        font.bold: true
        anchors.verticalCenter: parent.verticalCenter
      }
    }
  }
}
