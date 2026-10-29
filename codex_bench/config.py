from __future__ import annotations

import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import BenchError, digest, is_within, read_json, tree_hash


def default_config_path(directory: Path | None = None) -> Path:
    """Keep machine-specific settings out of the shared configuration."""
    root = Path.cwd() if directory is None else Path(directory)
    local = root / 'config.local.json'
    return local if local.is_file() else root / 'config.json'


@dataclass(frozen=True)
class Job:
    id: str
    model: str
    effort: str
    repeat: int

    def as_dict(self) -> dict:
        return vars(self)


@dataclass
class Config:
    path: Path
    raw: dict[str, Any]
    prompt_path: Path
    starter: Path
    output: Path
    scratch: Path
    grader_path: Path
    codex: str
    jobs: list[Job]
    workers: int
    run_timeout: float
    total_timeout: float
    test_timeout: float
    grading_timeout: float
    max_output_bytes: int
    max_project_bytes: int
    isolation: str
    _prompt_override: str | None = None

    @property
    def prompt(self) -> str:
        if self._prompt_override is not None:
            return self._prompt_override
        return self.prompt_path.read_text(encoding="utf-8-sig")

    @property
    def fingerprint(self) -> str:
        return digest({"config": self.raw, "prompt": self.prompt,
                       "starter": tree_hash(self.starter), "grader": tree_hash(self.grader_path)})


def load_config(path: Path, *, raw_override: dict | None = None, prompt_override: str | None = None) -> Config:
    path = path.resolve()
    raw = read_json(path) if raw_override is None else raw_override
    if not isinstance(raw, dict):
        raise BenchError("Configuration must be a JSON object")
    allowed = {"models", "reasoning_efforts", "repetitions", "run_timeout_seconds", "total_timeout_seconds",
               "mode", "max_workers", "max_experiments", "prompt_file", "starter_dir", "output_dir",
               "scratch_dir", "codex_executable", "grader", "max_output_bytes", "max_project_bytes", "isolation"}
    if set(raw) - allowed:
        raise BenchError(f"Unknown configuration keys: {sorted(set(raw) - allowed)}")

    def resolve(value: str) -> Path:
        return (path.parent / value).resolve()

    def positive(value, label, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise BenchError(f"{label} must be positive and finite")
        if integer and not isinstance(value, int):
            raise BenchError(f"{label} must be an integer")
        return value

    def names(values, label):
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", v) for v in values):
            raise BenchError(f"{label} must be a nonempty list of names")
        if len(values) != len(set(values)):
            raise BenchError(f"Duplicate {label}")
        return values

    models = raw.get("models", [])
    if not isinstance(models, list) or not models:
        raise BenchError("models must be a nonempty list")
    global_efforts = names(raw.get("reasoning_efforts", ["low", "medium", "high"]), "reasoning_efforts")
    repetitions = positive(raw.get("repetitions", 1), "repetitions", True)
    jobs = []
    model_names = []
    limit = positive(raw.get("max_experiments", 20), "max_experiments", True)
    for entry in models:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict) or set(entry) - {"name", "reasoning_efforts"}:
            raise BenchError("Each model must be a name or {name, reasoning_efforts}")
        model = names([entry.get("name")], "model")[0]
        model_names.append(model)
        efforts = names(entry.get("reasoning_efforts", global_efforts), "reasoning_efforts")
        if len(jobs) + len(efforts) * repetitions > limit:
            raise BenchError(f"Experiment count exceeds max_experiments={limit}; no partial matrix will run")
        for effort in efforts:
            for repeat in range(1, repetitions + 1):
                job_id = "job-" + digest([model, effort, repeat])[:16]
                jobs.append(Job(job_id, model, effort, repeat))
    if len(model_names) != len(set(model_names)):
        raise BenchError("Duplicate model entries")
    mode = raw.get("mode", "sequential")
    if mode not in {"sequential", "parallel"}:
        raise BenchError("mode must be sequential or parallel")
    workers = positive(raw.get("max_workers", 2), "max_workers", True)
    if workers > 16:
        raise BenchError("max_workers cannot exceed 16")
    grader = raw.get("grader", {"plugin_dir": "graders/sample"})
    if not isinstance(grader, dict) or set(grader) - {"plugin_dir", "test_timeout_seconds", "timeout_seconds", "options"}:
        raise BenchError("Invalid grader configuration")
    cfg = Config(path, raw, resolve(raw.get("prompt_file", "prompt.md")),
                 resolve(raw.get("starter_dir", "starter")), resolve(raw.get("output_dir", "benchmark-results")),
                 resolve(raw.get("scratch_dir", str(Path(tempfile.gettempdir()) / "codex-benchmark-workspaces"))), resolve(grader.get("plugin_dir", "graders/sample")),
                 raw.get("codex_executable", "codex"), jobs, workers if mode == "parallel" else 1,
                 positive(raw.get("run_timeout_seconds", 900), "run_timeout_seconds"),
                 positive(raw.get("total_timeout_seconds", 7200), "total_timeout_seconds"),
                 positive(grader.get("test_timeout_seconds", 5), "test_timeout_seconds"),
                 positive(grader.get("timeout_seconds", 120), "grader.timeout_seconds"),
                 positive(raw.get("max_output_bytes", 16 * 1024 * 1024), "max_output_bytes", True),
                 positive(raw.get("max_project_bytes", 128 * 1024 * 1024), "max_project_bytes", True),
                 raw.get("isolation", "restricted"))
    cfg._prompt_override = prompt_override
    if cfg.isolation not in {"restricted", "workspace-write"}:
        raise BenchError("isolation must be restricted (workspace writes + denied host reads) or workspace-write")
    if not isinstance(cfg.codex, str) or not cfg.codex.strip():
        raise BenchError("codex_executable must be a command name or executable path")
    if (prompt_override is None and not cfg.prompt_path.is_file()) or not cfg.prompt.strip():
        raise BenchError(f"Prompt is missing or empty: {cfg.prompt_path}")
    if not (cfg.grader_path / "plugin.py").is_file():
        raise BenchError(f"Grader requires plugin.py: {cfg.grader_path}")
    # Separate roots: a generated project is never inside the runner repository.
    for a, b in [(cfg.output, cfg.scratch), (cfg.starter, cfg.output), (cfg.starter, cfg.scratch),
                 (cfg.grader_path, cfg.output), (cfg.grader_path, cfg.scratch)]:
        if is_within(a, b) or is_within(b, a):
            raise BenchError(f"Paths must not overlap: {a} and {b}")
    if is_within(path, cfg.scratch) or is_within(cfg.prompt_path, cfg.scratch) or is_within(Path(__file__), cfg.scratch):
        raise BenchError("scratch_dir must not contain runner/config/prompt")
    # Validate symlinks/reparse points before any process starts.
    tree_hash(cfg.starter)
    tree_hash(cfg.grader_path)
    if any(p.parts[0].lower() in {".git", ".codex"} for p in (Path(s) for s in tree_hash(cfg.starter))):
        raise BenchError("starter_dir must not contain .git or .codex configuration")
    if any((ancestor / ".git").exists() for ancestor in [cfg.scratch, *cfg.scratch.parents]):
        raise BenchError("scratch_dir must be outside all Git repositories to keep sandbox roots separate")
    return cfg
