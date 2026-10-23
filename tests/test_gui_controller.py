import contextlib
import io
import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.gui_controller import BenchmarkController
from codex_bench.runner import read_results
from codex_bench.util import BenchError, read_json
from tests.helpers import DIAGNOSTICS, setup_experiment, test_directory


class GuiControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root)
        self.controller = BenchmarkController()

    def tearDown(self):
        if self.controller.busy:
            self.controller.stop()
            self.controller.thread.join(10)
        permitted = Path(tempfile.gettempdir()) / "codex-bench-test-workspaces"
        if self.cfg.scratch.is_dir() and self.cfg.scratch.resolve().is_relative_to(permitted.resolve()) and self.cfg.scratch != permitted:
            shutil.rmtree(self.cfg.scratch)
        self.temp.cleanup()

    def events(self):
        output = []
        while True:
            try:
                output.append(self.controller.events.get_nowait())
            except queue.Empty:
                return output

    def test_mock_runs_full_pipeline_without_codex_diagnosis(self):
        with patch("codex_bench.gui_controller.diagnose", side_effect=AssertionError("No real CLI in mock mode")), contextlib.redirect_stdout(io.StringIO()):
            self.controller.start("mock", self.cfg)
            self.controller.thread.join(15)
        self.assertFalse(self.controller.busy)
        self.assertTrue(self.controller.last_report.is_file())
        self.assertFalse(self.cfg.output.exists(), "Mock must not contaminate real output")
        data = read_json(self.controller.last_report.with_name("results.json"))
        self.assertTrue(data["metadata"]["synthetic"])
        self.assertEqual(data["metadata"]["real_model_calls"], 0)
        self.assertEqual([r["status"] for r in data["results"]], ["succeeded", "unsupported"])
        self.assertEqual(data["results"][0]["grade"]["planned_max_score"], 100)
        events = self.events()
        self.assertIn("progress", [kind for kind, _ in events])
        self.assertEqual(events[-1][0], "finished")

    def test_dry_run_never_runs_benchmark_or_writes_output(self):
        with patch("codex_bench.gui_controller.diagnose", return_value=DIAGNOSTICS), patch("codex_bench.gui_controller.run_benchmark") as run:
            self.controller.start("dry-run", self.cfg)
            self.controller.thread.join(5)
        run.assert_not_called()
        self.assertFalse(self.cfg.output.exists())
        self.assertIn("argv=", "\n".join(value for kind, value in self.events() if kind == "log"))

    def test_run_passes_shared_cancel_and_callbacks_to_runner(self):
        with patch("codex_bench.gui_controller.diagnose", return_value=DIAGNOSTICS), patch("codex_bench.gui_controller.run_benchmark", return_value=0) as run:
            self.controller.start("run", self.cfg)
            self.controller.thread.join(5)
        self.assertIs(run.call_args.kwargs["cancel_event"], self.controller.cancel_event)
        self.assertTrue(callable(run.call_args.kwargs["on_progress"]))

    def test_background_failures_delivered_to_main_thread(self):
        with patch("codex_bench.gui_controller.diagnose", side_effect=RuntimeError("fixture")):
            self.controller.start("doctor", self.cfg)
            self.controller.thread.join(5)
        events = self.events()
        self.assertIn(("error", "fixture"), events)
        self.assertEqual(events[-1][1]["exit_code"], 2)

    def test_stop_preserves_partial_report(self):
        self.cfg.raw["repetitions"] = 3
        # Build the matrix consistently with its raw source for the simulation.
        from codex_bench.config import load_config
        cfg = load_config(self.cfg.path, raw_override=self.cfg.raw)
        with contextlib.redirect_stdout(io.StringIO()):
            self.controller.start("mock", cfg)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                report = next(self.root.glob("output-mock/*/output/manifest.json"), None)
                if report:
                    break
                time.sleep(0.02)
            self.controller.stop()
            self.controller.thread.join(10)
        self.assertFalse(self.controller.busy)
        events = self.events()
        self.assertEqual(events[-1][1]["exit_code"], 130)
        self.assertTrue(self.controller.last_report.exists())
        data = read_json(self.controller.last_report.with_name("results.json"))
        self.assertTrue(any(r["status"] in {"pending", "interrupted"} for r in data["results"]))

    def test_cannot_start_second_operation_while_busy(self):
        release = threading.Event()
        def hold(_):
            release.wait(3)
            return DIAGNOSTICS
        try:
            with patch("codex_bench.gui_controller.diagnose", side_effect=hold):
                self.controller.start("doctor", self.cfg)
                with self.assertRaises(BenchError):
                    self.controller.start("mock", self.cfg)
                release.set()
                self.controller.thread.join(5)
        finally:
            release.set()
