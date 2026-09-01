import QtQuick
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.tomfaulkner.lotus"

  readonly property var lotus: bar && bar.shell
    ? bar.shell.serviceFor(moduleName)
    : null

  readonly property bool opened: lotusPanel.opened === true
  readonly property bool popoutSwitchClosing: lotusPanel.popoutSwitchClosing === true
  readonly property real openPanelIndicatorWidth: content.implicitWidth
  readonly property real openPanelIndicatorHeight: content.implicitHeight
  readonly property bool ready: !!lotus && lotus.initialized === true
  readonly property color led: bar ? bar.barForeground : Color.foreground

  function open() { lotusPanel.open() }
  function close() { lotusPanel.close() }
  function toggle() { lotusPanel.toggle() }
  function togglePanel() { lotusPanel.toggle() }
  function closeForPopoutSwitch() { lotusPanel.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    dimmed: root.ready && (!root.lotus.settings.power || root.lotus.activeMode === "off")
    tooltipText: root.ready
      ? ("Lotus · " + root.lotus.statusLabel + "\nLeft: panel · Right: next mode · Middle: sleep · Scroll: brightness")
      : "Lotus"
    fixedWidth: root.vertical
      ? -1
      : Math.round(content.implicitWidth + scaledHorizontalMargin * 2)
    fixedHeight: root.vertical
      ? Math.round(content.implicitHeight + scaledVerticalPadding * 2)
      : -1

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.LeftButton) root.togglePanel()
      else if (buttonCode === Qt.RightButton && root.ready) root.lotus.cycleMode(1)
      else if (buttonCode === Qt.MiddleButton && root.ready) root.lotus.togglePower()
    }
    onWheelMoved: function(delta) {
      if (root.ready) root.lotus.nudgeBrightness(delta > 0 ? 16 : -16)
    }

    MatrixPreview {
      id: content
      z: -1
      anchors.centerIn: parent
      pixels: root.ready ? root.lotus.pixels : []
      columns: 9
      rows: 34
      stride: root.vertical ? 1 : 3
      cell: root.vertical ? 1.6 : 2
      gap: root.vertical ? 1 : 0
      led: root.led
    }
  }

  LotusPanel {
    id: lotusPanel
    bar: root.bar
    settings: root.settings
    anchorItem: button
    hostWidget: root
    lotus: root.lotus
  }
}
