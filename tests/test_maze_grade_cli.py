import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.grading import TestResult, aggregate
from codex_bench.util import read_json
from tests.helpers import test_directory

spec = importlib.util.spec_from_file_location('maze_standalone_cli', Path(__file__).resolve().parent.parent / 'tools' / 'grade_maze.py')
cli = importlib.util.module_from_spec(spec)
with patch('sys.dont_write_bytecode', True):
    spec.loader.exec_module(cli)


class MazeGradeCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'submission'
        self.source.mkdir()
        (self.source / 'maze-core.js').write_text('// trusted dummy fixture; not executed')
        (self.source / 'index.html').write_text('<title>fixture</title>')

    def tearDown(self):
        self.temp.cleanup()

    def test_does_not_put_private_grader_inside_submission(self):
        output = self.root / 'output'
        grade = aggregate([TestResult('fixture', 'harness_only', 'skipped', None, 100)])
        with patch.object(cli, 'grade_submission', return_value=(grade, None, 0.1)), patch.object(cli, 'clean_containers'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([str(self.source), '--output', str(output)]), 0)
        report = read_json(output / 'results.json')
        self.assertTrue(report['metadata']['grading_only'])
        self.assertEqual(report['metadata']['real_model_calls'], 0)
        row = report['results'][0]
        self.assertIsNone(row['grade']['score'])
        self.assertIsNone(row['generation_seconds'])
        self.assertTrue((output / row['artifact_path'] / 'submission' / 'maze-core.js').is_file())
        self.assertFalse((output / row['artifact_path'] / 'submission' / 'plugin.py').exists())
        self.assertTrue((output / 'private' / 'grader' / 'bridge.js').is_file())
        self.assertTrue((output / 'report.html').is_file())

    def test_overlapping_output_is_rejected_before_writing(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(self.source), '--output', str(self.source / 'grading')]), 2)
        self.assertFalse((self.source / 'grading').exists())

    def test_existing_grade_is_preserved(self):
        output = self.root / 'output'
        output.mkdir()
        manifest = output / 'manifest.json'
        manifest.write_text('{"keep":true}')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([str(self.source), '--output', str(output)]), 2)
        self.assertEqual(manifest.read_text(), '{"keep":true}')
