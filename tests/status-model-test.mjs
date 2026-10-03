import assert from "node:assert/strict";
import model from "../StatusModel.js";

function base() {
  return { version: 1, state: "failed", framework: "pytest", tests: 2, passed: 1,
    failed: 1, errors: 0, skipped: 0, flakyTests: 0, durationMs: 25, reportMtime: 1720000000,
    failures: [{id: "a".repeat(24), name: "x", message: "boom"}], flaky: [], error: null };
}
function rejects(mutator, code) {
  const value = base(); mutator(value);
  const result = model.validate(value);
  assert.equal(result.state, "error");
  if (code) assert.equal(result.error.code, code);
}
assert.equal(model.validate(base()).state, "failed");
const passed = base(); Object.assign(passed, {state:"passed", tests:1, passed:1, failed:0, failures:[]});
assert.equal(model.validate(passed).state, "passed");
assert.equal(model.label(passed), "PASS");
assert.equal(model.colorKey(passed), "green");
const flaky = {...passed, flakyTests: 1, flaky: [{id:"b".repeat(24), name:"retried"}]};
assert.equal(model.validate(flaky).state, "passed");
assert.equal(model.label(flaky), "FLAKY");
assert.equal(model.colorKey(flaky), "amber");
const staleFailure = {...base(), state:"stale"};
assert.equal(model.validate(staleFailure).state, "stale");
assert.equal(model.colorKey(staleFailure), "amber");
rejects(x => x.tests = 3, "totals");
for (const invalid of ["toString", "constructor", "__proto__", ["pytest"], ["passed"]]) {
  rejects(x => x.state = invalid, "state");
  rejects(x => x.framework = invalid, "state");
  const forgedPass = {...passed, framework: invalid};
  assert.equal(model.colorKey(model.validate(forgedPass)), "amber");
}
rejects(x => x.state = "passed", "semantics");
rejects(x => x.state = "empty", "semantics");
rejects(x => x.durationMs = -1, "ranges");
rejects(x => x.reportMtime = Infinity, "ranges");
rejects(x => x.failures[0].name = "n".repeat(241), "failures");
rejects(x => x.failures[0].message = "m".repeat(501), "failures");
rejects(x => x.failures.push({...x.failures[0]}), "failure_subset");
rejects(x => { x.flakyTests = 0; x.flaky = [{id:"b".repeat(24), name:"x"}]; }, "flaky_total");
rejects(x => { x.flakyTests = 2; x.flaky = [{id:"a".repeat(24), name:"x"}, {id:"a".repeat(24), name:"x"}]; }, "flaky");
rejects(x => x.flakyTests = 3, "flaky_total");
rejects(x => delete x.flakyTests, "counts");
rejects(x => x.error = {code:"unexpected", message:"bad"}, "semantics");
assert.equal(model.parseAndValidate("not json").error.code, "json");
assert.equal(model.parseAndValidate("x".repeat(1048577)).error.code, "output_limit");
assert.equal(model.shouldPublishScan(4, 4), true);
assert.equal(model.shouldPublishScan(4, 5), false);
assert.equal(model.shouldPublishScan(-1, 0), false);
const unconfigured = model.errorState("unconfigured", "configure"); unconfigured.state = "unconfigured";
assert.equal(model.validate(unconfigured).state, "unconfigured");
console.log("StatusModel: security and visual invariants passed");
