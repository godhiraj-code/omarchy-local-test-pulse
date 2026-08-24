# Local Test Pulse 0.1.0

Local Test Pulse is a private, local release candidate for an Omarchy Quattro bar widget. It **only reads** one explicitly configured local test report and summarizes JUnit XML, `pytest-json-report` JSON, or Playwright JSON reporter output. It never runs tests.

## Status model

- **PASS**: validated, fresh, non-empty report with no failed/error results and no recovered flaky tests.
- **FLAKY** (amber): the normalized report state is passing, but one or more logical tests recovered on retry. This is never rendered green.
- **FAIL**: validated report with failed or error attempts.
- **STALE**: filesystem mtime or an available producer timestamp is older than `staleSeconds`; counts remain visible but the result is never green.
- **EMPTY**: valid report with no tests.
- **ERROR**: malformed, contradictory, ambiguous, unsafe, unsupported, or oversized input.
- **SETUP**: no report path is configured.

A flaky hint is strictly within one report. JUnit duplicate identities must contain both pass and fail/error results; Playwright must explicitly mark a logical test `flaky` and provide an earlier unexpected attempt followed by an expected attempt. `flakyTests` is the complete aggregate while at most 50 detail rows are displayed. Local Test Pulse keeps no cross-run history.

## Settings

| Setting | Values | Default |
|---|---|---:|
| `reportPath` | One absolute local regular-file path | empty |
| `format` | `auto`, `junit`, `pytest`, `playwright` | `auto` |
| `refreshSeconds` | 30–3600 | 60 |
| `staleSeconds` | 60–604800 | 86400 |

Auto-detection is conservative. Select a format explicitly if needed; selecting the wrong format produces an error rather than a pass.

## Private local installation

On the Omarchy Linux machine, from this checkout:

```sh
./scripts/install-local.sh
```

The helper refuses to overwrite any existing plugin directory, rejects symlinked or non-directory configuration ancestors, and never uses elevation. Enable `godhiraj.local-test-pulse`, place its bar widget, and configure `reportPath` in Omarchy's plugin settings. To remove only an installation carrying this plugin's ownership marker:

```sh
./scripts/remove-local.sh
```

## Adapter smoke checks

```sh
python3 scripts/report_status.py --report /absolute/path/report.xml --format auto --stale-seconds 86400
python -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
node --check StatusModel.js
node tests/status-model-test.mjs
bash -n scripts/install-local.sh scripts/remove-local.sh
python -m json.tool manifest.json
python -m compileall -q scripts tests
```

The adapter always emits one ASCII-safe JSON document and intentionally emits no stderr. Error responses do not include the configured path or report contents. Freshness uses the older of filesystem mtime and an available producer timestamp. A report is stale only when its age is strictly greater than `staleSeconds`; the exact boundary remains fresh. An mtime or producer timestamp more than five seconds in the future is rejected.

## Security and limitations

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). Notably, the plugin has no network, telemetry, credentials, elevation, recursive discovery, report writes, history database, arbitrary command setting, or remediation behavior. The secure reader opens nonblocking, accepts at most 4 MiB, and rejects descriptor identity/size/mtime/ctime or byte-count changes across the read. The QML service independently limits adapter stdout to 1 MiB characters, rejects nonzero exits, and kills adapter runs that exceed 10 seconds.

Pytest records must contain lifecycle stages consistent with their declared outcome; top-level pass claims alone are rejected. Playwright skipped tests must contain one skipped attempt and no retry history. Changing widget settings immediately invalidates the old result, and an in-flight result from the previous configuration is discarded.

This checkout has not been claimed as real-Quickshell validated. Before any publication, run the official `omarchy-plugin-validate` and live Quickshell checks against the exact Git archive on Omarchy Linux. Publishing, pushing, tagging, catalog submission, or announcement is outside this private RC.
