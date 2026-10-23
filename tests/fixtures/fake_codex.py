"""TRUSTED model simulator: never calls Codex/network or executes candidate code."""
import json
import sys
import time
from pathlib import Path

workspace = Path(sys.argv[1])
model = sys.argv[2]
effort = sys.argv[3]
prompt = sys.stdin.buffer.read().decode("utf-8")
(workspace / "received-prompt.txt").write_text(prompt, encoding="utf-8")
(workspace / "solution.py").write_text("# Simulated generated artifact. Never executed.\n", encoding="utf-8")
print(json.dumps({"type": "thread.started", "thread_id": "fake-" + workspace.parent.name}), flush=True)
print(json.dumps({"type": "session.configured", "model": model, "reasoning_effort": effort}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
if model == "slow":
    time.sleep(60)
if effort == "unsupported":
    print(json.dumps({"type": "error", "message": "Unsupported reasoning effort"}), flush=True)
    raise SystemExit(1)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Fixture completed."}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 200, "cached_input_tokens": 50, "output_tokens": 40, "reasoning_output_tokens": 10}}), flush=True)
