import json
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report_status as rs

FIXTURES = Path(__file__).parent / "fixtures"


class ReportStatusTests(unittest.TestCase):
    def normalize(self, name, fmt="auto", stale=86400, now=None):
        path = FIXTURES / name
        if now is None and name in {"pytest.json", "playwright.json"}:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if name == "pytest.json":
                payload["created"] = time.time()
            else:
                payload["stats"]["startTime"] = datetime.now(timezone.utc).isoformat()
            with tempfile.TemporaryDirectory() as td:
                current = Path(td) / name
                current.write_text(json.dumps(payload), encoding="utf-8")
                return rs.normalize_report(str(current.resolve()), fmt, stale,
                                           now=current.stat().st_mtime + 1)
        if now is None:
            now = path.stat().st_mtime + 1
        return rs.normalize_report(str(path.resolve()), fmt, stale, now=now)

    def assert_error(self, doc, code=None):
        self.assertEqual("error", doc["state"])
        self.assertNotEqual("passed", doc["state"])
        if code:
            self.assertEqual(code, doc["error"]["code"])

    def test_junit_passing_and_failing(self):
        passed = self.normalize("junit-passing.xml")
        self.assertEqual(("passed", "junit", 2, 2),
                         (passed["state"], passed["framework"], passed["tests"], passed["passed"]))
        failed = self.normalize("junit-failing.xml")
        self.assertEqual((3, 1, 1, "failed"),
                         (failed["tests"], failed["failed"], failed["errors"], failed["state"]))
        self.assertEqual("expected 4 but got 5", failed["failures"][0]["message"])

    def test_pytest_and_playwright(self):
        pytest_doc = self.normalize("pytest.json")
        self.assertEqual(("pytest", 2, 1, "failed"),
                         (pytest_doc["framework"], pytest_doc["tests"], pytest_doc["failed"], pytest_doc["state"]))
        pw = self.normalize("playwright.json")
        self.assertEqual(("playwright", 3, 2, 1, "failed"),
                         (pw["framework"], pw["tests"], pw["passed"], pw["failed"], pw["state"]))
        self.assertEqual(1, pw["flakyTests"])
        self.assertEqual(1, len(pw["flaky"]))
        self.assertNotEqual(pw["failures"][0]["id"], pw["flaky"][0]["id"])

    def test_explicit_format_and_conservative_auto_detection(self):
        self.assertEqual("pytest", self.normalize("pytest.json", "pytest")["framework"])
        self.assert_error(self.normalize("pytest.json", "playwright"), "format")
        self.assert_error(self.normalize("unsupported.json"), "unsupported")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ambiguous.json"
            p.write_text(json.dumps({"tests": [], "summary": {}, "suites": [], "config": {}}), encoding="utf-8")
            self.assert_error(rs.normalize_report(str(p.resolve()), "auto", 60, now=p.stat().st_mtime), "ambiguous")

    def test_malformed_contradictory_and_entity_fail_closed(self):
        self.assert_error(self.normalize("malformed.json"), "malformed")
        self.assert_error(self.normalize("contradictory.json"), "contradictory")
        self.assert_error(self.normalize("entity.xml"), "unsafe_xml")

    def test_strict_utf8_exact_file_limit_and_overflow(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_bytes(b'{"tests":[]}' + b"\xff")
            self.assert_error(rs.normalize_report(str(bad.resolve()), "auto", 60, now=time.time()), "encoding")
            base = b'<testsuite tests="0"></testsuite>'
            exact = Path(td) / "exact.xml"
            exact.write_bytes(base + b" " * (rs.MAX_FILE_BYTES - len(base)))
            self.assertEqual("empty", rs.normalize_report(str(exact.resolve()), "junit", 60, now=exact.stat().st_mtime)["state"])
            over = Path(td) / "over.xml"
            over.write_bytes(exact.read_bytes() + b"x")
            self.assert_error(rs.normalize_report(str(over.resolve()), "junit", 60, now=over.stat().st_mtime), "oversized")

    def test_requires_absolute_regular_non_symlink_path(self):
        self.assert_error(rs.normalize_report("relative.xml", "auto", 60, now=time.time()), "path")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "real.xml"
            target.write_text('<testsuite tests="0"/>', encoding="utf-8")
            link = Path(td) / "link.xml"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            self.assert_error(rs.normalize_report(os.path.abspath(link), "junit", 60, now=time.time()), "unsafe_file")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_rejects_special_file(self):
        with tempfile.TemporaryDirectory() as td:
            fifo = Path(td) / "pipe"
            os.mkfifo(fifo)
            self.assert_error(rs.normalize_report(str(fifo.resolve()), "auto", 60, now=time.time()), "unsafe_file")

    def test_stale_empty_and_unconfigured(self):
        p = FIXTURES / "junit-passing.xml"
        stale = rs.normalize_report(str(p.resolve()), "junit", 60, now=p.stat().st_mtime + 61)
        self.assertEqual("stale", stale["state"])
        empty = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
        try:
            empty.write(b'<testsuite tests="0"/>')
            empty.close()
            self.assertEqual("empty", rs.normalize_report(str(Path(empty.name).resolve()), "junit", 60,
                                                          now=os.stat(empty.name).st_mtime)["state"])
        finally:
            os.unlink(empty.name)
        self.assertEqual("unconfigured", rs.normalize_report("", "auto", 60, now=time.time())["state"])

    def test_name_message_failure_and_flaky_bounds(self):
        long_name = "n" * 500
        long_message = "m" * 900
        payload = {"created": 1, "duration": 1, "exitcode": 1,
                   "summary": {"failed": 101, "total": 101},
                   "tests": [{"nodeid": long_name + str(i), "outcome": "failed", "duration": 0,
                              "setup": {"outcome": "passed", "duration": 0},
                              "call": {"outcome": "failed", "duration": 0,
                                       "crash": {"message": long_message}},
                              "teardown": {"outcome": "passed", "duration": 0}} for i in range(101)]}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            doc = rs.normalize_report(str(p.resolve()), "pytest", 60, now=p.stat().st_mtime)
            self.assertEqual(100, len(doc["failures"]))
            self.assertTrue(all(len(x["name"]) <= 240 and len(x["message"]) <= 500 for x in doc["failures"]))

    def test_record_limit_and_depth_limit(self):
        payload = {"created": 1, "duration": 0, "exitcode": 0,
                   "summary": {"passed": 10001, "total": 10001},
                   "tests": [{"nodeid": f"t{i}", "outcome": "passed", "duration": 0} for i in range(10001)]}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "many.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            self.assert_error(rs.normalize_report(str(p.resolve()), "pytest", 60, now=p.stat().st_mtime), "records")
            deep = Path(td) / "deep.json"
            deep.write_text("[" * 80 + "0" + "]" * 80, encoding="utf-8")
            self.assert_error(rs.normalize_report(str(deep.resolve()), "auto", 60, now=deep.stat().st_mtime))

    def test_ascii_serialization_and_output_limit(self):
        doc = self.normalize("junit-failing.xml")
        raw = rs.serialize_document(doc)
        raw.encode("ascii")
        huge = dict(doc)
        huge["padding"] = "x" * (rs.MAX_OUTPUT_BYTES + 1)
        bounded = json.loads(rs.serialize_document(huge))
        self.assertEqual("error", bounded["state"])
        self.assertEqual("output_limit", bounded["error"]["code"])

    def test_cli_never_leaks_path_or_report_content(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "super-secret-name.json"
            secret = "ULTRA_SECRET_REPORT_CONTENT"
            p.write_text('{"x":"' + secret + '"}', encoding="utf-8")
            cp = subprocess.run([sys.executable, str(SCRIPTS / "report_status.py"), "--report", str(p.resolve()),
                                 "--format", "auto", "--stale-seconds", "60"],
                                text=True, capture_output=True, check=False)
            self.assertEqual(0, cp.returncode)
            self.assertEqual("", cp.stderr)
            self.assertNotIn(str(p), cp.stdout)
            self.assertNotIn(secret, cp.stdout)
            self.assertEqual("error", json.loads(cp.stdout)["state"])


if __name__ == "__main__":
    unittest.main()
