"""Example trusted plugin. All expected values stay in the host grader process."""
from __future__ import annotations

import json
from pathlib import Path

from codex_bench.grading import GradeContext, TestResult, aggregate


def grade(submission: Path, context: GradeContext) -> dict:
    # Freeze this file and cases.json BEFORE generation; never copy to the project.
    cases = json.loads((context.plugin_dir / "cases.json").read_text(encoding="utf-8"))
    executor = context.docker(submission)
    tests = []
    for case in cases:
        if case.get("generator") == "reverse_large":
            numbers = list(range(100000, 0, -1))
            payload = {"numbers": numbers}
            expected = {"sorted": list(reversed(numbers)), "sum": 5000050000, "median": 50000.5}
        elif case.get("generator") == "overlong":
            payload, expected = {"numbers": [0] * 100001}, {"error": "invalid_input"}
        elif "raw_input" in case:
            payload, expected = case["raw_input"].encode("utf-8"), case["expected"]
        else:
            payload, expected = case["input"], case["expected"]
        if not (submission / "solution.py").is_file():
            tests.append(TestResult(case["name"], case["category"], "failed", 0, case["points"], detail="solution.py missing"))
            continue
        result = executor.run(case["name"], ["python", "-I", "-B", "/submission/solution.py"], payload)
        if result["status"] == "skipped":
            tests.append(TestResult(case["name"], case["category"], "skipped", None, case["points"], detail=result["detail"]))
            continue
        try:
            observed = json.loads(result.get("stdout", ""))
            passed = result["status"] == "ok" and observed == expected
        except (TypeError, ValueError):
            passed = False
        tests.append(TestResult(case["name"], case["category"], "passed" if passed else "failed",
                                case["points"] if passed else 0, case["points"], result["duration_seconds"],
                                "Verified JSON output" if passed else "Incorrect JSON, execution failure, or time limit: " + result.get("detail", "")))
    # No fabricated score for unsupported metrics.
    tests.append(TestResult("memory_peak", "performance", "skipped", None, 0, detail="Peak memory not measured by this plugin"))
    return aggregate(tests)
