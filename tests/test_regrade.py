import contextlib
import copy
import io
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.grading import TestResult, aggregate
from codex_bench.regrade import regrade_benchmark
from codex_bench.runner import OutputLock, grade_submission
from codex_bench.util import BenchError, copy_tree, read_json, tree_hash, write_json
from tests.helpers import setup_experiment, test_directory


GRADE = aggregate([TestResult("new_check", "functionality", "passed", 5, 5)])


class RegradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root)
        self.manifest = {"schema_version": 1, "experiment_id": "original-experiment",
                         "jobs": [job.as_dict() for job in self.cfg.jobs], "active_seconds": 12}
        write_json(self.cfg.output / "manifest.json", self.manifest)
        self.first = self.add_submission(0)

    def tearDown(self):
        self.temp.cleanup()

    def add_submission(self, index, **changes):
        job = self.cfg.jobs[index].as_dict()
        attempt_id = "original-" + str(index)
        attempt = self.cfg.output / "runs" / job["id"] / "attempts" / attempt_id
        submission = attempt / "submission"
        submission.mkdir(parents=True)
        (submission / "seed.txt").write_text("same seed", encoding="utf-8")
        (submission / "solution.py").write_text("# This fixture is never executed", encoding="utf-8")
        row = {**job, "attempt_id": attempt_id, "generation_finished": True,
               "status": "failed", "success": False, "exit_code": 0,
               "error_type": None, "grading_error": "grader_error", "grade": None,
               "generation_seconds": 2.0, "grading_seconds": 0.3, "duration_seconds": 2.3,
               "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:03+00:00",
               "usage": {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
               "artifact_path": attempt.relative_to(self.cfg.output).as_posix(),
               "submission_hashes": tree_hash(submission), **changes}
        write_json(attempt / "result.json", row)
        (attempt / "stdout.log").write_text("original generation trace", encoding="utf-8")
        (attempt / "events.jsonl").write_text("original JSONL trace", encoding="utf-8")
        write_json(self.result_path(row), row)
        return row

    def result_path(self, row):
        return self.cfg.output / "runs" / row["id"] / "result.json"

    def histories(self):
        return sorted((self.cfg.output / "regrades").glob("*/runs/*/result.json"))

    def invoke(self, *, grade=GRADE, error=None, side_effect=None, **kwargs):
        with patch("codex_bench.regrade.grade_submission", side_effect=side_effect,
                   return_value=(copy.deepcopy(grade), error, 0.25)) as worker, \
             patch("codex_bench.regrade.clean_containers") as cleanup, \
             patch("codex_bench.runner.build_command", side_effect=AssertionError("No model command")), \
             patch("codex_bench.config.load_config", side_effect=AssertionError("No generation config")), \
             contextlib.redirect_stdout(io.StringIO()):
            code = regrade_benchmark(self.cfg.path, **kwargs)
        return code, worker, cleanup

    def test_regrade_preserves_generation_and_records_independent_history(self):
        original = tree_hash(self.cfg.output / "runs" / self.first["id"] / "attempts")
        code, worker, _ = self.invoke()
        self.assertEqual(code, 0)
        worker.assert_called_once()
        current = read_json(self.result_path(self.first))
        for key in ("usage", "attempt_id", "artifact_path", "generation_seconds", "started_at", "ended_at"):
            self.assertEqual(current[key], self.first[key])
        self.assertEqual(current["grade"], GRADE)
        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["duration_seconds"], 2.25)
        history_path = self.histories()[0]
        history = read_json(history_path)
        self.assertEqual(history["usage"], {})
        self.assertEqual(history["real_model_calls"], 0)
        self.assertEqual(history["source_attempt_id"], self.first["attempt_id"])
        self.assertEqual(history["source_artifact_path"], self.first["artifact_path"])
        self.assertEqual(current["regrade_artifact_path"], history["artifact_path"])
        self.assertEqual(read_json(history_path.parent / "source_result.json"), self.first)
        self.assertEqual(tree_hash(history_path.parent / "submission"), self.first["submission_hashes"])
        self.assertEqual(tree_hash(self.cfg.output / "runs" / self.first["id"] / "attempts"), original)
        self.assertEqual(read_json(self.cfg.output / "manifest.json"), self.manifest)

    def test_changed_model_settings_and_missing_generation_inputs_are_ignored(self):
        self.cfg.prompt_path.unlink()
        (self.cfg.starter / "seed.txt").unlink()
        self.cfg.starter.rmdir()
        raw = read_json(self.cfg.path)
        raw.update({"models": "unrelated future model matrix", "codex_executable": None,
                    "mode": "future mode", "run_timeout_seconds": -1})
        write_json(self.cfg.path, raw)
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(read_json(self.result_path(self.first))["model"], self.first["model"])

    def test_repeated_regrade_preserves_prior_snapshot_and_current_lineage(self):
        self.assertEqual(self.invoke()[0], 0)
        first_history = self.histories()[0]
        first_root = first_history.parents[2]
        first_snapshot = tree_hash(first_root)
        first_current = read_json(self.result_path(self.first))
        with (self.cfg.grader_path / "plugin.py").open("a", encoding="utf-8") as file:
            file.write("\n# Updated grader version\n")
        self.assertEqual(self.invoke()[0], 0)
        second = next(path for path in self.histories() if path != first_history)
        self.assertEqual(read_json(second.parent / "source_result.json"), first_current)
        self.assertEqual(tree_hash(first_root), first_snapshot)
        self.assertNotEqual(read_json(second)["regrade_id"], read_json(first_history)["regrade_id"])

    def test_changed_or_missing_submission_rejects_all_before_grading(self):
        second = self.add_submission(1)
        original = {row["id"]: self.result_path(row).read_bytes() for row in (self.first, second)}
        submission = self.cfg.output / second["artifact_path"] / "submission"
        (submission / "solution.py").write_text("changed", encoding="utf-8")
        with patch("codex_bench.regrade.grade_submission") as worker:
            with self.assertRaisesRegex(BenchError, "changed"):
                regrade_benchmark(self.cfg.path)
            worker.assert_not_called()
        for row in (self.first, second):
            self.assertEqual(self.result_path(row).read_bytes(), original[row["id"]])
        for path in submission.iterdir():
            path.unlink()
        submission.rmdir()
        with self.assertRaisesRegex(BenchError, "missing"):
            regrade_benchmark(self.cfg.path)
        self.assertFalse((self.cfg.output / "regrades").exists())

    def test_copy_race_rejects_all_before_current_results_change(self):
        second = self.add_submission(1)
        before = {row["id"]: self.result_path(row).read_bytes() for row in (self.first, second)}
        second_submission = self.cfg.output / second["artifact_path"] / "submission"
        def copy_then_change(source, target, max_bytes):
            copy_tree(source, target, max_bytes)
            if source == second_submission:
                (target / "solution.py").write_text("changed during copy", encoding="utf-8")
        with patch("codex_bench.regrade.copy_tree", side_effect=copy_then_change), \
             patch("codex_bench.regrade.grade_submission") as worker:
            with self.assertRaisesRegex(BenchError, "while copying"):
                regrade_benchmark(self.cfg.path)
            worker.assert_not_called()
        for row in (self.first, second):
            self.assertEqual(self.result_path(row).read_bytes(), before[row["id"]])

    def test_unverifiable_hashes_and_mismatched_paths_are_rejected(self):
        for changes in ({"submission_hashes": None}, {"attempt_id": "../../outside"},
                        {"artifact_path": "runs/another/attempts/other"}):
            with self.subTest(changes=changes):
                row = {**self.first, **changes}
                write_json(self.result_path(row), row)
                with self.assertRaises(BenchError):
                    regrade_benchmark(self.cfg.path)
                self.assertEqual(read_json(self.result_path(row)), row)

    def test_pending_and_early_generation_failures_are_noop(self):
        for row in ({**self.cfg.jobs[0].as_dict(), "status": "pending", "usage": {}},
                    {**self.cfg.jobs[0].as_dict(), "status": "failed", "usage": {}, "attempt_id": "early-failure"}):
            with self.subTest(status=row["status"]):
                write_json(self.result_path(row), row)
                logs = []
                code, worker, _ = self.invoke(on_log=logs.append)
                self.assertEqual(code, 0)
                worker.assert_not_called()
                self.assertTrue(any(message.startswith("SKIP") for message in logs))
                self.assertEqual(read_json(self.result_path(row)), row)
        self.assertFalse((self.cfg.output / "regrades").exists())

    def test_output_lock_prevents_overlapping_runs(self):
        with OutputLock(self.cfg.output), self.assertRaisesRegex(BenchError, "Another runner"):
            regrade_benchmark(self.cfg.path)
        self.assertFalse((self.cfg.output / "regrades").exists())

    def test_cancelled_regrade_keeps_active_and_unstarted_current_results(self):
        second = self.add_submission(1)
        before = {row["id"]: self.result_path(row).read_bytes() for row in (self.first, second)}
        cancel = threading.Event()
        def interrupted(cfg, target, event, deadline):
            event.set()
            return None, "interrupted", 0.1
        code, worker, cleanup = self.invoke(side_effect=interrupted, cancel_event=cancel)
        self.assertEqual(code, 130)
        worker.assert_called_once()
        cleanup.assert_called_once()
        self.assertEqual(read_json(self.histories()[0])["status"], "interrupted")
        for row in (self.first, second):
            self.assertEqual(self.result_path(row).read_bytes(), before[row["id"]])
        with OutputLock(self.cfg.output):
            pass

    def test_overall_timeout_keeps_current_result_and_records_interruption(self):
        before = self.result_path(self.first).read_bytes()
        clock = [100.0]
        raw = read_json(self.cfg.path)
        raw["total_timeout_seconds"] = 1
        write_json(self.cfg.path, raw)
        def timed_out(cfg, target, event, deadline):
            clock[0] = deadline + 0.1
            return None, "overall_timeout", 1.0
        with patch("codex_bench.regrade.time.monotonic", side_effect=lambda: clock[0]):
            code, _, _ = self.invoke(side_effect=timed_out)
        self.assertEqual(code, 130)
        self.assertEqual(self.result_path(self.first).read_bytes(), before)
        self.assertEqual(read_json(self.histories()[0])["grading_error"], "overall_timeout")

    def test_precancelled_regrade_creates_no_invocation(self):
        cancel = threading.Event()
        cancel.set()
        code, worker, _ = self.invoke(cancel_event=cancel)
        self.assertEqual(code, 130)
        worker.assert_not_called()
        self.assertFalse((self.cfg.output / "regrades").exists())

    def test_protocol_failure_is_recorded_and_returns_one(self):
        code, _, cleanup = self.invoke(side_effect=RuntimeError("worker broke"))
        self.assertEqual(code, 1)
        cleanup.assert_called_once()
        current = read_json(self.result_path(self.first))
        self.assertEqual(current["status"], "failed")
        self.assertIn("worker broke", current["grading_error"])
        self.assertEqual(read_json(self.histories()[0])["status"], "failed")

    def test_failed_functional_checks_are_completed_grading(self):
        grade = aggregate([TestResult("wrong", "functionality", "failed", 0, 5)])
        self.assertEqual(self.invoke(grade=grade)[0], 0)
        self.assertEqual(read_json(self.result_path(self.first))["grade"]["score"], 0)

    def test_regrading_never_turns_generation_failures_into_success(self):
        for status, error in (("failed", "model_error"), ("timed_out", "timeout"),
                              ("unsupported", "unsupported_combination"), ("interrupted", "interrupted")):
            with self.subTest(status=status):
                source = {**self.first, "status": status, "error_type": error, "exit_code": 1}
                write_json(self.result_path(source), source)
                self.assertEqual(self.invoke()[0], 0)
                current = read_json(self.result_path(source))
                self.assertEqual(current["status"], status)
                self.assertFalse(current["success"])
                self.assertEqual(current["grade"], GRADE)
                self.assertEqual(current["error_type"], error)

    def test_grader_cannot_contain_the_new_snapshot(self):
        grader = self.cfg.output / "regrades"
        grader.mkdir()
        (grader / "plugin.py").write_text("# trusted fixture", encoding="utf-8")
        raw = read_json(self.cfg.path)
        raw["grader"]["plugin_dir"] = str(grader)
        write_json(self.cfg.path, raw)
        with self.assertRaisesRegex(BenchError, "contain the new regrade"):
            regrade_benchmark(self.cfg.path)
        self.assertEqual([path.name for path in grader.iterdir()], ["plugin.py"])

    def test_regrade_settings_validate_only_relevant_limits_and_options(self):
        original = read_json(self.cfg.path)
        for changes in ({"total_timeout_seconds": 0}, {"max_output_bytes": True},
                        {"max_project_bytes": 0.5}, {"grader": {"options": []}}):
            with self.subTest(changes=changes):
                write_json(self.cfg.path, {**original, **changes})
                with self.assertRaises(BenchError):
                    regrade_benchmark(self.cfg.path)
        self.assertFalse((self.cfg.output / "regrades").exists())

    def test_real_trusted_worker_uses_frozen_grader_and_current_options(self):
        plugin = self.cfg.grader_path / "plugin.py"
        plugin.write_text('''from codex_bench.grading import TestResult, aggregate
def grade(submission, context):
    assert (submission / "seed.txt").read_text() == "same seed"
    score = context.options["score"]
    return aggregate([TestResult("trusted_fixture", "functionality", "passed", score, score)])
''', encoding="utf-8")
        raw = read_json(self.cfg.path)
        raw["grader"]["options"] = {"score": 9}
        write_json(self.cfg.path, raw)
        def use_frozen_grader(cfg, target, cancel, deadline):
            plugin.write_text("raise RuntimeError('live source must not be imported')", encoding="utf-8")
            return grade_submission(cfg, target, cancel, deadline)
        with patch("codex_bench.runner.clean_containers"), \
             patch("codex_bench.regrade.grade_submission", side_effect=use_frozen_grader), \
             patch("codex_bench.regrade.clean_containers"), contextlib.redirect_stdout(io.StringIO()):
            code = regrade_benchmark(self.cfg.path)
        self.assertEqual(code, 0)
        self.assertEqual(read_json(self.result_path(self.first))["grade"]["score"], 9)
        history = self.histories()[0]
        self.assertTrue((history.parent / "grading" / "grade.json").is_file())
        snapshot = read_json(history.parents[2] / "manifest.json")
        self.assertEqual(snapshot["settings"]["grader"]["options"], {"score": 9})
        self.assertEqual(snapshot["real_model_calls"], 0)
