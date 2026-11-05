"""Regrade immutable saved submissions without resolving or invoking a model."""
from __future__ import annotations

import copy
import json
import math
import re
import signal
import stat
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from .report import read_saved_results, write_reports
from .runner import OutputLock, clean_containers, grade_submission
from .util import BenchError, copy_tree, read_json, tree_hash, utc_now, write_json


@dataclass
class RegradeSettings:
    output: Path
    grader_path: Path
    raw: dict
    test_timeout: float
    grading_timeout: float
    total_timeout: float
    max_output_bytes: int
    max_project_bytes: int


def _load_settings(path: Path) -> RegradeSettings:
    path = path.resolve()
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise BenchError("Configuration must be a JSON object")
    grader = raw.get("grader", {"plugin_dir": "graders/sample"})
    if not isinstance(grader, dict) or set(grader) - {"plugin_dir", "options", "test_timeout_seconds", "timeout_seconds"}:
        raise BenchError("Invalid grader configuration")
    options = grader.get("options", {})
    if not isinstance(options, dict):
        raise BenchError("grader.options must be an object")
    try:
        json.dumps(options, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BenchError("grader.options must contain finite JSON values") from exc

    def resolve(value, label):
        if not isinstance(value, str) or not value.strip():
            raise BenchError(f"{label} must be a nonempty path")
        return (path.parent / value).resolve()

    def positive(value, label, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise BenchError(f"{label} must be positive and finite")
        if integer and not isinstance(value, int):
            raise BenchError(f"{label} must be an integer")
        return value

    settings = RegradeSettings(
        resolve(raw.get("output_dir", "benchmark-results"), "output_dir"),
        resolve(grader.get("plugin_dir", "graders/sample"), "grader.plugin_dir"), {},
        positive(grader.get("test_timeout_seconds", 5), "grader.test_timeout_seconds"),
        positive(grader.get("timeout_seconds", 120), "grader.timeout_seconds"),
        positive(raw.get("total_timeout_seconds", 7200), "total_timeout_seconds"),
        positive(raw.get("max_output_bytes", 16 * 1024 * 1024), "max_output_bytes", True),
        positive(raw.get("max_project_bytes", 128 * 1024 * 1024), "max_project_bytes", True))
    if not (settings.grader_path / "plugin.py").is_file():
        raise BenchError(f"Grader requires plugin.py: {settings.grader_path}")
    if settings.output.is_relative_to(settings.grader_path):
        raise BenchError("The grader source must not contain output_dir")
    settings.raw = {
        "output_dir": str(settings.output),
        "grader": {"plugin_dir": str(settings.grader_path), "options": copy.deepcopy(options),
                   "test_timeout_seconds": settings.test_timeout, "timeout_seconds": settings.grading_timeout},
        "total_timeout_seconds": settings.total_timeout,
        "max_output_bytes": settings.max_output_bytes, "max_project_bytes": settings.max_project_bytes}
    return settings


def _safe_path(output: Path, *parts: str) -> Path:
    path = output.joinpath(*parts)
    if not path.resolve().is_relative_to(output):
        raise BenchError(f"Saved path leaves the experiment: {path}")
    current = output
    for part in path.relative_to(output).parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise BenchError(f"Links/reparse points are not allowed in saved paths: {current}")
    return path


def _sources(output: Path, rows: list[dict], log) -> list[tuple[dict, Path]]:
    sources = []
    for row in rows:
        attempt_id, hashes = row.get("attempt_id"), row.get("submission_hashes")
        if row["status"] == "pending" or not attempt_id and hashes is None:
            log(f"SKIP {row['id']}: no saved generation attempt")
            continue
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", attempt_id):
            raise BenchError(f"Invalid saved attempt_id for {row['id']}")
        source = _safe_path(output, "runs", row["id"], "attempts", attempt_id, "submission")
        if hashes is None and not source.exists():
            log(f"SKIP {row['id']}: generation produced no saved submission")
            continue
        if (not isinstance(hashes, dict)
                or any(not isinstance(name, str) or not isinstance(value, str)
                       or not re.fullmatch(r"[0-9a-f]{64}", value) for name, value in hashes.items())):
            raise BenchError(f"Saved submission hashes are missing or invalid: {row['id']}")
        if not source.is_dir():
            raise BenchError(f"Saved submission is missing: {row['id']}")
        if tree_hash(source) != hashes:
            raise BenchError(f"Saved submission changed; refusing to regrade altered code: {row['id']}")
        generation_seconds = row.get("generation_seconds")
        if generation_seconds is not None and (isinstance(generation_seconds, bool)
                or not isinstance(generation_seconds, (int, float))
                or not math.isfinite(generation_seconds) or generation_seconds < 0):
            raise BenchError(f"Invalid saved generation duration: {row['id']}")
        expected_artifact = source.parent.relative_to(output).as_posix()
        if row.get("artifact_path") not in (None, expected_artifact):
            raise BenchError(f"Saved artifact_path does not match attempt_id: {row['id']}")
        _safe_path(output, "runs", row["id"], "result.json")
        sources.append((row, source))
    return sources


def _current_result(source: dict, record: dict) -> dict:
    current = copy.deepcopy(source)
    generation_failed = (source.get("error_type") or source.get("artifact_error")
                         or source.get("exit_code") not in (None, 0)
                         or source.get("generation_finished") is False
                         or source["status"] in {"unsupported", "timed_out"}
                         or source["status"] == "failed" and not source.get("grading_error"))
    if generation_failed:
        status = source["status"] if source["status"] in {"failed", "unsupported", "timed_out", "interrupted"} else "failed"
    else:
        status = record["status"]
    current.update({key: record[key] for key in ("grade", "grading_error", "grading_seconds", "regrade_id")})
    current.update({"status": status, "success": status == "succeeded", "regraded_at": record["ended_at"],
                    "regrade_artifact_path": record["artifact_path"]})
    generation_seconds = source.get("generation_seconds")
    current["duration_seconds"] = generation_seconds + record["grading_seconds"] if generation_seconds is not None else None
    return current


def regrade_benchmark(config_path: Path, *, cancel_event: threading.Event | None = None,
                     on_log=None, on_progress=None) -> int:
    settings = _load_settings(Path(config_path))
    if not (settings.output / "manifest.json").is_file():
        raise BenchError(f"Saved manifest is missing: {settings.output / 'manifest.json'}")

    def log(message):
        print(message, flush=True)
        if on_log:
            on_log(message)

    cancel = cancel_event if cancel_event is not None else threading.Event()
    with OutputLock(settings.output):
        started = time.monotonic()
        deadline = started + settings.total_timeout
        metadata, rows = read_saved_results(settings.output)
        sources = _sources(settings.output, rows, log)
        if not sources:
            log("No saved submissions to regrade; no model was called.")
            return 0
        if cancel.is_set() or time.monotonic() >= deadline:
            return 130
        regrade_id = uuid.uuid4().hex
        invocation = _safe_path(settings.output, "regrades", regrade_id)
        if invocation.is_relative_to(settings.grader_path):
            raise BenchError("The grader source must not contain the new regrade snapshot")
        invocation.mkdir(parents=True)
        manifest = {"schema_version": 1, "regrade_id": regrade_id,
                    "source_experiment_id": metadata.get("experiment_id"), "started_at": utc_now(),
                    "ended_at": None, "status": "running", "real_model_calls": 0,
                    "settings": settings.raw, "grader_hashes": None,
                    "jobs": [{key: row[key] for key in ("id", "model", "effort", "repeat")} for row, _ in sources],
                    "completed_jobs": [], "active_seconds": 0.0}
        write_json(invocation / "manifest.json", manifest)
        previous_handlers = {}

        def stop(signum, frame):
            cancel.set()

        def stopped():
            if time.monotonic() >= deadline:
                cancel.set()
            return cancel.is_set()

        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[sig] = signal.signal(sig, stop)
        protocol_failed = False
        try:
            frozen = invocation / "private"
            source_grader_hashes = tree_hash(settings.grader_path)
            copy_tree(settings.grader_path, frozen / "grader", settings.max_project_bytes)
            manifest["grader_hashes"] = tree_hash(frozen / "grader")
            if manifest["grader_hashes"] != source_grader_hashes:
                raise BenchError("Grader changed while creating its snapshot")
            write_json(frozen / "config.json", settings.raw)
            write_json(invocation / "manifest.json", manifest)
            staged = []
            for row, source in sources:
                if stopped():
                    break
                target = invocation / "runs" / row["id"]
                target.mkdir(parents=True)
                write_json(target / "source_result.json", row)
                copy_tree(source, target / "submission", settings.max_project_bytes)
                if tree_hash(target / "submission") != row["submission_hashes"]:
                    raise BenchError(f"Saved submission changed while copying: {row['id']}")
                staged.append((row, target))
            # Finish and verify every input copy before replacing any current result.
            worker_settings = replace(settings, output=invocation)
            if len(staged) == len(sources):
                for source, target in staged:
                    if stopped():
                        break
                    log(f"REGRADE {source['id']} {source['model']} / {source['effort']}")
                    record = {key: source[key] for key in ("id", "model", "effort", "repeat")}
                    record.update({"regrade_id": regrade_id, "source_attempt_id": source["attempt_id"],
                                   "source_artifact_path": "runs/" + source["id"] + "/attempts/" + source["attempt_id"],
                                   "artifact_path": target.relative_to(settings.output).as_posix(),
                                   "started_at": utc_now(), "ended_at": None, "status": "grading",
                                   "real_model_calls": 0, "usage": {}, "generation_seconds": None,
                                   "grade": None, "grading_error": None, "grading_seconds": 0.0,
                                   "submission_hashes": source["submission_hashes"]})
                    write_json(target / "result.json", record)
                    timer = time.monotonic()
                    try:
                        grade, error, duration = grade_submission(worker_settings, target, cancel, deadline)
                    except KeyboardInterrupt:
                        cancel.set()
                        grade, error, duration = None, "interrupted", time.monotonic() - timer
                    except Exception as exc:
                        grade, error, duration = None, f"regrade_error: {exc}", time.monotonic() - timer
                    finally:
                        clean_containers(target / "grading")
                    if error in {"interrupted", "overall_timeout"}:
                        cancel.set()
                    status = "interrupted" if error in {"interrupted", "overall_timeout"} else "failed" if error else "succeeded"
                    record.update({"grade": grade, "grading_error": error, "grading_seconds": duration,
                                   "duration_seconds": duration, "ended_at": utc_now(), "status": status,
                                   "success": status == "succeeded"})
                    write_json(target / "result.json", record)
                    # An interrupted regrade is history only. Leaving the previous
                    # current result intact also prevents normal resume from treating
                    # its original generation attempt as unfinished grading.
                    if status != "interrupted":
                        current = _current_result(source, record)
                        write_json(settings.output / "runs" / source["id"] / "result.json", current)
                        rows = [current if row["id"] == current["id"] else row for row in rows]
                        manifest["completed_jobs"].append(source["id"])
                    protocol_failed |= bool(error)
                    write_json(invocation / "manifest.json", manifest)
                    write_reports(settings.output, rows, metadata)
                    if on_progress:
                        on_progress(copy.deepcopy(rows))
            manifest["status"] = "interrupted" if stopped() else "failed" if protocol_failed else "succeeded"
        except BaseException as exc:
            manifest["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            manifest["error"] = str(exc)
            raise
        finally:
            manifest.update({"ended_at": utc_now(), "active_seconds": time.monotonic() - started})
            try:
                write_json(invocation / "manifest.json", manifest)
            finally:
                for sig, handler in previous_handlers.items():
                    signal.signal(sig, handler)
        log(f"Regrade history: {invocation}; model calls: 0")
        return 130 if cancel.is_set() else 1 if protocol_failed else 0
