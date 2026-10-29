"""Queue-based background work. Only the Tk main thread touches widgets."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from .codex import build_command, diagnose
from .runner import run_benchmark
from .simulation import create_simulation, run_simulation
from .util import BenchError


class BenchmarkController:
    def __init__(self):
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.thread = None
        self.last_report: Path | None = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.cancel_event.set()

    def start(self, action, cfg):
        if self.busy:
            raise BenchError("이미 실행 중입니다")
        if action not in {"doctor", "dry-run", "mock", "run"}:
            raise BenchError("알 수 없는 실행 방식입니다")
        self.cancel_event = threading.Event()
        self.thread = threading.Thread(target=self._work, args=(action, cfg), name="benchmark-gui-worker", daemon=False)
        self.thread.start()

    def _work(self, action, cfg):
        exit_code = 2
        report = None

        def log(message):
            self.events.put(("log", str(message)))

        def progress(rows):
            self.events.put(("progress", rows))

        try:
            if action == "mock":
                log("모의 실행: 실제 Codex·모델·Docker·후보 코드는 호출하지 않습니다. 점수와 토큰은 합성 데이터입니다.")
                simulation = create_simulation(cfg)
                self.events.put(("output", simulation.output))
                exit_code = run_simulation(simulation, cancel_event=self.cancel_event, on_log=log, on_progress=progress)
                report = simulation.output / "report.html"
            else:
                log("Codex 버전과 도움말을 진단합니다. 이 단계에서는 모델을 호출하지 않습니다.")
                diagnostics = diagnose(cfg.codex, config=cfg)
                if cfg.isolation == "restricted" and diagnostics.get("permission_profiles") is False:
                    diagnostics["errors"].append("restricted 격리에는 Codex CLI 0.138.0 이상이 필요합니다")
                self.events.put(("diagnostics", diagnostics))
                if self.cancel_event.is_set():
                    exit_code = 130
                elif action == "doctor":
                    log(json.dumps(diagnostics, ensure_ascii=False, indent=2))
                    exit_code = 2 if diagnostics["errors"] else 0
                elif action == "dry-run":
                    for job in cfg.jobs:
                        workspace = cfg.scratch / "<experiment-id>" / job.id / "<attempt-id>" / "project"
                        log(f"{job.model} / {job.effort} / 반복 {job.repeat}")
                        log("argv=" + json.dumps(build_command(cfg, job, workspace, diagnostics), ensure_ascii=False))
                    for error in diagnostics["errors"]:
                        log("환경 오류: " + error)
                    progress([{**job.as_dict(), "status": "pending", "usage": {}} for job in cfg.jobs])
                    log(f"실행 예정 {len(cfg.jobs)}회 · 동시 실행 {cfg.workers}개 · 실제 모델 호출 0회")
                    exit_code = 2 if diagnostics["errors"] else 0
                elif diagnostics["errors"]:
                    raise BenchError("\n".join(diagnostics["errors"]))
                else:
                    self.events.put(("output", cfg.output))
                    exit_code = run_benchmark(cfg, diagnostics, cancel_event=self.cancel_event, on_log=log, on_progress=progress)
                    report = cfg.output / "report.html"
        except Exception as exc:
            log(f"오류: {exc}")
            self.events.put(("error", str(exc)))
        finally:
            if report is not None and report.is_file():
                self.last_report = report
            self.events.put(("finished", {"action": action, "exit_code": exit_code,
                                          "report": report if report and report.exists() else None}))
