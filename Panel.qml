import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui
import "StatusModel.js" as StatusModel

BarWidget {
  id: root
  moduleName: "godhiraj.local-test-pulse"

  property bool popupOpen: false
  readonly property var pulseService: bar?.shell?.serviceFor("godhiraj.local-test-pulse")
  readonly property var pulse: pulseService && pulseService.state
    ? pulseService.state : StatusModel.errorState("service", "Local Test Pulse service is unavailable")
  readonly property string statusLabel: StatusModel.label(pulse)
  readonly property string statusColorKey: StatusModel.colorKey(pulse)
  readonly property color foreground: bar ? bar.foreground : Color.popups.text
  readonly property color badgeColor: {
    if (statusColorKey === "green") return Color.pick("local-test-pulse.clean", "#66d17a")
    if (statusColorKey === "red") return bar ? bar.urgent : Color.urgent
    return Color.pick("local-test-pulse.caution", "#e5b567")
  }

  function configureService() {
    var service = pulseService
    if (service && typeof service.configure === "function") service.configure(settings || {})
  }

  function refresh() {
    if (pulseService && typeof pulseService.refresh === "function") pulseService.refresh()
  }

  function open() { popupOpen = true }
  function close() { popupOpen = false }
  function toggle() { popupOpen = !popupOpen }
  readonly property bool opened: popupOpen

  onBarChanged: Qt.callLater(root.configureService)
  onSettingsChanged: Qt.callLater(root.configureService)
  onPulseServiceChanged: Qt.callLater(root.configureService)
  Component.onCompleted: Qt.callLater(root.configureService)

  implicitWidth: vertical ? barSize : badgeButton.implicitWidth
  implicitHeight: vertical ? badgeButton.implicitHeight : barSize

  BarIconButton {
    id: badgeButton
    anchors.fill: parent
    bar: root.bar
    text: root.statusLabel + (root.pulse.tests ? " " + root.pulse.passed + "/" + root.pulse.tests : "")
    foreground: root.badgeColor
    slotSize: Style.bar.statusSlot
    tooltipText: "Local Test Pulse: " + root.statusLabel
    onPressed: root.toggle()
  }

  KeyboardPanel {
    id: details
    anchorItem: badgeButton
    owner: root
    bar: root.bar
    open: root.popupOpen
    focusTarget: keyCatcher
    contentWidth: details.fittedContentWidth(Style.space(430))
    contentHeight: details.fittedContentHeight(content.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onActivateRequested: root.refresh()
      onTextKey: function(text) { if (text === "r" || text === "R") root.refresh() }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: content
          width: parent.width
          spacing: Style.space(8)

          Text {
            width: parent.width
            text: "Local Test Pulse — " + root.statusLabel
            textFormat: Text.PlainText
            color: root.badgeColor
            font.bold: true
            wrapMode: Text.Wrap
          }
          Text {
            width: parent.width
            text: root.pulse.state === "unconfigured"
              ? "Configure one absolute reportPath in plugin settings."
              : root.pulse.state === "error" ? root.pulse.error.message
              : root.pulse.framework + " — " + root.pulse.state
            textFormat: Text.PlainText
            color: root.foreground
            wrapMode: Text.Wrap
          }
          Text {
            width: parent.width
            text: "Tests " + root.pulse.tests + "  Passed " + root.pulse.passed +
                  "  Failed " + root.pulse.failed + "  Errors " + root.pulse.errors +
                  "  Skipped " + root.pulse.skipped + "  Flaky " + root.pulse.flakyTests
            textFormat: Text.PlainText
            color: root.foreground
            wrapMode: Text.Wrap
          }
          Text {
            width: parent.width
            text: "Duration " + root.pulse.durationMs + " ms"
            textFormat: Text.PlainText
            color: root.foreground
          }
          Text {
            text: "Failures"
            visible: root.pulse.failures.length > 0
            textFormat: Text.PlainText
            color: root.foreground
            font.bold: true
          }
          Repeater {
            model: root.pulse.failures
            delegate: Text {
              required property var modelData
              width: content.width
              text: modelData.name + ": " + modelData.message
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              color: bar ? bar.urgent : Color.urgent
            }
          }
          Text {
            text: "Within-run flaky hints"
            visible: root.pulse.flakyTests > 0
            textFormat: Text.PlainText
            color: root.badgeColor
            font.bold: true
          }
          Repeater {
            model: root.pulse.flaky
            delegate: Text {
              required property var modelData
              width: content.width
              text: modelData.name
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              color: root.badgeColor
            }
          }
          Text {
            visible: root.pulse.flakyTests > root.pulse.flaky.length
            width: parent.width
            text: "+ " + (root.pulse.flakyTests - root.pulse.flaky.length) + " more flaky tests"
            textFormat: Text.PlainText
            color: root.badgeColor
          }
          Button {
            text: root.pulseService && root.pulseService.scanActive ? "Refreshing…" : "Refresh"
            enabled: root.pulseService && !root.pulseService.scanActive
            onClicked: root.pulseService.refresh()
          }
        }
      }
    }
  }
}
