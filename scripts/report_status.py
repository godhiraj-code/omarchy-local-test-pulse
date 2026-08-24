#!/usr/bin/env python3
"""Safely normalize one local test report for Local Test Pulse."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
import stat
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_RECORDS = 10_000
MAX_FAILURES = 100
MAX_FLAKY = 50
MAX_NAME = 240
MAX_MESSAGE = 500
MAX_DEPTH = 64
MAX_DURATION_MS = 315_576_000_000
FUTURE_MTIME_TOLERANCE_SECONDS = 5
FORMATS = {"auto", "junit", "pytest", "playwright"}


class ReportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


def _clamp(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    return " ".join(value.replace("\x00", "").split())[:maximum]


def _number(value: Any, field: str, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ReportError("contradictory", f"Invalid {field}")
    if integer:
        if int(value) != value:
            raise ReportError("contradictory", f"Invalid {field}")
        return int(value)
    return float(value)


def _text_number(value: Any, field: str, *, integer: bool = False) -> float | int:
    if not isinstance(value, str) or not value.strip():
        raise ReportError("contradictory", f"Invalid {field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ReportError("contradictory", f"Invalid {field}") from None
    return _number(parsed, field, integer=integer)


def _ms_from_seconds(value: Any, field: str = "duration") -> int:
    result = int(round(_number(value, field) * 1000))
    if result > MAX_DURATION_MS:
        raise ReportError("contradictory", f"Invalid {field}")
    return result


def _xml_ms(value: str, field: str) -> int:
    result = int(round(_text_number(value, field) * 1000))
    if result > MAX_DURATION_MS:
        raise ReportError("contradictory", f"Invalid {field}")
    return result


def _ms(value: Any, field: str = "duration") -> int:
    result = int(round(_number(value, field)))
    if result > MAX_DURATION_MS:
        raise ReportError("contradictory", f"Invalid {field}")
    return result


def _iso_timestamp(value: Any, field: str) -> float:
    if not isinstance(value, str) or not value:
        raise ReportError("contradictory", f"Invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        result = parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        raise ReportError("contradictory", f"Invalid {field}") from None
    if not math.isfinite(result) or result < 0:
        raise ReportError("contradictory", f"Invalid {field}")
    return result


def _identity(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", "strict")).hexdigest()[:24]


def _error_document(code: str, message: str = "Report could not be read safely") -> dict[str, Any]:
    return {
        "version": 1, "state": "error", "framework": "unknown",
        "tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
        "flakyTests": 0, "durationMs": 0, "reportMtime": 0,
        "failures": [], "flaky": [],
        "error": {"code": _clamp(code, 40), "message": _clamp(message, 160)},
    }


def _unconfigured_document() -> dict[str, Any]:
    doc = _error_document("unconfigured", "Configure an absolute local report path")
    doc["state"] = "unconfigured"
    return doc


def _validate_depth(root: Any) -> None:
    stack = [(root, 1)]
    seen = 0
    while stack:
        value, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise ReportError("depth", "Report nesting exceeds the safety limit")
        seen += 1
        if seen > 250_000:
            raise ReportError("structure", "Report structure exceeds the safety limit")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values() if isinstance(item, (dict, list)))
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value if isinstance(item, (dict, list)))


def _secure_read(path: str) -> tuple[str, float]:
    if not os.path.isabs(path):
        raise ReportError("path", "Report path must be absolute")
    try:
        before = os.lstat(path)
    except OSError:
        raise ReportError("unavailable", "Report is unavailable") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReportError("unsafe_file", "Report must be a regular non-symlink file")

    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ReportError("unsafe_file", "Report could not be opened safely") from None
    try:
        initial = os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode):
            raise ReportError("unsafe_file", "Report must be a regular file")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            raise ReportError("unsafe_file", "Report changed while opening")
        if initial.st_size < 0 or initial.st_size > MAX_FILE_BYTES:
            raise ReportError("oversized", "Report exceeds the 4 MiB limit")
        chunks: list[bytes] = []
        remaining = initial.st_size + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        final = os.fstat(fd)
        initial_key = (initial.st_dev, initial.st_ino, initial.st_size,
                       getattr(initial, "st_mtime_ns", int(initial.st_mtime * 1_000_000_000)),
                       getattr(initial, "st_ctime_ns", int(initial.st_ctime * 1_000_000_000)))
        final_key = (final.st_dev, final.st_ino, final.st_size,
                     getattr(final, "st_mtime_ns", int(final.st_mtime * 1_000_000_000)),
                     getattr(final, "st_ctime_ns", int(final.st_ctime * 1_000_000_000)))
        if final_key != initial_key or len(raw) != initial.st_size:
            raise ReportError("unsafe_file", "Report changed while reading")
    finally:
        os.close(fd)
    try:
        return raw.decode("utf-8", "strict"), initial.st_mtime
    except UnicodeDecodeError:
        raise ReportError("encoding", "Report must be strict UTF-8") from None


def _new_accumulator(framework: str) -> dict[str, Any]:
    return {"framework": framework, "tests": 0, "passed": 0, "failed": 0,
            "errors": 0, "skipped": 0, "flakyTests": 0, "durationMs": 0,
            "failures": [], "flaky": [], "attempts": {}, "names": {}}


def _add_record(acc: dict[str, Any], raw_id: str, name: str, outcome: str,
                duration_ms: int, message: str = "") -> str:
    acc["tests"] += 1
    if acc["tests"] > MAX_RECORDS:
        raise ReportError("records", "Report exceeds the 10,000 record limit")
    if outcome not in {"passed", "failed", "errors", "skipped"}:
        raise ReportError("unsupported", "Report contains an unsupported test outcome")
    acc[outcome] += 1
    acc["durationMs"] += duration_ms
    if acc["durationMs"] > MAX_DURATION_MS:
        raise ReportError("contradictory", "Invalid total duration")
    canonical = _identity(raw_id)
    acc["names"].setdefault(canonical, _clamp(name, MAX_NAME))
    statuses = acc["attempts"].setdefault(canonical, set())
    statuses.add("passed" if outcome == "passed" else "failed" if outcome in {"failed", "errors"} else "skipped")
    if outcome in {"failed", "errors"} and len(acc["failures"]) < MAX_FAILURES:
        if not any(row["id"] == canonical for row in acc["failures"]):
            acc["failures"].append({"id": canonical, "name": _clamp(name, MAX_NAME),
                                    "message": _clamp(message or "Test failed", MAX_MESSAGE)})
    return canonical


def _mark_flaky(acc: dict[str, Any], canonical: str, name: str) -> None:
    acc["flakyTests"] += 1
    if len(acc["flaky"]) < MAX_FLAKY:
        acc["flaky"].append({"id": canonical, "name": _clamp(name, MAX_NAME)})


def _finish(acc: dict[str, Any], reported_duration: int | None = None,
            *, infer_flaky: bool = True) -> dict[str, Any]:
    attempts = acc.pop("attempts")
    names = acc.pop("names")
    if infer_flaky:
        flaky_ids = [key for key, statuses in attempts.items()
                     if "passed" in statuses and "failed" in statuses]
        acc["flakyTests"] = len(flaky_ids)
        acc["flaky"] = [{"id": key, "name": names[key]} for key in flaky_ids[:MAX_FLAKY]]
    if reported_duration is not None:
        acc["durationMs"] = reported_duration
    return acc


def _xml_depth(root: ET.Element) -> None:
    stack = [(root, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise ReportError("depth", "XML nesting exceeds the safety limit")
        count += 1
        if count > 50_000:
            raise ReportError("structure", "XML structure exceeds the safety limit")
        stack.extend((child, depth + 1) for child in list(node))


def _junit_case(case: ET.Element, acc: dict[str, Any]) -> dict[str, int]:
    name = case.attrib.get("name")
    if not isinstance(name, str) or not name:
        raise ReportError("incomplete", "JUnit testcase is missing a name")
    classname = case.attrib.get("classname", "")
    if not isinstance(classname, str):
        raise ReportError("incomplete", "JUnit testcase identity is invalid")
    duration = _xml_ms(case.attrib["time"], "test duration") if "time" in case.attrib else 0
    allowed_children = {"failure", "error", "skipped", "system-out", "system-err", "properties"}
    if any(child.tag not in allowed_children for child in list(case)):
        raise ReportError("unsupported", "JUnit testcase contains an unsupported result element")
    result_children = [child for child in list(case) if child.tag in {"failure", "error", "skipped"}]
    if len(result_children) > 1:
        raise ReportError("contradictory", "JUnit testcase has contradictory outcomes")
    child = result_children[0] if result_children else None
    raw_id = f"{classname}::{name}" if classname else name
    counts = {"tests": 1, "failures": 0, "errors": 0, "skipped": 0, "durationMs": duration}
    if child is None:
        _add_record(acc, raw_id, name, "passed", duration)
    elif child.tag == "failure":
        counts["failures"] = 1
        _add_record(acc, raw_id, name, "failed", duration,
                    child.attrib.get("message") or child.text or "Test failed")
    elif child.tag == "error":
        counts["errors"] = 1
        _add_record(acc, raw_id, name, "errors", duration,
                    child.attrib.get("message") or child.text or "Test error")
    else:
        counts["skipped"] = 1
        _add_record(acc, raw_id, name, "skipped", duration)
    return counts


def _validate_junit_totals(node: ET.Element, observed: dict[str, int]) -> None:
    for attr in ("tests", "failures", "errors", "skipped"):
        if attr in node.attrib and _text_number(node.attrib[attr], attr, integer=True) != observed[attr]:
            raise ReportError("contradictory", "JUnit totals contradict testcase records")
    if "time" in node.attrib:
        _xml_ms(node.attrib["time"], "suite duration")


def _parse_junit(text: str) -> dict[str, Any]:
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ReportError("unsafe_xml", "XML declarations with DOCTYPE or ENTITY are forbidden")
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, RecursionError):
        raise ReportError("malformed", "Malformed JUnit XML") from None
    _xml_depth(root)
    if root.tag not in {"testsuite", "testsuites"}:
        raise ReportError("format", "Document is not JUnit XML")

    acc = _new_accumulator("junit")
    report_times: list[float] = []
    observed_by_node: dict[int, dict[str, int]] = {}
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if not visited:
            if node.tag not in {"testsuite", "testsuites"}:
                raise ReportError("contradictory", "JUnit suite hierarchy is invalid")
            stack.append((node, True))
            for child in reversed(list(node)):
                if child.tag in {"testsuite", "testsuites"}:
                    stack.append((child, False))
        else:
            if "timestamp" in node.attrib:
                report_times.append(_iso_timestamp(node.attrib["timestamp"], "JUnit timestamp"))
            total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "durationMs": 0}
            for child in list(node):
                if child.tag == "testcase":
                    counts = _junit_case(child, acc)
                elif child.tag in {"testsuite", "testsuites"}:
                    counts = observed_by_node[id(child)]
                else:
                    continue
                for key in total:
                    total[key] += counts[key]
            _validate_junit_totals(node, total)
            observed_by_node[id(node)] = total
    root_totals = observed_by_node[id(root)]
    duration = _xml_ms(root.attrib["time"], "suite duration") if "time" in root.attrib else root_totals["durationMs"]
    result = _finish(acc, duration)
    if report_times:
        result["reportTime"] = min(report_times)
    return result


def _require_dict(value: Any, code: str = "format") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportError(code, "Report has an invalid structure")
    return value


def _pytest_message(item: dict[str, Any]) -> str:
    stages = ("call", "setup", "teardown")
    for failed_only in (True, False):
        for stage_name in stages:
            stage = item.get(stage_name)
            if not isinstance(stage, dict):
                continue
            if failed_only and stage.get("outcome") != "failed":
                continue
            crash = stage.get("crash")
            if isinstance(crash, dict) and isinstance(crash.get("message"), str):
                return crash["message"]
            if isinstance(stage.get("longrepr"), str):
                return stage["longrepr"]
    return ""


def _parse_pytest(data: Any) -> dict[str, Any]:
    root = _require_dict(data)
    required = {"created", "duration", "exitcode", "summary", "tests"}
    if not required.issubset(root) or not isinstance(root["tests"], list) or not isinstance(root["summary"], dict):
        raise ReportError("format", "Document is not complete pytest-json-report JSON")
    report_time = float(_number(root["created"], "created"))
    report_duration = _ms_from_seconds(root["duration"], "report duration")
    exitcode = _number(root["exitcode"], "exitcode", integer=True)
    tests = root["tests"]
    if len(tests) > MAX_RECORDS:
        raise ReportError("records", "Report exceeds the 10,000 record limit")

    collectors = root.get("collectors", [])
    if not isinstance(collectors, list) or len(collectors) > MAX_RECORDS:
        raise ReportError("incomplete", "pytest collectors are invalid")
    collector_stack = list(collectors)
    collector_count = 0
    while collector_stack:
        collector_count += 1
        if collector_count > MAX_RECORDS:
            raise ReportError("records", "pytest collectors exceed the safety limit")
        collector = _require_dict(collector_stack.pop(), "incomplete")
        outcome = collector.get("outcome")
        if outcome is not None and outcome not in {"passed", "skipped"}:
            raise ReportError("collector", "pytest collection did not complete cleanly")
        children = collector.get("result", [])
        if children is not None:
            if not isinstance(children, list):
                raise ReportError("incomplete", "pytest collector structure is invalid")
            collector_stack.extend(children)

    acc = _new_accumulator("pytest")
    mapping = {"passed": "passed", "failed": "failed", "error": "errors",
               "skipped": "skipped", "xfailed": "skipped", "xpassed": "passed"}
    raw_counts = {key: 0 for key in mapping}
    seen_nodeids: set[str] = set()
    stage_outcomes = {"passed", "failed", "skipped"}
    for raw_item in tests:
        item = _require_dict(raw_item, "incomplete")
        nodeid = item.get("nodeid")
        outcome = item.get("outcome")
        if not isinstance(nodeid, str) or not nodeid or outcome not in mapping:
            raise ReportError("incomplete", "pytest record is incomplete or unsupported")
        if nodeid in seen_nodeids:
            raise ReportError("contradictory", "pytest contains an ambiguous duplicate nodeid")
        seen_nodeids.add(nodeid)
        stage_by_name: dict[str, str] = {}
        stage_duration = 0
        for stage_name in ("setup", "call", "teardown"):
            if stage_name not in item:
                continue
            stage = _require_dict(item[stage_name], "incomplete")
            stage_outcome = stage.get("outcome")
            if stage_outcome not in stage_outcomes:
                raise ReportError("incomplete", "pytest stage outcome is invalid")
            stage_duration += _ms_from_seconds(stage.get("duration"), "stage duration")
            if stage_duration > MAX_DURATION_MS:
                raise ReportError("contradictory", "Invalid test duration")
            stage_by_name[stage_name] = stage_outcome
        if "setup" not in stage_by_name or "teardown" not in stage_by_name:
            raise ReportError("incomplete", "pytest lifecycle stages are incomplete")
        if stage_by_name["setup"] == "passed" and "call" not in stage_by_name:
            raise ReportError("incomplete", "pytest call stage is missing")
        if stage_by_name["setup"] != "passed" and "call" in stage_by_name:
            raise ReportError("contradictory", "pytest call stage exists after incomplete setup")
        signature = (stage_by_name["setup"], stage_by_name.get("call"), stage_by_name["teardown"])
        valid_lifecycles = {
            "passed": {("passed", "passed", "passed")},
            "failed": {("passed", "failed", "passed")},
            "error": {("failed", None, "passed"),
                      ("failed", None, "failed"),
                      ("skipped", None, "failed"),
                      ("passed", "passed", "failed"),
                      ("passed", "failed", "failed")},
            "skipped": {("skipped", None, "passed"),
                        ("passed", "skipped", "passed")},
            "xfailed": {("passed", "skipped", "passed")},
            "xpassed": {("passed", "passed", "passed")},
        }
        if signature not in valid_lifecycles[outcome]:
            raise ReportError("contradictory", "pytest lifecycle contradicts the test outcome")
        if "duration" in item:
            duration = _ms_from_seconds(item["duration"], "test duration")
        else:
            duration = stage_duration
        raw_counts[outcome] += 1
        _add_record(acc, nodeid, nodeid, mapping[outcome], duration, _pytest_message(item))

    summary = root["summary"]
    known_summary = {"passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed",
                     "total", "collected"}
    for key, value in summary.items():
        count = _number(value, f"summary {key}", integer=True)
        if key not in known_summary and count != 0:
            raise ReportError("unsupported", "pytest summary contains an unsupported nonzero category")
    expected_summary = dict(raw_counts)
    expected_summary["error"] = expected_summary.pop("error")
    for key in ("passed", "failed", "skipped", "xfailed", "xpassed"):
        if _number(summary.get(key, 0), f"summary {key}", integer=True) != expected_summary[key]:
            raise ReportError("contradictory", "pytest summary contradicts test records")
    reported_errors = summary.get("error", summary.get("errors", 0))
    if "error" in summary and "errors" in summary and summary["error"] != summary["errors"]:
        raise ReportError("contradictory", "pytest error totals are ambiguous")
    if _number(reported_errors, "summary error", integer=True) != raw_counts["error"]:
        raise ReportError("contradictory", "pytest summary contradicts test records")
    if _number(summary.get("total", len(tests)), "summary total", integer=True) != len(tests):
        raise ReportError("contradictory", "pytest summary contradicts test records")
    if "collected" in summary and _number(summary["collected"], "summary collected", integer=True) != len(tests):
        raise ReportError("contradictory", "pytest collected total contradicts test records")
    has_bad_results = acc["failed"] + acc["errors"] > 0
    if exitcode in {2, 3, 4}:
        raise ReportError("incomplete_run", "pytest did not complete a normal test run")
    if exitcode not in {0, 1, 5}:
        raise ReportError("incomplete_run", "pytest returned an unsupported session exit code")
    if exitcode == 5 and len(tests) != 0:
        raise ReportError("contradictory", "pytest no-tests exit code contradicts test records")
    if (exitcode == 0 and has_bad_results) or (exitcode == 1 and not has_bad_results):
        raise ReportError("contradictory", "pytest exit code contradicts test records")
    result = _finish(acc, report_duration)
    result["reportTime"] = report_time
    return result


def _attempt_matches_expected(status_value: str, expected: str) -> bool:
    return status_value == expected


def _parse_playwright(data: Any) -> dict[str, Any]:
    root = _require_dict(data)
    if not isinstance(root.get("suites"), list) or not isinstance(root.get("config"), dict):
        raise ReportError("format", "Document is not Playwright JSON reporter output")
    root_errors = root.get("errors")
    stats = root.get("stats")
    if not isinstance(root_errors, list) or not isinstance(stats, dict):
        raise ReportError("incomplete", "Playwright report metadata is incomplete")
    if root_errors:
        raise ReportError("global_error", "Playwright reported a global error")
    report_duration = _ms(stats.get("duration"), "report duration")
    if "startTime" not in stats:
        raise ReportError("incomplete", "Playwright report start time is missing")
    report_time = _iso_timestamp(stats["startTime"], "Playwright report start time")
    stats_keys = ("expected", "unexpected", "flaky", "skipped")
    supplied_stats = {key: _number(stats.get(key), f"stats {key}", integer=True) for key in stats_keys}

    acc = _new_accumulator("playwright")
    logical = {key: 0 for key in stats_keys}
    seen_identities: set[str] = set()
    seen_spec_ids: set[str] = set()
    test_statuses = {"expected", "unexpected", "flaky", "skipped"}
    attempt_statuses = {"passed", "failed", "timedOut", "skipped", "interrupted"}
    expected_statuses = set(attempt_statuses)
    stack: list[tuple[dict[str, Any], tuple[str, ...]]] = [
        (_require_dict(suite, "incomplete"), ()) for suite in reversed(root["suites"])]
    attempt_count = 0
    while stack:
        suite, parents = stack.pop()
        title = suite.get("title", "")
        if not isinstance(title, str):
            raise ReportError("incomplete", "Playwright suite title is invalid")
        current = parents + ((title,) if title else ())
        children = suite.get("suites", [])
        specs = suite.get("specs", [])
        if not isinstance(children, list) or not isinstance(specs, list):
            raise ReportError("incomplete", "Playwright suite structure is invalid")
        for child in reversed(children):
            stack.append((_require_dict(child, "incomplete"), current))
        for raw_spec in specs:
            spec = _require_dict(raw_spec, "incomplete")
            spec_title = spec.get("title")
            spec_id = spec.get("id")
            spec_ok = spec.get("ok")
            tests = spec.get("tests")
            if not isinstance(spec_id, str) or not spec_id:
                raise ReportError("incomplete", "Playwright spec is incomplete")
            if spec_id in seen_spec_ids:
                raise ReportError("contradictory", "Playwright spec identity is duplicated")
            if (not isinstance(spec_title, str) or not spec_title or
                    not isinstance(spec_ok, bool) or not isinstance(tests, list)):
                raise ReportError("incomplete", "Playwright spec is incomplete")
            seen_spec_ids.add(spec_id)
            spec_has_unexpected = False
            for raw_test in tests:
                test = _require_dict(raw_test, "incomplete")
                project_id = test.get("projectId")
                project_name = test.get("projectName")
                test_status = test.get("status")
                expected_status = test.get("expectedStatus")
                results = test.get("results")
                test_title = test.get("title", "")
                if (not isinstance(project_id, str) or not isinstance(project_name, str) or
                        not isinstance(test_title, str) or test_status not in test_statuses or
                        expected_status not in expected_statuses or not isinstance(results, list) or not results):
                    raise ReportError("incomplete", "Playwright test record is incomplete")
                raw_id = "\x1f".join(current + (spec_id, spec_title, test_title, project_id, project_name))
                if raw_id in seen_identities:
                    raise ReportError("contradictory", "Playwright test identity is duplicated")
                seen_identities.add(raw_id)
                attempt_count += len(results)
                if len(seen_identities) + attempt_count > MAX_RECORDS:
                    raise ReportError("records", "Playwright tests and attempts exceed the safety limit")
                retries: list[int] = []
                statuses: list[str] = []
                duration = 0
                message = ""
                for raw_result in results:
                    result = _require_dict(raw_result, "incomplete")
                    status_value = result.get("status")
                    retry = _number(result.get("retry"), "retry", integer=True)
                    if status_value not in attempt_statuses:
                        raise ReportError("unsupported", "Playwright attempt outcome is unsupported")
                    if retries and retry <= retries[-1]:
                        raise ReportError("contradictory", "Playwright retries are duplicated or unordered")
                    retries.append(retry)
                    statuses.append(status_value)
                    duration += _ms(result.get("duration"), "test duration")
                    if duration > MAX_DURATION_MS:
                        raise ReportError("contradictory", "Invalid test duration")
                    error = result.get("error")
                    if not message and isinstance(error, dict) and isinstance(error.get("message"), str):
                        message = error["message"]
                if retries != list(range(len(results))):
                    raise ReportError("contradictory", "Playwright retries contain a gap")
                matches = [_attempt_matches_expected(value, expected_status) for value in statuses]
                final_matches = matches[-1]
                display = " / ".join(current + (spec_title,))
                canonical = _identity(raw_id)
                if test_status == "flaky":
                    recovered = any(not matches[i] and any(matches[j] for j in range(i + 1, len(matches)))
                                    for i in range(len(matches)))
                    if not recovered or not final_matches:
                        raise ReportError("contradictory", "Playwright flaky status lacks a recovered retry")
                    _add_record(acc, raw_id, display, "passed", duration)
                    _mark_flaky(acc, canonical, display)
                elif test_status == "expected":
                    if not final_matches or any(not value for value in matches):
                        raise ReportError("contradictory", "Playwright expected status contradicts attempts")
                    _add_record(acc, raw_id, display, "passed", duration)
                elif test_status == "skipped":
                    if statuses != ["skipped"]:
                        raise ReportError("contradictory", "Playwright skipped status contradicts attempts")
                    _add_record(acc, raw_id, display, "skipped", duration)
                else:
                    if final_matches:
                        raise ReportError("contradictory", "Playwright unexpected status contradicts attempts")
                    spec_has_unexpected = True
                    output_outcome = "errors" if statuses[-1] in {"timedOut", "interrupted"} else "failed"
                    _add_record(acc, raw_id, display, output_outcome, duration, message)
                logical[test_status] += 1
            if spec_ok == spec_has_unexpected:
                raise ReportError("contradictory", "Playwright spec status contradicts logical tests")
    for key in stats_keys:
        if supplied_stats[key] != logical[key]:
            raise ReportError("contradictory", "Playwright stats contradict logical tests")
    result = _finish(acc, report_duration, infer_flaky=False)
    result["reportTime"] = report_time
    return result


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReportError("malformed", "Malformed report document")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ReportError("malformed", "Malformed report document")


def _parse(text: str, selected_format: str) -> dict[str, Any]:
    stripped = text.lstrip("\ufeff \t\r\n")
    if selected_format == "junit" or (selected_format == "auto" and stripped.startswith("<")):
        return _parse_junit(text)
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_object_keys,
                          parse_constant=_reject_json_constant)
    except ReportError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        raise ReportError("malformed", "Malformed report document") from None
    _validate_depth(data)
    if selected_format == "pytest":
        return _parse_pytest(data)
    if selected_format == "playwright":
        return _parse_playwright(data)
    if selected_format != "auto":
        raise ReportError("format", "Unsupported format selection")
    is_pytest = isinstance(data, dict) and isinstance(data.get("tests"), list) and isinstance(data.get("summary"), dict)
    is_playwright = isinstance(data, dict) and isinstance(data.get("suites"), list) and isinstance(data.get("config"), dict)
    if is_pytest and is_playwright:
        raise ReportError("ambiguous", "Report matches multiple formats")
    if is_pytest:
        return _parse_pytest(data)
    if is_playwright:
        return _parse_playwright(data)
    raise ReportError("unsupported", "Report format is unsupported")


def normalize_report(path: str, selected_format: str = "auto", stale_seconds: int = 86400,
                     *, now: float | None = None) -> dict[str, Any]:
    if not path:
        return _unconfigured_document()
    try:
        if selected_format not in FORMATS:
            raise ReportError("format", "Unsupported format selection")
        if isinstance(stale_seconds, bool) or not isinstance(stale_seconds, int) or not 60 <= stale_seconds <= 604800:
            raise ReportError("settings", "Stale threshold is invalid")
        text, mtime = _secure_read(path)
        parsed = _parse(text, selected_format)
        clock = time.time() if now is None else now
        if isinstance(clock, bool) or not isinstance(clock, (int, float)) or not math.isfinite(clock):
            raise ReportError("clock", "Current time is invalid")
        if mtime - clock > FUTURE_MTIME_TOLERANCE_SECONDS:
            raise ReportError("future_mtime", "Report timestamp is in the future")
        semantic_time = parsed.get("reportTime")
        if semantic_time is not None and semantic_time - clock > FUTURE_MTIME_TOLERANCE_SECONDS:
            raise ReportError("future_timestamp", "Report content timestamp is in the future")
        tests = parsed["tests"]
        failed = parsed["failed"]
        errors = parsed["errors"]
        state = "empty" if tests == 0 else "failed" if failed + errors else "passed"
        # Exact boundary is fresh; only an age strictly greater than the threshold is stale.
        freshness_time = min(mtime, semantic_time) if semantic_time is not None else mtime
        if clock - freshness_time > stale_seconds:
            state = "stale"
        return {
            "version": 1, "state": state, "framework": parsed["framework"],
            "tests": tests, "passed": parsed["passed"], "failed": failed,
            "errors": errors, "skipped": parsed["skipped"], "flakyTests": parsed["flakyTests"],
            "durationMs": parsed["durationMs"], "reportMtime": max(0, int(mtime)),
            "failures": parsed["failures"], "flaky": parsed["flaky"], "error": None,
        }
    except ReportError as exc:
        return _error_document(exc.code, exc.public_message)
    except (Exception, RecursionError):
        return _error_document("internal", "Report could not be parsed safely")


def serialize_document(document: dict[str, Any]) -> str:
    try:
        raw = json.dumps(document, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("ascii")) <= MAX_OUTPUT_BYTES:
            return raw
    except (TypeError, ValueError, RecursionError):
        pass
    return json.dumps(_error_document("output_limit", "Normalized output exceeded the safety limit"),
                      ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--format", choices=sorted(FORMATS), default="auto")
    parser.add_argument("--stale-seconds", type=int, default=86400)
    try:
        args = parser.parse_args(argv)
        document = normalize_report(args.report, args.format, args.stale_seconds)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        document = _error_document("arguments", "Adapter arguments are invalid")
    except (Exception, RecursionError):
        document = _error_document("internal", "Adapter failed safely")
    sys.stdout.write(serialize_document(document) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
