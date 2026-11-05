from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .codex import build_command, diagnose
from .config import default_config_path, load_config
from .report import read_saved_results, write_reports
from .runner import OutputLock, run_benchmark
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
        if args.report_only:
            # Reporting only needs the saved experiment, not its original inputs
            # or the model matrix currently selected for a future run.
            config_path = args.config.resolve()
            raw = read_json(config_path)
            if not isinstance(raw, dict):
                raise BenchError("Configuration must be a JSON object")
            output_dir = raw.get("output_dir", "benchmark-results")
            if not isinstance(output_dir, str) or not output_dir.strip():
                raise BenchError("output_dir must be a nonempty path")
            output = (config_path.parent / output_dir).resolve()
            if not (output / "manifest.json").is_file():
                raise BenchError(f"Saved experiment manifest is missing: {output / 'manifest.json'}")
            with OutputLock(output):
                metadata, rows = read_saved_results(output)
                try:
                    write_reports(output, rows, metadata)
                except (KeyError, AttributeError, TypeError, ValueError) as exc:
                    raise BenchError(f"Cannot render saved result data: {exc}") from exc
            print(output / "report.html")
            return 0
        cfg = load_config(args.config)
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
