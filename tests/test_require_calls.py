"""Coverage guards for CI workflows that expect a captured tool invocation."""

import json
import unittest

from agent_trace_check.core import check_lines
from test_cli import cli
from test_core import assistant, call, check, codes, end, message_call, result, tool


class RequireCallsTests(unittest.TestCase):
    def test_empty_or_blank_input_fails_only_when_required(self):
        for lines in ([], ["\n", "  \n"]):
            with self.subTest(lines=lines):
                self.assertEqual(check_lines(lines).status, "clean")
                report = check_lines(lines, require_calls=True)
                self.assertEqual(report.status, "invalid")
                self.assertEqual(report.exit_code, 2)
                self.assertEqual([error.code for error in report.errors], ["no_tool_calls"])
                self.assertIsNone(report.errors[0].line)

    def test_ignored_chat_messages_do_not_satisfy_requirement(self):
        report = check({"role": "user", "content": "hello"},
                       {"role": "assistant", "content": "hello", "tool_calls": None},
                       input_format="chat-messages", require_calls=True)
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.ignored_messages, 2)
        self.assertEqual(report.calls, 0)

    def test_trace_end_is_not_tool_coverage(self):
        report = check(end(), require_calls=True)
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.events, 1)
        self.assertEqual(report.traces, 1)

    def test_call_after_trace_end_does_not_satisfy_requirement(self):
        report = check(end(), call(), require_calls=True)
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(codes(report), ["event_after_trace_end"])
        self.assertEqual(report.calls, 0)

    def test_orphan_result_does_not_satisfy_requirement(self):
        report = check(result(), require_calls=True)
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(codes(report), ["orphan_result"])

    def test_malformed_batch_does_not_satisfy_requirement(self):
        report = check(assistant(message_call(), {"type": "custom", "id": "b"}),
                       input_format="chat-messages", require_calls=True)
        self.assertEqual(report.calls, 0)
        self.assertEqual([error.code for error in report.errors],
                         ["malformed_record", "no_tool_calls"])

    def test_normalized_call_pair_satisfies_requirement(self):
        report = check(call(), result(), end(), require_calls=True)
        self.assertEqual(report.status, "clean")
        self.assertEqual(report.matched_results, 1)

    def test_chat_call_pair_satisfies_requirement(self):
        report = check(assistant(message_call()), tool(),
                       input_format="chat-messages", require_calls=True)
        self.assertEqual(report.status, "clean")
        self.assertEqual(report.matched_results, 1)

    def test_guard_does_not_hide_missing_result(self):
        report = check(call(), require_calls=True)
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(codes(report), ["missing_result"])
        self.assertEqual(report.errors, [])

    def test_pending_call_satisfies_requirement(self):
        report = check(call(), allow_incomplete=True, require_calls=True)
        self.assertEqual(report.status, "incomplete")
        self.assertEqual(report.exit_code, 0)

    def test_requirement_is_whole_input_not_per_trace(self):
        report = check(end(trace="no-tools"), call(trace="tools"),
                       result(trace="tools"), require_calls=True)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.traces, 2)

    def test_cli_empty_stdin_json_error(self):
        process = cli("-", "--require-calls", "--json", data=b"")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stderr, b"")
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["summary"]["calls"], 0)
        self.assertEqual(report["errors"][0]["code"], "no_tool_calls")

    def test_cli_empty_stdin_human_error(self):
        process = cli("-", "--require-calls", data=b"")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stderr, b"")
        self.assertIn(b"no_tool_calls", process.stdout)

    def test_cli_ignored_chat_stdin_fails(self):
        process = cli("-", "--format", "chat-messages", "--require-calls", "--json",
                      data=b'{"role":"user","content":"PRIVATE_PAYLOAD"}\n')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["summary"]["ignored_messages"], 1)
        self.assertNotIn(b"PRIVATE_PAYLOAD", process.stdout)

    def test_cli_clean_file_passes(self):
        process = cli("examples/clean.jsonl", "--require-calls", "--json")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["status"], "clean")

    def test_cli_allow_incomplete_does_not_allow_empty_input(self):
        process = cli("-", "--require-calls", "--allow-incomplete", "--json", data=b"")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
