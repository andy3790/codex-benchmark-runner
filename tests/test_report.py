import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_bench.report import SCRIPT, describe, flatten, summarize, write_reports
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
            self.assertNotIn("https://", html)
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
