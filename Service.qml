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
  // Exact bytes of udev/50-framework-inputmodule.rules. Written from this
  // argv after pkexec; the plugin-tree copy is never opened as root.
  readonly property string udevRuleHex: "23204672616d65776f726b204c6170746f70203136204c4544204d617472697820496e707574204d6f64756c65206f6e6c79202855534220333261633a30303230292e0a23204d4f44452030363630202b20756163636573733a20746865207365617465642075736572206765747320616e2041434c3b206f746865722074747941434d20646576696365730a2320617265206c65667420616c6f6e652e0a53554253595354454d533d3d22757362222c2041545452537b696456656e646f727d3d3d2233326163222c2041545452537b696450726f647563747d3d3d2230303230222c204d4f44453d2230363630222c205441472b3d2275616363657373220a"

  property var settings: Model.normalize(null)
  property bool initialized: false
  property bool settingsFileLoaded: false
  property bool settingsSavePending: false
  property string loadedSettingsText: ""
  property int restartDelayMs: 1500
  property double lastBeatMs: 0

  property var pixels: []
  property string activeMode: "clock"
  property var devices: []
  property bool permission: true
  property string lastError: ""
  property int notifySerial: 0
  property int lastPopupCount: 0
  readonly property int previewBytes: 306
  readonly property int maxDevices: 4
  readonly property int maxLineChars: 8192
  readonly property int maxFieldChars: 64
  readonly property int watchdogMs: 5000

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
    if (!line || String(line).length > maxLineChars) return
    var msg = null
    try { msg = JSON.parse(line) } catch (e) { return }
    if (!msg || typeof msg !== "object") return
    if (msg.type === "preview") {
      var px = decodePixels(msg.px)
      if (px) {
        pixels = px
        noteBeat()
      }
    } else if (msg.type === "status") {
      var mode = String(msg.mode || "")
      if (mode === "notify" || Model.modeIndex(mode) >= 0) activeMode = mode
      devices = sanitizeDevices(msg.devices)
      permission = msg.permission !== false
      lastError = ""
      noteBeat()
    } else if (msg.type === "error") {
      lastError = String(msg.message || "daemon error").slice(0, 200)
    }
  }

  function sanitizeDevices(list) {
    if (!list || !list.length) return []
    var out = []
    for (var i = 0; i < list.length && out.length < maxDevices; i++) {
      var d = list[i]
      if (!d || typeof d !== "object") continue
      out.push({
        path: String(d.path || "").slice(0, maxFieldChars),
        panel: String(d.panel || "").slice(0, maxFieldChars),
        serial: String(d.serial || "").slice(0, maxFieldChars),
        open: d.open === true
      })
    }
    return out
  }

  function noteBeat() {
    lastBeatMs = Date.now()
    restartDelayMs = 1500
  }

  function killDaemon() {
    if (!daemon.running) return
    daemon.signal(15)
    killEscalateTimer.restart()
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
      "pkexec", "python3", "-c",
      "import os, pathlib, subprocess\n"
        + "rule = bytes.fromhex(" + JSON.stringify(udevRuleHex) + ")\n"
        + "dest = pathlib.Path('/etc/udev/rules.d/50-framework-inputmodule.rules')\n"
        + "tmp = dest.with_name('.50-framework-inputmodule.rules.tmp')\n"
        + "tmp.write_bytes(rule)\n"
        + "os.chmod(tmp, 0o644)\n"
        + "os.replace(tmp, dest)\n"
        + "subprocess.check_call(['udevadm', 'control', '--reload-rules'])\n"
        + "subprocess.check_call(['udevadm', 'trigger', '--action=change',"
        + " '--subsystem-match=usb', '--attr-match=idVendor=32ac',"
        + " '--attr-match=idProduct=0020'])\n"
    ]
    udevProcess.running = true
  }

  function scheduleSettingsSave() {
    if (!initialized) return
    settingsSavePending = true
    settingsSaveTimer.restart()
  }

  function flushSettings() {
    if (!settingsSavePending || settingsSaveProcess.running) return
    settingsSavePending = false
    settingsSaveProcess.command = ["python3", "-u", daemonPath, "--save-settings"]
    settingsSaveProcess.running = true
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
    if (raw.length !== previewBytes) return null
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
    settingsLoadProcess.running = true
  }
  Component.onDestruction: {
    if (daemon.running) daemon.signal(15)
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
        if (value) root.lastError = String(value).slice(0, 200)
      }
    }
    onStarted: {
      root.lastBeatMs = Date.now()
      killEscalateTimer.stop()
      Qt.callLater(function() { root.pushState() })
    }
    onExited: function(code) {
      killEscalateTimer.stop()
      if (code !== 0 && root.lastError === "")
        root.lastError = "lotusd exited (" + code + ")"
      restartTimer.interval = root.restartDelayMs
      root.restartDelayMs = Math.min(root.restartDelayMs * 2, 30000)
      restartTimer.restart()
    }
  }

  Process {
    id: settingsLoadProcess
    command: ["python3", "-u", root.daemonPath, "--load-settings"]
    stdout: StdioCollector {
      onStreamFinished: root.loadedSettingsText = text
    }
    onExited: function(code) {
      if (code !== 0) root.loadedSettingsText = ""
      root.settingsFileLoaded = true
      root.initializeIfReady()
    }
  }

  Process {
    id: settingsSaveProcess
    stdinEnabled: true
    onStarted: settingsSaveProcess.write(JSON.stringify(root.settings) + "\n")
    onExited: function(code) {
      if (root.settingsSavePending) root.flushSettings()
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

  Timer {
    id: watchdogTimer
    interval: 1000
    repeat: true
    running: daemon.running
    onTriggered: {
      if (root.lastBeatMs > 0 && Date.now() - root.lastBeatMs > root.watchdogMs)
        root.killDaemon()
    }
  }

  Timer {
    id: killEscalateTimer
    interval: 800
    onTriggered: {
      if (daemon.running) daemon.signal(9)
    }
  }
}
