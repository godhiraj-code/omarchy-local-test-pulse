# Local Test Pulse engineering rules

## Product boundary

Local Test Pulse is a read-only Omarchy Quattro bar plugin for displaying normalized local automation-test results. It does not run tests, execute project-provided commands, contact CI or network services, modify reports, remediate failures, or collect telemetry.

## Trust boundaries

Treat report paths, files, XML/JSON, test names, timestamps, durations, failure messages, framework metadata, QML settings, process output, and plugin paths as untrusted.

- Use fixed argv arrays with `shell=False`; never interpolate settings into shell commands.
- Open only regular files; reject symlinks and special files.
- Read at most 4 MiB from a report and decode UTF-8 strictly.
- Reject XML declarations containing DOCTYPE or ENTITY before parsing.
- Bound nesting, suites, tests, failures, flaky hints, names, and messages.
- Contradictory, malformed, incomplete, stale-unknown, oversized, or unsupported data must never render green.
- Adapter output must be ASCII-safe JSON and independently bounded by QML.
- QML contains presentation validation only; parser/framework logic belongs in Python.
- One singleton service must serve all monitor widgets; scans never overlap.
- Render all untrusted text as plain text.

## Supported v1 formats

- JUnit XML (`testsuite` or `testsuites` roots)
- pytest-json-report JSON
- Playwright JSON reporter output

Auto-detection must be conservative. Ambiguous or unsupported documents fail visibly.

## Non-goals

No arbitrary test launcher, shell command configuration, network API, credentials, notifications, cross-run database, AI summarization, automatic file discovery across the home directory, or report deletion.

## Verification

Use Python standard-library `unittest`, Node for the presentation model tests, Bash syntax checks, JSON validation, `git diff --check`, official `omarchy-plugin-validate`, exact-archive Linux tests, and real Quickshell validation before publication. Publishing, pushing, catalog submission, or announcements require explicit user approval.
