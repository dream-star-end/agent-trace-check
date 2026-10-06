"""Synthetic regressions inspired by public issue structures, not copied logs.

Sources:
https://github.com/CherryHQ/cherry-studio/issues/14128
https://github.com/anomalyco/opencode/issues/24090

Controls also probe input scope and partial-snapshot boundaries. These tests
make no assertion that any released upstream product currently has the bug.
"""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from agent_trace_check.core import check_lines

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "public_patterns"


def report(name, **kwargs):
    with (FIXTURES / name).open(encoding="utf-8") as stream:
        return check_lines(stream, input_format="chat-messages", **kwargs)


def codes(value):
    return [item.code for item in value.findings]


class PublicPatternTests(unittest.TestCase):
    def test_cherry_duplicate_call_and_result_shape(self):
        value = report("cherry_duplicate_ids.jsonl")
        self.assertEqual(codes(value), ["duplicate_call_id", "duplicate_result"])
        self.assertEqual(value.exit_code, 1)
        self.assertEqual(value.errors, [])
        self.assertEqual(value.pending, [])
        self.assertEqual(value.matched_results, 1)
        self.assertEqual([(d.line, d.related_line) for d in value.findings], [(1, 1), (3, 2)])

    def test_cherry_distinct_parallel_ids_no_false_positive(self):
        value = report("cherry_distinct_parallel_ids.jsonl")
        self.assertEqual(value.status, "clean")
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.matched_results, 2)

    def test_cherry_deduplicated_control_clean(self):
        value = report("cherry_deduplicated.jsonl")
        self.assertEqual(value.status, "clean")
        self.assertEqual(value.matched_results, 1)

    def test_opencode_assistant_calls_missing_but_result_retained(self):
        value = report("opencode_missing_assistant_calls.jsonl")
        self.assertEqual(codes(value), ["orphan_result"])
        self.assertEqual(value.exit_code, 1)
        self.assertEqual(value.calls, 0)
        self.assertEqual(value.results, 1)
        self.assertEqual(value.errors, [])
        self.assertEqual(value.findings[0].line, 2)

    def test_opencode_restored_calls_control_clean(self):
        value = report("opencode_restored_assistant_calls.jsonl")
        self.assertEqual(value.status, "clean")
        self.assertEqual(value.matched_results, 1)

    def test_allow_incomplete_does_not_hide_orphans(self):
        value = report("opencode_missing_assistant_calls.jsonl", allow_incomplete=True)
        self.assertEqual(codes(value), ["orphan_result"])
        self.assertEqual(value.exit_code, 1)
        self.assertEqual(value.pending, [])

    def test_allow_incomplete_does_not_hide_duplicates(self):
        value = report("cherry_duplicate_ids.jsonl", allow_incomplete=True)
        self.assertEqual(codes(value), ["duplicate_call_id", "duplicate_result"])
        self.assertEqual(value.exit_code, 1)

    def test_pending_parallel_call_is_allowed_for_snapshot(self):
        value = report("parallel_snapshot_incomplete.jsonl", allow_incomplete=True)
        self.assertEqual(value.status, "incomplete")
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.findings, [])
        self.assertEqual([d.call_id for d in value.pending], ["case-a"])

    def test_same_snapshot_requires_result_when_assumed_complete(self):
        value = report("parallel_snapshot_incomplete.jsonl")
        self.assertEqual(codes(value), ["missing_result"])
        self.assertEqual(value.exit_code, 1)

    def test_flattened_replayed_requests_are_not_an_event_stream(self):
        # A known scope hazard, not another upstream regression: the same
        # legitimate history appears twice in two separate request snapshots.
        value = report("replayed_snapshots_flattened.jsonl")
        self.assertEqual(codes(value), ["duplicate_call_id", "duplicate_result"])
        self.assertEqual(value.exit_code, 1)

    def test_per_request_scope_avoids_replay_false_positive(self):
        value = report("replayed_snapshots_scoped.jsonl")
        self.assertEqual(value.status, "clean")
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.traces, 2)
        self.assertEqual(value.matched_results, 2)

    def test_json_cli_matches_pattern_expectations(self):
        cases = [
            ("cherry_duplicate_ids.jsonl", 1, ["duplicate_call_id", "duplicate_result"]),
            ("cherry_distinct_parallel_ids.jsonl", 0, []),
            ("cherry_deduplicated.jsonl", 0, []),
            ("opencode_missing_assistant_calls.jsonl", 1, ["orphan_result"]),
            ("opencode_restored_assistant_calls.jsonl", 0, []),
            ("replayed_snapshots_scoped.jsonl", 0, []),
        ]
        for name, expected_code, expected_findings in cases:
            with self.subTest(fixture=name):
                process = subprocess.run(
                    [sys.executable, "-m", "agent_trace_check", str(FIXTURES / name),
                     "--format", "chat-messages", "--json"],
                    cwd=ROOT, capture_output=True, text=True, check=False)
                self.assertEqual(process.returncode, expected_code, process.stderr)
                data = json.loads(process.stdout)
                self.assertEqual([item["code"] for item in data["findings"]], expected_findings)
                self.assertEqual(data["errors"], [])
                self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
