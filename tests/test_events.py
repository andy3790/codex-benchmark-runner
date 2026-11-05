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

    def test_same_turn_missing_usage_replay_preserves_or_completes_usage(self):
        usage = {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2, "reasoning_output_tokens": 1}
        for missing_first in (False, True):
            for via_started in (False, True):
                for missing in ({}, {"usage": None}, {"usage": {}}):
                    with self.subTest(missing_first=missing_first, via_started=via_started, missing=missing):
                        common = {"type": "turn.completed", **({} if via_started else {"turn_id": "t1"})}
                        known, absent = {**common, "usage": usage}, {**common, **missing}
                        events = [{"type": "turn.started", "turn_id": "t1"}] if via_started else []
                        events.extend([absent, known] if missing_first else [known, absent])
                        parsed = self.parse(events)
                        self.assertEqual(parsed["usage"], {**usage, "total_tokens": 12})
                        self.assertEqual(parsed["usage_source"], "turn_final")
                        self.assertTrue(parsed["completed_event"])
                        self.assertEqual(parsed["warnings"], [])

    def test_duplicate_turns_do_not_hide_a_different_turn_with_missing_usage(self):
        known = {"type": "turn.completed", "turn_id": "t1", "usage": {"input_tokens": 10, "output_tokens": 2}}
        parsed = self.parse([known, {"type": "turn.completed", "turn_id": "t1"},
                             {"type": "turn.completed", "turn_id": "t2"}, known])
        self.assertTrue(all(value is None for value in parsed["usage"].values()))
        self.assertEqual(parsed["usage_source"], "turn_final")

    def test_identical_usage_in_distinct_turns_is_counted_once_per_turn(self):
        first = {"type": "turn.completed", "turn_id": "t1", "usage": {"input_tokens": 10, "output_tokens": 2}}
        second = {**first, "turn_id": "t2"}
        parsed = self.parse([first, second, first, {"type": "turn.completed", "turn_id": "t2"}])
        self.assertEqual(parsed["usage"]["input_tokens"], 20)
        self.assertEqual(parsed["usage"]["output_tokens"], 4)
        self.assertEqual(parsed["usage"]["total_tokens"], 24)

    def test_same_turn_partial_usage_is_completed_without_adding_values(self):
        parsed = self.parse([{"type": "turn.completed", "turn_id": "t1", "usage": {"input_tokens": 10}},
                             {"type": "turn.completed", "turn_id": "t1", "usage": {"input_tokens": 10, "output_tokens": 2}}])
        self.assertEqual(parsed["usage"]["total_tokens"], 12)
        self.assertIsNone(parsed["usage"]["cached_input_tokens"])
        self.assertEqual(parsed["warnings"], [])

    def test_conflicting_turn_usage_remains_unknown_after_more_replays(self):
        original = {"type": "turn.completed", "turn_id": "t1", "usage": {"input_tokens": 10, "output_tokens": 2}}
        conflict = {**original, "usage": {"input_tokens": 12, "output_tokens": 2}}
        for events in ([original, conflict, original], [conflict, original, conflict]):
            with self.subTest(events=events):
                parsed = self.parse(events)
                self.assertIsNone(parsed["usage"]["input_tokens"])
                self.assertIsNone(parsed["usage"]["total_tokens"])
                self.assertEqual(parsed["usage"]["output_tokens"], 2)
                self.assertEqual(parsed["warnings"], ["Conflicting input_tokens for turn 't1'; value left unknown"])

    def test_replayed_turns_keep_cumulative_over_turn_over_delta_precedence(self):
        events = [{"type": "usage.delta", "usage": {"input_tokens": 1, "output_tokens": 1}},
                  {"type": "turn.completed", "turn_id": "t1"},
                  {"type": "turn.completed", "turn_id": "t1", "usage": {"input_tokens": 10, "output_tokens": 2}}]
        parsed = self.parse(events)
        self.assertEqual(parsed["usage"]["total_tokens"], 12)
        self.assertEqual(parsed["usage_source"], "turn_final")
        events.extend([{"type": "usage.total", "usage": {"input_tokens": 30, "output_tokens": 4}},
                       {"type": "turn.completed", "turn_id": "t1"}])
        parsed = self.parse(events)
        self.assertEqual(parsed["usage"]["total_tokens"], 34)
        self.assertEqual(parsed["usage_source"], "session_cumulative")

    def test_replayed_event_id_does_not_change_turn_usage(self):
        event = {"type": "turn.completed", "event_id": "done", "turn_id": "t1", "usage": {"input_tokens": 10, "output_tokens": 2}}
        parsed = self.parse([event, {**event, "usage": {"input_tokens": 999, "output_tokens": 999}}])
        self.assertEqual(parsed["usage"]["total_tokens"], 12)
        self.assertEqual(parsed["warnings"], [])

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
