import contextlib
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.cli import main
from codex_bench.runner import OutputLock, execute_job, prepare, read_results, run_benchmark
from codex_bench.util import BenchError, read_json, write_json
from tests.helpers import DIAGNOSTICS, fake_command, setup_experiment, test_directory


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root)

    def tearDown(self):
        permitted = Path(tempfile.gettempdir()) / "codex-bench-test-workspaces"
        if self.cfg.scratch.is_dir() and self.cfg.scratch.resolve().is_relative_to(permitted.resolve()) and self.cfg.scratch.resolve() != permitted.resolve():
            shutil.rmtree(self.cfg.scratch)
        self.temp.cleanup()

    def test_full_mock_pipeline_and_resume(self):
        with patch("codex_bench.runner.build_command", side_effect=fake_command) as command, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_benchmark(self.cfg, DIAGNOSTICS), 1)
            self.assertEqual(command.call_count, 2)
            rows = read_results(self.cfg)
            self.assertEqual([r["status"] for r in rows], ["succeeded", "unsupported"])
            self.assertEqual(rows[0]["usage"]["total_tokens"], 240)
            self.assertIsNone(rows[1]["usage"]["total_tokens"])
            self.assertEqual(run_benchmark(self.cfg, DIAGNOSTICS), 1)
            self.assertEqual(command.call_count, 2, "Completed jobs must not use more model tokens")
        for row in rows:
            attempt = self.cfg.output / row["artifact_path"]
            for name in ["stdout.log", "stderr.log", "events.jsonl", "final_message.md", "result.json", "submission/solution.py"]:
                self.assertTrue((attempt / name).exists(), name)
            self.assertEqual((attempt / "submission" / "received-prompt.txt").read_text(encoding="utf-8"), self.cfg.prompt)
            self.assertNotIn("plugin.py", [p.name for p in (attempt / "submission").rglob("*")])
        for name in ["report.html", "results.csv", "results.json", "diagnostics.json"]:
            self.assertTrue((self.cfg.output / name).exists())

    def test_changed_input_refuses_resume(self):
        self.cfg.output.mkdir()
        prepare(self.cfg)
        (self.root / "prompt.md").write_text("different", encoding="utf-8")
        with self.assertRaises(BenchError):
            prepare(self.cfg)

    def test_frozen_grader_tamper_detected(self):
        self.cfg.output.mkdir()
        prepare(self.cfg)
        (self.cfg.output / "private" / "grader" / "plugin.py").write_text("tampered")
        with self.assertRaises(BenchError):
            prepare(self.cfg)

    def test_parallel_mock_pipeline(self):
        self.cfg.workers = 2
        with patch("codex_bench.runner.build_command", side_effect=fake_command), contextlib.redirect_stdout(io.StringIO()):
            run_benchmark(self.cfg, DIAGNOSTICS)
        rows = read_results(self.cfg)
        self.assertTrue(all("workspace" in row for row in rows), rows)
        self.assertNotEqual(rows[0]["workspace"], rows[1]["workspace"])
        self.assertNotEqual(rows[0]["thread_id"], rows[1]["thread_id"])

    def test_generation_timeout_preserves_artifacts(self):
        self.cfg.jobs = [type(self.cfg.jobs[0])("slow-id", "slow", "low", 1)]
        self.cfg.run_timeout = 0.4
        with patch("codex_bench.runner.build_command", side_effect=fake_command), contextlib.redirect_stdout(io.StringIO()):
            run_benchmark(self.cfg, DIAGNOSTICS)
        row = read_results(self.cfg)[0]
        self.assertEqual(row["status"], "timed_out")
        self.assertTrue((self.cfg.output / row["artifact_path"] / "events.jsonl").exists())

    def test_resume_interrupted_grading_without_new_generation(self):
        with patch("codex_bench.runner.build_command", side_effect=fake_command), contextlib.redirect_stdout(io.StringIO()):
            run_benchmark(self.cfg, DIAGNOSTICS)
        result = read_results(self.cfg)[0]
        result["status"] = "interrupted"
        write_json(self.cfg.output / "runs" / result["id"] / "result.json", result)
        with patch("codex_bench.runner.build_command", side_effect=AssertionError("Generation must not rerun")), contextlib.redirect_stdout(io.StringIO()):
            run_benchmark(self.cfg, DIAGNOSTICS)
        self.assertEqual(read_results(self.cfg)[0]["status"], "succeeded")

    def test_dry_run_does_not_write_or_call_models(self):
        with patch("codex_bench.cli.diagnose", return_value=DIAGNOSTICS), patch("codex_bench.cli.run_benchmark") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.cfg.path), "--dry-run"]), 0)
        run.assert_not_called()
        self.assertFalse(self.cfg.output.exists())
        self.assertFalse(self.cfg.scratch.exists())

    def test_grader_failure_not_success(self):
        (self.cfg.grader_path / "plugin.py").write_text("def grade(*args): raise RuntimeError('fixture failure')")
        with patch("codex_bench.runner.build_command", side_effect=fake_command), contextlib.redirect_stdout(io.StringIO()):
            run_benchmark(self.cfg, DIAGNOSTICS)
        row = read_results(self.cfg)[0]
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["grade"])
        self.assertEqual(row["grading_error"], "grader_error")

    def test_overall_timeout_keeps_unstarted_work_and_resumable_state(self):
        first = self.cfg.jobs[0]
        self.cfg.jobs[0] = type(first)(first.id, "slow", first.effort, first.repeat)
        self.cfg.total_timeout = 0.35
        with patch("codex_bench.runner.build_command", side_effect=fake_command), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_benchmark(self.cfg, DIAGNOSTICS), 130)
        rows = read_results(self.cfg)
        self.assertEqual(rows[0]["status"], "interrupted")
        self.assertFalse(rows[0]["generation_finished"])
        self.assertEqual(rows[1]["status"], "pending")

    def test_tampered_submission_is_not_regraded(self):
        with patch("codex_bench.runner.build_command", side_effect=fake_command), contextlib.redirect_stdout(io.StringIO()):
            run_benchmark(self.cfg, DIAGNOSTICS)
        row = read_results(self.cfg)[0]
        row["status"] = "interrupted"
        write_json(self.cfg.output / "runs" / row["id"] / "result.json", row)
        (self.cfg.output / row["artifact_path"] / "submission" / "solution.py").write_text("changed")
        with patch("codex_bench.runner.build_command", side_effect=AssertionError("No new generation")), contextlib.redirect_stdout(io.StringIO()):
            run_benchmark(self.cfg, DIAGNOSTICS)
        result = read_results(self.cfg)[0]
        self.assertEqual(result["error_type"], "runner_error")
        self.assertIsNone(result["grade"])
        self.assertEqual(result["usage"]["total_tokens"], 240)

    def test_output_lock_prevents_concurrent_writers(self):
        with OutputLock(self.cfg.output):
            with self.assertRaises(BenchError):
                with OutputLock(self.cfg.output):
                    pass

    def test_doctor_json_survives_legacy_console(self):
        sink = io.BytesIO()
        stream = io.TextIOWrapper(sink, encoding="cp949", errors="strict")
        diagnostics = {**DIAGNOSTICS, "warnings": ["nonbreaking\u2011hyphen and emoji \U0001f680"]}
        with patch("codex_bench.cli.diagnose", return_value=diagnostics), contextlib.redirect_stdout(stream):
            self.assertEqual(main(["--config", str(self.cfg.path), "--doctor"]), 0)
        stream.flush()
        self.assertEqual(json.loads(sink.getvalue().decode("cp949"))["warnings"], diagnostics["warnings"])
        stream.detach()
