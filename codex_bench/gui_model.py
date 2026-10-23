"""Testable GUI configuration editing without importing Tk or launching models."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from .config import Config, load_config
from .util import BenchError, read_json, replace_file, write_json

DEFAULTS = {"repetitions": 1, "run_timeout_seconds": 900, "total_timeout_seconds": 7200,
            "mode": "sequential", "max_workers": 2, "max_experiments": 20, "isolation": "restricted",
            "prompt_file": "prompt.md", "starter_dir": "starter", "output_dir": "benchmark-results",
            "scratch_dir": "", "codex_executable": "codex", "max_output_bytes": 16777216, "max_project_bytes": 134217728}
GRADER_DEFAULTS = {"plugin_dir": "graders/sample", "test_timeout_seconds": 5, "timeout_seconds": 120}
INTEGER_FIELDS = {"repetitions", "max_workers", "max_experiments", "max_output_bytes", "max_project_bytes"}
NUMBER_FIELDS = {"run_timeout_seconds", "total_timeout_seconds", "grader.test_timeout_seconds", "grader.timeout_seconds"}


def form_values(raw: dict) -> dict:
    values = {key: str(raw.get(key, default)) for key, default in DEFAULTS.items()}
    grader = raw.get("grader", {})
    values.update({"grader." + key: str(grader.get(key, default)) for key, default in GRADER_DEFAULTS.items()})
    values["docker_image"] = grader.get("options", {}).get("docker_image", "python:3.12-slim")
    efforts = raw.get("reasoning_efforts", ["low", "medium", "high"])
    values["models"] = [{"name": entry if isinstance(entry, str) else entry["name"],
                         "reasoning_efforts": list(efforts if isinstance(entry, str) else entry.get("reasoning_efforts", efforts))}
                        for entry in raw["models"]]
    return values


@dataclass
class ConfigDocument:
    path: Path
    original_raw: dict
    original_form: dict
    original_prompt_path: Path
    original_prompt: str

    @classmethod
    def open(cls, path: Path):
        cfg = load_config(path)
        return cls(cfg.path, copy.deepcopy(cfg.raw), form_values(cfg.raw), cfg.prompt_path, cfg.prompt)

    def build(self, values: dict, prompt: str) -> Config:
        raw = copy.deepcopy(self.original_raw)
        for key, value in values.items():
            if value == self.original_form.get(key):
                continue  # Preserve omitted defaults/string model lists so resume fingerprints stay stable.
            if key == "models":
                raw[key] = copy.deepcopy(value)
                continue
            value = str(value).strip()
            try:
                if key in INTEGER_FIELDS:
                    value = int(value)
                elif key in NUMBER_FIELDS:
                    value = float(value)
            except ValueError as exc:
                raise BenchError(f"{key}: 올바른 숫자를 입력하세요") from exc
            if key.startswith("grader."):
                raw.setdefault("grader", {})[key.split(".", 1)[1]] = value
            elif key == "docker_image":
                raw.setdefault("grader", {}).setdefault("options", {})[key] = value
            elif key == "scratch_dir" and not value:
                raw.pop(key, None)
            else:
                raw[key] = value
        cfg = load_config(self.path, raw_override=raw, prompt_override=prompt)
        if cfg.prompt_path == self.path:
            raise BenchError("설정 파일과 프롬프트 파일은 서로 달라야 합니다")
        return cfg

    def save(self, values: dict, prompt: str) -> Config:
        cfg = self.build(values, prompt)
        if read_json(self.path) != self.original_raw:
            raise BenchError("설정 파일이 외부에서 변경되었습니다. 다시 열어 변경 내용을 확인하세요.")
        if self.original_prompt_path.read_text(encoding="utf-8-sig") != self.original_prompt:
            raise BenchError("프롬프트가 외부에서 변경되었습니다. 다시 열어 변경 내용을 확인하세요.")
        original_config = self.path.read_bytes()
        previous_prompt = cfg.prompt_path.read_bytes() if cfg.prompt_path.exists() else None
        try:
            if cfg.prompt_path != self.original_prompt_path or prompt != self.original_prompt:
                cfg.prompt_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cfg.prompt_path.with_name(cfg.prompt_path.name + ".gui-tmp")
                temporary.write_text(prompt, encoding="utf-8", newline="\n")
                replace_file(temporary, cfg.prompt_path)
            if cfg.raw != self.original_raw:
                write_json(self.path, cfg.raw)
        except OSError:
            # Restore prior content if one of the two atomic writes failed.
            if previous_prompt is not None:
                cfg.prompt_path.write_bytes(previous_prompt)
            elif cfg.prompt_path.exists():
                cfg.prompt_path.unlink()
            self.path.write_bytes(original_config)
            raise
        loaded = load_config(self.path)
        self.original_raw = copy.deepcopy(loaded.raw)
        self.original_form = form_values(loaded.raw)
        self.original_prompt_path, self.original_prompt = loaded.prompt_path, loaded.prompt
        return loaded
