"""Trusted grader API. Candidate code must only run through an isolated executor."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .process import run_process
from .util import BenchError


@dataclass
class TestResult:
    name: str
    category: str
    status: str  # passed, failed, skipped, error
    score: float | None
    max_score: float
    duration_seconds: float | None = None
    detail: str = ""


class Grader(Protocol):
    def grade(self, submission: Path, context: "GradeContext") -> dict[str, Any]: ...


@dataclass
class GradeContext:
    artifact_dir: Path
    plugin_dir: Path
    test_timeout_seconds: float
    options: dict

    def docker(self, submission: Path) -> "DockerExecutor":
        return DockerExecutor(submission, self.artifact_dir, self.options, self.test_timeout_seconds)


def aggregate(tests: list[TestResult]) -> dict:
    measured = [t for t in tests if t.status != "skipped"]
    scored = [t for t in measured if t.score is not None]
    return {"schema_version": 1, "source": "independent_grader", "tests": [vars(t) for t in tests],
            "score": sum(t.score for t in scored) if scored else None,
            "max_score": sum(t.max_score for t in scored) if scored else None,
            "planned_max_score": sum(t.max_score for t in tests),
            "pass_rate": sum(t.status == "passed" for t in measured) / len(measured) if measured else None,
            "measured_tests": len(measured), "skipped_tests": len(tests) - len(measured)}


def validate_grade(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("source") != "independent_grader":
        raise BenchError("Grader must return schema_version=1, source=independent_grader")
    if not isinstance(value.get("tests"), list):
        raise BenchError("Grader tests must be a list")
    tests = []
    for row in value["tests"]:
        if not isinstance(row, dict):
            raise BenchError("Invalid test result")
        try:
            test = TestResult(**row)
        except TypeError as exc:
            raise BenchError(str(exc)) from exc
        if test.status not in {"passed", "failed", "skipped", "error"}:
            raise BenchError("Unknown test status")
        if not test.name or not test.category:
            raise BenchError("Each test requires a name and category")
        for number in [test.max_score, test.score, test.duration_seconds]:
            if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0):
                raise BenchError("Scores/times must be finite nonnegative numbers or null")
        if test.max_score is None or test.score is not None and test.score > test.max_score:
            raise BenchError("Score exceeds max_score")
        if test.status == "skipped" and test.score is not None:
            raise BenchError("An unmeasured test must have score=null")
        if test.status == "passed" and test.score is None:
            raise BenchError("Passed tests require an actual score")
        tests.append(test)
    # Recompute all totals; do not trust plugin summary or candidate-generated tests.
    return aggregate(tests)


class DockerExecutor:
    """Only a submission directory is exposed. No host grader/tests/auth mounts."""
    def __init__(self, submission: Path, logs: Path, options: dict, timeout: float):
        self.submission = submission.resolve()
        self.logs = logs
        self.timeout = timeout
        self.executable = shutil.which("docker")
        self.image = options.get("docker_image", "python:3.12-slim")
        if not isinstance(self.image, str) or self.image.startswith("-"):
            raise BenchError("docker_image must be an image reference")
        self.unavailable = None
        if not self.executable:
            self.unavailable = "Docker executable unavailable; candidate code was not executed"
        else:
            for args in [["info", "--format", "{{.OSType}}"], ["image", "inspect", self.image]]:
                try:
                    p = subprocess.run([self.executable, *args], capture_output=True, timeout=10)
                    if p.returncode or args[0] == "info" and p.stdout.strip() != b"linux":
                        self.unavailable = "Docker Linux engine/image unavailable; pre-pull the configured image"
                        break
                except (OSError, subprocess.TimeoutExpired) as exc:
                    self.unavailable = str(exc)
                    break

    def run(self, name: str, arguments: list[str], input_value: Any, timeout: float | None = None) -> dict:
        if self.unavailable:
            return {"status": "skipped", "detail": self.unavailable, "duration_seconds": None}
        token = "codex-bench-" + uuid.uuid4().hex
        case_dir = self.logs / token
        case_dir.mkdir(parents=True)
        # Docker --mount comma delimiter cannot safely represent a comma in a source path.
        if "," in str(self.submission):
            return {"status": "skipped", "detail": "Docker submission path must not contain commas", "duration_seconds": None}
        cmd = [self.executable, "run", "--rm", "--pull=never", "--name", token,
               "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
               "--pids-limit=64", "--memory=256m", "--cpus=1", "--user=65534:65534",
               "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--mount",
               f"type=bind,src={self.submission},dst=/submission,readonly", "--workdir=/submission",
               "--env=PYTHONDONTWRITEBYTECODE=1", "--interactive", self.image, *arguments]
        try:
            process = run_process(cmd, cwd=case_dir, stdout=case_dir / "stdout.log", stderr=case_dir / "stderr.log",
                                  timeout=timeout or self.timeout, max_output_bytes=1024 * 1024,
                                  input_bytes=input_value if isinstance(input_value, bytes) else json.dumps(input_value).encode())
        finally:
            # Stopping the docker client does not stop its container. Always force cleanup.
            try:
                subprocess.run([self.executable, "rm", "--force", token], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        stdout = (case_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
        stderr = (case_dir / "stderr.log").read_text(encoding="utf-8", errors="replace")
        if process.exit_code in {125, 126, 127}:
            status = "skipped"
        else:
            status = "ok" if process.exit_code == 0 and not process.termination else "error"
        return {"status": status, "stdout": stdout, "stderr": stderr, "exit_code": process.exit_code,
                "duration_seconds": process.duration_seconds,
                "detail": process.termination or process.error or stderr[-2000:]}
