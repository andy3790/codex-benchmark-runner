"""Build an offline SYNTHETIC report. No Codex, Docker, network, or candidate execution."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_bench.grading import TestResult, aggregate
from codex_bench.report import write_reports
from codex_bench.util import write_json


def main():
    output = Path(__file__).resolve().parent.parent / "examples" / "sample-report"
    rows = []
    for model_index, model in enumerate(["demo-model-a", "demo-model-b"]):
        for effort in ["low", "high"]:
            for repeat in [1, 2]:
                failed = model_index == 1 and effort == "high" and repeat == 2
                unsupported = model_index == 1 and effort == "low" and repeat == 2
                job_id = f"demo-{model_index}-{effort}-{repeat}"
                usage = {"input_tokens": 1200 + repeat * 100, "cached_input_tokens": 300,
                         "output_tokens": 240 if effort == "low" else 480,
                         "reasoning_output_tokens": 100 if effort == "low" else 300}
                usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
                if unsupported:
                    usage = {k: None for k in usage}
                grade = None if unsupported else aggregate([
                    TestResult("synthetic_basic", "functionality", "passed", 40, 40, 0.1, "SYNTHETIC demonstration value"),
                    TestResult("synthetic_algorithm", "algorithm", "failed" if failed else "passed", 0 if failed else 30, 30, 0.2, "SYNTHETIC demonstration value"),
                    TestResult("synthetic_exceptions", "exceptions", "passed", 20, 20, 0.1, "SYNTHETIC demonstration value"),
                    TestResult("synthetic_speed", "performance", "failed" if failed else "passed", 0 if failed else 10, 10, 0.3, "SYNTHETIC demonstration value"),
                    TestResult("memory", "performance", "skipped", None, 0, detail="Not measured")])
                row = {"id": job_id, "attempt_id": "synthetic", "model": model, "effort": effort, "repeat": repeat,
                       "status": "unsupported" if unsupported else "timed_out" if failed else "succeeded", "success": not failed and not unsupported,
                       "started_at": "2026-10-23T00:00:00+00:00", "ended_at": "2026-10-23T00:01:00+00:00",
                       "generation_seconds": 35 + repeat * 5 if effort == "low" else 75 + repeat * 5,
                       "grading_seconds": 2.0, "duration_seconds": 60.0, "codex_version": "SYNTHETIC (not executed)",
                       "exit_code": 1 if failed or unsupported else 0, "usage": usage, "grade": grade,
                       "error_type": "unsupported_combination" if unsupported else "timeout" if failed else None,
                       "usage_source": None if unsupported else "turn_final", "warnings": ["All values are synthetic"],
                       "artifact_path": f"runs/{job_id}/attempts/synthetic"}
                attempt = output / row["artifact_path"]
                attempt.mkdir(parents=True, exist_ok=True)
                event = {"type": "turn.failed", "error": {"message": "SYNTHETIC unsupported combination"}} if unsupported else {"type": "turn.completed", "usage": usage}
                import json
                for name in ["stdout.log", "events.jsonl"]:
                    (attempt / name).write_text(json.dumps(event) + "\n", encoding="utf-8")
                (attempt / "stderr.log").write_text("SYNTHETIC sample\n", encoding="utf-8")
                (attempt / "final_message.md").write_text("Synthetic sample; no model was executed.\n", encoding="utf-8")
                (attempt / "submission").mkdir(exist_ok=True)
                (attempt / "submission" / "README.md").write_text("Synthetic artifact only; no generated code.\n", encoding="utf-8")
                write_json(attempt / "result.json", row)
                rows.append(row)
    write_reports(output, rows, {"experiment_id": "synthetic-offline-sample", "synthetic": True})
    print(output / "report.html")


if __name__ == "__main__":
    main()
