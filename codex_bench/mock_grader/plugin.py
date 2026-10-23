"""Read simulator metadata, never execute generated code or claim real validation."""
import json

from codex_bench.grading import TestResult, aggregate


def grade(submission, context):
    path = submission / "simulation.json"
    if not path.is_file():
        return aggregate([TestResult("simulation_unavailable", "simulation", "skipped", None, 100,
                                     detail="No simulated submission; no real functional test was performed")])
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("simulation") is not True:
        raise ValueError("The mock grader only accepts simulation artifacts")
    tests = []
    for name, points, passed in [("functionality", 40, True), ("algorithm", 30, data["algorithm_ok"]),
                                  ("exceptions", 20, data["exceptions_ok"]), ("performance", 10, True)]:
        tests.append(TestResult("simulated_" + name, name, "passed" if passed else "failed", points if passed else 0, points,
                                detail="SYNTHETIC score for GUI/harness demonstration; no candidate code was tested"))
    return aggregate(tests)
