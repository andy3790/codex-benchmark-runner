"""Conservative usage parsing: totals > per-turn final values > explicit deltas.

Unknown shapes are retained in the raw trace, never guessed as zero.
"""
from __future__ import annotations

import json
from pathlib import Path

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def normalize(value: dict) -> dict:
    aliases = {"input_tokens": ("input_tokens", "prompt_tokens"),
               "output_tokens": ("output_tokens", "completion_tokens"),
               "cached_input_tokens": ("cached_input_tokens",),
               "reasoning_output_tokens": ("reasoning_output_tokens", "reasoning_tokens")}
    result = {}
    for field, keys in aliases.items():
        number = next((value[k] for k in keys if k in value), None)
        if number is None and field == "cached_input_tokens":
            detail = value.get("input_tokens_details", value.get("prompt_tokens_details", {}))
            number = detail.get("cached_tokens") if isinstance(detail, dict) else None
        if number is None and field == "reasoning_output_tokens":
            detail = value.get("output_tokens_details", value.get("completion_tokens_details", {}))
            number = detail.get("reasoning_tokens") if isinstance(detail, dict) else None
        result[field] = number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else None
    return result


def combine(items: list[dict]) -> dict:
    # Missing in any turn means the session-wide value is unknown.
    return {k: sum(x[k] for x in items) if items and all(x[k] is not None for x in items) else None for k in FIELDS}


def parse_events(path: Path) -> dict:
    turns, deltas, cumulative = [], [], None
    seen, turn_ids = set(), set()
    malformed = 0
    errors, warnings, observations = [], [], []
    final, thread_id, current_turn, completed = "", None, None, False
    current_index = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError()
        except (ValueError, TypeError):
            malformed += 1
            continue
        # Only event IDs are deduplicated. Identical usage in distinct turns is legitimate.
        event_id = event.get("event_id")
        if event_id is not None:
            if event_id in seen:
                continue
            seen.add(event_id)
        kind = event.get("type", "")
        if kind == "turn.completed":
            completed = True
        if kind == "thread.started":
            thread_id = event.get("thread_id")
        if kind == "turn.started":
            current_index += 1
            current_turn = event.get("turn_id", f"sequence-{current_index}")
        if kind == "item.completed" and isinstance(event.get("item"), dict):
            if event["item"].get("type") == "agent_message":
                final = event["item"].get("text", "")
        if kind in {"session.configured", "session.started", "session_meta", "turn_context"}:
            info = event.get("payload", event)
            if isinstance(info, dict):
                observations.append({"source": kind, "model": info.get("model"),
                                     "reasoning_effort": info.get("reasoning_effort", info.get("effort"))})
        if kind in {"error", "turn.failed"}:
            errors.append(event.get("error", event.get("message", event)))
        usage = event.get("usage")
        # Raw rollout token_count explicitly distinguishes total/last; never sum both.
        payload = event.get("payload", {})
        if kind == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            if isinstance(info.get("total_token_usage"), dict):
                cumulative = normalize(info["total_token_usage"])
            continue
        if not isinstance(usage, dict):
            if kind == "turn.completed":
                completed = True
                turns.append(normalize({}))
            continue
        scope = event.get("usage_scope", usage.get("scope"))
        if kind in {"session.completed", "usage.total"} or scope in {"cumulative", "total", "session"}:
            cumulative = normalize(usage)
        elif kind == "turn.completed":
            completed = True
            key = event.get("turn_id", current_turn)
            if key is not None and key in turn_ids:
                continue
            if key is not None:
                turn_ids.add(key)
            turns.append(normalize(usage))
        elif kind == "usage.delta" or scope in {"delta", "incremental"}:
            deltas.append(normalize(usage))
        else:
            warnings.append(f"Unrecognized usage scope on {kind!r}; not aggregated")
    source = "session_cumulative" if cumulative is not None else "turn_final" if turns else "explicit_delta" if deltas else None
    totals = cumulative if cumulative is not None else combine(turns or deltas)
    if malformed:
        warnings.append("Malformed JSONL lines; measured usage can be partial")
    totals["total_tokens"] = (totals["input_tokens"] + totals["output_tokens"]
                              if totals["input_tokens"] is not None and totals["output_tokens"] is not None else None)
    if totals["cached_input_tokens"] is not None and totals["input_tokens"] is not None and totals["cached_input_tokens"] > totals["input_tokens"]:
        warnings.append("cached_input_tokens exceeds input_tokens")
    if totals["reasoning_output_tokens"] is not None and totals["output_tokens"] is not None and totals["reasoning_output_tokens"] > totals["output_tokens"]:
        warnings.append("reasoning_output_tokens exceeds output_tokens")
    return {"usage": totals, "usage_source": source, "completed_event": completed,
            "thread_id": thread_id, "observed_configuration": observations, "final_message": final,
            "errors": errors, "warnings": warnings, "malformed_lines": malformed}
