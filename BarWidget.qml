import QtQuick
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.tomfaulkner.lotus"

  readonly property var lotus: bar && bar.shell
    ? bar.shell.serviceFor(moduleName)
    : null

  readonly property bool opened: panelLoader.item
    ? panelLoader.item.opened === true
    : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false
  readonly property real openPanelIndicatorWidth: content.implicitWidth
  readonly property real openPanelIndicatorHeight: content.implicitHeight
  readonly property bool ready: !!lotus && lotus.initialized === true
  readonly property color led: bar ? bar.barForeground : Color.foreground

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    if ("lotus" in target) target.lotus = root.lotus
  }

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: Qt.callLater(injectPanel)
  onSettingsChanged: Qt.callLater(injectPanel)
  onLotusChanged: Qt.callLater(injectPanel)
  Component.onCompleted: Qt.callLater(injectPanel)

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: root.injectPanel()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    dimmed: root.ready && (!root.lotus.settings.power || root.lotus.activeMode === "off")
    tooltipText: root.ready
      ? ("Lotus · " + root.lotus.statusLabel)
      : "Lotus"
    fixedWidth: root.vertical
      ? -1
      : Math.round(content.implicitWidth + scaledHorizontalMargin * 2)
    fixedHeight: root.vertical
      ? Math.round(content.implicitHeight + scaledVerticalPadding * 2)
      : -1

    onPressed: function(buttonCode) {
      if (!root.ready) return
      if (buttonCode === Qt.LeftButton) root.toggle()
      else if (buttonCode === Qt.RightButton) root.lotus.cycleMode(1)
      else if (buttonCode === Qt.MiddleButton) root.lotus.togglePower()
    }
    onWheelMoved: function(delta) {
      if (root.ready) root.lotus.nudgeBrightness(delta > 0 ? 16 : -16)
    }

    MatrixPreview {
      id: content
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
}
