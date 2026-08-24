# Local Test Pulse Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a native, read-only Omarchy Quattro bar plugin that safely normalizes one configured local JUnit, pytest JSON, or Playwright JSON report and displays trustworthy status, failures, and within-run flake signals.

**Architecture:** A dependency-free Python adapter owns file opening, format detection, parsing, validation, bounding, and normalization. One singleton QML service invokes the adapter using a fixed argv vector, applies a second output ceiling, and exposes one validated state to all monitor widgets. A small JavaScript model independently validates normalized output before the panel renders it.

**Tech Stack:** Python 3.11 standard library, Quickshell/QML, JavaScript, Bash lifecycle helpers, `unittest`, Node.

---

## Security contract

1. Runtime is Omarchy Linux only.
2. User configures exactly one absolute report path; no recursive discovery.
3. Adapter uses `os.open` with `O_NONBLOCK` and `O_NOFOLLOW` where available, verifies a regular file using pre/post-read `fstat`, rejects symlinks/special files, and rejects device/inode/size/mtime/ctime or byte-count changes. It accepts at most 4 MiB and decodes UTF-8 strictly.
4. XML containing `<!DOCTYPE` or `<!ENTITY` is rejected before `ElementTree` parsing.
5. JSON rejects duplicate keys and nonstandard numeric constants. JSON/XML nesting and output collections are bounded: at most 10,000 normalized records (or Playwright logical tests plus attempts), 100 normalized failure rows, 50 displayed flaky hints, 240 characters per name, 500 characters per message, and 1 MiB serialized adapter output.
6. Unknown, malformed, contradictory, incomplete, oversized, invalidly encoded, or unsupported reports return a non-green error document. Pytest lifecycle stages and Playwright skipped/retry histories must be internally complete and consistent.
7. State is one of `passed`, `failed`, `empty`, `stale`, `error`, or `unconfigured`. Green is allowed only for a validated, non-stale report with at least one test, zero failures/errors, and `flakyTests === 0`.
8. JUnit flakiness requires duplicate canonical identities with both failing and passing records. Playwright flakiness requires the authoritative logical-test status plus an earlier unexpected attempt and later expected attempt. `flakyTests` is complete even when rows cap at 50. No unsupported cross-run claim.
9. Fixed argv only. The plugin never executes tests or report-provided commands.
10. QML accumulates at most 1 MiB characters from adapter stdout using `SplitParser`, discards stderr, rejects nonzero exits, force-kills on overflow or after 10 seconds, rejects overlapping scans, invalidates results on settings changes, discards older-generation scan results, queues one current-generation refresh, and clears buffers every run.
11. Untrusted UI strings use `Text.PlainText` and are length-clamped again in JavaScript.
12. No network, credentials, telemetry, elevated privileges, automatic writes, report mutation, history database, or remediation.

## Task 1: Repository and manifest

Create `manifest.json`, `.gitattributes`, `.gitignore`, `LICENSE`, and `README.md`. Declare plugin ID `godhiraj.local-test-pulse`, version `0.1.0`, one singleton service, and one bar widget. Settings: `reportPath` string default empty, `format` enum `auto|junit|pytest|playwright`, `refreshSeconds` integer 30–3600 default 60, and `staleSeconds` integer 60–604800 default 86400.

Verification: `python -m json.tool manifest.json` and static manifest tests.

## Task 2: Failing adapter tests and fixtures

Create fixtures for passing/failing JUnit, pytest JSON, Playwright JSON with retries, malformed JSON, unsupported JSON, contradictory totals, and entity-bearing XML. Add red tests for all three parsers, auto-detection, strict UTF-8, 4 MiB overflow, symlink/special-file rejection, stale handling, exact boundary, name/message bounds, record limits, and fail-closed output.

Verification: tests fail because adapter is absent.

## Task 3: Bounded adapter

Create `scripts/report_status.py` with secure file reading, conservative detection, three parsers, normalization, within-run flaky detection, stale evaluation using `fstat` modification time, and ASCII-safe JSON output. It must expose pure functions for tests and a CLI using `--report`, `--format`, and `--stale-seconds`.

Verification: adapter tests pass with `python -W error::ResourceWarning -m unittest tests.test_report_status -v`.

## Task 4: Presentation model tests and implementation

Create `StatusModel.js` and Node tests. Validate every normalized field, cross-field totals, state semantics, timestamp/duration ranges, duplicate identities, failure/flaky subset constraints, and maximum lengths. Any contradiction returns an error state.

Verification: `node --check StatusModel.js` and `node tests/status-model-test.mjs`.

## Task 5: Singleton QML service and panel

Create `Service.qml` singleton and `Panel.qml`. Service invokes only `python3 <plugin>/scripts/report_status.py --report <setting> --format <setting> --stale-seconds <setting>` as an argv list. Use `SplitParser` with empty split marker, 1 MiB retained-character ceiling, stderr drain/discard, `signal(9)` on overflow, overlap prevention, refresh timer, and validated model ingestion. Panel shows concise state and a popup with framework, counts, duration, failures, flaky hints, refresh action, and configuration guidance. It must not open or execute report-provided content.

Verification: static QML contract tests and real Quickshell later.

## Task 6: Lifecycle, threat model, and adversarial suite

Create safe local install/remove helpers, `docs/THREAT_MODEL.md`, CI, and tests ensuring no shell interpolation, no test execution, no network, no privilege elevation, LF executable scripts, bounded output, plain-text rendering, and singleton use.

Verification:

```sh
python -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
node --check StatusModel.js
node tests/status-model-test.mjs
bash -n scripts/install-local.sh scripts/remove-local.sh
python -m json.tool manifest.json
python -m compileall -q scripts tests
git diff --check
```

## Task 7: Independent review and live validation

Run independent spec compliance, security/correctness, and final artifact reviews. Repair every critical, high, or medium finding. Validate the exact Git archive with official `omarchy-plugin-validate` and Linux tests. When `omarchy-desktop` is online, install locally, verify normal/failed/stale/error/overflow states, singleton multi-monitor behavior, refresh overlap prevention, removal, and exact shell restoration.

Do not push, publish, submit, tag, release, or announce without a separate explicit approval packet.
