import json
import tempfile
import unittest
from pathlib import Path

from codex_bench.config import load_config
from codex_bench.util import BenchError, copy_tree
from tests.helpers import setup_experiment, test_directory


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def change(self, **kwargs):
        raw = self.cfg.raw.copy()
        raw.update(kwargs)
        self.cfg.path.write_text(json.dumps(raw), encoding="utf-8")
        return load_config(self.cfg.path)

    def test_matrix_and_deterministic_ids(self):
        cfg = self.change(models=["model-a", "model-b"], reasoning_efforts=["low", "high"], repetitions=3)
        self.assertEqual(len(cfg.jobs), 12)
        self.assertEqual(len({j.id for j in cfg.jobs}), 12)
        self.assertEqual(cfg.jobs, load_config(cfg.path).jobs)
        self.assertEqual(cfg.workers, 1)

    def test_limit_rejects_whole_matrix(self):
        with self.assertRaises(BenchError):
            self.change(repetitions=1000)

    def test_invalid_time_and_repetition(self):
        for kwargs in [{"run_timeout_seconds": -1}, {"run_timeout_seconds": True}, {"repetitions": 1.5}, {"mode": "auto"}, {"max_workers": 17}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(BenchError):
                self.change(**kwargs)

    def test_duplicates_and_unknown_config(self):
        for kwargs in [{"models": ["a", "a"]}, {"reasoning_efforts": ["low", "low"]}, {"extra_args": ["--yolo"]}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(BenchError):
                self.change(**kwargs)

    def test_overlapping_roots_rejected(self):
        with self.assertRaises(BenchError):
            self.change(scratch_dir="output/scratch")

    def test_repository_workspace_rejected(self):
        (self.root / "scratch").mkdir()
        (self.root / "scratch" / ".git").mkdir()
        with self.assertRaises(BenchError):
            self.change(scratch_dir="scratch")

    def test_starter_cannot_override_sandbox(self):
        (self.root / "starter" / ".codex").mkdir()
        (self.root / "starter" / ".codex" / "config.toml").write_text("sandbox_mode='danger-full-access'")
        with self.assertRaises(BenchError):
            load_config(self.cfg.path)

    def test_snapshot_size_limit(self):
        with self.assertRaises(BenchError):
            copy_tree(self.cfg.starter, self.root / "snapshot", max_bytes=1)
