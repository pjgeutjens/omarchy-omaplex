import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "." as PlexCore
import "Model.js" as Model

Item {
  id: root

  property var shell: null
  property var manifest: null
  property bool opened: false
  property string browseKind: "movies"
  property string parentRatingKey: ""
  property string seriesTitle: ""
  property string query: ""
  property int offset: 0
  property int limit: 40
  property int total: 0
  property int selectedIndex: 0
  property string playbackMode: "windowed"
  property var items: []
  property bool loading: false
  property string error: ""
  property string _browseOutput: ""
  property string _browseError: ""
  property string _requestedKind: ""
  property string _requestedScope: ""
  property string _requestedQuery: ""
  property int _requestedOffset: 0
  property string _requestedParent: ""

  readonly property string pluginRoot: Quickshell.env("HOME")
    + "/.config/omarchy/plugins/io.github.pjgeutjens.omaplex"
  readonly property string helperCommand: pluginRoot + "/bin/omaplex"
  readonly property bool inSeries: browseKind === "episodes"
  readonly property bool searching: !inSeries && query.trim() !== ""
  readonly property string requestKind: searching ? "search" : browseKind
  readonly property string searchScope: browseKind === "shows" ? "shows" : "movies"
  readonly property bool hasPrevious: offset > 0
  readonly property bool hasNext: offset + items.length < total
  readonly property color onScrim: "white"
  readonly property color onScrimDim: Qt.rgba(1, 1, 1, 0.58)
  readonly property color onScrimUrgent: "#ff6b6b"

  function open(payloadJson) {
    var payload = {}
    try { payload = JSON.parse(payloadJson || "{}") || {} } catch (e) {}
    browseKind = payload.view === "series" ? "shows" : "movies"
    parentRatingKey = ""
    seriesTitle = ""
    query = ""
    offset = 0
    selectedIndex = 0
    opened = true
    loadPage()
    Qt.callLater(function() { if (root.opened) keyCatcher.forceActiveFocus() })
  }

  function close() {
    opened = false
    browseProcess.running = false
    searchTimer.stop()
    items = []
    error = ""
  }

  function dismiss() {
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide((root.manifest && root.manifest.id) || "io.github.pjgeutjens.omaplex")
    else close()
  }

  function setKind(kind) {
    browseKind = kind === "shows" ? "shows" : "movies"
    parentRatingKey = ""
    seriesTitle = ""
    query = ""
    offset = 0
    selectedIndex = 0
    loadPage()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function openSeries(item) {
    var key = String(item && item.ratingKey || "")
    if (!/^\d{1,96}$/.test(key)) return
    parentRatingKey = key
    seriesTitle = Model.plainText(item.title, 256)
    browseKind = "episodes"
    query = ""
    offset = 0
    selectedIndex = 0
    loadPage()
  }

  function goBack() {
    if (inSeries) setKind("shows")
    else dismiss()
  }

  function loadPage() {
    if (!opened || browseProcess.running) return
    _browseOutput = ""
    _browseError = ""
    error = ""
    loading = true
    _requestedKind = requestKind
    _requestedScope = searchScope
    _requestedQuery = query
    _requestedOffset = offset
    _requestedParent = parentRatingKey
    var command = [
      "timeout", "--signal=TERM", "25", helperCommand, "browse",
      "--kind", requestKind,
      "--query", query,
      "--offset", String(offset),
      "--limit", String(limit)
    ]
    if (searching) command.push("--search-scope", searchScope)
    if (inSeries) command.push("--parent-rating-key", parentRatingKey)
    browseProcess.command = command
    browseProcess.running = true
  }

  function applyPage(raw) {
    var document = Model.normalizeBrowseDocument(JSON.parse(String(raw || "")))
    if (document.kind !== requestKind) throw new Error("Plex returned the wrong browser view")
    items = document.items
    total = document.total
    selectedIndex = Math.max(0, Math.min(selectedIndex, Math.max(0, items.length - 1)))
  }

  function moveSelection(delta) {
    if (items.length === 0) return
    selectedIndex = (selectedIndex + delta + items.length) % items.length
    Qt.callLater(function() { browserList.positionViewAtIndex(selectedIndex, ListView.Contain) })
  }

  function activate(item) {
    if (!item) return
    if (item.playable === false) openSeries(item)
    else PlexCore.PlexState.playItem(item, playbackMode)
  }

  function previousPage() {
    if (!hasPrevious || loading) return
    offset = Math.max(0, offset - limit)
    selectedIndex = 0
    loadPage()
  }

  function nextPage() {
    if (!hasNext || loading) return
    offset += limit
    selectedIndex = 0
    loadPage()
  }

  Timer {
    id: searchTimer
    interval: 350
    repeat: false
    onTriggered: {
      root.offset = 0
      root.selectedIndex = 0
      root.loadPage()
    }
  }

  Connections {
    target: PlexCore.PlexState
    function onPlayingChanged() {
      if (!PlexCore.PlexState.playing && root.opened) Qt.callLater(root.loadPage)
    }
  }

  Process {
    id: browseProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: browseStdout
      waitForEnd: true
      onStreamFinished: root._browseOutput = text
    }
    stderr: StdioCollector {
      id: browseStderr
      waitForEnd: true
      onStreamFinished: root._browseError = text
    }
    onExited: function(exitCode) {
      root.loading = false
      if (root._requestedKind !== root.requestKind
          || root._requestedScope !== root.searchScope
          || root._requestedQuery !== root.query
          || root._requestedOffset !== root.offset
          || root._requestedParent !== root.parentRatingKey) {
        Qt.callLater(root.loadPage)
        return
      }
      var stdout = String(root._browseOutput || browseStdout.text || "")
      var stderr = String(root._browseError || browseStderr.text || "")
      if (exitCode !== 0) {
        root.error = Model.plainText(stderr || "Could not browse Plex", 220)
        return
      }
      try { root.applyPage(stdout) }
      catch (e) { root.error = Model.plainText(e, 220) }
    }
  }

  PanelWindow {
    id: browserWindow
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-plex-browser"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

    Rectangle {
      anchors.fill: parent
      color: Qt.rgba(0, 0, 0, 0.86)

      MouseArea {
        anchors.fill: parent
        onClicked: root.dismiss()
      }
    }

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true

      Keys.onEscapePressed: root.goBack()
      Keys.onUpPressed: root.moveSelection(-1)
      Keys.onDownPressed: root.moveSelection(1)
      Keys.onReturnPressed: if (root.items.length > 0) root.activate(root.items[root.selectedIndex])
      Keys.onEnterPressed: if (root.items.length > 0) root.activate(root.items[root.selectedIndex])
      Keys.onPressed: function(event) {
        var text = String(event.text || "")
        if (text === "/") { searchField.forceActiveFocus(); event.accepted = true }
        else if (text === "m" || text === "M") { root.setKind("movies"); event.accepted = true }
        else if (text === "s" || text === "S") { root.setKind("shows"); event.accepted = true }
        else if (text === "w" || text === "W") { root.playbackMode = "windowed"; event.accepted = true }
        else if (text === "f" || text === "F") { root.playbackMode = "fullscreen"; event.accepted = true }
        else if (text === "n" || text === "N") { root.nextPage(); event.accepted = true }
        else if (text === "p" || text === "P") { root.previousPage(); event.accepted = true }
      }

      Rectangle {
        id: browserCard
        anchors.centerIn: parent
        width: Math.min(parent.width - Style.space(64), Style.space(1080))
        height: Math.min(parent.height - Style.space(64), Style.space(760))
        radius: Style.cornerRadius
        color: Qt.rgba(0.055, 0.055, 0.065, 0.98)
        border.width: Math.max(1, Style.space(1))
        border.color: Qt.rgba(1, 1, 1, 0.15)

        MouseArea { anchors.fill: parent; onClicked: {} }

        Column {
          anchors.fill: parent
          anchors.margins: Style.space(24)
          spacing: Style.space(12)

          Item {
            width: parent.width
            implicitHeight: Math.max(browserTitle.implicitHeight, closeButton.implicitHeight)

            Column {
              id: browserTitle
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                text: root.inSeries ? root.seriesTitle : "Browse Plex"
                textFormat: Text.PlainText
                color: root.onScrim
                font.family: Style.font.family
                font.pixelSize: Style.font.display
                font.bold: true
              }

              Text {
                text: root.inSeries ? "EPISODES"
                  : (root.searching ? (root.searchScope === "movies" ? "SEARCH MOVIES" : "SEARCH SHOWS & EPISODES")
                    : (root.browseKind === "movies" ? "ALL MOVIES" : "ALL SHOWS"))
                textFormat: Text.PlainText
                color: root.onScrimDim
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.letterSpacing: 1.5
              }
            }

            Button {
              id: closeButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: root.inSeries ? "Back  Esc" : "Close  Esc"
              foreground: root.onScrim
              fontFamily: Style.font.family
              bordered: true
              onClicked: root.goBack()
            }
          }

          Row {
            visible: !root.inSeries
            spacing: Style.space(6)

            Button {
              text: "Movies"
              foreground: root.onScrim
              fontFamily: Style.font.family
              bordered: true
              active: root.browseKind === "movies"
              onClicked: root.setKind("movies")
            }

            Button {
              text: "Shows"
              foreground: root.onScrim
              fontFamily: Style.font.family
              bordered: true
              active: root.browseKind === "shows"
              onClicked: root.setKind("shows")
            }
          }

          Item {
            width: parent.width
            implicitHeight: Math.max(searchField.implicitHeight, playbackButtons.implicitHeight)

            TextField {
              id: searchField
              anchors.left: parent.left
              width: Math.min(parent.width * 0.58, Style.space(520))
              text: root.query
              maximumLength: 80
              placeholderText: root.inSeries ? "Search this show  /"
                : (root.searchScope === "movies" ? "Fuzzy-search movies  /" : "Fuzzy-search shows  /")
              foreground: root.onScrim
              font.family: Style.font.family
              onTextChanged: {
                root.query = text
                searchTimer.restart()
              }
              Keys.onDownPressed: {
                keyCatcher.forceActiveFocus()
                root.moveSelection(1)
              }
              Keys.onEscapePressed: {
                if (text !== "") text = ""
                else keyCatcher.forceActiveFocus()
              }
            }

            Row {
              id: playbackButtons
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(6)

              Button {
                text: "Windowed"
                foreground: root.onScrim
                fontFamily: Style.font.family
                bordered: true
                active: root.playbackMode === "windowed"
                onClicked: root.playbackMode = "windowed"
              }

              Button {
                text: "Fullscreen"
                foreground: root.onScrim
                fontFamily: Style.font.family
                bordered: true
                active: root.playbackMode === "fullscreen"
                onClicked: root.playbackMode = "fullscreen"
              }
            }
          }

          Text {
            visible: root.error !== ""
            width: parent.width
            text: Model.plainText(root.error, 220)
            textFormat: Text.PlainText
            color: root.onScrimUrgent
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Text {
            visible: root.loading || (!root.loading && root.items.length === 0)
            width: parent.width
            height: Style.space(80)
            text: root.loading ? "Loading Plex library…" : "No matching media"
            textFormat: Text.PlainText
            color: root.onScrimDim
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
          }

          ListView {
            id: browserList
            visible: !root.loading && root.items.length > 0
            width: parent.width
            height: parent.height - y - browserKeys.implicitHeight - pager.implicitHeight - Style.space(20)
            clip: true
            spacing: Style.space(4)
            model: root.items
            currentIndex: root.selectedIndex
            boundsBehavior: Flickable.StopAtBounds

            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: CursorSurface {
              id: mediaRow
              required property var modelData
              required property int index
              width: browserList.width
              implicitHeight: Style.space(54)
              foreground: root.onScrim
              hasCursor: root.selectedIndex === index
              enabled: !PlexCore.PlexState.playing

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: root.selectedIndex = mediaRow.index
                onClicked: root.activate(mediaRow.modelData)
              }

              Text {
                id: kindIcon
                width: Style.space(28)
                anchors.left: parent.left
                anchors.leftMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                text: Model.itemIcon(mediaRow.modelData.kind)
                textFormat: Text.PlainText
                color: Color.accent
                font.family: Style.font.family
                font.pixelSize: Style.font.title
                horizontalAlignment: Text.AlignHCenter
              }

              Column {
                anchors.left: kindIcon.right
                anchors.leftMargin: Style.space(10)
                anchors.right: watchState.left
                anchors.rightMargin: Style.space(14)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(1)

                Text {
                  width: parent.width
                  text: Model.plainText(mediaRow.modelData.title, 256)
                  textFormat: Text.PlainText
                  color: root.onScrim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: mediaRow.modelData.watchState !== "watched"
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  text: Model.plainText(mediaRow.modelData.subtitle, 256)
                  textFormat: Text.PlainText
                  color: root.onScrimDim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              Text {
                id: watchState
                anchors.right: parent.right
                anchors.rightMargin: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                text: mediaRow.modelData.playable === false
                  ? "OPEN" : Model.watchLabel(mediaRow.modelData.watchState)
                textFormat: Text.PlainText
                color: root.onScrimDim
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
              }
            }
          }

          Text {
            id: browserKeys
            width: parent.width
            text: "M Movies · S Shows · / Search selected scope · N/P pages · Esc close"
            textFormat: Text.PlainText
            color: root.onScrimDim
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
          }

          Item {
            id: pager
            width: parent.width
            implicitHeight: Math.max(pageLabel.implicitHeight, pageButtons.implicitHeight)

            Text {
              id: pageLabel
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: root.total === 0 ? "0 items"
                : (root.offset + 1) + "–" + Math.min(root.offset + root.items.length, root.total) + " of " + root.total
              textFormat: Text.PlainText
              color: root.onScrimDim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }

            Row {
              id: pageButtons
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(6)

              Button {
                text: "Previous  P"
                foreground: root.onScrim
                fontFamily: Style.font.family
                bordered: true
                enabled: root.hasPrevious && !root.loading
                onClicked: root.previousPage()
              }

              Button {
                text: "Next  N"
                foreground: root.onScrim
                fontFamily: Style.font.family
                bordered: true
                enabled: root.hasNext && !root.loading
                onClicked: root.nextPage()
              }
            }
          }
        }
      }
    }
  }
}
