pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property bool installed: false
  property bool initialized: false
  property bool configured: false
  property bool settingsRequested: false
  property bool stale: true
  property var items: []
  property var continueItems: []
  property var movieItems: []
  property var seriesItems: []
  property int newCount: 0
  property string sourceState: "unconfigured"
  property string lastSuccessAt: ""
  property string lastError: ""
  property string playbackMode: "windowed"
  property string playingTitle: ""
  property bool autoPlayNextEpisode: false
  property string subtitleSearchLanguage: "en"
  property string connectionServer: ""
  property string connectionName: ""
  property string authenticationMode: ""
  property var movieLibraries: []
  property var seriesLibraries: []
  property var authenticationServers: []
  property string authenticationState: "idle"
  property string setupMessage: ""
  property string _statusOutput: ""
  property string _statusError: ""
  property string _refreshOutput: ""
  property string _refreshError: ""
  property string _scanOutput: ""
  property string _scanError: ""
  property string scanMessage: ""
  property string _markError: ""
  property string markingRatingKey: ""
  property string markingTargetState: ""
  property string markMessage: ""
  property int scanRefreshesRemaining: 0
  property string _playError: ""
  property string _windowError: ""
  property string _windowStatusOutput: ""
  property bool playerWindowActive: false
  property string _setupOutput: ""
  property string _setupError: ""
  property string _setupPayload: ""
  property string _clearOutput: ""
  property string _clearError: ""
  property string _authOutput: ""
  property string _authError: ""

  signal configurationFinished(bool success, string detail)

  readonly property string pluginRoot: Quickshell.env("HOME")
    + "/.config/omarchy/plugins/io.github.pjgeutjens.omaplex"
  readonly property string helperCommand: pluginRoot + "/bin/omaplex"
  readonly property bool scanning: scanProcess.running
  readonly property bool configuring: setupProcess.running
  readonly property bool clearingConfiguration: clearProcess.running
  readonly property bool authenticating: authenticationState !== "idle"
    || authStartProcess.running || authPollProcess.running
    || authSelectProcess.running || authCancelProcess.running
  readonly property bool authCancelProcessRunning: authCancelProcess.running
  readonly property bool settingsBusy: configuring || clearingConfiguration
    || authStartProcess.running || authPollProcess.running || authSelectProcess.running
  readonly property bool marking: markProcess.running
  readonly property bool updating: refreshProcess.running || scanning || marking || settingsBusy
  readonly property bool playing: playbackProcess.running
  readonly property bool playingWindowed: playerWindowActive
  readonly property bool movingPlayer: windowProcess.running
  readonly property bool openingWeb: webProcess.running
  readonly property string sourceLabel: Model.sourceLabel(sourceState, updating)
  readonly property string freshnessText: Model.relativeTime(lastSuccessAt, Date.now())
  readonly property string tooltipText: Model.tooltip({
    configured: configured,
    items: items,
    newCount: newCount
  }, updating)

  function safeText(value, maximum) {
    return Model.plainText(value, maximum || 220)
  }

  function routeThroughShell(method) {
    if (routeProcess.running || ["summon", "hide", "toggle"].indexOf(method) < 0)
      return false
    routeProcess.command = [
      "timeout", "--signal=TERM", "5", "omarchy-shell", "shell", method,
      "io.github.pjgeutjens.omaplex"
    ]
    routeProcess.running = true
    return true
  }

  function normalizeLibraries(value) {
    if (!(value instanceof Array) || value.length > 16)
      throw new Error("Plex returned invalid library settings")
    var result = []
    for (var index = 0; index < value.length; index++) {
      var library = value[index]
      var id = String(library && library.id || "")
      if (!/^\d{1,12}$/.test(id) || !library || typeof library.title !== "string")
        throw new Error("Plex returned invalid library settings")
      result.push({ id: id, title: safeText(library.title, 128) })
    }
    return result
  }

  function applyConnection(value) {
    if (!value) return
    if (typeof value.server !== "string" || typeof value.serverName !== "string"
        || typeof value.authMode !== "string")
      throw new Error("Plex returned invalid connection settings")
    connectionServer = safeText(value.server, 512)
    connectionName = safeText(value.serverName, 128)
    authenticationMode = value.authMode === "plex" ? "plex"
      : (value.authMode === "manual" ? "manual" : "")
    movieLibraries = normalizeLibraries(value.movieLibraries)
    seriesLibraries = normalizeLibraries(value.seriesLibraries)
  }

  function applyDocument(raw) {
    var value = JSON.parse(String(raw || ""))
    var document = Model.normalizeDocument(value)
    applyConnection(value.connection)
    configured = document.configured
    sourceState = document.sourceState
    stale = document.stale
    items = document.items
    continueItems = document.continueItems
    movieItems = document.movieItems
    seriesItems = document.seriesItems
    newCount = document.newCount
    lastSuccessAt = document.lastSuccessAt
    lastError = document.error
    initialized = true
  }

  function loadStatus() {
    if (!installed || statusProcess.running || refreshProcess.running || marking || settingsBusy) return false
    _statusOutput = ""
    _statusError = ""
    statusProcess.command = ["timeout", "--signal=TERM", "12", helperCommand, "status"]
    statusProcess.running = true
    return true
  }

  function refresh() {
    if (!installed || refreshProcess.running || marking || settingsBusy) return false
    _refreshOutput = ""
    _refreshError = ""
    lastError = ""
    refreshProcess.command = ["timeout", "--signal=TERM", "25", helperCommand, "refresh"]
    refreshProcess.running = true
    return true
  }

  function scanLibraries() {
    if (!installed || !configured || scanProcess.running || marking || settingsBusy) return false
    _scanOutput = ""
    _scanError = ""
    scanMessage = ""
    lastError = ""
    scanProcess.command = ["timeout", "--signal=TERM", "30", helperCommand, "scan"]
    scanProcess.running = true
    return true
  }

  function configure(values) {
    if (!installed || statusProcess.running || refreshProcess.running || scanning || settingsBusy)
      return false
    var server = safeText(values.server, 512)
    var token = String(values.token || "")
    if (server === "") {
      lastError = "Enter the Plex server URL"
      return false
    }
    if (!configured && token === "") {
      lastError = "Enter a Plex token"
      return false
    }
    if (token.length > 256) {
      lastError = "The Plex token is too long"
      return false
    }
    _setupOutput = ""
    _setupError = ""
    _setupPayload = JSON.stringify({ server: server, token: token })
    token = ""
    lastError = ""
    setupMessage = ""
    setupProcess.command = ["timeout", "--signal=TERM", "35", helperCommand, "configure"]
    setupProcess.running = true
    return true
  }

  function startPlexSignIn() {
    if (!installed || statusProcess.running || refreshProcess.running || scanning
        || settingsBusy || authenticating)
      return false
    _authOutput = ""
    _authError = ""
    authenticationServers = []
    authenticationState = "starting"
    lastError = ""
    setupMessage = "Opening Plex sign-in in your browser…"
    authStartProcess.command = [
      "timeout", "--signal=TERM", "25", helperCommand, "auth-start"
    ]
    authStartProcess.running = true
    return true
  }

  function pollPlexSignIn() {
    if (authenticationState !== "waiting" || authPollProcess.running)
      return false
    _authOutput = ""
    _authError = ""
    authPollProcess.command = [
      "timeout", "--signal=TERM", "25", helperCommand, "auth-poll"
    ]
    authPollProcess.running = true
    return true
  }

  function selectPlexServer(machineIdentifier) {
    var value = String(machineIdentifier || "")
    if (!/^[A-Za-z0-9._-]{1,128}$/.test(value) || authSelectProcess.running)
      return false
    authPollTimer.stop()
    _authOutput = ""
    _authError = ""
    authenticationState = "connecting"
    setupMessage = "Connecting to the selected Plex server…"
    authSelectProcess.command = [
      "timeout", "--signal=TERM", "50", helperCommand, "auth-complete",
      "--machine-identifier", value
    ]
    authSelectProcess.running = true
    return true
  }

  function cancelPlexSignIn() {
    if (!authenticating || authCancelProcess.running) return false
    authPollTimer.stop()
    authenticationState = "idle"
    authenticationServers = []
    setupMessage = ""
    _authError = ""
    authCancelProcess.command = [
      "timeout", "--signal=TERM", "12", helperCommand, "auth-cancel"
    ]
    authCancelProcess.running = true
    return true
  }

  function clearConfiguration() {
    if (!installed || statusProcess.running || refreshProcess.running || scanning || settingsBusy)
      return false
    _clearOutput = ""
    _clearError = ""
    lastError = ""
    setupMessage = ""
    clearProcess.command = ["timeout", "--signal=TERM", "12", helperCommand, "clear-configuration"]
    clearProcess.running = true
    return true
  }

  function playItem(item, mode) {
    if (!item || playbackProcess.running) return false
    var ratingKey = String(item.playbackRatingKey || item.ratingKey || "")
    var requestedMode = mode === "fullscreen" ? "fullscreen" : "windowed"
    if (!/^\d{1,96}$/.test(ratingKey)) {
      lastError = "This Plex item has an invalid rating key"
      return false
    }
    playbackMode = requestedMode
    playingTitle = safeText(item.title, 256)
    _playError = ""
    playbackProcess.command = [helperCommand, "play", "--rating-key", ratingKey, "--mode", requestedMode]
    if (autoPlayNextEpisode) playbackProcess.command.push("--auto-play-next")
    playbackProcess.command.push("--subtitle-language", subtitleSearchLanguage)
    playbackProcess.running = true
    return true
  }

  function setPlaybackMode(mode) {
    playbackMode = mode === "fullscreen" ? "fullscreen" : "windowed"
  }

  function bringPlayerHere() {
    if (!playingWindowed || windowProcess.running) return false
    _windowError = ""
    lastError = ""
    windowProcess.command = [
      "timeout", "--signal=TERM", "5", helperCommand, "bring-player"
    ]
    windowProcess.running = true
    return true
  }

  function checkPlayerWindow() {
    if (!installed || windowStatusProcess.running) return false
    _windowStatusOutput = ""
    windowStatusProcess.command = [
      "timeout", "--signal=TERM", "4", helperCommand, "player-window-status"
    ]
    windowStatusProcess.running = true
    return true
  }

  function setWatchState(item, state) {
    if (!item || playbackProcess.running || refreshProcess.running || scanning || marking || settingsBusy)
      return false
    var ratingKey = String(item.ratingKey || "")
    var targetState = state === "watched" ? "watched"
      : (state === "unwatched" ? "unwatched" : "")
    if (!/^\d{1,96}$/.test(ratingKey) || targetState === "") {
      lastError = "This Plex item has an invalid watch state"
      return false
    }
    _markError = ""
    lastError = ""
    markMessageTimer.stop()
    markMessage = targetState === "watched" ? "Marking watched…" : "Marking unwatched…"
    markingRatingKey = ratingKey
    markingTargetState = targetState
    markProcess.command = [
      "timeout", "--signal=TERM", "15", helperCommand, "mark",
      "--rating-key", ratingKey, "--state", targetState
    ]
    markProcess.running = true
    return true
  }

  function toggleWatchState(item) {
    return setWatchState(item, item && item.watchState === "watched" ? "unwatched" : "watched")
  }

  function applyWatchState(ratingKey, state) {
    items = Model.updateWatchState(items, ratingKey, state)
    continueItems = Model.updateWatchState(continueItems, ratingKey, state)
    movieItems = Model.updateWatchState(movieItems, ratingKey, state)
    seriesItems = Model.updateWatchState(seriesItems, ratingKey, state)
    newCount = movieItems.concat(seriesItems).filter(function(item) { return item.isNew }).length
  }

  function openWebItem(item) {
    if (!item || webProcess.running) return false
    var ratingKey = String(item.ratingKey || "")
    if (!/^\d{1,96}$/.test(ratingKey)) return false
    webProcess.command = [helperCommand, "open-web", "--rating-key", ratingKey]
    webProcess.running = true
    return true
  }

  function openPlexWeb() {
    if (!configured || webProcess.running) return false
    webProcess.command = [helperCommand, "open-web"]
    webProcess.running = true
    return true
  }

  // Bar widgets are instantiated per monitor. Keeping data IPC on this
  // singleton avoids duplicate target registration; panel open/close is
  // routed by Omarchy's shell summon/hide commands to the focused monitor.
  IpcHandler {
    target: "io.github.pjgeutjens.omaplex"

    function open() { root.routeThroughShell("summon") }
    function close() { root.routeThroughShell("hide") }
    function show() { root.routeThroughShell("summon") }
    function hide() { root.routeThroughShell("hide") }
    function toggle() { root.routeThroughShell("toggle") }

    function refresh(): string {
      return root.refresh() ? "Plex refresh started" : "Plex refresh already running"
    }

    function scan(): string {
      return root.scanLibraries() ? "Plex library scan started" : "Plex library scan unavailable"
    }

    function settings(): string {
      root.settingsRequested = true
      root.routeThroughShell("summon")
      return "Plex connection settings opened"
    }

    function status(): string {
      return JSON.stringify({
        configured: root.configured,
        state: root.sourceState,
        stale: root.stale,
        updating: root.updating,
        scanning: root.scanning,
        marking: root.marking,
        playing: root.playing,
        configuring: root.configuring,
        authenticationMode: root.authenticationMode,
        authenticating: root.authenticating,
        items: root.items.length,
        continueItems: root.continueItems.length,
        movieItems: root.movieItems.length,
        seriesItems: root.seriesItems.length,
        movieLibraries: root.movieLibraries.length,
        seriesLibraries: root.seriesLibraries.length,
        newItems: root.newCount,
        lastSuccessAt: root.lastSuccessAt,
        error: root.lastError
      })
    }
  }

  Timer {
    interval: 15 * 60 * 1000
    repeat: true
    running: root.configured
    onTriggered: root.scanLibraries()
  }

  Timer {
    interval: 2 * 1000
    repeat: true
    running: root.playing || root.playerWindowActive
    onTriggered: root.checkPlayerWindow()
  }

  Timer {
    id: authPollTimer
    interval: 1200
    repeat: true
    onTriggered: root.pollPlexSignIn()
  }

  Timer {
    id: scanFollowupTimer
    interval: 12 * 1000
    repeat: true
    onTriggered: {
      if (refreshProcess.running) return
      root.refresh()
      root.scanRefreshesRemaining -= 1
      if (root.scanRefreshesRemaining <= 0) stop()
    }
  }

  Timer {
    id: scanMessageTimer
    interval: 8 * 1000
    onTriggered: root.scanMessage = ""
  }

  Timer {
    id: markMessageTimer
    interval: 5 * 1000
    onTriggered: root.markMessage = ""
  }

  Process {
    id: whichProcess
    running: true
    command: ["test", "-x", root.helperCommand]
    onExited: function(exitCode) {
      root.installed = exitCode === 0
      if (root.installed) root.loadStatus()
      else {
        root.initialized = true
        root.lastError = "Bundled Plex helper is missing"
      }
    }
  }

  Process {
    id: statusProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: statusStdout
      waitForEnd: true
      onStreamFinished: root._statusOutput = text
    }
    stderr: StdioCollector {
      id: statusStderr
      waitForEnd: true
      onStreamFinished: root._statusError = text
    }
    onExited: function(exitCode) {
      var stdout = String(root._statusOutput || statusStdout.text || "")
      var stderr = String(root._statusError || statusStderr.text || "")
      if (exitCode !== 0) {
        root.initialized = true
        root.lastError = root.safeText(stderr || "Could not read saved Plex data", 220)
        return
      }
      try {
        root.applyDocument(stdout)
      } catch (error) {
        root.initialized = true
        root.lastError = root.safeText(error, 220)
      }
    }
  }

  Process {
    id: refreshProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: refreshStdout
      waitForEnd: true
      onStreamFinished: root._refreshOutput = text
    }
    stderr: StdioCollector {
      id: refreshStderr
      waitForEnd: true
      onStreamFinished: root._refreshError = text
    }
    onExited: function(exitCode) {
      var stdout = String(root._refreshOutput || refreshStdout.text || "")
      var stderr = String(root._refreshError || refreshStderr.text || "")
      try {
        if (stdout.trim() !== "") root.applyDocument(stdout)
        else if (exitCode !== 0) root.lastError = root.safeText(stderr || "Plex refresh failed", 220)
      } catch (error) {
        root.lastError = root.safeText(stderr || error, 220)
      }
    }
  }

  Process {
    id: scanProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: scanStdout
      waitForEnd: true
      onStreamFinished: root._scanOutput = text
    }
    stderr: StdioCollector {
      id: scanStderr
      waitForEnd: true
      onStreamFinished: root._scanError = text
    }
    onExited: function(exitCode) {
      var stdout = String(root._scanOutput || scanStdout.text || "")
      var stderr = String(root._scanError || scanStderr.text || "")
      if (exitCode !== 0) {
        root.lastError = root.safeText(stderr || "Plex library scan failed", 220)
        return
      }
      try {
        var document = JSON.parse(stdout)
        var count = Math.floor(Number(document.sectionCount) || 0)
        if (document.accepted !== true || count < 1 || count > 16)
          throw new Error("Plex returned an invalid scan response")
        root.scanMessage = "Scan started for " + count + (count === 1 ? " library" : " libraries")
        scanMessageTimer.restart()
        root.scanRefreshesRemaining = 2
        scanFollowupTimer.restart()
      } catch (error) {
        root.lastError = root.safeText(error, 220)
      }
    }
  }

  Process {
    id: routeProcess
    running: false
    command: []
  }

  Process {
    id: markProcess
    running: false
    command: []
    stderr: StdioCollector {
      id: markStderr
      waitForEnd: true
      onStreamFinished: root._markError = text
    }
    onExited: function(exitCode) {
      var ratingKey = root.markingRatingKey
      var targetState = root.markingTargetState
      var error = String(root._markError || markStderr.text || "")
      root.markingRatingKey = ""
      root.markingTargetState = ""
      if (exitCode !== 0) {
        root.markMessage = ""
        root.lastError = root.safeText(error || "Could not update Plex watch state", 220)
        return
      }
      root.applyWatchState(ratingKey, targetState)
      root.markMessage = targetState === "watched" ? "Marked watched" : "Marked unwatched"
      markMessageTimer.restart()
      Qt.callLater(root.refresh)
    }
  }

  Process {
    id: setupProcess
    running: false
    command: []
    stdinEnabled: true
    onStarted: {
      write(root._setupPayload + "\n")
      root._setupPayload = ""
    }
    stdout: StdioCollector {
      id: setupStdout
      waitForEnd: true
      onStreamFinished: root._setupOutput = text
    }
    stderr: StdioCollector {
      id: setupStderr
      waitForEnd: true
      onStreamFinished: root._setupError = text
    }
    onExited: function(exitCode) {
      root._setupPayload = ""
      var stdout = String(root._setupOutput || setupStdout.text || "")
      var stderr = String(root._setupError || setupStderr.text || "")
      try {
        if (exitCode !== 0) throw new Error(stderr || "Plex setup failed")
        root.applyDocument(stdout)
        var movieCount = root.movieLibraries.length
        var showCount = root.seriesLibraries.length
        root.setupMessage = "Connected to " + movieCount + " movie "
          + (movieCount === 1 ? "library" : "libraries") + " and "
          + showCount + " show " + (showCount === 1 ? "library" : "libraries")
        root.configurationFinished(true, root.setupMessage)
      } catch (error) {
        root.lastError = root.safeText(stderr || (exitCode === 124
          ? "Plex setup exceeded thirty seconds" : error), 220)
        root.configurationFinished(false, root.lastError)
      }
    }
  }

  Process {
    id: authStartProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: authStartStdout
      waitForEnd: true
      onStreamFinished: root._authOutput = text
    }
    stderr: StdioCollector {
      id: authStartStderr
      waitForEnd: true
      onStreamFinished: root._authError = text
    }
    onExited: function(exitCode) {
      var stdout = String(root._authOutput || authStartStdout.text || "")
      var stderr = String(root._authError || authStartStderr.text || "")
      try {
        if (exitCode !== 0) throw new Error(stderr || "Could not start Plex sign-in")
        var document = JSON.parse(stdout)
        if (!document || document.state !== "pending" || document.browserOpened !== true)
          throw new Error("Plex returned an invalid sign-in state")
        root.authenticationState = "waiting"
        root.setupMessage = "Finish signing in with Plex in your browser."
        authPollTimer.restart()
      } catch (error) {
        root.authenticationState = "idle"
        root.lastError = root.safeText(stderr || error, 220)
        root.setupMessage = ""
        root.configurationFinished(false, root.lastError)
      }
    }
  }

  Process {
    id: authPollProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: authPollStdout
      waitForEnd: true
      onStreamFinished: root._authOutput = text
    }
    stderr: StdioCollector {
      id: authPollStderr
      waitForEnd: true
      onStreamFinished: root._authError = text
    }
    onExited: function(exitCode) {
      if (root.authenticationState === "idle") return
      var stdout = String(root._authOutput || authPollStdout.text || "")
      var stderr = String(root._authError || authPollStderr.text || "")
      try {
        if (exitCode !== 0) throw new Error(stderr || "Could not finish Plex sign-in")
        var document = JSON.parse(stdout)
        if (document && document.state === "pending") return
        if (!document || document.state !== "servers"
            || !(document.servers instanceof Array) || document.servers.length < 1
            || document.servers.length > 64)
          throw new Error("Plex returned an invalid server selection")
        var servers = []
        for (var index = 0; index < document.servers.length; index++) {
          var server = document.servers[index]
          var machineIdentifier = String(server && server.machineIdentifier || "")
          if (!/^[A-Za-z0-9._-]{1,128}$/.test(machineIdentifier)
              || !server || typeof server.name !== "string")
            throw new Error("Plex returned an invalid media server")
          servers.push({
            machineIdentifier: machineIdentifier,
            name: root.safeText(server.name, 128),
            owned: server.owned === true,
            available: server.available === true
          })
        }
        authPollTimer.stop()
        root.authenticationServers = servers
        if (servers.length === 1 && servers[0].available)
          root.selectPlexServer(servers[0].machineIdentifier)
        else {
          root.authenticationState = "servers"
          root.setupMessage = "Choose a Plex Media Server."
        }
      } catch (error) {
        authPollTimer.stop()
        root.authenticationState = "waiting"
        root.lastError = root.safeText(stderr || error, 220)
        root.setupMessage = "Plex sign-in paused. Cancel and try again."
        root.configurationFinished(false, root.lastError)
      }
    }
  }

  Process {
    id: authSelectProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: authSelectStdout
      waitForEnd: true
      onStreamFinished: root._authOutput = text
    }
    stderr: StdioCollector {
      id: authSelectStderr
      waitForEnd: true
      onStreamFinished: root._authError = text
    }
    onExited: function(exitCode) {
      var stdout = String(root._authOutput || authSelectStdout.text || "")
      var stderr = String(root._authError || authSelectStderr.text || "")
      try {
        if (exitCode !== 0) throw new Error(stderr || "Could not connect to Plex")
        root.applyDocument(stdout)
        root.authenticationState = "idle"
        root.authenticationServers = []
        root.setupMessage = "Signed in with Plex and connected to " + root.connectionName
        root.configurationFinished(true, root.setupMessage)
      } catch (error) {
        root.authenticationState = "servers"
        root.lastError = root.safeText(stderr || error, 220)
        root.setupMessage = "Choose a Plex Media Server or cancel sign-in."
        root.configurationFinished(false, root.lastError)
      }
    }
  }

  Process {
    id: authCancelProcess
    running: false
    command: []
    stderr: StdioCollector {
      id: authCancelStderr
      waitForEnd: true
      onStreamFinished: root._authError = text
    }
    onExited: function(exitCode) {
      if (exitCode !== 0)
        root.lastError = root.safeText(
          root._authError || authCancelStderr.text || "Could not cancel Plex sign-in",
          220
        )
    }
  }

  Process {
    id: clearProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: clearStdout
      waitForEnd: true
      onStreamFinished: root._clearOutput = text
    }
    stderr: StdioCollector {
      id: clearStderr
      waitForEnd: true
      onStreamFinished: root._clearError = text
    }
    onExited: function(exitCode) {
      var stdout = String(root._clearOutput || clearStdout.text || "")
      var stderr = String(root._clearError || clearStderr.text || "")
      try {
        if (exitCode !== 0) throw new Error(stderr || "Could not clear Plex settings")
        root.applyDocument(stdout)
        root.setupMessage = "Connection settings cleared"
      } catch (error) {
        root.lastError = root.safeText(stderr || error, 220)
      }
    }
  }

  Process {
    id: playbackProcess
    running: false
    command: []
    stderr: StdioCollector {
      id: playStderr
      waitForEnd: true
      onStreamFinished: root._playError = text
    }
    onExited: function(exitCode) {
      if (exitCode !== 0)
        root.lastError = root.safeText(root._playError || playStderr.text || "Playback failed", 220)
      root.playingTitle = ""
      Qt.callLater(root.refresh)
    }
  }

  Process {
    id: windowProcess
    running: false
    command: []
    stderr: StdioCollector {
      id: windowStderr
      waitForEnd: true
      onStreamFinished: root._windowError = text
    }
    onExited: function(exitCode) {
      if (exitCode !== 0)
        root.lastError = root.safeText(
          root._windowError || windowStderr.text || "Could not move the Omaplex player",
          220
        )
      root.checkPlayerWindow()
    }
  }

  Process {
    id: windowStatusProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: windowStatusStdout
      waitForEnd: true
      onStreamFinished: root._windowStatusOutput = text
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.playerWindowActive = false
        return
      }
      try {
        var document = JSON.parse(String(
          root._windowStatusOutput || windowStatusStdout.text || ""
        ))
        root.playerWindowActive = document && document.active === true
      } catch (error) {
        root.playerWindowActive = false
      }
    }
  }

  Process {
    id: webProcess
    running: false
    command: []
    stderr: StdioCollector {
      id: webStderr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.lastError = root.safeText(webStderr.text || "Could not open Plex Web", 220)
    }
  }
}
