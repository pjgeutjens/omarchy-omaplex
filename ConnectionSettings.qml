import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "." as PlexCore

Rectangle {
  id: root

  property bool opened: false
  property bool confirmClear: false
  property color foreground: Color.foreground
  property color dimForeground: Qt.darker(foreground, 1.55)
  property color urgentForeground: Color.urgent
  property string fontFamily: Style.font.family
  property Component iconComponent
  property bool showNewItemCount: true
  property bool usePlexGoldForNewItems: true

  signal dismissRequested()
  signal showNewItemCountRequested(bool value)
  signal usePlexGoldForNewItemsRequested(bool value)

  readonly property bool inputFocused: serverField.activeFocus || tokenField.activeFocus
    || saveSettingsButton.activeFocus || closeSettingsButton.activeFocus
    || clearSettingsButton.activeFocus || newItemCountToggle.activeFocus
    || plexGoldToggle.activeFocus
  readonly property real contentImplicitHeight: settingsColumn.implicitHeight

  visible: opened
  color: Color.background

  function open() {
    confirmClear = false
    opened = true
    serverField.text = PlexCore.PlexState.connectionServer
    tokenField.text = ""
    Qt.callLater(function() { serverField.forceActiveFocus() })
  }

  function close() {
    if (!PlexCore.PlexState.configured) {
      dismissRequested()
      return
    }
    dismiss()
  }

  function dismiss() {
    opened = false
    confirmClear = false
    tokenField.text = ""
  }

  function submit() {
    var submitted = PlexCore.PlexState.configure({
      server: serverField.text,
      token: tokenField.text
    })
    if (submitted) tokenField.text = ""
  }

  function finishConfiguration(success) {
    if (!success) return
    opened = false
    confirmClear = false
  }

  function syncServer() {
    if (opened && !PlexCore.PlexState.configured)
      serverField.text = PlexCore.PlexState.connectionServer
  }

  function librarySummary() {
    var movies = PlexCore.PlexState.movieLibraries
    var series = PlexCore.PlexState.seriesLibraries
    var movieNames = movies.map(function(item) { return item.title || "Section " + item.id })
    var seriesNames = series.map(function(item) { return item.title || "Section " + item.id })
    var lines = []
    if (movieNames.length) lines.push("Movies · " + movieNames.join(", "))
    if (seriesNames.length) lines.push("Shows · " + seriesNames.join(", "))
    return lines.length ? lines.join("\n") : "Libraries are discovered when the connection is tested."
  }

  Flickable {
    anchors.fill: parent
    contentWidth: width
    contentHeight: settingsColumn.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: settingsColumn
      width: parent.width
      spacing: Style.space(10)

      PanelHero {
        width: parent.width
        iconComponent: root.iconComponent
        title: "Plex"
        meta: PlexCore.PlexState.configured ? "Connection settings" : "Connect to Plex"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      PanelSeparator { foreground: root.foreground }

      PanelSectionHeader {
        width: parent.width
        text: "SERVER"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      TextField {
        id: serverField
        width: parent.width
        placeholderText: "http://plex-server:32400"
        maximumLength: 512
        foreground: root.foreground
        font.family: root.fontFamily
        enabled: !PlexCore.PlexState.updating
        inputMethodHints: Qt.ImhUrlCharactersOnly
        onAccepted: tokenField.forceActiveFocus()
        Keys.onEscapePressed: root.close()
      }

      PanelSectionHeader {
        width: parent.width
        text: "PLEX TOKEN"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      TextField {
        id: tokenField
        width: parent.width
        placeholderText: PlexCore.PlexState.configured
          ? "Leave blank to keep the saved token" : "Paste your Plex token"
        password: true
        maximumLength: 256
        foreground: root.foreground
        font.family: root.fontFamily
        enabled: !PlexCore.PlexState.updating
        onAccepted: root.submit()
        Keys.onEscapePressed: root.close()
      }

      Text {
        visible: PlexCore.PlexState.configured
        width: parent.width
        text: root.librarySummary()
        textFormat: Text.PlainText
        color: root.dimForeground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }

      PanelSectionHeader {
        width: parent.width
        text: "APPEARANCE"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Toggle {
        id: newItemCountToggle
        width: parent.width
        label: "Show new-item count"
        description: "Display the number beside the Plex icon in the bar."
        checked: root.showNewItemCount
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.showNewItemCountRequested(!root.showNewItemCount)
        Keys.onEscapePressed: root.close()
      }

      Toggle {
        id: plexGoldToggle
        width: parent.width
        label: "Use Plex gold for new items"
        description: "Otherwise use the Omarchy theme's active color."
        checked: root.usePlexGoldForNewItems
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.usePlexGoldForNewItemsRequested(!root.usePlexGoldForNewItems)
        Keys.onEscapePressed: root.close()
      }

      Text {
        visible: PlexCore.PlexState.lastError !== ""
        width: parent.width
        text: PlexCore.PlexState.safeText(PlexCore.PlexState.lastError, 220)
        textFormat: Text.PlainText
        color: root.urgentForeground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }

      Text {
        visible: PlexCore.PlexState.setupMessage !== ""
        width: parent.width
        text: PlexCore.PlexState.safeText(PlexCore.PlexState.setupMessage, 220)
        textFormat: Text.PlainText
        color: Color.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }

      Button {
        id: saveSettingsButton
        width: parent.width
        text: PlexCore.PlexState.configuring ? "Testing connection…" : "Test and save"
        iconText: PlexCore.PlexState.configuring ? "󰑐" : ""
        iconSpinning: PlexCore.PlexState.configuring
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        focusable: true
        enabled: !PlexCore.PlexState.updating
        onClicked: root.submit()
      }

      Row {
        visible: PlexCore.PlexState.configured
        width: parent.width
        spacing: Style.space(5)

        Button {
          id: closeSettingsButton
          width: (parent.width - parent.spacing) / 2
          text: "Close"
          foreground: root.foreground
          fontFamily: root.fontFamily
          bordered: true
          focusable: true
          enabled: !PlexCore.PlexState.updating
          onClicked: root.close()
        }

        Button {
          id: clearSettingsButton
          width: (parent.width - parent.spacing) / 2
          text: root.confirmClear ? "Confirm remove" : "Remove credentials"
          foreground: root.confirmClear ? root.urgentForeground : root.foreground
          fontFamily: root.fontFamily
          bordered: true
          focusable: true
          enabled: !PlexCore.PlexState.updating
          onClicked: {
            if (root.confirmClear) {
              if (PlexCore.PlexState.clearConfiguration()) root.confirmClear = false
            } else root.confirmClear = true
          }
        }
      }

      Text {
        width: parent.width
        text: "The token is sent to the helper over stdin and stored in the desktop secret service. It is never written to the plugin settings, command line, logs, or cache. A failed test keeps the previous connection."
        textFormat: Text.PlainText
        color: root.dimForeground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }
    }
  }
}
