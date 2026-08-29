import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "." as PlexCore

Rectangle {
  id: root

  property bool opened: false
  property bool confirmClear: false
  property bool manualExpanded: false
  property bool serverButtonFocused: false
  property color foreground: Color.foreground
  property color dimForeground: Qt.darker(foreground, 1.55)
  property color urgentForeground: Color.urgent
  property string fontFamily: Style.font.family
  property Component iconComponent
  property bool showNewItemCount: true
  property bool usePlexGoldForNewItems: true
  property bool autoPlayNextEpisode: false
  property string subtitleSearchLanguage: "en"

  signal dismissRequested()
  signal showNewItemCountRequested(bool value)
  signal usePlexGoldForNewItemsRequested(bool value)
  signal autoPlayNextEpisodeRequested(bool value)
  signal subtitleSearchLanguageRequested(string value)

  readonly property bool inputFocused: serverField.activeFocus || tokenField.activeFocus
    || saveSettingsButton.activeFocus || closeSettingsButton.activeFocus
    || clearSettingsButton.activeFocus || newItemCountToggle.activeFocus
    || plexGoldToggle.activeFocus || autoPlayNextToggle.activeFocus
    || subtitleLanguageField.activeFocus || signInButton.activeFocus
    || cancelSignInButton.activeFocus || advancedButton.activeFocus
    || serverButtonFocused
  readonly property real contentImplicitHeight: settingsColumn.implicitHeight

  visible: opened
  color: Color.background

  function open() {
    confirmClear = false
    opened = true
    manualExpanded = false
    serverButtonFocused = false
    serverField.text = PlexCore.PlexState.connectionServer
    tokenField.text = ""
    subtitleLanguageField.text = root.subtitleSearchLanguage
    Qt.callLater(function() { signInButton.forceActiveFocus() })
  }

  function close() {
    if (PlexCore.PlexState.authenticating) {
      if (PlexCore.PlexState.authenticationState === "waiting"
          || PlexCore.PlexState.authenticationState === "servers")
        PlexCore.PlexState.cancelPlexSignIn()
      return
    }
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
    manualExpanded = false
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

  function commitSubtitleLanguage() {
    var language = subtitleLanguageField.text.trim().toLowerCase()
    if (/^[a-z]{2}$/.test(language)) {
      subtitleLanguageField.text = language
      root.subtitleSearchLanguageRequested(language)
    } else {
      subtitleLanguageField.text = root.subtitleSearchLanguage
    }
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
        text: "ACCOUNT"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Text {
        visible: PlexCore.PlexState.configured
        width: parent.width
        text: (PlexCore.PlexState.authenticationMode === "plex"
          ? "Signed in with Plex" : "Manual connection")
          + " · " + (PlexCore.PlexState.connectionName
            || PlexCore.PlexState.connectionServer)
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }

      Button {
        id: signInButton
        width: parent.width
        text: PlexCore.PlexState.authenticationState === "starting"
          ? "Opening Plex sign-in…"
          : (PlexCore.PlexState.authenticationState === "waiting"
            ? "Waiting for Plex…"
            : (PlexCore.PlexState.authenticationState === "connecting"
              ? "Connecting to Plex…"
              : (PlexCore.PlexState.authenticationState === "servers"
                ? "Choose a server below"
                : (PlexCore.PlexState.authenticationMode === "plex"
                  ? "Sign in with Plex again" : "Sign in with Plex"))))
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        focusable: true
        enabled: !PlexCore.PlexState.updating && !PlexCore.PlexState.authenticating
        onClicked: PlexCore.PlexState.startPlexSignIn()
        Keys.onEscapePressed: root.close()
      }

      Text {
        width: parent.width
        text: "Your browser handles the Plex password. Omaplex stores the Plex account and server tokens in the desktop secret service."
        textFormat: Text.PlainText
        color: root.dimForeground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Column {
        visible: PlexCore.PlexState.authenticationState === "servers"
        width: parent.width
        spacing: Style.space(5)

        Repeater {
          model: PlexCore.PlexState.authenticationServers

          Button {
            required property var modelData
            width: settingsColumn.width
            text: modelData.name + (modelData.owned ? " · Owned" : " · Shared")
              + (modelData.available ? "" : " · Offline")
            foreground: root.foreground
            fontFamily: root.fontFamily
            bordered: true
            focusable: true
            enabled: !PlexCore.PlexState.updating && modelData.available
            onActiveFocusChanged: root.serverButtonFocused = activeFocus
            onClicked: PlexCore.PlexState.selectPlexServer(modelData.machineIdentifier)
            Keys.onEscapePressed: root.close()
          }
        }
      }

      Button {
        id: cancelSignInButton
        visible: PlexCore.PlexState.authenticationState === "waiting"
          || PlexCore.PlexState.authenticationState === "servers"
        width: parent.width
        text: "Cancel sign-in"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        focusable: true
        enabled: !PlexCore.PlexState.authCancelProcessRunning
        onClicked: PlexCore.PlexState.cancelPlexSignIn()
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

      Button {
        id: advancedButton
        visible: !PlexCore.PlexState.authenticating
        width: parent.width
        text: root.manualExpanded ? "Hide manual connection" : "Advanced manual connection"
        foreground: root.dimForeground
        fontFamily: root.fontFamily
        bordered: false
        focusable: true
        enabled: !PlexCore.PlexState.updating
        onClicked: {
          root.manualExpanded = !root.manualExpanded
          if (root.manualExpanded) Qt.callLater(function() { serverField.forceActiveFocus() })
        }
        Keys.onEscapePressed: root.close()
      }

      Column {
        visible: root.manualExpanded && !PlexCore.PlexState.authenticating
        width: parent.width
        spacing: Style.space(8)

        PanelSectionHeader {
          width: parent.width
          text: "SERVER URL"
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
          maximumLength: 8192
          foreground: root.foreground
          font.family: root.fontFamily
          enabled: !PlexCore.PlexState.updating
          onAccepted: root.submit()
          Keys.onEscapePressed: root.close()
        }

        Button {
          id: saveSettingsButton
          width: parent.width
          text: PlexCore.PlexState.configuring ? "Testing connection…" : "Test and save manual connection"
          iconText: PlexCore.PlexState.configuring ? "󰑐" : ""
          iconSpinning: PlexCore.PlexState.configuring
          foreground: root.foreground
          fontFamily: root.fontFamily
          bordered: true
          focusable: true
          enabled: !PlexCore.PlexState.updating
          onClicked: root.submit()
        }
      }

      PanelSectionHeader {
        width: parent.width
        text: "PLAYBACK"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Toggle {
        id: autoPlayNextToggle
        width: parent.width
        label: "Auto-play next episode"
        description: "Continue with Plex's next episode after the current episode ends."
        checked: root.autoPlayNextEpisode
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.autoPlayNextEpisodeRequested(!root.autoPlayNextEpisode)
        Keys.onEscapePressed: root.close()
      }

      Text {
        width: parent.width
        text: "Subtitle search language"
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: true
      }

      TextField {
        id: subtitleLanguageField
        width: parent.width
        placeholderText: "en"
        maximumLength: 2
        foreground: root.foreground
        font.family: root.fontFamily
        inputMethodHints: Qt.ImhLatinOnly | Qt.ImhNoPredictiveText
        onAccepted: {
          root.commitSubtitleLanguage()
          autoPlayNextToggle.forceActiveFocus()
        }
        onActiveFocusChanged: {
          if (!activeFocus && root.opened) root.commitSubtitleLanguage()
        }
        Keys.onEscapePressed: root.close()
      }

      Text {
        width: parent.width
        text: "Two-letter language used by Ctrl+J in the player, such as en, nl, fr, or de."
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
        label: "Override theme colors"
        description: "Force the black-and-gold Plex logo when new items are available."
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
        visible: root.manualExpanded
        width: parent.width
        text: "Manual tokens are sent to the helper over stdin and stored in the desktop secret service. They are never written to plugin settings, command lines, logs, or cache. A failed test keeps the previous connection."
        textFormat: Text.PlainText
        color: root.dimForeground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Text {
        visible: PlexCore.PlexState.configured
        width: parent.width
        text: "Removing credentials signs out this installation locally. You can also revoke Plex for Omarchy from Plex's Authorized Devices page."
        textFormat: Text.PlainText
        color: root.dimForeground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }
    }
  }
}
