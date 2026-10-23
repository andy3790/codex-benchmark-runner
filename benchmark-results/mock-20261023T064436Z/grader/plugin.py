from codex_bench.grading import TestResult, aggregate
def grade(submission, context):
    assert (submission / "seed.txt").read_text() == "same seed"
    assert not (submission / "plugin.py").exists()
    return aggregate([TestResult("fixture_only", "harness_fixture", "passed", 1, 1, detail="Synthetic harness test, not code correctness")])
