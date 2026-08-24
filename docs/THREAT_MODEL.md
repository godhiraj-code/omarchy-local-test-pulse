# Threat model

## Assets and trust boundaries

The user trusts Local Test Pulse not to execute test-project content and not to turn an untrusted report into a green result. The configured path, filesystem object, report bytes, XML/JSON structure, names, messages, timestamps, durations, adapter stdout, QML settings, and plugin install path are untrusted. Omarchy/Quickshell, `python3`, and this installed plugin's own files are part of the local trusted computing base.

## Security properties

1. **Read only:** the runtime opens one configured path for reading. It performs no report mutation, recursive discovery, test invocation, remediation, history storage, telemetry, or network access.
2. **No command injection:** `Service.qml` constructs a fixed argv list for `python3` and the bundled adapter. Settings occupy individual argv elements. There is no shell, interpolation, `eval`, or report-provided command.
3. **Filesystem containment:** the adapter requires an absolute path, checks `lstat`, rejects links and non-regular files, opens with `O_NONBLOCK` and `O_NOFOLLOW` where available, and uses pre/post-read `fstat`. Descriptor device, inode, size, nanosecond mtime, and nanosecond ctime must remain identical, and the byte count must equal the initial descriptor size. The reported mtime comes from the opened descriptor.
4. **Bounded input:** one regular file, maximum 4 MiB, strict UTF-8. JSON rejects duplicate keys and nonstandard `NaN`/infinity constants. XML containing `DOCTYPE` or `ENTITY` is rejected before `ElementTree` parsing.
5. **Bounded parsing:** nesting is capped at 64, normalized records at 10,000, Playwright logical tests plus attempts at 10,000, normalized failures at 100, displayed flaky hints at 50, names at 240 characters, and messages at 500 characters. Parser exceptions and `RecursionError` fail closed.
6. **Bounded output and execution:** adapter JSON is ASCII-safe and capped at 1 MiB. QML independently retains at most 1 MiB characters, discards stderr, rejects a nonzero adapter exit, sends signal 9 on overflow or after 10 seconds, clears buffers, prevents overlapping scans, and queues one configuration refresh if settings change during a scan.
7. **No false green:** malformed, incomplete, contradictory, ambiguous, unsupported, invalidly encoded, oversized, stale, empty, future-dated beyond the five-second tolerance, or unsafe reports cannot become visually green. Pytest records require an authentic setup/call/teardown lifecycle shape for their declared outcome. Playwright skipped tests cannot contain retry history. Staleness uses the older of filesystem mtime and an available producer timestamp (`pytest.created`, Playwright `stats.startTime`, or JUnit suite timestamp), with a strict `age > staleSeconds` boundary. JavaScript independently checks counts, states, ranges, identities, collection subsets, lengths, and `flaky.length <= flakyTests <= tests`.
8. **Safe presentation:** report-derived strings are displayed only with `Text.PlainText` after adapter and JavaScript length bounds.
9. **Singleton lifecycle:** the manifest declares one keep-loaded service; each bar widget resolves it through `bar.shell.serviceFor`, sends bounded settings through `configure`, and uses that singleton for refresh. Refresh attempts do not overlap. Configuration changes immediately invalidate the displayed state, increment a generation, and discard any completed scan from an older generation. The adapter path derives only from the trusted manifest source directory.

## Flaky semantics

A flaky hint is not historical. JUnit can infer it only from duplicate records sharing one hashed identity with both pass and fail/error outcomes. Playwright counts each `tests[]` entry once and requires reporter status `flaky`, an earlier unexpected attempt, and a later expected attempt. A recovered Playwright test contributes to `passed` and `flakyTests`, but not to current failures. The complete aggregate is preserved even when the detail list is capped at 50. A clean, fresh document with `flakyTests > 0` may have normalized state `passed`, but the presentation model labels it **FLAKY** and colors it amber. No report history is retained.

## Error privacy

Parser and filesystem failures use small fixed public messages. They do not echo paths, raw parser exceptions, traceback text, or report content. Adapter stderr is empty in normal and error operation and is discarded by QML.

## Residual risks

- A privileged local process can replace the plugin, Python interpreter, or report after checks; protecting the local account and installed files is outside this plugin.
- Local lifecycle helpers reject symlinked or non-directory config, `omarchy`, and `plugins` ancestors, but cannot defend against a privileged process racing those checks.
- Filesystem and report timestamps can be manipulated by a local writer. Staleness is a conservative freshness hint, not cryptographic provenance.
- Counts describe only the configured report's claims after structural validation; the plugin cannot prove that a test runner actually executed the tests.
- QML runtime compatibility must be verified on the target Omarchy Quattro build before distribution. This RC does not claim that live validation.

## Out of scope

Network/CI integrations, credentials, notifications, arbitrary launch commands, AI summaries, home-directory scans, elevated installation, report cleanup, remediation, and cross-run databases are deliberately excluded.
