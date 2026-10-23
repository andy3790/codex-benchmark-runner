import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.grading import DockerExecutor, GradeContext, TestResult, aggregate, validate_grade
from codex_bench.process import ProcessResult
from codex_bench.util import BenchError


class GradingTests(unittest.TestCase):
    def test_skipped_not_zero_or_pass(self):
        grade = aggregate([TestResult("unsupported", "performance", "skipped", None, 50)])
        self.assertIsNone(grade["score"])
        self.assertIsNone(grade["pass_rate"])
        self.assertEqual(grade["planned_max_score"], 50)

    def test_validation_recomputes_totals(self):
        raw = aggregate([TestResult("bad", "functional", "failed", 0, 10), TestResult("ok", "functional", "passed", 10, 10)])
        raw["score"] = 999
        grade = validate_grade(raw)
        self.assertEqual(grade["score"], 10)
        self.assertEqual(grade["pass_rate"], 0.5)

    def test_invalid_grader_scores_rejected(self):
        for test in [TestResult("x", "a", "skipped", 0, 10), TestResult("x", "a", "passed", None, 10), TestResult("x", "a", "passed", 11, 10), TestResult("x", "a", "passed", float("nan"), 10)]:
            raw = {"schema_version": 1, "source": "independent_grader", "tests": [vars(test)]}
            with self.subTest(test=test), self.assertRaises(BenchError):
                validate_grade(raw)

    def test_missing_docker_does_not_run_candidate(self):
        with tempfile.TemporaryDirectory() as directory, patch("codex_bench.grading.shutil.which", return_value=None), patch("codex_bench.grading.run_process") as run:
            executor = DockerExecutor(Path(directory), Path(directory), {}, 1)
            self.assertEqual(executor.run("x", ["python", "solution.py"], {})["status"], "skipped")
            run.assert_not_called()

    def test_docker_restrictions_and_cleanup(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submission = root / "submission"
            submission.mkdir()
            def fake_run(command, **kwargs):
                kwargs["stdout"].write_text('{"ok": true}', encoding="utf-8")
                kwargs["stderr"].write_text("", encoding="utf-8")
                return ProcessResult(0, 0.1)
            with patch("codex_bench.grading.shutil.which", return_value="docker"), patch("codex_bench.grading.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"linux\n", b"")) as docker, patch("codex_bench.grading.run_process", side_effect=fake_run) as run:
                executor = DockerExecutor(submission, root / "logs", {}, 2)
                result = executor.run("test", ["python", "/submission/solution.py"], {})
            command = run.call_args.args[0]
            for flag in ["--network=none", "--read-only", "--cap-drop=ALL", "--pull=never", "--security-opt=no-new-privileges", "--user=65534:65534"]:
                self.assertIn(flag, command)
            self.assertEqual(sum("type=bind" in part for part in command), 1)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(docker.call_args.args[0][1:3], ["rm", "--force"])

    def test_sample_grader_uses_actual_observation(self):
        plugin = Path(__file__).resolve().parent.parent / "graders" / "sample" / "plugin.py"
        spec = importlib.util.spec_from_file_location("sample_grader_test", plugin)
        module = importlib.util.module_from_spec(spec)
        with patch("sys.dont_write_bytecode", True):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "solution.py").write_text("# never imported")
            context = GradeContext(root, plugin.parent, 1, {})
            with patch.object(context, "docker") as factory:
                factory.return_value.run.return_value = {"status": "ok", "stdout": '{"sorted":[1,2,3],"sum":6,"median":2}', "duration_seconds": 0.01, "detail": ""}
                grade = validate_grade(module.grade(root, context))
            self.assertEqual(grade["score"], 15)
            self.assertGreater(len([t for t in grade["tests"] if t["status"] == "failed"]), 0)
            self.assertEqual(grade["skipped_tests"], 1)
