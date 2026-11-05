import contextlib
import io
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codex_bench.process import ProcessResult
from codex_bench.runner import OutputLock, prepare, read_results, run_benchmark
from codex_bench.util import BenchError, read_json, tree_hash, write_json
from tests.helpers import DIAGNOSTICS, fake_command, setup_experiment, test_directory


class RetryTests(unittest.TestCase):
    STATUSES = ["failed", "unsupported", "timed_out", "succeeded", "pending", "interrupted", "running", "grading"]

    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root, models=[
            {"name": f"fake-{index}", "reasoning_efforts": ["low"]} for index in range(len(self.STATUSES))])

    def tearDown(self):
        permitted = Path(tempfile.gettempdir()) / "codex-bench-test-workspaces"
        if (self.cfg.scratch.is_dir() and self.cfg.scratch.resolve().is_relative_to(permitted.resolve())
                and self.cfg.scratch.resolve() != permitted.resolve()):
            shutil.rmtree(self.cfg.scratch)
        self.temp.cleanup()

    def seed_results(self, statuses=None):
        with OutputLock(self.cfg.output):
            prepare(self.cfg)
        for index, (job, status) in enumerate(zip(self.cfg.jobs, statuses or self.STATUSES)):
            if status == "pending":
                continue
            attempt = self.cfg.output / "runs" / job.id / "attempts" / f"original-{index}"
            submission = attempt / "submission"
            submission.mkdir(parents=True)
            (submission / "seed.txt").write_text("same seed", encoding="utf-8")
            (submission / "previous-only.txt").write_bytes(b"Preserve the original submission.\r\n")
            for name in ("stdout.log", "stderr.log", "events.jsonl", "final_message.md"):
                (attempt / name).write_bytes((name + " original evidence\r\n").encode())
            row = {**job.as_dict(), "status": status, "attempt_id": attempt.name,
                   "success": status == "succeeded", "generation_finished": True,
                   "usage": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24},
                   "grade": None, "submission_hashes": tree_hash(submission),
                   "artifact_path": attempt.relative_to(self.cfg.output).as_posix()}
            write_json(attempt / "result.json", row)
            write_json(attempt.parent.parent / "result.json", row)

    def original_attempts(self):
        return {path: tree_hash(path) for path in self.cfg.output.glob("runs/*/attempts/*")}

    def assert_attempts_preserved(self, attempts):
        for path, hashes in attempts.items():
            self.assertEqual(tree_hash(path), hashes, str(path))

    def retry(self, **kwargs):
        kwargs.setdefault("command_factory", fake_command)
        with contextlib.redirect_stdout(io.StringIO()):
            return run_benchmark(self.cfg, DIAGNOSTICS, retry_failed=True, **kwargs)

    def test_selects_only_terminal_failures_and_preserves_prior_attempts(self):
        self.seed_results()
        originals = self.original_attempts()
        old_rows = read_results(self.cfg)
        current_paths = [self.cfg.output / "runs" / job.id / "result.json" for job in self.cfg.jobs]
        current_bytes = [path.read_bytes() if path.exists() else None for path in current_paths]
        command = Mock(side_effect=fake_command)
        self.assertEqual(self.retry(command_factory=command), 0, "Unselected pending jobs do not fail a retry command")
        self.assertEqual([call.args[1].id for call in command.call_args_list], [job.id for job in self.cfg.jobs[:3]])
        rows = read_results(self.cfg)
        for index, (old, row) in enumerate(zip(old_rows, rows)):
            if index < 3:
                self.assertEqual(row["status"], "succeeded")
                self.assertNotEqual(row["attempt_id"], old["attempt_id"])
                self.assertEqual(row["attempt_kind"], "retry")
                self.assertEqual(row["retry_of_attempt_id"], old["attempt_id"])
                submission = self.cfg.output / row["artifact_path"] / "submission"
                self.assertFalse((submission / "previous-only.txt").exists())
                self.assertTrue((submission / "solution.py").is_file(), "Generation must rerun even after a saved generation")
                self.assertEqual(tree_hash(submission), row["submission_hashes"])
            else:
                self.assertEqual(row, old)
                path = current_paths[index]
                self.assertEqual(path.read_bytes() if path.exists() else None, current_bytes[index])
        self.assert_attempts_preserved(originals)
        history = read_json(self.cfg.output / "results.json")["attempt_history"]
        self.assertEqual(len(history), len(originals) + 3)

    def test_repeated_retry_uses_latest_failed_attempt_and_then_becomes_noop(self):
        self.seed_results(["failed"])
        originals = self.original_attempts()

        def unsupported(cfg, job, workspace, diagnostics):
            command = fake_command(cfg, job, workspace, diagnostics)
            command[-1] = "unsupported"
            return command

        self.assertEqual(self.retry(command_factory=unsupported), 1)
        failed_retry = read_results(self.cfg)[0]
        self.assertEqual(failed_retry["status"], "unsupported")
        self.assert_attempts_preserved(originals)
        originals.update(self.original_attempts())
        self.assertEqual(self.retry(), 0)
        successful_retry = read_results(self.cfg)[0]
        self.assertNotEqual(successful_retry["attempt_id"], failed_retry["attempt_id"])
        self.assertEqual(successful_retry["retry_of_attempt_id"], failed_retry["attempt_id"])
        self.assert_attempts_preserved(originals)
        before = tree_hash(self.cfg.output)
        command = Mock(side_effect=AssertionError("No failed jobs remain"))
        self.assertEqual(self.retry(command_factory=command), 0)
        command.assert_not_called()
        self.assertEqual(tree_hash(self.cfg.output), before)

    def test_retry_requires_existing_manifest_without_creating_an_experiment(self):
        command = Mock(side_effect=AssertionError("No new experiment"))
        with self.assertRaisesRegex(BenchError, "existing experiment manifest"):
            self.retry(command_factory=command)
        command.assert_not_called()
        self.assertFalse(self.cfg.output.exists())
        self.assertFalse(self.cfg.scratch.exists())

    def test_changed_configuration_refuses_retry_before_writing(self):
        self.seed_results(["failed"])
        before = tree_hash(self.cfg.output)
        self.cfg.raw["run_timeout_seconds"] += 1
        command = Mock(side_effect=AssertionError("Changed configuration"))
        with self.assertRaisesRegex(BenchError, "changed"):
            self.retry(command_factory=command)
        command.assert_not_called()
        self.assertEqual(tree_hash(self.cfg.output), before)

    def test_modified_frozen_grader_refuses_retry_before_writing(self):
        self.seed_results(["failed"])
        (self.cfg.output / "private" / "grader" / "plugin.py").write_text("changed", encoding="utf-8")
        before = tree_hash(self.cfg.output)
        command = Mock(side_effect=AssertionError("Modified frozen grader"))
        with self.assertRaisesRegex(BenchError, "Frozen inputs"):
            self.retry(command_factory=command)
        command.assert_not_called()
        self.assertEqual(tree_hash(self.cfg.output), before)

    def test_cancellation_during_retry_preserves_previous_and_unstarted_jobs(self):
        self.seed_results(["failed", "timed_out"])
        originals = self.original_attempts()
        before = read_results(self.cfg)
        cancel = threading.Event()

        def stop_generation(command, **kwargs):
            cancel.set()
            kwargs["stdout"].write_text("", encoding="utf-8")
            kwargs["stderr"].write_text("", encoding="utf-8")
            return ProcessResult(-1, 0.01, "interrupted")

        command = Mock(side_effect=fake_command)
        with patch("codex_bench.runner.run_process", side_effect=stop_generation):
            self.assertEqual(self.retry(command_factory=command, cancel_event=cancel), 130)
        self.assertEqual(command.call_count, 1)
        rows = read_results(self.cfg)
        self.assertEqual(rows[0]["status"], "interrupted")
        self.assertFalse(rows[0]["generation_finished"])
        self.assertEqual(rows[0]["attempt_kind"], "retry")
        self.assertEqual(rows[0]["retry_of_attempt_id"], before[0]["attempt_id"])
        self.assertNotEqual(rows[0]["attempt_id"], before[0]["attempt_id"])
        self.assertEqual(rows[1:], before[1:])
        self.assert_attempts_preserved(originals)

    def test_cancelled_retry_does_not_start_or_replace_old_attempts(self):
        self.seed_results(["failed"])
        originals = self.original_attempts()
        before = read_results(self.cfg)
        cancel = threading.Event()
        cancel.set()
        command = Mock(side_effect=AssertionError("Already cancelled"))
        self.assertEqual(self.retry(command_factory=command, cancel_event=cancel), 130)
        command.assert_not_called()
        self.assertEqual(read_results(self.cfg), before)
        self.assertEqual(self.original_attempts(), originals)

    def test_worker_exception_before_checkpoint_creates_separate_failure_attempt(self):
        self.seed_results(["failed"])
        originals = self.original_attempts()
        old = read_results(self.cfg)[0]
        with patch("codex_bench.runner.execute_job", side_effect=RuntimeError("before checkpoint")):
            self.assertEqual(self.retry(), 1)
        row = read_results(self.cfg)[0]
        self.assertEqual(row["error_type"], "runner_error")
        self.assertEqual(row["attempt_kind"], "retry")
        self.assertEqual(row["retry_of_attempt_id"], old["attempt_id"])
        self.assertNotEqual(row["attempt_id"], old["attempt_id"])
        self.assertEqual(read_json(self.cfg.output / row["artifact_path"] / "result.json"), row)
        self.assert_attempts_preserved(originals)

    def test_worker_exception_after_checkpoint_preserves_original_attempt(self):
        self.seed_results(["failed"])
        originals = self.original_attempts()
        old = read_results(self.cfg)[0]
        command = Mock(side_effect=RuntimeError("command setup failed"))
        self.assertEqual(self.retry(command_factory=command), 1)
        row = read_results(self.cfg)[0]
        self.assertEqual(row["error_type"], "runner_error")
        self.assertEqual(row["attempt_kind"], "retry")
        self.assertEqual(row["retry_of_attempt_id"], old["attempt_id"])
        self.assertNotEqual(row["attempt_id"], old["attempt_id"])
        self.assertEqual(len(self.original_attempts()), len(originals) + 1)
        self.assert_attempts_preserved(originals)
