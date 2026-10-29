from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

from . import __version__
from .codex import build_command, classify_failure, execution_environment
from .config import Config, Job
from .events import parse_events
from .process import run_process
from .report import write_reports
from .util import BenchError, copy_tree, digest, read_json, tree_hash, utc_now, write_json

TERMINAL = {"succeeded", "failed", "unsupported", "timed_out"}


class OutputLock:
    """OS-backed advisory lock; released after crash without stale-lock heuristics."""
    def __init__(self, output: Path):
        self.path = output / ".runner.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = None
        try:
            self.file = self.path.open("a+b")
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.file:
                self.file.close()
            raise BenchError(f"Another runner owns {self.path.parent}") from exc
        return self

    def __exit__(self, *args):
        self.file.close()


def prepare(cfg: Config) -> dict:
    manifest_path = cfg.output / "manifest.json"
    fingerprint = cfg.fingerprint
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if manifest.get("fingerprint") != fingerprint:
            raise BenchError("Config/prompt/starter/grader changed; use a new output_dir for a fair experiment")
        if tree_hash(cfg.output / "private" / "grader") != manifest["grader_hashes"] or tree_hash(cfg.output / "private" / "starter") != manifest["starter_hashes"]:
            raise BenchError("Frozen inputs were modified; refusing to resume")
        if (cfg.output / "private" / "prompt.md").read_text(encoding="utf-8") != cfg.prompt:
            raise BenchError("Frozen prompt was modified; refusing to resume")
        return manifest
    # A half-written initial snapshot is never reused as a valid experiment.
    if any(p.name != ".runner.lock" for p in cfg.output.iterdir()):
        raise BenchError("output_dir contains files without a manifest; choose a new empty output_dir")
    private = cfg.output / "private"
    private.mkdir()
    copy_tree(cfg.starter, private / "starter", cfg.max_project_bytes)
    copy_tree(cfg.grader_path, private / "grader", cfg.max_project_bytes)
    (private / "prompt.md").write_text(cfg.prompt, encoding="utf-8", newline="\n")
    write_json(private / "config.json", cfg.raw)
    manifest = {"schema_version": 1, "experiment_id": uuid.uuid4().hex, "created_at": utc_now(),
                "runner_version": __version__, "fingerprint": fingerprint,
                "starter_hashes": tree_hash(private / "starter"), "grader_hashes": tree_hash(private / "grader"),
                "jobs": [j.as_dict() for j in cfg.jobs], "active_seconds": 0.0}
    write_json(manifest_path, manifest)
    return manifest


def read_results(cfg: Config) -> list[dict]:
    rows = []
    for job in cfg.jobs:
        path = cfg.output / "runs" / job.id / "result.json"
        rows.append(read_json(path) if path.exists() else {**job.as_dict(), "status": "pending", "usage": {}})
    return rows


def clean_containers(logs: Path) -> None:
    docker = shutil.which("docker")
    if docker:
        for path in logs.glob("codex-bench-*"):
            if len(path.name) == len("codex-bench-") + 32 and all(c in "0123456789abcdef" for c in path.name[12:]):
                try:
                    import subprocess
                    subprocess.run([docker, "rm", "--force", path.name], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass


def grade_submission(cfg: Config, attempt: Path, cancel: threading.Event, deadline: float) -> tuple[dict | None, str | None, float]:
    logs = attempt / "grading"
    logs.mkdir(exist_ok=True)
    request = {"plugin_dir": str(cfg.output / "private" / "grader"), "submission": str(attempt / "submission"),
               "artifact_dir": str(logs), "result_file": str(logs / "grade.json"),
               "test_timeout_seconds": cfg.test_timeout,
               "options": cfg.raw.get("grader", {}).get("options", {})}
    write_json(logs / "request.json", request)
    # A trusted plugin receives no inherited API keys or Python injection settings.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
               "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    process = run_process([sys.executable, "-I", "-B", str(Path(__file__).with_name("grader_worker.py")), str(logs / "request.json")],
                          cwd=logs, stdout=logs / "stdout.log", stderr=logs / "stderr.log", input_bytes=b"",
                          timeout=max(0.01, min(cfg.grading_timeout, deadline - time.monotonic())),
                          cancel=cancel, max_output_bytes=cfg.max_output_bytes, env=env)
    clean_containers(logs)
    write_json(logs / "process.json", vars(process))
    if process.termination or process.exit_code != 0 or not (logs / "grade.json").exists():
        return None, process.termination or process.error or "grader_error", process.duration_seconds
    from .grading import validate_grade
    try:
        return validate_grade(read_json(logs / "grade.json")), None, process.duration_seconds
    except (BenchError, ValueError, OSError) as exc:
        return None, f"invalid_grader_result: {exc}", process.duration_seconds


def execute_job(cfg: Config, job: Job, diagnostics: dict, experiment_id: str,
                cancel: threading.Event, deadline: float, old: dict | None = None, command_factory=None) -> dict:
    run_root = cfg.output / "runs" / job.id
    run_root.mkdir(parents=True, exist_ok=True)
    # Resume grading without spending generation tokens again if generation was checkpointed.
    reuse = old and old.get("generation_finished") and old.get("attempt_id") and (run_root / "attempts" / old["attempt_id"] / "submission").is_dir()
    if reuse:
        result = old.copy()
        attempt = run_root / "attempts" / old["attempt_id"]
        if tree_hash(attempt / "submission") != old.get("submission_hashes"):
            raise BenchError("Saved submission changed; refusing to regrade altered code")
        # Preserve all prior grading logs as a separate attempt too.
        previous = attempt / "grading"
        if previous.exists():
            clean_containers(previous)
            previous.rename(attempt / ("grading-previous-" + uuid.uuid4().hex[:8]))
    else:
        attempt_id = uuid.uuid4().hex
        attempt = run_root / "attempts" / attempt_id
        attempt.mkdir(parents=True)
        workspace = cfg.scratch / experiment_id / job.id / attempt_id / "project"
        copy_tree(cfg.output / "private" / "starter", workspace, cfg.max_project_bytes)
        result = {**job.as_dict(), "attempt_id": attempt_id, "started_at": utc_now(), "ended_at": None,
                  "status": "running", "codex_version": diagnostics.get("version"), "workspace": str(workspace),
                  "success": False, "generation_finished": False, "usage": {}, "grade": None}
        write_json(run_root / "result.json", result)
        write_json(attempt / "result.json", result)
        command = (command_factory or build_command)(cfg, job, workspace, diagnostics)
        write_json(attempt / "command.json", {"argv": command, "stdin": "common frozen prompt.md", "cwd": str(workspace)})
        generation_env = execution_environment(diagnostics)
        # Keep temporary shell writes within this workspace, while auth uses existing CODEX_HOME.
        (workspace / ".benchmark-tmp").mkdir(exist_ok=True)
        generation_env.update({"TEMP": str(workspace / ".benchmark-tmp"), "TMP": str(workspace / ".benchmark-tmp"),
                               "TMPDIR": str(workspace / ".benchmark-tmp")})
        process = run_process(command, cwd=workspace, stdout=attempt / "stdout.log", stderr=attempt / "stderr.log",
                              input_bytes=(cfg.output / "private" / "prompt.md").read_bytes(), env=generation_env,
                              timeout=max(0.01, min(cfg.run_timeout, deadline - time.monotonic())),
                              cancel=cancel, max_output_bytes=cfg.max_output_bytes)
        shutil.copyfile(attempt / "stdout.log", attempt / "events.jsonl")
        parsed = parse_events(attempt / "events.jsonl")
        (attempt / "final_message.md").write_text(parsed.pop("final_message"), encoding="utf-8")
        stderr = (attempt / "stderr.log").read_text(encoding="utf-8", errors="replace")
        error_type = classify_failure(process.exit_code, parsed, stderr, process.termination)
        if process.termination == "timeout" and time.monotonic() >= deadline and cfg.run_timeout > process.duration_seconds:
            error_type = "overall_timeout"
        result.update(parsed)
        result.update({"exit_code": process.exit_code, "generation_seconds": process.duration_seconds,
                       "generation_ended_at": utc_now(), "error_type": error_type,
                       "process_error": process.error, "generation_finished": True, "status": "grading"})
        if error_type:
            messages = [line.strip() for line in stderr.splitlines() if line.strip()]
            message = next((line for line in messages if line.lower().startswith('error:')), '')
            if not message:
                message = next((line for line in messages if ' ERROR ' in line), '')
            if not message:
                message = next((line for line in messages if not line.lower().startswith(('warning:', 'warn:'))
                                and ' WARN ' not in line), '')
            result['error_summary'] = (process.error or message or str(parsed.get('errors') or error_type))[:400]
        if parsed["malformed_lines"]:
            result["warnings"].append("Non-JSON stdout lines preserved; usage may be incomplete")
        try:
            copy_tree(workspace, attempt / "submission", cfg.max_project_bytes)
            result["submission_hashes"] = tree_hash(attempt / "submission")
        except (BenchError, OSError) as exc:
            result["artifact_error"] = str(exc)
            result["error_type"] = error_type or "artifact_error"
        write_json(run_root / "result.json", result)
        write_json(attempt / "result.json", result)
    result["grade"], result["grading_error"], result["grading_seconds"] = (None, None, 0.0)
    if (attempt / "submission").is_dir() and not result.get("artifact_error") and not cancel.is_set() and time.monotonic() < deadline:
        result["grade"], result["grading_error"], result["grading_seconds"] = grade_submission(cfg, attempt, cancel, deadline)
    else:
        result["grading_error"] = "interrupted" if cancel.is_set() else "overall_timeout" if time.monotonic() >= deadline else "no_submission"
    error = result.get("error_type")
    if error in {"interrupted", "overall_timeout"} or result["grading_error"] == "interrupted":
        result["status"] = "interrupted"
        # An interrupted model generation is restarted from the common seed on resume.
        if error in {"interrupted", "overall_timeout"}:
            result["generation_finished"] = False
    elif error == "timeout":
        result["status"] = "timed_out"
    elif error == "unsupported_combination":
        result["status"] = "unsupported"
    elif result["grading_error"] == "overall_timeout":
        result["status"] = "interrupted"
    else:
        result["status"] = "failed" if error or result["grading_error"] else "succeeded"
    result["success"] = result["status"] == "succeeded"
    result["ended_at"] = utc_now()
    result["duration_seconds"] = result.get("generation_seconds", 0) + result["grading_seconds"]
    result["artifact_path"] = str(attempt.relative_to(cfg.output)).replace("\\", "/")
    write_json(attempt / "result.json", result)
    write_json(run_root / "result.json", result)
    return result


def run_benchmark(cfg: Config, diagnostics: dict, *, cancel_event: threading.Event | None = None,
                  on_log=None, on_progress=None, command_factory=None) -> int:
    def log(message):
        print(message, flush=True)
        if on_log:
            on_log(message)

    def progress(rows):
        if on_progress:
            on_progress(rows)

    with OutputLock(cfg.output):
        manifest = prepare(cfg)
        write_json(cfg.output / "diagnostics.json", diagnostics)
        cancel = cancel_event if cancel_event is not None else threading.Event()
        previous_handlers = {}

        def stop(signum, frame):
            if not cancel.is_set():
                log("\nStop requested; terminating active process trees and preserving results...")
            cancel.set()

        if threading.current_thread() is threading.main_thread():
            for sig in [signal.SIGINT, signal.SIGTERM]:
                previous_handlers[sig] = signal.signal(sig, stop)
        started = time.monotonic()
        # This is a per-invocation wall-clock limit, explicitly recorded alongside accumulated active time.
        deadline = started + cfg.total_timeout
        last_update = started
        pending = []
        for row, job in zip(read_results(cfg), cfg.jobs):
            if row["status"] not in TERMINAL:
                pending.append((job, row))
        log(f"Experiments: {len(cfg.jobs)} | complete: {len(cfg.jobs)-len(pending)} | queued: {len(pending)} | workers: {cfg.workers}")
        initial_rows = read_results(cfg)
        write_reports(cfg.output, initial_rows, manifest)
        progress(initial_rows)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.workers) as pool:
                active = {}
                while pending or active:
                    if time.monotonic() >= deadline:
                        cancel.set()
                    while pending and len(active) < cfg.workers and not cancel.is_set():
                        job, old = pending.pop(0)
                        log(f"START {job.id} {job.model} / {job.effort} repeat={job.repeat}")
                        future = pool.submit(execute_job, cfg, job, diagnostics, manifest["experiment_id"], cancel, deadline, old, command_factory)
                        active[future] = job
                        progress(read_results(cfg))
                    if not active:
                        break
                    done, _ = concurrent.futures.wait(active, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED)
                    for future in done:
                        job = active.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:
                            result_path = cfg.output / "runs" / job.id / "result.json"
                            result = read_json(result_path) if result_path.exists() else job.as_dict()
                            result.update({"status": "failed", "success": False, "error_type": "runner_error",
                                           "process_error": repr(exc), "grade": None, "ended_at": utc_now()})
                            result.setdefault("usage", {})
                            if result.get("attempt_id"):
                                attempt = result_path.parent / "attempts" / result["attempt_id"]
                                result["artifact_path"] = str(attempt.relative_to(cfg.output)).replace("\\", "/")
                                write_json(attempt / "result.json", result)
                            write_json(result_path, result)
                        rows = read_results(cfg)
                        complete = sum(r["status"] in TERMINAL for r in rows)
                        failed = sum(r["status"] in TERMINAL - {"succeeded"} for r in rows)
                        log(f"DONE {job.model} / {job.effort}: {result['status']} | complete {complete}/{len(rows)} | failed {failed}")
                        reason = result.get('error_summary') or result.get('process_error') or result.get('grading_error')
                        if reason:
                            log('  Reason: ' + str(reason)[:400])
                        write_reports(cfg.output, rows, manifest)
                        progress(rows)
                    if time.monotonic() - last_update >= 30:
                        names = ", ".join(f"{j.model}/{j.effort}" for j in active.values())
                        log(f"RUNNING {names} | remaining budget {max(0, deadline-time.monotonic()):.0f}s")
                        progress(read_results(cfg))
                        last_update = time.monotonic()
        finally:
            manifest["active_seconds"] += time.monotonic() - started
            manifest["last_finished_at"] = utc_now()
            write_json(cfg.output / "manifest.json", manifest)
            write_reports(cfg.output, read_results(cfg), manifest)
            progress(read_results(cfg))
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
        log(f"Report: {cfg.output / 'report.html'}")
        if cancel.is_set():
            return 130
        return 1 if any(r["status"] != "succeeded" for r in read_results(cfg)) else 0
