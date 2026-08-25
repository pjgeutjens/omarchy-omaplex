import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui
import "." as PlexCore
import "Model.js" as Model

Panel {
  id: root
  moduleName: "io.github.pjgeutjens.plex-recently-added"
  ipcTarget: "io.github.pjgeutjens.plex-recently-added"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property int selectedIndex: 0
  property bool cursorActive: true
  property string playbackMode: "windowed"
  property bool showWatched: true
  property bool helpOpen: false
  property string helpQuery: ""
  property bool mediaSearchOpen: false
  property string mediaQuery: ""
  property string activeView: "continue"
  property bool settingsOpen: false
  property bool confirmClear: false

  readonly property var helpBindings: [
    { category: "Views & navigation", keys: "↑/↓ or J/K", action: "Move through media" },
    { category: "Views & navigation", keys: "Enter", action: "Play the selected item" },
    { category: "Views & navigation", keys: "[ / ]", action: "Move between compact Plex views" },
    { category: "Views & navigation", keys: "T", action: "Show or hide watched items" },
    { category: "Views & navigation", keys: "C", action: "Show Continue Watching" },
    { category: "Views & navigation", keys: "A", action: "Show all recently added media" },
    { category: "Views & navigation", keys: "M", action: "Show recently added movies" },
    { category: "Views & navigation", keys: "S", action: "Show recently added series" },
    { category: "Views & navigation", keys: "B", action: "Open Browse All fullscreen" },
    { category: "Views & navigation", keys: "/", action: "Search the current compact media view" },
    { category: "Panel actions", keys: "?", action: "Toggle this keybindings list" },
    { category: "Panel actions", keys: ",", action: "Open connection settings" },
    { category: "Panel actions", keys: "W", action: "Use a floating window" },
    { category: "Panel actions", keys: "F", action: "Use fullscreen playback" },
    { category: "Panel actions", keys: "O", action: "Open the item in Plex Web" },
    { category: "Panel actions", keys: "X", action: "Toggle selected item watched or unwatched" },
    { category: "Panel actions", keys: "R", action: "Refresh recently added media" },
    { category: "Panel actions", keys: "U", action: "Discover and scan all movie and series libraries" },
    { category: "Panel actions", keys: "Esc", action: "Close help or the panel" },
    { category: "Playback", keys: "Mouse", action: "Show mpv playback controls" },
    { category: "Playback", keys: "Space", action: "Pause or resume" },
    { category: "Playback", keys: "←/→", action: "Seek backward or forward" },
    { category: "Playback", keys: "↑/↓", action: "Raise or lower volume" },
    { category: "Playback", keys: "M", action: "Mute or unmute" },
    { category: "Playback", keys: "#", action: "Cycle audio streams" },
    { category: "Playback", keys: "J", action: "Cycle subtitle streams" },
    { category: "Playback", keys: "F", action: "Toggle player fullscreen" },
    { category: "Playback", keys: "Q", action: "Quit the player" },
    { category: "Browse All", keys: "/", action: "Fuzzy-search the selected Movies or Series scope" },
    { category: "Browse All", keys: "M/S", action: "Browse movies or series" },
    { category: "Browse All", keys: "N/P", action: "Move to the next or previous page" },
    { category: "Browse All", keys: "Esc", action: "Go back or close Browse All" }
  ]

  readonly property var sourceItems: activeView === "movies"
    ? PlexCore.PlexState.movieItems
    : (activeView === "series" ? PlexCore.PlexState.seriesItems
      : (activeView === "recent" ? PlexCore.PlexState.items : PlexCore.PlexState.continueItems))
  readonly property var watchFilteredItems: showWatched
    ? sourceItems
    : sourceItems.filter(function(item) { return item.watchState !== "watched" })
  readonly property var visibleItems: watchFilteredItems.filter(function(item) {
    return Model.matchesMediaQuery(item, mediaQuery)
  })
  readonly property int watchedCount: sourceItems.filter(function(item) {
    return item.watchState === "watched"
  }).length
  readonly property string activeViewTitle: activeView === "movies"
    ? "RECENT MOVIES" : (activeView === "series" ? "RECENT SERIES"
      : (activeView === "recent" ? "RECENTLY ADDED" : "CONTINUE WATCHING"))
  readonly property var filteredHelpBindings: filterHelpBindings()
  readonly property var groupedHelpBindings: groupHelpBindings()

  readonly property var barIdentity: hostWidget || root
  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property color dimForeground: Qt.darker(contentForeground, 1.55)
  readonly property color urgentForeground: bar ? bar.urgent : Color.urgent
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

  function open() {
    var showSettings = PlexCore.PlexState.settingsRequested
      || (PlexCore.PlexState.initialized && !PlexCore.PlexState.configured)
    PlexCore.PlexState.settingsRequested = false
    root.controller.show()
    root.clampSelection()
    if (!PlexCore.PlexState.initialized) PlexCore.PlexState.loadStatus()
    if (showSettings)
      root.openSettings()
    else {
      Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      if (PlexCore.PlexState.configured) Qt.callLater(PlexCore.PlexState.refresh)
    }
  }

  function close() {
    mediaSearchOpen = false
    mediaQuery = ""
    root.controller.hide()
  }
  function toggle() { if (root.opened) root.close(); else root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function clampSelection() {
    selectedIndex = Math.max(0, Math.min(selectedIndex, Math.max(0, root.visibleItems.length - 1)))
  }

  function focusItem(index) {
    cursorActive = true
    selectedIndex = Math.max(0, Math.min(Number(index), Math.max(0, root.visibleItems.length - 1)))
    keyCatcher.forceActiveFocus()
    Qt.callLater(function() { mediaList.positionViewAtIndex(selectedIndex, ListView.Contain) })
  }

  function moveSelection(direction) {
    var count = root.visibleItems.length
    if (count === 0) return
    selectedIndex = (selectedIndex + direction + count) % count
    cursorActive = true
    Qt.callLater(function() { mediaList.positionViewAtIndex(selectedIndex, ListView.Contain) })
  }

  function playSelection() {
    if (root.visibleItems.length > 0)
      PlexCore.PlexState.playItem(root.visibleItems[selectedIndex], playbackMode)
  }

  function toggleSelectedWatchState() {
    if (root.visibleItems.length > 0)
      PlexCore.PlexState.toggleWatchState(root.visibleItems[selectedIndex])
  }

  function setPlaybackMode(mode) {
    playbackMode = mode === "fullscreen" ? "fullscreen" : "windowed"
    keyCatcher.forceActiveFocus()
  }

  function toggleWatched() {
    showWatched = !showWatched
    clampSelection()
    keyCatcher.forceActiveFocus()
  }

  function setView(view) {
    activeView = ["continue", "recent", "movies", "series"].indexOf(view) === -1 ? "continue" : view
    selectedIndex = 0
    cursorActive = true
    Qt.callLater(function() {
      if (root.mediaSearchOpen) mediaSearchField.forceActiveFocus()
      else keyCatcher.forceActiveFocus()
    })
  }

  function cycleView(delta) {
    var views = ["continue", "recent", "movies", "series"]
    var index = views.indexOf(activeView)
    if (index < 0) index = 0
    setView(views[(index + delta + views.length) % views.length])
  }

  function summonBrowser() {
    if (!root.bar || !root.bar.shell || typeof root.bar.shell.summon !== "function") return
    root.controller.hide()
    root.bar.shell.summon(root.moduleName, JSON.stringify({ view: "movies" }))
  }

  function openMediaSearch() {
    if (settingsOpen) return
    helpOpen = false
    helpQuery = ""
    mediaSearchOpen = true
    selectedIndex = 0
    Qt.callLater(function() { mediaSearchField.forceActiveFocus() })
  }

  function closeMediaSearch() {
    mediaSearchOpen = false
    mediaQuery = ""
    selectedIndex = 0
    clampSelection()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function filterHelpBindings() {
    var query = String(helpQuery || "").trim().toLowerCase()
    if (query === "") return helpBindings
    return helpBindings.filter(function(binding) {
      return (binding.category + " " + binding.keys + " " + binding.action)
        .toLowerCase().indexOf(query) !== -1
    })
  }

  function groupHelpBindings() {
    var rows = []
    var category = ""
    for (var index = 0; index < filteredHelpBindings.length; index++) {
      var binding = filteredHelpBindings[index]
      if (binding.category !== category) {
        category = binding.category
        rows.push({ kind: "header", category: category, keys: "", action: "" })
      }
      rows.push({
        kind: "binding",
        category: binding.category,
        keys: binding.keys,
        action: binding.action
      })
    }
    return rows
  }

  function openHelp() {
    mediaSearchOpen = false
    mediaQuery = ""
    helpOpen = true
    Qt.callLater(function() { helpSearch.forceActiveFocus() })
  }

  function closeHelp() {
    helpOpen = false
    helpQuery = ""
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function toggleHelp() {
    if (helpOpen) closeHelp()
    else openHelp()
  }

  function openSettings() {
    helpOpen = false
    helpQuery = ""
    mediaSearchOpen = false
    mediaQuery = ""
    confirmClear = false
    settingsOpen = true
    serverField.text = PlexCore.PlexState.connectionServer
    tokenField.text = ""
    Qt.callLater(function() { serverField.forceActiveFocus() })
  }

  function closeSettings() {
    confirmClear = false
    if (!PlexCore.PlexState.configured) {
      root.close()
      return
    }
    settingsOpen = false
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function submitSetup() {
    var submitted = PlexCore.PlexState.configure({
      server: serverField.text,
      token: tokenField.text
    })
    if (submitted) tokenField.text = ""
  }

  function librarySummary() {
    var movies = PlexCore.PlexState.movieLibraries
    var series = PlexCore.PlexState.seriesLibraries
    var movieNames = movies.map(function(item) { return item.title || "Section " + item.id })
    var seriesNames = series.map(function(item) { return item.title || "Section " + item.id })
    var lines = []
    if (movieNames.length) lines.push("Movies · " + movieNames.join(", "))
    if (seriesNames.length) lines.push("Series · " + seriesNames.join(", "))
    return lines.length ? lines.join("\n") : "Libraries are discovered when the connection is tested."
  }

  onOpenedChanged: if (opened) {
    root.cursorActive = true
    root.clampSelection()
    if (PlexCore.PlexState.initialized && !PlexCore.PlexState.configured)
      root.openSettings()
    else Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Connections {
    target: PlexCore.PlexState
    function onItemsChanged() { root.clampSelection() }
    function onContinueItemsChanged() { root.clampSelection() }
    function onMovieItemsChanged() { root.clampSelection() }
    function onSeriesItemsChanged() { root.clampSelection() }
    function onInitializedChanged() {
      if (root.opened && PlexCore.PlexState.initialized && !PlexCore.PlexState.configured)
        root.openSettings()
    }
    function onConfigurationFinished(success, detail) {
      if (success) {
        root.settingsOpen = false
        root.confirmClear = false
        Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      }
    }
    function onConfiguredChanged() {
      if (root.settingsOpen && !PlexCore.PlexState.configured)
        serverField.text = PlexCore.PlexState.connectionServer
    }
  }

  Component {
    id: plexIcon
    Text {
      text: "󰚺"
      textFormat: Text.PlainText
      color: Color.accent
      font.family: root.contentFontFamily
      font.pixelSize: Style.font.display
      horizontalAlignment: Text.AlignHCenter
      verticalAlignment: Text.AlignVCenter
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(430))
    contentHeight: panel.fittedContentHeight(
      root.settingsOpen ? settingsColumn.implicitHeight : panelColumn.implicitHeight,
      Style.space(760)
    )

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: helpSearch.activeFocus || mediaSearchField.activeFocus
        || serverField.activeFocus || tokenField.activeFocus
        || saveSettingsButton.activeFocus || closeSettingsButton.activeFocus
        || clearSettingsButton.activeFocus
      onMoveRequested: function(dx, dy) {
        if (!root.settingsOpen && !root.helpOpen) {
          if (dy !== 0) root.moveSelection(dy)
          else if (dx !== 0) root.switchPanel(dx)
        }
      }
      onActivateRequested: if (!root.settingsOpen && !root.helpOpen) root.playSelection()
      onDeleteRequested: if (!root.settingsOpen && !root.helpOpen) root.toggleSelectedWatchState()
      onCloseRequested: {
        if (root.helpOpen) root.closeHelp()
        else if (root.settingsOpen) root.closeSettings()
        else if (root.mediaSearchOpen) root.closeMediaSearch()
        else root.close()
      }
      onTabRequested: function(direction) {
        if (!root.settingsOpen && !root.helpOpen) root.switchPanel(direction)
      }
      onTextKey: function(text) {
        if (root.settingsOpen) {
          if (text === ",") root.closeSettings()
          return
        }
        if (root.helpOpen) {
          if (text === "?") root.closeHelp()
          else if (text === "/") helpSearch.forceActiveFocus()
          return
        }
        if (text === ",") root.openSettings()
        else if (text === "j" || text === "J") root.moveSelection(1)
        else if (text === "k" || text === "K") root.moveSelection(-1)
        else if (text === "r" || text === "R") PlexCore.PlexState.refresh()
        else if (text === "u" || text === "U") PlexCore.PlexState.scanLibraries()
        else if (text === "w" || text === "W") root.setPlaybackMode("windowed")
        else if (text === "f" || text === "F") root.setPlaybackMode("fullscreen")
        else if (text === "[") root.cycleView(-1)
        else if (text === "]") root.cycleView(1)
        else if (text === "t" || text === "T") root.toggleWatched()
        else if (text === "c" || text === "C") root.setView("continue")
        else if (text === "a" || text === "A") root.setView("recent")
        else if (text === "m" || text === "M") root.setView("movies")
        else if (text === "s" || text === "S") root.setView("series")
        else if (text === "b" || text === "B") root.summonBrowser()
        else if (text === "?") root.toggleHelp()
        else if (text === "/") root.openMediaSearch()
        else if ((text === "o" || text === "O") && root.visibleItems.length > 0)
          PlexCore.PlexState.openWebItem(root.visibleItems[root.selectedIndex])
      }

      Column {
        id: panelColumn
        width: parent.width
        spacing: Style.space(10)

        PanelHero {
          width: parent.width
          iconComponent: plexIcon
          title: "Plex"
          meta: PlexCore.PlexState.safeText(
            PlexCore.PlexState.configured
              ? PlexCore.PlexState.freshnessText + " · " + root.sourceItems.length + " items"
              : "Plex setup required",
            180
          )
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
          trailingControl: Component {
            Row {
              spacing: Style.space(3)

              PanelActionButton {
                iconText: "\uEB4C" // cod-screen-full
                tooltipText: "Fullscreen playback (F)"
                foreground: root.playbackMode === "fullscreen" ? Color.accent : root.contentForeground
                fontFamily: root.contentFontFamily
                bordered: root.playbackMode === "fullscreen"
                enabled: !PlexCore.PlexState.playing
                onClicked: root.setPlaybackMode("fullscreen")
              }

              PanelActionButton {
                iconText: "\uEB4D" // cod-screen-normal
                tooltipText: "Windowed playback (W)"
                foreground: root.playbackMode === "windowed" ? Color.accent : root.contentForeground
                fontFamily: root.contentFontFamily
                bordered: root.playbackMode === "windowed"
                enabled: !PlexCore.PlexState.playing
                onClicked: root.setPlaybackMode("windowed")
              }

              PanelActionButton {
                iconText: "?"
                tooltipText: "Keybindings (?)"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                onClicked: root.openHelp()
              }

              PanelActionButton {
                iconText: "\uEB37" // cod-refresh
                tooltipText: PlexCore.PlexState.scanning
                  ? "Scanning libraries…"
                  : "Scan all libraries (U)"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                enabled: PlexCore.PlexState.configured && !PlexCore.PlexState.scanning
                onClicked: PlexCore.PlexState.scanLibraries()
              }
            }
          }
        }

        Text {
          visible: PlexCore.PlexState.lastError !== ""
          width: parent.width
          text: PlexCore.PlexState.safeText(PlexCore.PlexState.lastError, 220)
          textFormat: Text.PlainText
          color: root.urgentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        PanelSeparator { foreground: root.contentForeground }

        Row {
          width: parent.width
          spacing: Style.space(5)

          Button {
            text: "Continue"
            fontSize: Style.font.caption
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            bordered: true
            active: root.activeView === "continue"
            onClicked: root.setView("continue")
          }

          Button {
            text: "Added"
            fontSize: Style.font.caption
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            bordered: true
            active: root.activeView === "recent"
            onClicked: root.setView("recent")
          }

          Button {
            text: "Movies"
            fontSize: Style.font.caption
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            bordered: true
            active: root.activeView === "movies"
            onClicked: root.setView("movies")
          }

          Button {
            text: "Series"
            fontSize: Style.font.caption
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            bordered: true
            active: root.activeView === "series"
            onClicked: root.setView("series")
          }

          Button {
            text: "Browse all"
            fontSize: Style.font.caption
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            bordered: true
            onClicked: root.summonBrowser()
          }
        }

        TextField {
          id: mediaSearchField
          visible: root.mediaSearchOpen
          width: parent.width
          text: root.mediaQuery
          maximumLength: 80
          placeholderText: "Search " + root.activeViewTitle.toLowerCase() + "…"
          foreground: root.contentForeground
          font.family: root.contentFontFamily
          onTextChanged: {
            root.mediaQuery = text
            root.selectedIndex = 0
            root.clampSelection()
          }
          Keys.onDownPressed: {
            keyCatcher.forceActiveFocus()
            root.moveSelection(1)
          }
          onAccepted: {
            keyCatcher.forceActiveFocus()
            root.playSelection()
          }
          Keys.onEscapePressed: root.closeMediaSearch()
        }

        Text {
          visible: PlexCore.PlexState.markMessage !== "" || PlexCore.PlexState.scanMessage !== ""
          width: parent.width
          text: PlexCore.PlexState.safeText(
            PlexCore.PlexState.markMessage !== ""
              ? PlexCore.PlexState.markMessage : PlexCore.PlexState.scanMessage,
            120
          )
          textFormat: Text.PlainText
          color: Color.accent
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }

        Text {
          visible: PlexCore.PlexState.playing
          width: parent.width
          text: "Playing " + PlexCore.PlexState.safeText(PlexCore.PlexState.playingTitle, 160)
            + " in " + PlexCore.PlexState.playbackMode + " mode"
          textFormat: Text.PlainText
          color: Color.accent
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
        }

        Item {
          width: parent.width
          implicitHeight: Math.max(
            listHeader.implicitHeight,
            sourceBadge.implicitHeight,
            watchedVisibilityButton.implicitHeight
          )

          PanelSectionHeader {
            id: listHeader
            text: root.activeViewTitle
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
          }

          Rectangle {
            id: sourceBadge
            width: sourceLabel.implicitWidth + Style.space(14)
            height: Style.space(20)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            radius: Style.cornerRadius
            color: PlexCore.PlexState.sourceState === "updated" ? Color.accent : "transparent"
            border.width: Math.max(1, Style.space(1))
            border.color: PlexCore.PlexState.sourceState === "updated" ? Color.accent : root.dimForeground

            Text {
              id: sourceLabel
              anchors.centerIn: parent
              text: PlexCore.PlexState.sourceLabel
              textFormat: Text.PlainText
              color: PlexCore.PlexState.sourceState === "updated" ? Color.background : root.dimForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }
          }

          PanelActionButton {
            id: watchedVisibilityButton
            visible: root.watchedCount > 0
            anchors.right: sourceBadge.left
            anchors.rightMargin: Style.space(4)
            anchors.verticalCenter: parent.verticalCenter
            iconText: root.showWatched ? "\uEAE7" : "\uEA70" // cod-eye-closed / cod-eye
            tooltipText: root.showWatched ? "Hide watched (T)" : "Show watched (T)"
            foreground: root.showWatched ? root.contentForeground : Color.accent
            fontFamily: root.contentFontFamily
            bordered: !root.showWatched
            onClicked: root.toggleWatched()
          }
        }

        Text {
          visible: !PlexCore.PlexState.initialized
          width: parent.width
          height: Style.space(56)
          text: "Reading saved Plex data…"
          textFormat: Text.PlainText
          color: root.dimForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
          verticalAlignment: Text.AlignVCenter
        }

        Text {
          visible: PlexCore.PlexState.initialized && !PlexCore.PlexState.configured
          width: parent.width
          text: "Enter your Plex server and token in Connection settings. Movie and series libraries are discovered automatically."
          textFormat: Text.PlainText
          color: root.dimForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
          horizontalAlignment: Text.AlignHCenter
        }

        Text {
          visible: PlexCore.PlexState.initialized && PlexCore.PlexState.configured
            && root.sourceItems.length === 0
          width: parent.width
          height: Style.space(56)
          text: PlexCore.PlexState.updating ? "Checking Plex…" : "No items in this view"
          textFormat: Text.PlainText
          color: root.dimForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
          verticalAlignment: Text.AlignVCenter
        }


        Text {
          visible: PlexCore.PlexState.initialized && PlexCore.PlexState.configured
            && root.sourceItems.length > 0 && root.visibleItems.length === 0
          width: parent.width
          height: Style.space(56)
          text: root.mediaQuery.trim() !== ""
            ? "No matching media in this view" : "All items in this view are watched"
          textFormat: Text.PlainText
          color: root.dimForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
          verticalAlignment: Text.AlignVCenter
        }

        ListView {
          id: mediaList
          visible: root.visibleItems.length > 0
          width: parent.width
          height: Math.min(contentHeight, Style.space(root.mediaSearchOpen ? 365 : 410))
          clip: true
          spacing: Style.space(3)
          model: root.visibleItems
          currentIndex: root.selectedIndex
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          reuseItems: true

          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          delegate: CursorSurface {
            id: mediaRow
            required property var modelData
            required property int index
            width: mediaList.width
            implicitHeight: Style.space(58)
            foreground: root.contentForeground
            hasCursor: root.cursorActive && root.selectedIndex === index
            enabled: !PlexCore.PlexState.playing

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: PlexCore.PlexState.playing ? Qt.ArrowCursor : Qt.PointingHandCursor
              enabled: !PlexCore.PlexState.playing
              onEntered: root.focusItem(mediaRow.index)
              onClicked: PlexCore.PlexState.playItem(mediaRow.modelData, root.playbackMode)
            }

            Text {
              id: kindIcon
              width: Style.space(24)
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              text: Model.itemIcon(mediaRow.modelData.kind)
              textFormat: Text.PlainText
              color: mediaRow.modelData.isNew ? Color.accent : root.dimForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.title
              horizontalAlignment: Text.AlignHCenter
            }

            Column {
              anchors.left: kindIcon.right
              anchors.leftMargin: Style.space(8)
              anchors.right: watchBadge.left
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)

              Text {
                width: parent.width
                text: Model.plainText(mediaRow.modelData.title, 256)
                textFormat: Text.PlainText
                color: mediaRow.modelData.watchState === "watched" ? root.dimForeground : root.contentForeground
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.body
                font.bold: mediaRow.modelData.watchState !== "watched"
                elide: Text.ElideRight
              }

              Text {
                width: parent.width
                text: Model.plainText(mediaRow.modelData.subtitle, 256)
                  + (mediaRow.modelData.addedLabel === "" ? "" : " · " + Model.plainText(mediaRow.modelData.addedLabel, 80))
                  + (mediaRow.modelData.playbackHint === "" ? "" : " · " + Model.plainText(mediaRow.modelData.playbackHint, 80))
                textFormat: Text.PlainText
                color: root.dimForeground
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            Button {
              id: watchBadge
              anchors.right: parent.right
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              z: 1
              text: Model.watchLabel(mediaRow.modelData.watchState)
              tooltipText: mediaRow.modelData.watchState === "watched"
                ? "Mark unwatched" : "Mark watched"
              fontSize: Style.font.caption
              foreground: mediaRow.modelData.isNew ? Color.accent : root.dimForeground
              fontFamily: root.contentFontFamily
              horizontalPadding: Style.space(6)
              verticalPadding: Style.space(2)
              bordered: true
              active: PlexCore.PlexState.markingRatingKey === String(mediaRow.modelData.ratingKey)
              enabled: !PlexCore.PlexState.playing && !PlexCore.PlexState.updating
              onHovered: function(isHovered) {
                if (isHovered) root.focusItem(mediaRow.index)
              }
              onClicked: {
                root.focusItem(mediaRow.index)
                PlexCore.PlexState.toggleWatchState(mediaRow.modelData)
              }
            }
          }
        }

        Item {
          width: parent.width
          implicitHeight: Math.max(footerKeys.implicitHeight, settingsButton.implicitHeight)

          Text {
            id: footerKeys
            anchors.left: parent.left
            anchors.right: settingsButton.left
            anchors.rightMargin: Style.space(6)
            anchors.verticalCenter: parent.verticalCenter
            text: "[ ] views · / find · J/K move · Enter play · X mark · ? keys"
            textFormat: Text.PlainText
            color: root.dimForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
          }

          PanelActionButton {
            id: settingsButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: "󰒓"
            tooltipText: "Connection settings (,)"
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            enabled: !PlexCore.PlexState.settingsBusy
            onClicked: root.openSettings()
          }
        }

      }

      Rectangle {
        visible: root.helpOpen
        anchors.fill: parent
        z: 20
        color: Color.background

        Column {
          anchors.fill: parent
          spacing: Style.space(10)

          Item {
            width: parent.width
            implicitHeight: Math.max(helpTitle.implicitHeight, closeHelpButton.implicitHeight)

            Text {
              id: helpTitle
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: "Keybindings"
              textFormat: Text.PlainText
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Button {
              id: closeHelpButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "Close  ?"
              fontSize: Style.font.caption
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              onClicked: root.closeHelp()
            }
          }

          TextField {
            id: helpSearch
            width: parent.width
            text: root.helpQuery
            maximumLength: 80
            placeholderText: "Search keybindings…  /"
            foreground: root.contentForeground
            font.family: root.contentFontFamily
            onTextChanged: root.helpQuery = text
            Keys.onEscapePressed: {
              if (text !== "") text = ""
              else root.closeHelp()
            }
            Keys.onPressed: function(event) {
              if (event.text === "?") {
                root.closeHelp()
                event.accepted = true
              }
            }
          }

          Text {
            visible: root.filteredHelpBindings.length === 0
            width: parent.width
            text: "No matching keybindings"
            textFormat: Text.PlainText
            color: root.dimForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
          }

          ListView {
            width: parent.width
            height: parent.height - y
            clip: true
            spacing: Style.space(4)
            model: root.groupedHelpBindings
            boundsBehavior: Flickable.StopAtBounds

            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: Item {
              id: helpRow
              required property var modelData
              width: ListView.view.width
              implicitHeight: modelData.kind === "header"
                ? helpSectionHeader.implicitHeight + Style.space(8)
                : Math.max(bindingKey.implicitHeight, bindingAction.implicitHeight) + Style.space(14)

              PanelSectionHeader {
                id: helpSectionHeader
                visible: helpRow.modelData.kind === "header"
                anchors.left: parent.left
                anchors.leftMargin: Style.space(4)
                anchors.bottom: parent.bottom
                text: Model.plainText(helpRow.modelData.category, 40).toUpperCase()
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
              }

              Rectangle {
                visible: helpRow.modelData.kind === "binding"
                anchors.fill: parent
                radius: Style.cornerRadius
                color: Qt.rgba(root.contentForeground.r, root.contentForeground.g, root.contentForeground.b, 0.05)

                Text {
                  id: bindingKey
                  width: Style.space(100)
                  anchors.left: parent.left
                  anchors.leftMargin: Style.space(10)
                  anchors.verticalCenter: parent.verticalCenter
                  text: Model.plainText(helpRow.modelData.keys, 40)
                  textFormat: Text.PlainText
                  color: Color.accent
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  id: bindingAction
                  anchors.left: bindingKey.right
                  anchors.right: parent.right
                  anchors.rightMargin: Style.space(10)
                  anchors.verticalCenter: parent.verticalCenter
                  text: Model.plainText(helpRow.modelData.action, 120)
                  textFormat: Text.PlainText
                  color: root.contentForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }
              }
            }
          }
        }
      }

      Rectangle {
        visible: root.settingsOpen
        anchors.fill: parent
        z: 30
        color: Color.background

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
              iconComponent: plexIcon
              title: "Plex"
              meta: PlexCore.PlexState.configured ? "Connection settings" : "Connect to Plex"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
            }

            PanelSeparator { foreground: root.contentForeground }

            PanelSectionHeader {
              width: parent.width
              text: "SERVER"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
            }

            TextField {
              id: serverField
              width: parent.width
              placeholderText: "http://plex-server:32400"
              maximumLength: 512
              foreground: root.contentForeground
              font.family: root.contentFontFamily
              enabled: !PlexCore.PlexState.updating
              inputMethodHints: Qt.ImhUrlCharactersOnly
              onAccepted: tokenField.forceActiveFocus()
              Keys.onEscapePressed: root.closeSettings()
            }

            PanelSectionHeader {
              width: parent.width
              text: "PLEX TOKEN"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
            }

            TextField {
              id: tokenField
              width: parent.width
              placeholderText: PlexCore.PlexState.configured
                ? "Leave blank to keep the saved token" : "Paste your Plex token"
              password: true
              maximumLength: 256
              foreground: root.contentForeground
              font.family: root.contentFontFamily
              enabled: !PlexCore.PlexState.updating
              onAccepted: root.submitSetup()
              Keys.onEscapePressed: root.closeSettings()
            }

            Text {
              visible: PlexCore.PlexState.configured
              width: parent.width
              text: root.librarySummary()
              textFormat: Text.PlainText
              color: root.dimForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            Text {
              visible: PlexCore.PlexState.lastError !== ""
              width: parent.width
              text: PlexCore.PlexState.safeText(PlexCore.PlexState.lastError, 220)
              textFormat: Text.PlainText
              color: root.urgentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            Text {
              visible: PlexCore.PlexState.setupMessage !== ""
              width: parent.width
              text: PlexCore.PlexState.safeText(PlexCore.PlexState.setupMessage, 220)
              textFormat: Text.PlainText
              color: Color.accent
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            Button {
              id: saveSettingsButton
              width: parent.width
              text: PlexCore.PlexState.configuring ? "Testing connection…" : "Test and save"
              iconText: PlexCore.PlexState.configuring ? "󰑐" : ""
              iconSpinning: PlexCore.PlexState.configuring
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              focusable: true
              enabled: !PlexCore.PlexState.updating
              onClicked: root.submitSetup()
            }

            Row {
              visible: PlexCore.PlexState.configured
              width: parent.width
              spacing: Style.space(5)

              Button {
                id: closeSettingsButton
                width: (parent.width - parent.spacing) / 2
                text: "Close"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                bordered: true
                focusable: true
                enabled: !PlexCore.PlexState.updating
                onClicked: root.closeSettings()
              }

              Button {
                id: clearSettingsButton
                width: (parent.width - parent.spacing) / 2
                text: root.confirmClear ? "Confirm remove" : "Remove credentials"
                foreground: root.confirmClear ? root.urgentForeground : root.contentForeground
                fontFamily: root.contentFontFamily
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
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }
        }
      }
    }
  }
}
