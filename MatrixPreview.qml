import QtQuick
import qs.Commons

Item {
  id: root

  property var pixels: []
  property int columns: 9
  property int rows: 34
  property int stride: 1
  property real cell: 3
  property real gap: 1
  property color led: "#f4f1ea"
  property color well: Qt.rgba(led.r, led.g, led.b, 0.08)

  readonly property int displayRows: Math.ceil(rows / Math.max(1, stride))
  readonly property real pitch: cell + gap

  implicitWidth: columns * cell + Math.max(0, columns - 1) * gap
  implicitHeight: displayRows * cell + Math.max(0, displayRows - 1) * gap

  onPixelsChanged: canvas.requestPaint()
  onCellChanged: canvas.requestPaint()
  onLedChanged: canvas.requestPaint()
  onWidthChanged: canvas.requestPaint()
  onHeightChanged: canvas.requestPaint()

  Canvas {
    id: canvas
    anchors.fill: parent
    onPaint: {
      var ctx = getContext("2d")
      ctx.clearRect(0, 0, width, height)
      var stride = Math.max(1, root.stride)
      var cell = root.cell
      var gap = root.gap
      var cols = root.columns
      var srcRows = root.rows
      var px = root.pixels || []
      var yOut = 0
      for (var y = 0; y < srcRows; y += stride) {
        for (var x = 0; x < cols; x++) {
          var peak = 0
          for (var dy = 0; dy < stride && y + dy < srcRows; dy++) {
            var v = px[(y + dy) * cols + x] || 0
            if (v > peak) peak = v
          }
          var a = peak / 255
          var c = a <= 0.02 ? root.well : root.led
          var alpha = a <= 0.02 ? root.well.a : (0.12 + 0.88 * a)
          ctx.fillStyle = "rgba("
            + Math.round(c.r * 255) + ","
            + Math.round(c.g * 255) + ","
            + Math.round(c.b * 255) + ","
            + alpha + ")"
          ctx.fillRect(x * (cell + gap), yOut * (cell + gap), cell, cell)
        }
        yOut++
      }
    }
  }
}
