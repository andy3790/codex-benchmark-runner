"""Run the real orchestration/report pipeline using only offline Python simulators."""
from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

from .config import Config, load_config
from .runner import OutputLock, prepare, run_benchmark
from .util import BenchError, read_json, write_json


def mock_command(cfg, job, workspace, diagnostics):
    return [sys.executable, "-I", "-B", str(Path(__file__).with_name("mock_codex.py")),
            str(workspace), job.model, job.effort, str(job.repeat)]


def create_simulation(cfg: Config) -> Config:
    # Keep demo data out of the real output directory, including before its first run.
    root = cfg.output.with_name(cfg.output.name + "-mock") / ("demo-" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    raw = copy.deepcopy(cfg.raw)
    raw.update({"prompt_file": str(root / "prompt.md"), "starter_dir": str(cfg.starter),
                "output_dir": str(root / "output"), "scratch_dir": str(cfg.scratch),
                "codex_executable": "SIMULATED-NO-CODEX", "run_timeout_seconds": min(cfg.run_timeout, 30),
                "total_timeout_seconds": min(cfg.total_timeout, 300)})
    raw["grader"] = {"plugin_dir": str(Path(__file__).with_name("mock_grader")),
                     "test_timeout_seconds": cfg.test_timeout, "timeout_seconds": min(cfg.grading_timeout, 30), "options": {}}
    (root / "prompt.md").write_text(cfg.prompt, encoding="utf-8", newline="\n")
    write_json(root / "config.json", raw)
    simulation = load_config(root / "config.json")
    with OutputLock(simulation.output):
        manifest = prepare(simulation)
        manifest.update({"synthetic": True, "source_config": str(cfg.path), "real_model_calls": 0})
        write_json(simulation.output / "manifest.json", manifest)
    return simulation


def run_simulation(cfg: Config, **kwargs) -> int:
    if cfg.codex != "SIMULATED-NO-CODEX" or not read_json(cfg.output / "manifest.json").get("synthetic"):
        raise BenchError("Use create_simulation() to create an explicitly labeled mock experiment first")
    diagnostics = {"executable": sys.executable, "version": "SIMULATED — no Codex/model calls", "errors": [], "no_daemon": False}
    return run_benchmark(cfg, diagnostics, command_factory=mock_command, **kwargs)
