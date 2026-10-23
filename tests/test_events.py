import json
import tempfile
import unittest
from pathlib import Path

from codex_bench.events import parse_events
from tests.helpers import FIXTURES


class EventTests(unittest.TestCase):
    def parse(self, events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
            return parse_events(path)

    def test_turn_final_not_delta_or_replay(self):
        parsed = parse_events(FIXTURES / "turns.jsonl")
        self.assertEqual(parsed["usage"], {"input_tokens": 200, "cached_input_tokens": 80, "output_tokens": 40, "reasoning_output_tokens": 16, "total_tokens": 240})
        self.assertEqual(parsed["usage_source"], "turn_final")
        self.assertEqual(parsed["final_message"], "Implemented fixture project.")

    def test_last_cumulative_wins_without_adding_turns(self):
        parsed = parse_events(FIXTURES / "cumulative.jsonl")
        self.assertEqual(parsed["usage"]["total_tokens"], 285)
        self.assertEqual(parsed["usage_source"], "session_cumulative")

    def test_missing_metrics_are_null(self):
        parsed = self.parse([{"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}}])
        self.assertIsNone(parsed["usage"]["reasoning_output_tokens"])
        self.assertIsNone(parsed["usage"]["cached_input_tokens"])
        self.assertEqual(parsed["usage"]["total_tokens"], 15)

    def test_partial_metric_in_multiple_turns_is_unknown(self):
        parsed = self.parse([{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}, {"type": "turn.completed", "usage": {"input_tokens": 10}}])
        self.assertEqual(parsed["usage"]["input_tokens"], 20)
        self.assertIsNone(parsed["usage"]["total_tokens"])

    def test_explicit_deltas_deduplicate_event_id(self):
        event = {"type": "usage.delta", "event_id": "x", "usage": {"input_tokens": 10, "output_tokens": 2}}
        parsed = self.parse([event, event, {**event, "event_id": "y"}])
        self.assertEqual(parsed["usage"]["total_tokens"], 24)

    def test_unknown_usage_is_not_guessed(self):
        parsed = self.parse([{"type": "some.usage", "usage": {"input_tokens": 100}}])
        self.assertIsNone(parsed["usage"]["input_tokens"])
        self.assertTrue(parsed["warnings"])

    def test_details_and_zero(self):
        parsed = self.parse([{"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}])
        self.assertEqual(parsed["usage"]["total_tokens"], 0)
        self.assertEqual(parsed["usage"]["reasoning_output_tokens"], 0)

    def test_malformed_lines_are_retained_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw"
            path.write_bytes(b'broken\n[]\n{"type":"turn.completed"}\n')
            self.assertEqual(parse_events(path)["malformed_lines"], 2)
            self.assertIn(b"broken", path.read_bytes())

    def test_invalid_numbers_not_coerced(self):
        result = self.parse([{"type": "turn.completed", "usage": {"input_tokens": True, "output_tokens": -1, "output_tokens_details": None}}])
        self.assertIsNone(result["usage"]["input_tokens"])
        self.assertIsNone(result["usage"]["total_tokens"])

    def test_cumulative_turn_is_completed(self):
        result = self.parse([{"type": "turn.completed", "usage_scope": "cumulative", "usage": {"input_tokens": 3, "output_tokens": 2}}])
        self.assertTrue(result["completed_event"])
