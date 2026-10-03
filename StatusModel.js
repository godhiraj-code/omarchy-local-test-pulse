/* Independent, presentation-only validation for adapter output. */
var MAX_NAME = 240;
var MAX_MESSAGE = 500;
var MAX_FAILURES = 100;
var MAX_FLAKY = 50;
var MAX_TESTS = 10000;
var MAX_DURATION_MS = 315576000000;
var STATES = { passed: true, failed: true, empty: true, stale: true, error: true, unconfigured: true };
var FRAMEWORKS = { junit: true, pytest: true, playwright: true, unknown: true };

function errorState(code, message) {
    return {
        version: 1, state: "error", framework: "unknown",
        tests: 0, passed: 0, failed: 0, errors: 0, skipped: 0, flakyTests: 0,
        durationMs: 0, reportMtime: 0, failures: [], flaky: [],
        error: { code: String(code || "validation").slice(0, 40),
                 message: String(message || "Invalid adapter output").slice(0, 160) }
    };
}

function isIntegerIn(value, maximum) {
    return typeof value === "number" && Number.isFinite(value) && Math.floor(value) === value && value >= 0 && value <= maximum;
}

function validId(value) {
    return typeof value === "string" && /^[0-9a-f]{24}$/.test(value);
}

function validate(input) {
    try {
        if (!input || typeof input !== "object" || Array.isArray(input) || input.version !== 1)
            return errorState("schema", "Invalid adapter schema");
        if (typeof input.state !== "string" || STATES[input.state] !== true ||
            typeof input.framework !== "string" || FRAMEWORKS[input.framework] !== true)
            return errorState("state", "Invalid status or framework");
        var counts = [input.tests, input.passed, input.failed, input.errors, input.skipped, input.flakyTests];
        for (var i = 0; i < counts.length; ++i)
            if (!isIntegerIn(counts[i], MAX_TESTS)) return errorState("counts", "Invalid result counts");
        if (input.tests !== input.passed + input.failed + input.errors + input.skipped)
            return errorState("totals", "Result totals are contradictory");
        if (input.flakyTests > input.tests)
            return errorState("flaky_total", "Flaky aggregate exceeds test count");
        if (!isIntegerIn(input.durationMs, MAX_DURATION_MS) || !isIntegerIn(input.reportMtime, 4102444800))
            return errorState("ranges", "Invalid duration or timestamp");
        if (!Array.isArray(input.failures) || input.failures.length > MAX_FAILURES ||
            !Array.isArray(input.flaky) || input.flaky.length > MAX_FLAKY)
            return errorState("collections", "Result collections exceed limits");
        if (input.failures.length > input.failed + input.errors)
            return errorState("failure_subset", "Failures exceed failed results");
        if (input.flaky.length > input.flakyTests)
            return errorState("flaky_total", "Flaky rows exceed the aggregate");

        var failureIds = Object.create(null);
        var failures = [];
        for (i = 0; i < input.failures.length; ++i) {
            var row = input.failures[i];
            if (!row || typeof row !== "object" || !validId(row.id) || failureIds[row.id] ||
                typeof row.name !== "string" || row.name.length > MAX_NAME ||
                typeof row.message !== "string" || row.message.length > MAX_MESSAGE)
                return errorState("failures", "Invalid or duplicate failure row");
            failureIds[row.id] = true;
            failures.push({ id: row.id, name: row.name.slice(0, MAX_NAME), message: row.message.slice(0, MAX_MESSAGE) });
        }
        var flakyIds = Object.create(null);
        var flaky = [];
        for (i = 0; i < input.flaky.length; ++i) {
            row = input.flaky[i];
            if (!row || typeof row !== "object" || !validId(row.id) || flakyIds[row.id] ||
                typeof row.name !== "string" || row.name.length > MAX_NAME)
                return errorState("flaky", "Invalid flaky hint");
            flakyIds[row.id] = true;
            flaky.push({ id: row.id, name: row.name.slice(0, MAX_NAME) });
        }

        var hasFailure = input.failed + input.errors > 0;
        if (input.state === "passed" && !(input.tests > 0 && !hasFailure && input.framework !== "unknown"))
            return errorState("semantics", "Passed state is contradictory");
        if (input.state === "failed" && !hasFailure)
            return errorState("semantics", "Failed state is contradictory");
        if (input.state === "empty" && input.tests !== 0)
            return errorState("semantics", "Empty state is contradictory");
        if ((input.state === "passed" || input.state === "failed" || input.state === "empty" || input.state === "stale") &&
            (input.framework === "unknown" || input.reportMtime === 0 || input.error !== null))
            return errorState("semantics", "Report state lacks validated metadata");
        if ((input.state === "error" || input.state === "unconfigured") &&
            (!input.error || typeof input.error !== "object" || typeof input.error.code !== "string" ||
             input.error.code.length > 40 || typeof input.error.message !== "string" || input.error.message.length > 160))
            return errorState("error", "Invalid error metadata");

        return {
            version: 1, state: input.state, framework: input.framework,
            tests: input.tests, passed: input.passed, failed: input.failed, errors: input.errors,
            skipped: input.skipped, flakyTests: input.flakyTests,
            durationMs: input.durationMs, reportMtime: input.reportMtime,
            failures: failures, flaky: flaky,
            error: input.error === null ? null : { code: input.error.code.slice(0, 40), message: input.error.message.slice(0, 160) }
        };
    } catch (_) {
        return errorState("validation", "Invalid adapter output");
    }
}

function label(input) {
    if (input && input.state === "passed" && input.flakyTests > 0) return "FLAKY";
    if (input && input.state === "passed") return "PASS";
    if (input && input.state === "failed") return "FAIL";
    if (input && input.state === "stale") return "STALE";
    if (input && input.state === "empty") return "EMPTY";
    if (input && input.state === "unconfigured") return "SETUP";
    return "ERROR";
}

function colorKey(input) {
    if (input && input.state === "failed") return "red";
    if (input && input.state === "passed" && input.flakyTests === 0) return "green";
    return "amber";
}

function parseAndValidate(raw) {
    if (typeof raw !== "string" || raw.length > 1048576)
        return errorState("output_limit", "Adapter output exceeded the safety limit");
    try { return validate(JSON.parse(raw)); }
    catch (_) { return errorState("json", "Adapter returned malformed JSON"); }
}

function shouldPublishScan(activeGeneration, currentGeneration) {
    return Number.isInteger(activeGeneration) && Number.isInteger(currentGeneration) &&
        activeGeneration >= 0 && activeGeneration === currentGeneration;
}

if (typeof module !== "undefined") module.exports = {
    validate: validate, parseAndValidate: parseAndValidate, errorState: errorState,
    label: label, colorKey: colorKey, shouldPublishScan: shouldPublishScan
};
