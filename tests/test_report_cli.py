import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.cli import main
from codex_bench.pricing import API_COST_FIELD
from codex_bench.report import write_reports
from codex_bench.util import read_json, write_json
from tests.helpers import setup_experiment, test_directory


class ReportOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root)
        self.manifest = {"schema_version": 1, "experiment_id": "saved-experiment",
                         "jobs": [job.as_dict() for job in self.cfg.jobs]}
        self.completed = {**self.manifest["jobs"][0], "status": "succeeded",
                          "usage": {"total_tokens": 123}, "grade": {"score": 5, "tests": []}}
        self.pending = {**self.manifest["jobs"][1], "status": "pending", "usage": {}}
        self.result_path = self.cfg.output / "runs" / self.completed["id"] / "result.json"
        write_json(self.cfg.output / "manifest.json", self.manifest)
        write_json(self.result_path, self.completed)
        write_reports(self.cfg.output, [self.completed, self.pending], self.manifest)

    def tearDown(self):
        self.temp.cleanup()

    def report_only(self):
        stderr = io.StringIO()
        with patch("codex_bench.cli.diagnose", side_effect=AssertionError("No CLI diagnosis")), \
             patch("codex_bench.cli.run_benchmark", side_effect=AssertionError("No benchmark run")), \
             patch("codex_bench.cli.load_config", side_effect=AssertionError("No execution config validation")), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = main(["--config", str(self.cfg.path), "--report-only"])
        return code, stderr.getvalue()

    def reports(self):
        return {name: (self.cfg.output / name).read_bytes()
                for name in ("results.json", "results.csv", "report.html")}

    def test_changed_model_matrix_preserves_saved_completed_and_pending_jobs(self):
        raw = read_json(self.cfg.path)
        raw.update({"models": [{"name": "future-model", "reasoning_efforts": ["high"]}], "repetitions": 3})
        write_json(self.cfg.path, raw)
        self.assertEqual(self.report_only(), (0, ""))
        data = read_json(self.cfg.output / "results.json")
        self.assertEqual(data["metadata"], self.manifest)
        self.assertEqual(data["results"], [{**row, API_COST_FIELD: None} for row in (self.completed, self.pending)])
        self.assertNotIn("future-model", (self.cfg.output / "report.html").read_text(encoding="utf-8"))

    def test_original_prompt_starter_and_grader_are_not_required(self):
        self.cfg.prompt_path.unlink()
        (self.cfg.starter / "seed.txt").unlink()
        self.cfg.starter.rmdir()
        (self.cfg.grader_path / "plugin.py").unlink()
        self.cfg.grader_path.rmdir()
        self.assertEqual(self.report_only(), (0, ""))
        self.assertEqual(read_json(self.cfg.output / "results.json")["results"],
                         [{**row, API_COST_FIELD: None} for row in (self.completed, self.pending)])

    def test_config_only_needs_output_dir_resolved_relative_to_config(self):
        write_json(self.cfg.path, {"output_dir": "output", "models": "unused future configuration"})
        self.assertEqual(self.report_only(), (0, ""))
        self.assertEqual(read_json(self.cfg.output / "results.json")["metadata"]["experiment_id"], "saved-experiment")

    def test_grading_only_result_labels_may_differ_from_manifest(self):
        completed = {**self.completed, "model": "saved-submission (grading only)", "effort": "not applicable"}
        write_json(self.result_path, completed)
        self.assertEqual(self.report_only(), (0, ""))
        self.assertEqual(read_json(self.cfg.output / "results.json")["results"][0], {**completed, API_COST_FIELD: None})

    def test_invalid_manifest_preserves_existing_reports(self):
        before = self.reports()
        invalid = [None, {**self.manifest, "schema_version": 2}, {"schema_version": 1},
                   {**self.manifest, "jobs": []},
                   {**self.manifest, "jobs": [self.manifest["jobs"][0]] * 2},
                   {**self.manifest, "jobs": [{**self.manifest["jobs"][0], "id": "../outside"}]}]
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                write_json(self.cfg.output / "manifest.json", manifest)
                code, error = self.report_only()
                self.assertEqual(code, 2)
                self.assertIn("ERROR:", error)
                self.assertEqual(self.reports(), before)

    def test_invalid_saved_result_preserves_all_report_formats(self):
        before = self.reports()
        invalid = [None, {**self.completed, "id": "different-job"},
                   {**self.completed, "usage": []},
                   {**self.completed, "artifact_path": {"invalid": "path"}}]
        for result in invalid:
            with self.subTest(result=result):
                write_json(self.result_path, result)
                code, error = self.report_only()
                self.assertEqual(code, 2)
                self.assertIn("ERROR:", error)
                self.assertEqual(self.reports(), before)

    def test_missing_manifest_does_not_create_output_directory(self):
        write_json(self.cfg.path, {"output_dir": "missing-output"})
        code, error = self.report_only()
        self.assertEqual(code, 2)
        self.assertIn("manifest is missing", error)
        self.assertFalse((self.root / "missing-output").exists())
