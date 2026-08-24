import QtQuick
import Quickshell
import Quickshell.Io
import "StatusModel.js" as StatusModel

Item {
  id: root
  visible: false

  property var manifest: null
  property string reportPath: ""
  property string selectedFormat: "auto"
  property int refreshSeconds: 60
  property int staleSeconds: 86400
  readonly property int outputLimit: 1048576
  property bool scanActive: false
  property bool overflowed: false
  property bool timedOut: false
  property bool refreshPending: false
  property int configurationGeneration: 0
  property int activeGeneration: -1
  property string stdoutBuffer: ""
  property var state: StatusModel.errorState("unconfigured", "Configure an absolute local report path")

  readonly property string adapterPath: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir).replace(/\/$/, "") + "/scripts/report_status.py"
    : ""

  function boundedInteger(value, fallback, minimum, maximum) {
    var number = Number(value)
    if (!Number.isFinite(number)) return fallback
    number = Math.round(number)
    return Math.max(minimum, Math.min(maximum, number))
  }

  function configure(settings) {
    var values = settings && typeof settings === "object" ? settings : ({})
    var configuredReport = values.reportPath === undefined ? "" : String(values.reportPath)
    configuredReport = configuredReport.slice(0, 4096)
    var configuredFormat = values.format === undefined ? "auto" : String(values.format)
    if (["auto", "junit", "pytest", "playwright"].indexOf(configuredFormat) < 0)
      configuredFormat = "auto"
    var configuredRefresh = boundedInteger(values.refreshSeconds, 60, 30, 3600)
    var configuredStale = boundedInteger(values.staleSeconds, 86400, 60, 604800)
    var changed = reportPath !== configuredReport || selectedFormat !== configuredFormat ||
      refreshSeconds !== configuredRefresh || staleSeconds !== configuredStale
    reportPath = configuredReport
    selectedFormat = configuredFormat
    refreshSeconds = configuredRefresh
    staleSeconds = configuredStale
    if (changed) {
      configurationGeneration += 1
      state = StatusModel.errorState("configuration_changed", "Waiting for a scan using the current settings")
      if (scanActive || adapter.running) refreshPending = true
      else Qt.callLater(root.refresh)
    }
  }

  function localScriptPath() { return adapterPath }

  function refresh() {
    if (scanActive || adapter.running) return false
    if (adapterPath === "") return false
    var report = reportPath
    var format = selectedFormat
    var stale = staleSeconds
    stdoutBuffer = ""
    overflowed = false
    timedOut = false
    activeGeneration = configurationGeneration
    scanActive = true
    adapter.command = ["python3", localScriptPath(), "--report", String(report),
                       "--format", String(format), "--stale-seconds", String(stale)]
    adapter.running = true
    scanTimeout.restart()
    return true
  }

  function appendStdout(data) {
    if (!scanActive || overflowed) return
    data = String(data)
    if (stdoutBuffer.length + data.length > outputLimit) {
      overflowed = true
      stdoutBuffer = ""
      state = StatusModel.errorState("output_limit", "Adapter output exceeded the safety limit")
      adapter.signal(9)
      return
    }
    stdoutBuffer += data
  }

  onManifestChanged: Qt.callLater(root.refresh)
  Component.onCompleted: Qt.callLater(root.refresh)

  Process {
    id: adapter
    command: []
    stdout: SplitParser {
      splitMarker: ""
      onRead: function(data) { root.appendStdout(data) }
    }
    stderr: SplitParser {
      splitMarker: ""
      onRead: function(_data) { /* Deliberately discarded: may contain paths. */ }
    }
    onExited: function(exitCode, _exitStatus) {
      scanTimeout.stop()
      var resultIsCurrent = StatusModel.shouldPublishScan(root.activeGeneration,
                                                          root.configurationGeneration)
      if (resultIsCurrent) {
        if (root.timedOut)
          root.state = StatusModel.errorState("timeout", "Adapter exceeded the execution time limit")
        else if (exitCode !== 0)
          root.state = StatusModel.errorState("process", "Adapter process failed")
        else if (!root.overflowed)
          root.state = StatusModel.parseAndValidate(root.stdoutBuffer)
      }
      root.stdoutBuffer = ""
      root.scanActive = false
      var pending = root.refreshPending
      root.refreshPending = false
      if (pending) Qt.callLater(root.refresh)
    }
  }

  Timer {
    id: scanTimeout
    interval: 10000
    repeat: false
    onTriggered: {
      if (!root.scanActive) return
      root.timedOut = true
      root.stdoutBuffer = ""
      root.state = StatusModel.errorState("timeout", "Adapter exceeded the execution time limit")
      if (adapter.running) adapter.signal(9)
    }
  }

  Timer {
    id: refreshTimer
    interval: root.refreshSeconds * 1000
    repeat: true
    running: true
    onTriggered: root.refresh()
  }
}
