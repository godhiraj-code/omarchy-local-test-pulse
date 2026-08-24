import json
from datetime import datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_status as rs


class SecurityAcceptanceTests(unittest.TestCase):
    def normalize_text(self, text, suffix=".json", fmt="auto", stale=60):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ("report" + suffix)
            path.write_text(text, encoding="utf-8")
            return rs.normalize_report(str(path.resolve()), fmt, stale, now=path.stat().st_mtime)

    def normalize_json(self, value, fmt, stale=60):
        return self.normalize_text(json.dumps(value), ".json", fmt, stale)

    def assert_error(self, document, code=None):
        self.assertEqual("error", document["state"], document)
        if code is not None:
            self.assertEqual(code, document["error"]["code"])

    def test_json_rejects_duplicate_keys_and_nonstandard_numbers(self):
        duplicate = '{"created":1,"duration":0,"exitcode":0,"summary":{"total":1},"tests":[],"tests":[]}'
        self.assert_error(self.normalize_text(duplicate, fmt="pytest"))
        for constant in ("NaN", "Infinity", "-Infinity"):
            payload = ('{"created":1,"duration":' + constant +
                       ',"exitcode":0,"summary":{"total":0},"tests":[]}')
            self.assert_error(self.normalize_text(payload, fmt="pytest"))

    def test_secure_open_is_nonblocking_and_rejects_post_read_change(self):
        source = (ROOT / "scripts" / "report_status.py").read_text(encoding="utf-8")
        self.assertIn("O_NONBLOCK", source)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.xml"
            path.write_text('<testsuite tests="0"/>', encoding="utf-8")
            real_fstat = os.fstat
            calls = 0

            def changing_fstat(fd):
                nonlocal calls
                calls += 1
                info = real_fstat(fd)
                if calls < 2:
                    return info
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev,
                                       st_ino=info.st_ino, st_size=info.st_size + 1,
                                       st_mtime=info.st_mtime + 1,
                                       st_mtime_ns=getattr(info, "st_mtime_ns", 0) + 1,
                                       st_ctime=info.st_ctime + 1,
                                       st_ctime_ns=getattr(info, "st_ctime_ns", 0) + 1)

            with mock.patch.object(rs.os, "fstat", side_effect=changing_fstat):
                doc = rs.normalize_report(str(path.resolve()), "junit", 60, now=path.stat().st_mtime)
            self.assertGreaterEqual(calls, 2)
            self.assert_error(doc)

    def test_future_mtime_is_not_green(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.xml"
            path.write_text('<testsuite tests="1"><testcase name="ok"/></testsuite>', encoding="utf-8")
            now = time.time()
            os.utime(path, (now + 600, now + 600))
            self.assert_error(rs.normalize_report(str(path.resolve()), "junit", 60, now=now))

    def test_junit_nested_suite_totals_are_checked_at_every_level(self):
        valid = """<testsuites tests="2" failures="1" errors="0" skipped="0">
          <testsuite name="outer" tests="2" failures="1" errors="0" skipped="0">
            <testcase classname="a" name="ok"/>
            <testsuite name="inner" tests="1" failures="1" errors="0" skipped="0">
              <testcase classname="a" name="bad"><failure message="boom"/></testcase>
            </testsuite>
          </testsuite>
        </testsuites>"""
        doc = self.normalize_text(valid, ".xml", "junit")
        self.assertEqual(("failed", 2, 1), (doc["state"], doc["tests"], doc["failed"]))
        bad_child = valid.replace('name="inner" tests="1" failures="1"',
                                  'name="inner" tests="1" failures="0"')
        self.assert_error(self.normalize_text(bad_child, ".xml", "junit"), "contradictory")

    def test_junit_rejects_multiple_same_kind_or_mixed_result_children(self):
        for children in ("<failure/><failure/>", "<failure/><skipped/>", "<error/><error/>"):
            xml = '<testsuite tests="1"><testcase name="x">' + children + "</testcase></testsuite>"
            self.assert_error(self.normalize_text(xml, ".xml", "junit"), "contradictory")

    def test_junit_rejects_unsupported_result_dialects_instead_of_passing(self):
        for child in ("<rerunFailure message=\"retry failed\"/>",
                      "<flakyFailure message=\"flaky\"/>", "<unknown-result/>"):
            xml = '<testsuite tests="1"><testcase name="x">' + child + "</testcase></testsuite>"
            self.assert_error(self.normalize_text(xml, ".xml", "junit"), "unsupported")

    @staticmethod
    def pytest_report(tests, summary, exitcode=0, collectors=None):
        value = {"created": time.time(), "duration": 0.1, "exitcode": exitcode,
                 "summary": summary, "tests": tests}
        if collectors is not None:
            value["collectors"] = collectors
        return value

    def test_pytest_rejects_stage_and_collector_false_green(self):
        teardown_lie = self.pytest_report(
            [{"nodeid": "test_a.py::test_ok", "outcome": "passed", "duration": 0.1,
              "setup": {"outcome": "passed", "duration": 0.01},
              "call": {"outcome": "passed", "duration": 0.05},
              "teardown": {"outcome": "failed", "duration": 0.04}}],
            {"passed": 1, "total": 1})
        self.assert_error(self.normalize_json(teardown_lie, "pytest"), "contradictory")

        collector_lie = self.pytest_report(
            [{"nodeid": "test_a.py::test_ok", "outcome": "passed", "duration": 0.1,
              "call": {"outcome": "passed", "duration": 0.1}}],
            {"passed": 1, "total": 1},
            collectors=[{"nodeid": "broken.py", "outcome": "failed"}])
        self.assert_error(self.normalize_json(collector_lie, "pytest"))

        no_stages = self.pytest_report(
            [{"nodeid": "test_a.py::test_ok", "outcome": "passed", "duration": 0.1}],
            {"passed": 1, "total": 1})
        self.assert_error(self.normalize_json(no_stages, "pytest"), "incomplete")

        missing_call = self.pytest_report(
            [{"nodeid": "test_a.py::test_ok", "outcome": "passed", "duration": 0.1,
              "setup": {"outcome": "passed", "duration": 0.01},
              "teardown": {"outcome": "passed", "duration": 0.01}}],
            {"passed": 1, "total": 1})
        self.assert_error(self.normalize_json(missing_call, "pytest"), "incomplete")

    def test_pytest_supports_real_xfail_xpass_shapes_without_inventing_green(self):
        report = self.pytest_report(
            [{"nodeid": "test_a.py::test_expected", "outcome": "xfailed", "duration": 0.1,
              "setup": {"outcome": "passed", "duration": 0},
              "call": {"outcome": "skipped", "duration": 0.1},
              "teardown": {"outcome": "passed", "duration": 0}},
             {"nodeid": "test_a.py::test_xpass", "outcome": "xpassed", "duration": 0.1,
              "setup": {"outcome": "passed", "duration": 0},
              "call": {"outcome": "passed", "duration": 0.1},
              "teardown": {"outcome": "passed", "duration": 0}}],
            {"xfailed": 1, "xpassed": 1, "total": 2})
        doc = self.normalize_json(report, "pytest")
        self.assertEqual(("passed", 2, 1, 1),
                         (doc["state"], doc["tests"], doc["passed"], doc["skipped"]))

    def test_pytest_interrupted_internal_and_usage_exit_codes_never_pass_as_complete(self):
        failed = [{"nodeid": "test_a.py::test_bad", "outcome": "failed", "duration": 0.1,
                   "setup": {"outcome": "passed", "duration": 0},
                   "call": {"outcome": "failed", "duration": 0.1},
                   "teardown": {"outcome": "passed", "duration": 0}}]
        for exitcode in (2, 3, 4):
            report = self.pytest_report(failed, {"failed": 1, "total": 1}, exitcode=exitcode)
            self.assert_error(self.normalize_json(report, "pytest"), "incomplete_run")

    def test_pytest_accepts_authentic_lifecycle_shapes_without_hiding_failures(self):
        def stage(outcome):
            return {"outcome": outcome, "duration": 0}
        tests = [
            {"nodeid": "t.py::passed", "outcome": "passed",
             "setup": stage("passed"), "call": stage("passed"), "teardown": stage("passed")},
            {"nodeid": "t.py::call_failed", "outcome": "failed",
             "setup": stage("passed"), "call": stage("failed"), "teardown": stage("passed")},
            {"nodeid": "t.py::setup_error", "outcome": "error",
             "setup": stage("failed"), "teardown": stage("passed")},
            {"nodeid": "t.py::teardown_error", "outcome": "error",
             "setup": stage("passed"), "call": stage("passed"), "teardown": stage("failed")},
            {"nodeid": "t.py::skipped", "outcome": "skipped",
             "setup": stage("skipped"), "teardown": stage("passed")},
            {"nodeid": "t.py::xfail", "outcome": "xfailed",
             "setup": stage("passed"), "call": stage("skipped"), "teardown": stage("passed")},
            {"nodeid": "t.py::xpass", "outcome": "xpassed",
             "setup": stage("passed"), "call": stage("passed"), "teardown": stage("passed")},
        ]
        report = self.pytest_report(
            tests, {"passed": 1, "failed": 1, "error": 2, "skipped": 1,
                    "xfailed": 1, "xpassed": 1, "total": 7}, exitcode=1)
        doc = self.normalize_json(report, "pytest")
        self.assertEqual(("failed", 7, 2, 1, 2, 2),
                         (doc["state"], doc["tests"], doc["passed"], doc["failed"],
                          doc["errors"], doc["skipped"]))

    def test_pytest_accepts_producer_combined_setup_and_teardown_errors(self):
        fixture = ROOT / "tests" / "fixtures" / "pytest-combined-lifecycle-errors.json"
        report = json.loads(fixture.read_text(encoding="utf-8"))
        report["created"] = time.time()
        doc = self.normalize_json(report, "pytest")
        self.assertEqual(("failed", 2, 0, 0, 2, 0),
                         (doc["state"], doc["tests"], doc["passed"], doc["failed"],
                          doc["errors"], doc["skipped"]))
        self.assertEqual(
            ["RuntimeError: setup failed", "RuntimeError: teardown failed"],
            [row["message"] for row in doc["failures"]])

    def test_pytest_authentic_shape_derives_duration_from_stages_and_allows_xpass_with_other_failures(self):
        tests = [
            {"nodeid": "test_a.py::test_xpass", "outcome": "xpassed",
             "setup": {"outcome": "passed", "duration": 0.01},
             "call": {"outcome": "passed", "duration": 0.02},
             "teardown": {"outcome": "passed", "duration": 0.01}},
            {"nodeid": "test_a.py::test_bad", "outcome": "failed",
             "setup": {"outcome": "passed", "duration": 0.01},
             "call": {"outcome": "failed", "duration": 0.02},
             "teardown": {"outcome": "passed", "duration": 0.01}},
        ]
        report = self.pytest_report(tests, {"xpassed": 1, "failed": 1, "total": 2}, exitcode=1)
        doc = self.normalize_json(report, "pytest")
        self.assertEqual(("failed", 2, 1, 1),
                         (doc["state"], doc["tests"], doc["passed"], doc["failed"]))

    @staticmethod
    def playwright_report(tests, stats, root_errors=None, spec_ok=True):
        return {
            "config": {"rootDir": "/private/not-emitted"},
            "suites": [{"title": "suite", "suites": [], "specs": [{
                "id": "spec-1", "title": "works", "ok": spec_ok, "tests": tests
            }]}],
            "errors": [] if root_errors is None else root_errors,
            "stats": {"startTime": datetime.now(timezone.utc).isoformat(), "duration": 20, **stats},
        }

    def test_playwright_counts_logical_tests_not_retry_attempts_and_reports_flaky(self):
        report = self.playwright_report(
            [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "passed",
              "status": "flaky", "results": [
                  {"status": "failed", "duration": 10, "retry": 0,
                   "error": {"message": "first attempt failed"}},
                  {"status": "passed", "duration": 8, "retry": 1}
              ]}],
            {"expected": 0, "skipped": 0, "unexpected": 0, "flaky": 1})
        doc = self.normalize_json(report, "playwright")
        self.assertEqual(("passed", 1, 1, 0, 1),
                         (doc["state"], doc["tests"], doc["passed"], doc["failed"], doc["flakyTests"]))
        self.assertEqual(1, len(doc["flaky"]))

    def test_playwright_rejects_false_flaky_global_errors_and_stats_lies(self):
        lone_flaky = self.playwright_report(
            [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "passed",
              "status": "flaky", "results": [{"status": "passed", "duration": 8, "retry": 0}]}],
            {"expected": 0, "skipped": 0, "unexpected": 0, "flaky": 1})
        self.assert_error(self.normalize_json(lone_flaky, "playwright"), "contradictory")

        passed = self.playwright_report(
            [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "passed",
              "status": "expected", "results": [{"status": "passed", "duration": 8, "retry": 0}]}],
            {"expected": 1, "skipped": 0, "unexpected": 0, "flaky": 0})
        with_global_error = dict(passed)
        with_global_error["errors"] = [{"message": "worker crashed"}]
        self.assert_error(self.normalize_json(with_global_error, "playwright"))

        stats_lie = json.loads(json.dumps(passed))
        stats_lie["stats"]["expected"] = 0
        stats_lie["stats"]["unexpected"] = 1
        self.assert_error(self.normalize_json(stats_lie, "playwright"), "contradictory")

    def test_playwright_unexpected_is_failed_even_when_attempt_passed(self):
        report = self.playwright_report(
            [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "failed",
              "status": "unexpected", "results": [{"status": "passed", "duration": 8, "retry": 0}]}],
            {"expected": 0, "skipped": 0, "unexpected": 1, "flaky": 0}, spec_ok=False)
        doc = self.normalize_json(report, "playwright")
        self.assertEqual(("failed", 1, 0, 1),
                         (doc["state"], doc["tests"], doc["passed"], doc["failed"]))

    def test_playwright_rejects_skipped_status_with_retry_history(self):
        for prior in ("failed", "timedOut", "interrupted"):
            report = self.playwright_report(
                [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "skipped",
                  "status": "skipped", "results": [
                      {"status": prior, "duration": 1, "retry": 0},
                      {"status": "skipped", "duration": 1, "retry": 1}]}],
                {"expected": 0, "skipped": 1, "unexpected": 0, "flaky": 0})
            self.assert_error(self.normalize_json(report, "playwright"), "contradictory")

    def test_playwright_rejects_retry_gaps_invalid_start_time_and_duplicate_spec_ids(self):
        report = self.playwright_report(
            [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "passed",
              "status": "flaky", "results": [
                  {"status": "failed", "duration": 1, "retry": 0},
                  {"status": "passed", "duration": 1, "retry": 2}]}],
            {"expected": 0, "skipped": 0, "unexpected": 0, "flaky": 1})
        self.assert_error(self.normalize_json(report, "playwright"), "contradictory")

        invalid_time = json.loads(json.dumps(report))
        invalid_time["suites"][0]["specs"][0]["tests"][0]["results"][1]["retry"] = 1
        invalid_time["stats"]["startTime"] = "not-a-time"
        self.assert_error(self.normalize_json(invalid_time, "playwright"), "contradictory")

        clean = self.playwright_report(
            [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "passed",
              "status": "expected", "results": [{"status": "passed", "duration": 1, "retry": 0}]}],
            {"expected": 1, "skipped": 0, "unexpected": 0, "flaky": 0})
        duplicate = json.loads(json.dumps(clean["suites"][0]["specs"][0]))
        duplicate["title"] = "another title"
        clean["suites"][0]["specs"].append(duplicate)
        clean["stats"]["expected"] = 2
        self.assert_error(self.normalize_json(clean, "playwright"), "contradictory")

    def test_flaky_aggregate_is_not_lost_when_display_rows_are_capped(self):
        tests = []
        for index in range(51):
            tests.append({"projectId": "chromium", "projectName": "chromium",
                          "expectedStatus": "passed", "status": "flaky", "title": f"t{index}",
                          "results": [{"status": "failed", "duration": 1, "retry": 0},
                                      {"status": "passed", "duration": 1, "retry": 1}]})
        # Each spec normally supplies one title. Use separate specs to preserve unique identities.
        suites = []
        for index, test in enumerate(tests):
            suites.append({"id": f"spec-{index}", "title": f"t{index}", "ok": True, "tests": [test]})
        report = {"config": {}, "suites": [{"title": "suite", "suites": [], "specs": suites}],
                  "errors": [], "stats": {"startTime": datetime.now(timezone.utc).isoformat(),
                  "duration": 102, "expected": 0, "skipped": 0, "unexpected": 0, "flaky": 51}}
        doc = self.normalize_json(report, "playwright")
        self.assertEqual(("passed", 51, 51, 50),
                         (doc["state"], doc["tests"], doc["flakyTests"], len(doc["flaky"])))

    def test_producer_timestamps_participate_in_staleness_and_future_rejection(self):
        old_pytest = self.pytest_report(
            [{"nodeid": "test_a.py::test_ok", "outcome": "passed", "duration": 0,
              "setup": {"outcome": "passed", "duration": 0},
              "call": {"outcome": "passed", "duration": 0},
              "teardown": {"outcome": "passed", "duration": 0}}],
            {"passed": 1, "total": 1})
        old_pytest["created"] = time.time() - 61
        self.assertEqual("stale", self.normalize_json(old_pytest, "pytest", stale=60)["state"])

        future_playwright = self.playwright_report(
            [{"projectId": "chromium", "projectName": "chromium", "expectedStatus": "passed",
              "status": "expected", "results": [{"status": "passed", "duration": 1, "retry": 0}]}],
            {"expected": 1, "skipped": 0, "unexpected": 0, "flaky": 0})
        future_playwright["stats"]["startTime"] = datetime.fromtimestamp(
            time.time() + 60, timezone.utc).isoformat()
        self.assert_error(self.normalize_json(future_playwright, "playwright"), "future_timestamp")


if __name__ == "__main__":
    unittest.main()
