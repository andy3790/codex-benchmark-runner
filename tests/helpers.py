import json
import sys
import tempfile
from pathlib import Path

from codex_bench.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


def test_directory():
    # The Codex Windows execution sandbox denies atomic rename in redirected %TEMP%.
    # Keep test-owned result files in the writable workspace; no production fallback
    # from atomic replacement to partial writes is introduced.
    parent = Path(__file__).resolve().parent.parent / ".test-data"
    parent.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=parent)


def setup_experiment(root: Path, **overrides):
    (root / "starter").mkdir()
    (root / "starter" / "seed.txt").write_text("same seed", encoding="utf-8")
    (root / "prompt.md").write_text("공통 프롬프트\n", encoding="utf-8")
    (root / "grader").mkdir()
    # Purely synthetic grading for harness tests. It is never represented as real functional validation.
    (root / "grader" / "plugin.py").write_text('''from codex_bench.grading import TestResult, aggregate
def grade(submission, context):
    assert (submission / "seed.txt").read_text() == "same seed"
    assert not (submission / "plugin.py").exists()
    return aggregate([TestResult("fixture_only", "harness_fixture", "passed", 1, 1, detail="Synthetic harness test, not code correctness")])
''', encoding="utf-8")
    raw = {"models": [{"name": "fake", "reasoning_efforts": ["low", "unsupported"]}], "repetitions": 1,
           "starter_dir": "starter", "prompt_file": "prompt.md", "output_dir": "output",
           "scratch_dir": str(Path(tempfile.gettempdir()) / "codex-bench-test-workspaces" / root.name),
           "grader": {"plugin_dir": "grader", "test_timeout_seconds": 1, "timeout_seconds": 10},
           "run_timeout_seconds": 10, "total_timeout_seconds": 30}
    raw.update(overrides)
    path = root / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_config(path)


def fake_command(cfg, job, workspace, diagnostics):
    return [sys.executable, "-I", str(FIXTURES / "fake_codex.py"), str(workspace), job.model, job.effort]


DIAGNOSTICS = {"executable": "unused", "version": "fake 1.0", "errors": [], "no_daemon": False}
