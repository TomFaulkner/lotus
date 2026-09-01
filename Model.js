.pragma library

var MODES = [
  { id: "auto", label: "Auto", hint: "Clock, then the useful thing" },
  { id: "clock", label: "Clock", hint: "Hours over minutes" },
  { id: "battery", label: "Battery", hint: "Charge as a cell" },
  { id: "spaces", label: "Spaces", hint: "Hyprland workspaces" },
  { id: "lotus", label: "Lotus", hint: "Breathing bloom" },
  { id: "rain", label: "Rain", hint: "Falling sparks" },
  { id: "meter", label: "Meter", hint: "Per-core load" },
  { id: "breathe", label: "Breathe", hint: "Full-panel pulse" },
  { id: "off", label: "Sleep", hint: "Put the matrix to bed" }
]

var DEFAULTS = {
  mode: "auto",
  power: true,
  brightness: 96,
  sleepLocked: true,
  idleArt: "rain",
  flashNotify: true,
  lowBattery: 15
}

function fileUrlToPath(url) {
  var s = String(url || "")
  if (s.indexOf("file://") === 0) s = s.substring(7)
  try { s = decodeURIComponent(s) } catch (e) {}
  if (s.length > 1 && s.charAt(s.length - 1) === "/") s = s.substring(0, s.length - 1)
  return s
}

function modeIndex(id) {
  for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return i
  return -1
}

function nextMode(id, delta) {
  var i = modeIndex(id)
  if (i < 0) i = 0
  var n = MODES.length
  delta = delta || 1
  return MODES[(i + delta % n + n) % n].id
}

function modeMeta(id) {
  var i = modeIndex(id)
  return MODES[i < 0 ? 0 : i]
}

function clampInt(value, fallback, min, max) {
  var n = parseInt(String(value), 10)
  if (!isFinite(n)) n = fallback
  if (n < min) n = min
  if (n > max) n = max
  return n
}

function boolOr(value, fallback) {
  return typeof value === "boolean" ? value : fallback
}

function modeOr(value, fallback) {
  var id = String(value || "")
  return modeIndex(id) >= 0 ? id : fallback
}

function normalize(raw) {
  var src = raw && typeof raw === "object" ? raw : {}
  return {
    mode: modeOr(src.mode, DEFAULTS.mode),
    power: boolOr(src.power, DEFAULTS.power),
    brightness: clampInt(src.brightness, DEFAULTS.brightness, 0, 255),
    sleepLocked: boolOr(src.sleepLocked, DEFAULTS.sleepLocked),
    idleArt: modeOr(src.idleArt, DEFAULTS.idleArt),
    flashNotify: boolOr(src.flashNotify, DEFAULTS.flashNotify),
    lowBattery: clampInt(src.lowBattery, DEFAULTS.lowBattery, 5, 40)
  }
}

function pixelsFromB64(b64) {
  if (!b64) return []
  var raw = Qt.atob(String(b64))
  var out = []
  for (var i = 0; i < raw.length; i++) out.push(raw.charCodeAt(i) & 255)
  return out
}

function downsample(pixels, stride) {
  stride = stride || 3
  var w = 9
  var h = 34
  var rows = []
  for (var y = 0; y < h; y += stride) {
    var row = []
    for (var x = 0; x < w; x++) {
      var peak = 0
      for (var dy = 0; dy < stride && y + dy < h; dy++) {
        var v = pixels[(y + dy) * w + x] || 0
        if (v > peak) peak = v
      }
      row.push(peak)
    }
    rows.push(row)
  }
  return rows
}

if (typeof module !== "undefined") {
  module.exports = {
    MODES: MODES,
    DEFAULTS: DEFAULTS,
    nextMode: nextMode,
    normalize: normalize,
    downsample: downsample
  }
}
