pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "io.github.tomfaulkner.lotus"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var lotus: null
  readonly property var barIdentity: hostWidget || root

  readonly property color foreground: Color.popups.text
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool ready: !!lotus && lotus.initialized === true
  readonly property var modes: Model.MODES
  property int selectedIndex: 0
  property bool cursorActive: true

  function open() {
    selectedIndex = Math.max(0, Model.modeIndex(ready ? lotus.settings.mode : "auto"))
    cursorActive = true
    controller.show()
  }
  function close() { controller.hide() }
  function toggle() { opened ? close() : open() }

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(barIdentity, direction)
    return false
  }

  function selectDelta(delta) {
    cursorActive = true
    selectedIndex = (selectedIndex + delta + modes.length) % modes.length
  }

  function activateSelected() {
    if (!ready) return
    lotus.setMode(modes[selectedIndex].id)
  }

  onOpenedChanged: if (opened) {
    selectedIndex = Math.max(0, Model.modeIndex(ready ? lotus.settings.mode : "auto"))
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (dx !== 0 && root.ready) root.lotus.nudgeBrightness(dx > 0 ? 16 : -16)
        else root.selectDelta(dy)
      }
      onActivateRequested: root.activateSelected()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (!root.ready) return
        if (t === " " || t === "p" || t === "P") root.lotus.togglePower()
        else if (t === "n" || t === "N") root.lotus.flash()
        else if (t === "u" || t === "U") root.lotus.installUdev()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          Item {
            id: header
            width: parent.width
            implicitHeight: hero.implicitHeight
            readonly property bool ringVisible: root.cursorActive && root.selectedIndex === -1
            function focusHero() { root.selectedIndex = -1; root.cursorActive = true }

            PanelHero {
              id: hero
              width: parent.width
              title: "Lotus"
              meta: root.ready ? root.lotus.statusLabel : "Starting…"
              detail: root.ready && root.lotus.hasDevice
                ? (root.lotus.devices.length === 1 ? "1 module" : root.lotus.devices.length + " modules")
                : "No module"
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconComponent: Component {
                MatrixPreview {
                  pixels: root.ready ? root.lotus.pixels : []
                  columns: 9
                  rows: 34
                  stride: 3
                  cell: 2
                  gap: 0
                  led: root.foreground
                }
              }
              trailingControl: Component {
                ToggleSwitch {
                  id: powerSwitch
                  checked: root.ready ? root.lotus.settings.power : true
                  hasCursor: header.ringVisible
                  foreground: hero.foreground
                  onHovered: function(on) { if (on) header.focusHero() }
                  onToggled: if (root.ready) root.lotus.togglePower()
                  PanelToolTip {
                    visible: powerSwitch.containsMouse
                    text: root.ready && root.lotus.settings.power ? "Sleep the matrix" : "Wake the matrix"
                    fontFamily: hero.fontFamily
                  }
                }
              }
            }
          }

          Item {
            width: parent.width
            implicitHeight: plate.implicitHeight + Style.space(16)
            BorderSurface {
              id: plate
              anchors.horizontalCenter: parent.horizontalCenter
              implicitWidth: preview.implicitWidth + Style.space(16)
              implicitHeight: preview.implicitHeight + Style.space(16)
              color: Style.normalFillFor(root.foreground, Color.accent)
              radius: Style.cornerRadius
              MatrixPreview {
                id: preview
                anchors.centerIn: parent
                pixels: root.ready ? root.lotus.pixels : []
                columns: 9
                rows: 34
                stride: 1
                cell: 4
                gap: 1
                led: root.foreground
              }
            }
          }

          Text {
            visible: root.ready && (root.lotus.needsUdev || root.lotus.lastError !== "")
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.ready && root.lotus.needsUdev
              ? "The LED matrix is there, but this session cannot open it. Install the udev rule once."
              : (root.ready ? root.lotus.lastError : "")
            color: Color.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Button {
            visible: root.ready && root.lotus.needsUdev
            text: "Install udev rule"
            foreground: root.foreground
            onClicked: root.lotus.installUdev()
          }

          Text {
            text: "MODE"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.letterSpacing: 1.2
          }

          Column {
            id: modeColumn
            width: parent.width
            spacing: Style.space(2)

            Repeater {
              model: root.modes
              delegate: Item {
                id: row
                required property var modelData
                required property int index
                width: modeColumn.width
                implicitHeight: Style.space(36)
                readonly property bool selected: root.ready && root.lotus.settings.mode === modelData.id
                readonly property bool hovered: root.cursorActive && root.selectedIndex === index

                BorderSurface {
                  anchors.fill: parent
                  color: row.hovered
                    ? Style.hoverFillFor(root.foreground, Color.accent)
                    : (row.selected ? Style.selectedFillFor(root.foreground, Color.accent) : "transparent")
                  borderSpec: row.hovered
                    ? Border.controlSpec("hover", root.foreground, Color.accent)
                    : (row.selected ? Border.controlSpec("normal", root.foreground, Color.accent) : Border.none())
                  radius: Style.cornerRadius
                }

                Column {
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Style.space(12)
                  anchors.rightMargin: Style.space(12)
                  spacing: 0
                  Text {
                    text: row.modelData.label
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: row.selected
                  }
                  Text {
                    text: row.modelData.hint
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }
                }

                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  onEntered: { root.cursorActive = true; root.selectedIndex = row.index }
                  onClicked: if (root.ready) root.lotus.setMode(row.modelData.id)
                }
              }
            }
          }

          Text {
            text: "BRIGHTNESS  " + (root.ready ? Math.round(root.lotus.settings.brightness / 2.55) + "%" : "")
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.letterSpacing: 1.2
          }

          PanelSlider {
            width: parent.width
            bar: root.bar
            value: root.ready ? root.lotus.settings.brightness : 96
            minimum: 10
            maximum: 255
            step: 8
            integer: true
            onMoved: function(v) {
              if (root.ready) root.lotus.updateSettings({ brightness: Math.round(v) })
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(8)

            Toggle {
              width: parent.width
              label: "Sleep when locked"
              checked: root.ready ? root.lotus.settings.sleepLocked : true
              foreground: root.foreground
              onClicked: if (root.ready) root.lotus.updateSettings({ sleepLocked: !root.lotus.settings.sleepLocked })
            }
            Toggle {
              width: parent.width
              label: "Flash on notifications"
              checked: root.ready ? root.lotus.settings.flashNotify : true
              foreground: root.foreground
              onClicked: if (root.ready) root.lotus.updateSettings({ flashNotify: !root.lotus.settings.flashNotify })
            }
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Scroll the bar icon for brightness. Right-click cycles modes. Middle-click sleeps."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }
      }
    }
  }
}
