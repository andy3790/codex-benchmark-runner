from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .codex import build_command, diagnose
from .config import default_config_path, load_config
from .report import write_reports
from .runner import OutputLock, read_results, run_benchmark
from .util import BenchError, read_json


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Resumable local Codex CLI benchmarks. No API key required.")
    parser.add_argument("--config", type=Path, help="Settings file (default: config.local.json if present, otherwise config.json)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--gui", action="store_true", help="Open the desktop GUI (no model calls on startup)")
    action.add_argument("--dry-run", action="store_true", help="Show combinations/argv without model calls or output writes")
    action.add_argument("--doctor", action="store_true", help="Inspect local CLI version/help without model calls")
    action.add_argument("--report-only", action="store_true", help="Regenerate reports from saved results without model calls")
    args = parser.parse_args(argv)
    if args.config is None:
        args.config = default_config_path()
    try:
        if args.gui:
            try:
                from .gui import main as gui_main
            except ImportError as exc:
                raise BenchError("GUI에는 Python의 Tcl/Tk(Tkinter)가 필요합니다. Python 설치 옵션을 확인하세요.") from exc
            return gui_main(["--config", str(args.config)])
        cfg = load_config(args.config)
        if args.report_only:
            with OutputLock(cfg.output):
                metadata = read_json(cfg.output / "manifest.json")
                write_reports(cfg.output, read_results(cfg), metadata)
            print(cfg.output / "report.html")
            return 0
        diagnostics = diagnose(cfg.codex, config=cfg)
        if cfg.isolation == "restricted" and diagnostics.get("permission_profiles") is False:
            diagnostics["errors"].append("restricted isolation requires Codex permission profiles (CLI >= 0.138.0); no automatic weakening of isolation")
        if args.doctor:
            # ASCII-safe JSON also works when PowerShell pipes use a legacy code page.
            print(json.dumps(diagnostics, ensure_ascii=True, indent=2))
            return 2 if diagnostics["errors"] else 0
        if args.dry_run:
            for job in cfg.jobs:
                workspace = cfg.scratch / "<experiment-id>" / job.id / "<attempt-id>" / "project"
                command = build_command(cfg, job, workspace, diagnostics)
                print(f"{job.id}  model={job.model} effort={job.effort} repeat={job.repeat}")
                print("  argv=" + json.dumps(command, ensure_ascii=False))
                if sys.platform == "win32":
                    print("  PowerShell: & " + " ".join("'" + value.replace("'", "''") + "'" for value in command))
            print(f"{len(cfg.jobs)} experiments; workers={cfg.workers}; per-run={cfg.run_timeout}s; invocation={cfg.total_timeout}s")
            print("No model calls. stdin is the exact same prompt.md for every run. Model/effort availability is verified by each actual invocation.")
            for error in diagnostics["errors"]:
                print("DIAGNOSTIC ERROR: " + error, file=sys.stderr)
            return 2 if diagnostics["errors"] else 0
        if diagnostics["errors"]:
            raise BenchError("Environment diagnosis failed:\n" + "\n".join(diagnostics["errors"]))
        return run_benchmark(cfg, diagnostics)
    except (BenchError, OSError, ValueError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
