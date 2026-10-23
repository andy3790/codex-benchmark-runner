"""Offline simulator used by the GUI demo; never executes Codex or candidate code."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


def main():
    workspace, model, effort, repeat = sys.argv[1:5]
    project = Path(workspace)
    prompt = sys.stdin.buffer.read().decode("utf-8")
    (project / "received-prompt.txt").write_text(prompt, encoding="utf-8", newline="\n")
    seed = int(hashlib.sha256(f"{model}/{effort}/{repeat}".encode()).hexdigest()[:8], 16)

    def emit(event):
        print(json.dumps(event, ensure_ascii=True), flush=True)

    emit({"type": "thread.started", "thread_id": "simulated-" + project.parent.name})
    emit({"type": "session.configured", "model": model, "reasoning_effort": effort})
    emit({"type": "turn.started"})
    time.sleep(0.25)
    if effort == "unsupported":
        emit({"type": "error", "message": "SIMULATED unsupported reasoning effort"})
        return 1
    (project / "solution.py").write_text("# SIMULATED artifact. This is not a model implementation.\n", encoding="utf-8")
    (project / "simulation.json").write_text(json.dumps({"simulation": True, "algorithm_ok": seed % 4 != 0,
                                                        "exceptions_ok": seed % 5 != 0}), encoding="utf-8")
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": "SIMULATION ONLY: no model was called; candidate code was not executed."}})
    output = 100 + seed % 400
    emit({"type": "turn.completed", "usage": {"input_tokens": 800 + seed % 1000, "cached_input_tokens": seed % 300,
                                                  "output_tokens": output, "reasoning_output_tokens": output // 3}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
