import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "." as PlexCore

BarWidget {
  id: root
  moduleName: "io.github.pjgeutjens.omaplex"

  readonly property color plexGold: "#e5a00d"
  readonly property bool showNewItemCount: setting("showNewItemCount", true) !== false
  readonly property bool usePlexGoldForNewItems:
    setting("usePlexGoldForNewItems", true) !== false

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
    activeColor: root.usePlexGoldForNewItems
      ? root.plexGold : (root.bar ? root.bar.urgent : Color.urgent)
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

      Item {
        readonly property bool branded: button.active && root.usePlexGoldForNewItems

        width: Style.bar.iconFont
        height: Style.bar.iconFont
        anchors.verticalCenter: parent.verticalCenter

        Rectangle {
          anchors.fill: parent
          radius: 2
          color: "#000000"
          visible: parent.branded
        }

        Text {
          anchors.centerIn: parent
          text: "󰚺"
          textFormat: Text.PlainText
          color: button.active ? button.activeColor : button.foreground
          font.family: button.fontFamily
          font.pixelSize: Style.bar.iconFont
          renderType: Text.NativeRendering
          visible: !parent.branded
        }

        Text {
          anchors.centerIn: parent
          anchors.horizontalCenterOffset: 0.5
          text: "❯"
          textFormat: Text.PlainText
          color: root.plexGold
          font.family: button.fontFamily
          font.pixelSize: Style.bar.iconFont * 0.9
          font.bold: true
          renderType: Text.NativeRendering
          visible: parent.branded
        }
      }

      Text {
        visible: !root.vertical && root.showNewItemCount && PlexCore.PlexState.newCount > 0
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
