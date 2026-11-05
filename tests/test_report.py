import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_bench.pricing import API_COST_FIELD, API_COST_LABEL, API_PRICING, estimate_api_cost_usd
from codex_bench.report import SCRIPT, describe, flatten, summarize, write_reports
from codex_bench.util import read_json, write_json
from tests.helpers import test_directory


class ReportTests(unittest.TestCase):
    def row(self, score=None, status="failed"):
        return {"id": "test", "model": "<script>bad</script>", "effort": "low", "repeat": 1, "status": status,
                "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 50, "reasoning_output_tokens": 10, "total_tokens": 150},
                "generation_seconds": 2, "grade": {"score": score, "tests": []}}

    def test_statistics_include_missing_and_failures(self):
        stats = summarize([self.row(0), self.row(10, "succeeded"), self.row()])[0]
        self.assertEqual(stats["runs"], 3)
        self.assertEqual(stats["metrics"]["score"], {"n": 2, "missing": 1, "mean": 5, "min": 0, "max": 10, "stddev": 5.0})
        self.assertEqual(stats["statuses"]["failed"], 2)

    def test_efficiency_and_subtokens(self):
        row = flatten(self.row(30))
        self.assertEqual(row["score_per_1000_tokens"], 200)
        self.assertEqual(row["score_per_second"], 15)
        self.assertEqual(row["cache_ratio"], 0.2)
        self.assertEqual(row["reasoning_ratio"], 0.2)

    def test_published_api_rates_and_missing_usage(self):
        row = self.row()
        for model, cost in (("gpt-6.1-sol", 0.000662), ("gpt-6.1-terra", 0.000331), ("gpt-6-luna", 0.0000332)):
            with self.subTest(model=model):
                self.assertEqual(estimate_api_cost_usd({**row, "model": model}), cost)
        priced = {**row, "model": "gpt-6.1-sol"}
        self.assertIsNone(estimate_api_cost_usd(row))
        self.assertIsNone(estimate_api_cost_usd(priced, synthetic=True))
        for cached in (None, 101, False):
            with self.subTest(cached=cached):
                usage = {**row["usage"], "cached_input_tokens": cached}
                self.assertIsNone(estimate_api_cost_usd({**priced, "usage": usage}))

    def test_saved_result_cost_is_exported_without_modifying_the_original(self):
        row = {**self.row(), "model": "gpt-6-luna", "attempt_id": "a1"}
        with test_directory() as directory:
            root = Path(directory)
            attempt = root / "runs/test/attempts/a1/result.json"
            write_json(attempt, row)
            write_reports(root, [row], {})
            data = read_json(root / "results.json")
            self.assertEqual(data["results"][0][API_COST_FIELD], 0.0000332)
            self.assertEqual(data["attempt_history"][0][API_COST_FIELD], 0.0000332)
            self.assertEqual(data["api_pricing"], API_PRICING)
            self.assertEqual(read_json(attempt), row)
            with (root / "results.csv").open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(float(next(csv.DictReader(file))[API_COST_FIELD]), 0.0000332)
            html = (root / "report.html").read_text(encoding="utf-8")
            self.assertIn(API_COST_LABEL, html)
            self.assertIn(">0.0000332</td>", html)
            write_reports(root, [row], {"synthetic": True})
            synthetic = read_json(root / "results.json")
            self.assertIsNone(synthetic["results"][0][API_COST_FIELD])
            self.assertIsNone(synthetic["experiment_cost"]["total_estimated_api_cost_usd"])

    def test_experiment_cost_counts_each_attempt_once_and_averages_current_rows(self):
        original = {**self.row(status="interrupted"), "id": "first", "model": "gpt-6.1-sol", "attempt_id": "a1"}
        retry = {**original, "attempt_id": "a2", "status": "succeeded",
                 "usage": {key: value * 2 for key, value in original["usage"].items()}}
        legacy = {**self.row(status="succeeded"), "id": "legacy", "model": original["model"], "repeat": 2}
        missing = {**self.row(), "id": "missing", "model": original["model"], "repeat": 3, "usage": {}}
        pending = {**missing, "id": "pending", "repeat": 4, "status": "pending"}
        luna = {**self.row(status="succeeded"), "id": "luna", "model": "gpt-6-luna", "effort": "high",
                "artifact_path": "runs/luna/attempts/a3"}
        with test_directory() as directory:
            root = Path(directory)
            for attempt in (original, retry):
                write_json(root / "runs" / attempt["id"] / "attempts" / attempt["attempt_id"] / "result.json", attempt)
            write_json(root / "runs/luna/attempts/a3/result.json", {**luna, "usage": {}})
            write_json(root / "regrades/r1/runs/first/result.json", {**retry, "regrade_id": "r1"})
            write_reports(root, [retry, legacy, missing, pending, luna], {})
            data = read_json(root / "results.json")
            self.assertEqual(data["experiment_cost"], {
                "total_estimated_api_cost_usd": 0.0026812, "priced_attempts": 4, "unpriced_attempts": 1,
            })
            groups = {(group["model"], group["effort"]): group["metrics"][API_COST_FIELD] for group in data["summary"]}
            self.assertAlmostEqual(groups[("gpt-6.1-sol", "low")]["mean"], 0.000993)
            self.assertEqual(groups[("gpt-6.1-sol", "low")]["missing"], 2)
            self.assertEqual(groups[("gpt-6-luna", "high")]["mean"], 0.0000332)
            html = (root / "report.html").read_text(encoding="utf-8")
            self.assertIn("<strong>0.0026812</strong>", html)
            self.assertIn('id="api-costs"', html)
            self.assertIn(">0.000993</td>", html)
            self.assertIn("미산정 1회", html)

    def test_zero_denominator_remains_null(self):
        row = self.row(0)
        row["usage"]["total_tokens"] = 0
        row["generation_seconds"] = 0
        self.assertIsNone(flatten(row)["score_per_second"])
        self.assertIsNone(flatten(row)["score_per_1000_tokens"])

    def test_report_offline_escaped_and_complete(self):
        with test_directory() as directory:
            root = Path(directory)
            write_reports(root, [self.row(0), self.row()], {"experiment_id": "fixture"})
            html = (root / "report.html").read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;bad&lt;/script&gt;", html)
            self.assertNotIn("<script>bad</script>", html)
            self.assertNotIn("https://", html.replace(API_PRICING["source"], ""))
            self.assertNotIn('src="http', html)
            self.assertIn("addEventListener", html)
            data = json.loads((root / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["results"]), 2)
            with (root / "results.csv").open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(len(list(csv.DictReader(file))), 2)

    @unittest.skipUnless(shutil.which("node"), "Node is optional; only used to exercise report JavaScript")
    def test_sort_and_search_javascript_without_browser_or_network(self):
        harness = r'''
const assert = require('node:assert/strict');
let sortHandler, filterHandler;
const rows = ['20','3',''].map(value=>({cells:[{dataset:{value}}],textContent:value==='3'?'model-a':'model-b',hidden:false}));
const body = {rows,appendChild(row){this.rows=this.rows.filter(x=>x!==row);this.rows.push(row);}};
const th = {dataset:{},querySelector(){return {addEventListener(name,fn){sortHandler=fn}}}};
const table = {tBodies:[body],querySelectorAll(){return [th]}};
const document = {querySelectorAll(selector){return selector==='table.sortable'?[table]:body.rows},getElementById(){return {addEventListener(name,fn){filterHandler=fn}}}};
'''
        checks = r'''
sortHandler(); assert.deepEqual(body.rows.map(x=>x.cells[0].dataset.value),['3','20','']);
sortHandler(); assert.deepEqual(body.rows.map(x=>x.cells[0].dataset.value),['20','3','']);
filterHandler({target:{value:'MODEL-A'}}); assert.equal(body.rows.filter(x=>!x.hidden).length,1);
filterHandler({target:{value:''}}); assert.equal(body.rows.filter(x=>!x.hidden).length,3);
'''
        result = subprocess.run([shutil.which("node"), "-e", harness + SCRIPT + checks], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_csv_formula_text_is_not_executable(self):
        row = self.row()
        row["grading_error"] = '=HYPERLINK("https://example.invalid")'
        with test_directory() as directory:
            root = Path(directory)
            write_reports(root, [row], {})
            with (root / "results.csv").open(encoding="utf-8-sig", newline="") as file:
                self.assertTrue(next(csv.DictReader(file))["grading_error"].startswith("'="))
