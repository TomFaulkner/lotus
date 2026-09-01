import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Services.Mpris
import Quickshell.Services.UPower
import qs.Commons
import "Model.js" as Model

Item {
  id: root

  property var shell: null
  property var manifest: null
  property string omarchyPath: Quickshell.env("OMARCHY_PATH") || ""

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || (home + "/.local/state")
  readonly property string stateDir: stateHome + "/omarchy"
  readonly property string settingsPath: stateDir + "/lotus.json"
  readonly property string pluginDir: Model.fileUrlToPath(Qt.resolvedUrl("."))
  readonly property string daemonPath: pluginDir + "/scripts/lotusd.py"
  readonly property string udevRulePath: pluginDir + "/udev/50-framework-inputmodule.rules"

  property var settings: Model.normalize(null)
  property bool initialized: false
  property bool settingsFileLoaded: false
  property bool stateDirReady: false
  property bool settingsSavePending: false
  property string loadedSettingsText: ""

  property var pixels: []
  property string activeMode: "clock"
  property var devices: []
  property bool permission: true
  property string lastError: ""
  property int notifySerial: 0
  property int lastPopupCount: 0

  readonly property var idleService: shell ? shell.serviceFor("omarchy.idle") : null
  readonly property var lockService: shell ? shell.serviceFor("omarchy.lock") : null
  readonly property var mediaService: shell ? shell.serviceFor("omarchy.media") : null
  readonly property var notifService: shell ? shell.serviceFor("omarchy.notifications") : null

  readonly property bool sessionIdle: idleService ? idleService.idledThisCycle === true : false
  readonly property bool sessionLocked: lockService ? lockService.locked === true : false
  readonly property bool playing: {
    var player = mediaService ? mediaService.activePlayer : null
    if (player && player.isPlaying) return true
    var players = Mpris.players ? Mpris.players.values : []
    for (var i = 0; i < players.length; i++) {
      if (players[i] && players[i].isPlaying) return true
    }
    return false
  }
  readonly property int popupCount: notifService && notifService.popupModel ? notifService.popupModel.count : 0
  readonly property int batteryPercent: {
    var device = UPower.displayDevice
    if (!device || !device.isPresent) return -1
    return Math.round(Number(device.percentage || 0) * 100)
  }
  readonly property bool charging: {
    var device = UPower.displayDevice
    if (!device || !device.isPresent) return false
    return !UPower.onBattery
  }
  readonly property var workspaces: workspaceSnapshot()
  readonly property bool daemonUp: daemon.running
  readonly property bool hasDevice: devices.length > 0
  readonly property bool needsUdev: hasDevice && !permission
  readonly property string statusLabel: {
    if (!settings.power || activeMode === "off") return "Asleep"
    if (needsUdev) return "Needs access"
    if (!hasDevice) return "Preview"
    var meta = Model.modeMeta(activeMode)
    return meta ? meta.label : activeMode
  }

  function workspaceSnapshot() {
    var values = []
    try { values = Hyprland.workspaces.values } catch (e) { values = [] }
    var focusedId = -1
    try { if (Hyprland.focusedWorkspace) focusedId = Hyprland.focusedWorkspace.id } catch (e2) {}
    var list = []
    for (var i = 0; i < values.length; i++) {
      var ws = values[i]
      if (!ws || ws.id <= 0 || ws.id > 10) continue
      var occupied = false
      try { occupied = ws.toplevels && ws.toplevels.values && ws.toplevels.values.length > 0 } catch (e3) {}
      list.push({ id: ws.id, occupied: occupied, focused: ws.id === focusedId })
    }
    list.sort(function(a, b) { return a.id - b.id })
    return list
  }

  function updateSettings(patch) {
    var next = {}
    var cur = settings
    for (var key in cur) next[key] = cur[key]
    for (var k in patch) next[k] = patch[k]
    settings = Model.normalize(next)
    scheduleSettingsSave()
    pushState()
  }

  function setMode(id) { updateSettings({ mode: id }) }
  function cycleMode(delta) { setMode(Model.nextMode(settings.mode, delta || 1)) }
  function togglePower() { updateSettings({ power: !settings.power }) }
  function nudgeBrightness(delta) {
    updateSettings({ brightness: Model.clampInt(settings.brightness + delta, settings.brightness, 10, 255) })
  }

  function flash() {
    notifySerial++
    pushState({ op: "notify", duration: 1.6 })
  }

  function pluginFile(relative) {
    return Model.fileUrlToPath(Qt.resolvedUrl(relative))
  }

  function currentState(extra) {
    var msg = {
      op: "state",
      mode: settings.mode,
      power: settings.power,
      brightness: settings.brightness,
      idle: sessionIdle,
      locked: sessionLocked,
      battery: batteryPercent < 0 ? null : batteryPercent,
      charging: charging,
      workspaces: workspaces,
      playing: playing,
      sleepLocked: settings.sleepLocked,
      idleArt: settings.idleArt,
      lowBattery: settings.lowBattery
    }
    if (extra) for (var k in extra) msg[k] = extra[k]
    return msg
  }

  function pushState(extra) {
    if (!daemon.running) return
    daemon.write(JSON.stringify(currentState(extra)) + "\n")
  }

  function handleLine(line) {
    var msg = null
    try { msg = JSON.parse(line) } catch (e) { return }
    if (!msg || typeof msg !== "object") return
    if (msg.type === "preview" && msg.px) {
      pixels = decodePixels(msg.px)
    } else if (msg.type === "status") {
      activeMode = String(msg.mode || activeMode)
      devices = msg.devices || []
      permission = msg.permission !== false
      lastError = ""
    } else if (msg.type === "error") {
      lastError = String(msg.message || "daemon error")
    }
  }

  function ensureDaemon() {
    if (daemon.running) return
    daemon.command = ["python3", "-u", daemonPath]
    daemon.running = true
  }

  function installUdev() {
    if (udevProcess.running) return
    lastError = "Asking for permission to install the LED matrix udev rule…"
    udevProcess.command = [
      "pkexec", "/bin/sh", "-c",
      "install -m 644 " + quote(udevRulePath)
        + " /etc/udev/rules.d/50-framework-inputmodule.rules"
        + " && udevadm control --reload-rules"
        + " && udevadm trigger --subsystem-match=tty --action=change"
        + " && chmod a+rw /dev/ttyACM* 2>/dev/null || true"
    ]
    udevProcess.running = true
  }

  function quote(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'"
  }

  function scheduleSettingsSave() {
    if (!initialized) return
    settingsSavePending = true
    settingsSaveTimer.restart()
  }

  function flushSettings() {
    if (!settingsSavePending || !stateDirReady) return
    settingsSavePending = false
    settingsFile.setText(JSON.stringify(settings, null, 2) + "\n")
  }

  function initializeIfReady() {
    if (initialized || !settingsFileLoaded) return
    settings = Model.normalize(parseJson(loadedSettingsText))
    initialized = true
    ensureDaemon()
    pushTimer.start()
  }

  function parseJson(text) {
    if (!text) return null
    try { return JSON.parse(text) } catch (e) { return null }
  }

  function decodePixels(b64) {
    var raw = Qt.atob(String(b64 || ""))
    var out = []
    for (var i = 0; i < raw.length; i++) out.push(raw.charCodeAt(i) & 255)
    return out
  }

  onPopupCountChanged: {
    if (!initialized || !settings.flashNotify) {
      lastPopupCount = popupCount
      return
    }
    if (popupCount > lastPopupCount) flash()
    lastPopupCount = popupCount
  }

  onSessionIdleChanged: if (initialized) pushState()
  onSessionLockedChanged: if (initialized) pushState()
  onPlayingChanged: if (initialized) pushState()
  onBatteryPercentChanged: if (initialized) pushState()
  onChargingChanged: if (initialized) pushState()
  onWorkspacesChanged: if (initialized) pushState()

  Component.onCompleted: {
    stateDirProcess.running = true
    ensureDaemon()
  }
  Component.onDestruction: {
    if (daemon.running) {
      daemon.write(JSON.stringify({ op: "quit" }) + "\n")
      daemon.signal(15)
    }
  }

  IpcHandler {
    target: "io.github.tomfaulkner.lotus"
    function sleep(): void { root.togglePower() }
    function cycle(): void { root.cycleMode(1) }
    function mode(name: string): void { root.setMode(name) }
    function brighter(): void { root.nudgeBrightness(16) }
    function dimmer(): void { root.nudgeBrightness(-16) }
    function flash(): void { root.flash() }
    function udev(): void { root.installUdev() }
    function status(): string { return root.statusLabel }
  }

  Process {
    id: daemon
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(value) { root.handleLine(value) }
    }
    stderr: SplitParser {
      onRead: function(value) {
        if (value) root.lastError = value
      }
    }
    onStarted: Qt.callLater(function() { root.pushState() })
    onExited: function(code) {
      if (code !== 0 && root.lastError === "")
        root.lastError = "lotusd exited (" + code + ")"
      restartTimer.restart()
    }
  }

  Process {
    id: stateDirProcess
    command: ["mkdir", "-p", root.stateDir]
    onExited: function(code) {
      root.stateDirReady = code === 0
      if (root.stateDirReady) root.flushSettings()
    }
  }

  Process {
    id: udevProcess
    onExited: function(code) {
      if (code === 0) {
        root.lastError = ""
        root.pushState()
      } else {
        root.lastError = "Udev install cancelled or failed"
      }
    }
  }

  FileView {
    id: settingsFile
    path: root.settingsPath
    watchChanges: false
    atomicWrites: true
    printErrors: false
    onLoaded: {
      root.loadedSettingsText = text()
      root.settingsFileLoaded = true
      root.initializeIfReady()
    }
    onLoadFailed: {
      root.loadedSettingsText = ""
      root.settingsFileLoaded = true
      root.initializeIfReady()
    }
  }

  Timer {
    id: pushTimer
    interval: 1000
    repeat: true
    onTriggered: root.pushState()
  }

  Timer {
    id: settingsSaveTimer
    interval: 120
    onTriggered: root.flushSettings()
  }

  Timer {
    id: restartTimer
    interval: 1500
    onTriggered: root.ensureDaemon()
  }
}
