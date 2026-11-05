import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.cli import main
from codex_bench.runner import read_results, run_benchmark
from codex_bench.util import read_json, tree_hash, write_json
from tests.helpers import DIAGNOSTICS, fake_command, setup_experiment, test_directory


class RecoveryCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root)

    def tearDown(self):
        permitted = Path(tempfile.gettempdir()) / "codex-bench-test-workspaces"
        if self.cfg.scratch.is_dir() and self.cfg.scratch.resolve().is_relative_to(permitted.resolve()) and self.cfg.scratch.resolve() != permitted.resolve():
            shutil.rmtree(self.cfg.scratch)
        self.temp.cleanup()

    def initial_run(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_benchmark(self.cfg, DIAGNOSTICS, command_factory=fake_command), 1)
        return read_results(self.cfg)

    def test_regrade_uses_new_grader_without_loading_generation_configuration(self):
        original = self.initial_run()
        attempts = {row["artifact_path"]: tree_hash(self.cfg.output / row["artifact_path"]) for row in original}
        frozen = tree_hash(self.cfg.output / "private")
        (self.cfg.grader_path / "plugin.py").write_text(
            'from codex_bench.grading import TestResult, aggregate\n'
            'def grade(submission, context):\n'
            '    assert (submission / "solution.py").is_file()\n'
            '    points = context.options["points"]\n'
            '    return aggregate([TestResult("updated", "fixture", "passed", points, 10)])\n',
            encoding="utf-8")
        self.cfg.prompt_path.unlink()
        (self.cfg.starter / "seed.txt").unlink()
        self.cfg.starter.rmdir()
        raw = {"output_dir": "output", "grader": {"plugin_dir": "grader", "options": {"points": 7}}}
        write_json(self.cfg.path, raw)

        with patch("codex_bench.cli.load_config", side_effect=AssertionError("No generation config")), \
             patch("codex_bench.cli.diagnose", side_effect=AssertionError("No Codex diagnosis")), \
             patch("codex_bench.cli.run_benchmark", side_effect=AssertionError("No benchmark generation")), \
             patch("codex_bench.runner.build_command", side_effect=AssertionError("No model command")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.cfg.path), "--regrade"]), 0)
            first_history = read_json(self.cfg.output / "results.json")["regrade_history"]
            self.assertEqual(len(first_history), 2)
            first_id = first_history[0]["regrade_id"]
            first_snapshot = tree_hash(self.cfg.output / "regrades" / first_id)
            raw["grader"]["options"]["points"] = 9
            write_json(self.cfg.path, raw)
            self.assertEqual(main(["--config", str(self.cfg.path), "--regrade"]), 0)

        current = read_results(self.cfg)
        for old, new in zip(original, current):
            self.assertEqual(new["attempt_id"], old["attempt_id"])
            self.assertEqual(new["usage"], old["usage"])
            self.assertEqual(new["artifact_path"], old["artifact_path"])
            self.assertEqual(new["grade"]["score"], 9)
            self.assertEqual(tree_hash(self.cfg.output / old["artifact_path"]), attempts[old["artifact_path"]])
        self.assertEqual(current[1]["status"], "unsupported")
        self.assertEqual(tree_hash(self.cfg.output / "private"), frozen)
        self.assertEqual(tree_hash(self.cfg.output / "regrades" / first_id), first_snapshot)
        report = read_json(self.cfg.output / "results.json")
        self.assertEqual(len(report["attempt_history"]), 2)
        self.assertEqual(len(report["regrade_history"]), 4)
        self.assertEqual({row["grade"]["score"] for row in report["regrade_history"]}, {7, 9})
        self.assertTrue(all(row["real_model_calls"] == 0 for row in report["regrade_history"]))

    def test_retry_failed_cli_creates_only_a_failed_jobs_new_attempt(self):
        original = self.initial_run()
        snapshots = {row["artifact_path"]: tree_hash(self.cfg.output / row["artifact_path"]) for row in original}
        with patch("codex_bench.cli.diagnose", return_value=DIAGNOSTICS), \
             patch("codex_bench.runner.build_command", side_effect=fake_command) as command, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.cfg.path), "--retry-failed"]), 1)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[1].id, original[1]["id"])
        current = read_results(self.cfg)
        self.assertEqual(current[0], original[0])
        self.assertNotEqual(current[1]["attempt_id"], original[1]["attempt_id"])
        self.assertEqual(current[1]["retry_of_attempt_id"], original[1]["attempt_id"])
        for row in original:
            self.assertEqual(tree_hash(self.cfg.output / row["artifact_path"]), snapshots[row["artifact_path"]])
        self.assertEqual(len(read_json(self.cfg.output / "results.json")["attempt_history"]), 3)

    def test_retry_requires_an_existing_experiment_before_diagnosing(self):
        with patch("codex_bench.cli.diagnose", side_effect=AssertionError("No diagnosis")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--config", str(self.cfg.path), "--retry-failed"]), 2)
        self.assertFalse(self.cfg.output.exists())

    def test_retry_without_failures_needs_no_codex_diagnosis_or_writes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            def successful(cfg, job, workspace, diagnostics):
                command = fake_command(cfg, job, workspace, diagnostics)
                command[-1] = "low"
                return command
            self.assertEqual(run_benchmark(self.cfg, DIAGNOSTICS, command_factory=successful), 0)
        before = tree_hash(self.cfg.output)
        with patch("codex_bench.cli.diagnose", side_effect=AssertionError("No unnecessary diagnosis")), \
             patch("codex_bench.cli.run_benchmark", side_effect=AssertionError("No model generation")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.cfg.path), "--retry-failed"]), 0)
            self.assertEqual(tree_hash(self.cfg.output), before)
            raw = read_json(self.cfg.path)
            raw["run_timeout_seconds"] += 1
            write_json(self.cfg.path, raw)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(self.cfg.path), "--retry-failed"]), 2)
            self.assertEqual(tree_hash(self.cfg.output), before)

    def test_recovery_actions_are_mutually_exclusive(self):
        for flags in [("--regrade", "--retry-failed"), ("--regrade", "--dry-run"),
                      ("--regrade", "--report-only"), ("--retry-failed", "--gui")]:
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(list(flags))
            self.assertEqual(error.exception.code, 2)
