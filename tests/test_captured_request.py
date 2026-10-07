"""Offline request-export recipe tests using synthetic public-pattern shapes.

No upstream applications or provider APIs are executed. Source attribution and
diagnostic boundaries are documented in docs/captured-request.md.
"""

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_trace_check.core import check_lines

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "request_to_jsonl.py"
FIXTURES = ROOT / "examples" / "requests"
spec = importlib.util.spec_from_file_location("request_to_jsonl_example", SCRIPT)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def convert_fixture(name):
    return example.request_to_jsonl((FIXTURES / name).read_text(encoding="utf-8"))


def check_export(text, **kwargs):
    return check_lines(text.splitlines(), input_format="chat-messages", **kwargs)


class RequestConversionTests(unittest.TestCase):
    def test_preserves_messages_and_order_omits_envelope(self):
        messages = [
            {"role": "system", "content": "Synthetic context."},
            {"role": "user", "content": "Synthetic line one\n第二行", "metadata": {"x": 2}},
            {"role": "assistant", "content": None, "tool_calls": []},
        ]
        exported = example.request_to_jsonl(json.dumps({
            "model": "synthetic-model", "stream": True, "messages": messages,
            "synthetic_envelope_only": "not a message field",
        }))
        self.assertEqual([json.loads(line) for line in exported.splitlines()], messages)
        self.assertEqual(len(exported.splitlines()), 3)
        self.assertNotIn("synthetic_envelope_only", exported)
        self.assertTrue(exported.endswith("\n"))

    def test_stream_setting_does_not_change_request_messages(self):
        request = json.loads((FIXTURES / "parallel-complete.json").read_text())
        streaming = example.request_to_jsonl(json.dumps(request))
        request["stream"] = False
        self.assertEqual(example.request_to_jsonl(json.dumps(request)), streaming)

    def test_top_level_must_be_object(self):
        for value in (None, True, 1, "request", [], [{"role": "user"}]):
            with self.subTest(value=value), self.assertRaisesRegex(
                    example.RequestError, "top-level JSON object"):
                example.request_to_jsonl(json.dumps(value))

    def test_messages_must_be_array(self):
        for request in ({}, {"messages": None}, {"messages": {}},
                        {"messages": "[]"}, {"messages": True}):
            with self.subTest(request=request), self.assertRaisesRegex(
                    example.RequestError, "messages must be an array"):
                example.request_to_jsonl(json.dumps(request))

    def test_every_message_must_be_object_before_export(self):
        for bad in (None, True, 1, "message", []):
            request = {"messages": [{"role": "user", "content": "synthetic"}, bad]}
            with self.subTest(bad=bad), self.assertRaisesRegex(
                    example.RequestError, r"messages\[1\] must be an object"):
                example.request_to_jsonl(json.dumps(request))

    def test_empty_and_text_only_requests_do_not_establish_coverage(self):
        for messages in ([], [{"role": "user", "content": "Synthetic text."}]):
            with self.subTest(messages=messages):
                value = check_export(example.request_to_jsonl(json.dumps({"messages": messages})))
                self.assertEqual(value.status, "clean")
                self.assertEqual(value.calls, 0)
                self.assertEqual(value.matched_results, 0)

    def test_duplicate_keys_are_not_silently_replaced(self):
        cases = [
            '{"messages": [], "messages": []}',
            '{"messages": [{"role": "user", "role": "tool"}]}',
            '{"messages": [], "metadata": {"secret-sentinel": 1, "secret-sentinel": 2}}',
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaisesRegex(
                    example.RequestError, "duplicate key") as raised:
                example.request_to_jsonl(text)
            self.assertNotIn("secret-sentinel", str(raised.exception))

    def test_nonfinite_constants_rejected_in_any_field(self):
        for number in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(number=number), self.assertRaisesRegex(
                    example.RequestError, "not valid JSON"):
                example.request_to_jsonl('{"messages": [], "metadata": ' + number + '}')

    def test_unrepresentable_message_numbers_rejected_before_writing(self):
        with self.assertRaisesRegex(example.RequestError, "supported limits"):
            example.request_to_jsonl('{"messages": [{"role": "user", "metadata": 1e400}]}')

    def test_parser_recursion_errors_are_payload_free(self):
        # Tolerated nesting depth differs between Python versions. Exercise the
        # error contract for both parsing and serialization without a fixed cap.
        for operation in ("loads", "dumps"):
            with self.subTest(operation=operation), patch.object(
                    example.json, operation, side_effect=RecursionError("private-sentinel")):
                with self.assertRaisesRegex(example.RequestError, "supported limits") as raised:
                    example.request_to_jsonl('{"messages": [{"role": "user", '
                                             '"content": "private-sentinel"}]}')
                self.assertNotIn("private-sentinel", str(raised.exception))

    def test_malformed_json_errors_do_not_include_payload(self):
        for text in ('{"private-sentinel": ]}', '', '\ufeff{"messages": []}'):
            with self.subTest(text=text), self.assertRaisesRegex(
                    example.RequestError, "invalid JSON at line") as raised:
                example.request_to_jsonl(text)
            self.assertNotIn("private-sentinel", str(raised.exception))

    def test_sse_response_envelopes_and_jsonl_are_not_request_inputs(self):
        cases = [
            'data: {"choices": [{"delta": {"content": "synthetic"}}]}\n\n',
            '{"choices": [{"message": {"role": "assistant"}}]}',
            '{"role": "user"}\n{"role": "assistant"}',
            '{"body": {"messages": []}}',
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(example.RequestError):
                example.request_to_jsonl(text)

    def test_message_schema_validation_stays_with_checker(self):
        exported = example.request_to_jsonl('{"messages": [{"role": "unsupported"}]}')
        value = check_export(exported)
        self.assertEqual(value.exit_code, 2)
        self.assertEqual([error.code for error in value.errors], ["malformed_record"])


class RequestRecipeCLITests(unittest.TestCase):
    def run_export(self, input_path, output_path, **kwargs):
        return subprocess.run([sys.executable, str(SCRIPT), str(input_path), str(output_path)],
                              cwd=ROOT, capture_output=True, text=True, check=False, **kwargs)

    def test_all_synthetic_request_shapes_through_export_and_existing_cli(self):
        cases = [
            ("parallel-missing-result.json", 1, 2, 1,
             [("missing_result", 2, None, "parallel-b")]),
            ("parallel-complete.json", 0, 2, 2, []),
            ("paused-history-duplicate.json", 1, 2, 1,
             [("duplicate_call_id", 4, 2, "paused-a")]),
            ("paused-history-once.json", 0, 1, 1, []),
        ]
        for name, exit_code, calls, matches, findings in cases:
            with self.subTest(fixture=name), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "messages.jsonl"
                export = self.run_export(FIXTURES / name, output)
                self.assertEqual(export.returncode, 0, export.stderr)
                self.assertEqual(export.stdout, "")
                self.assertEqual(export.stderr, "")
                request = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
                self.assertEqual([json.loads(line) for line in output.read_text().splitlines()],
                                 request["messages"])
                process = subprocess.run([
                    sys.executable, "-m", "agent_trace_check", str(output),
                    "--format", "chat-messages", "--json"],
                    cwd=ROOT, capture_output=True, text=True, check=False)
                self.assertEqual(process.returncode, exit_code, process.stderr)
                self.assertEqual(process.stderr, "")
                report = json.loads(process.stdout)
                self.assertEqual(report["exit_code"], exit_code)
                self.assertEqual(report["status"], "findings" if findings else "clean")
                self.assertEqual(report["summary"]["calls"], calls)
                self.assertEqual(report["summary"]["matched_results"], matches)
                self.assertEqual(report["summary"]["records"], len(request["messages"]))
                self.assertEqual(report["errors"], [])
                self.assertEqual(report["pending"], [])
                self.assertEqual([(d["code"], d["line"], d["related_line"], d["call_id"])
                                  for d in report["findings"]], findings)

    def test_invalid_input_does_not_create_partial_output(self):
        cases = [b'{"messages": [{"role": "user"}, null]}',
                 b'{"messages": [{"role": "user"}]} trailing-private-sentinel',
                 b'\xff']
        for content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "request.json"
                output = Path(directory) / "messages.jsonl"
                source.write_bytes(content)
                process = self.run_export(source, output)
                self.assertEqual(process.returncode, 2)
                self.assertFalse(output.exists())
                self.assertEqual(process.stdout, "")
                self.assertNotIn("private-sentinel", process.stderr)
                self.assertNotIn("Traceback", process.stderr)

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "messages.jsonl"
            output.write_text("existing synthetic data\n", encoding="utf-8")
            if os.name == "posix":
                output.chmod(0o640)
            original_mode = stat.S_IMODE(output.stat().st_mode)
            process = self.run_export(FIXTURES / "parallel-complete.json", output)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(output.read_text(), "existing synthetic data\n")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), original_mode)

    @unittest.skipUnless(os.name == "posix", "POSIX file-mode regression")
    def test_new_export_is_owner_only_even_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "private-request.json"
            content = (FIXTURES / "parallel-complete.json").read_bytes()
            source.write_bytes(content)
            source.chmod(0o600)
            for mask in (0o022, 0o000, 0o077):
                with self.subTest(umask=oct(mask)):
                    output = root / f"messages-{mask:o}.jsonl"
                    process = self.run_export(source, output, umask=mask)
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
                    self.assertEqual(source.read_bytes(), content)
                    self.assertEqual(check_export(output.read_text()).status, "clean")

    @unittest.skipUnless(os.name == "posix", "POSIX symlink regression")
    def test_existing_and_dangling_output_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for existing in (True, False):
                with self.subTest(target_exists=existing):
                    target = root / f"target-{existing}.jsonl"
                    if existing:
                        target.write_text("existing synthetic data\n")
                        target.chmod(0o640)
                    output = root / f"link-{existing}.jsonl"
                    output.symlink_to(target)
                    process = self.run_export(FIXTURES / "parallel-complete.json", output)
                    self.assertEqual(process.returncode, 2)
                    self.assertTrue(output.is_symlink())
                    if existing:
                        self.assertEqual(target.read_text(), "existing synthetic data\n")
                        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
                    else:
                        self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX hard-link regression")
    def test_output_hard_link_to_input_is_refused_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "private-request.json"
            content = (FIXTURES / "parallel-complete.json").read_bytes()
            source.write_bytes(content)
            source.chmod(0o600)
            output = root / "hard-link.jsonl"
            os.link(source, output)
            process = self.run_export(source, output)
            self.assertEqual(process.returncode, 2)
            self.assertTrue(source.samefile(output))
            self.assertEqual(source.read_bytes(), content)
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)

    def test_input_is_not_overwritten_when_output_path_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "request.json"
            content = '{"messages": [{"role": "user", "content": "synthetic"}]}'
            source.write_text(content)
            process = self.run_export(source, source)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(source.read_text(), content)

    def test_missing_or_directory_input_and_output_errors_are_controlled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in (root / "missing.json", root):
                with self.subTest(source=source):
                    process = self.run_export(source, root / "output.jsonl")
                    self.assertEqual(process.returncode, 2)
                    self.assertFalse((root / "output.jsonl").exists())
                    self.assertNotIn("Traceback", process.stderr)
            process = self.run_export(FIXTURES / "parallel-complete.json",
                                      root / "missing-parent" / "output.jsonl")
            self.assertEqual(process.returncode, 2)
            self.assertNotIn("Traceback", process.stderr)

    def test_usage_error_is_exit_two(self):
        process = subprocess.run([sys.executable, str(SCRIPT)],
                                 cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, "")


class RequestBoundaryTests(unittest.TestCase):
    def test_parallel_control_returns_in_reverse_order(self):
        exported = convert_fixture("parallel-complete.json")
        messages = [json.loads(line) for line in exported.splitlines()]
        self.assertEqual([m["tool_call_id"] for m in messages if m["role"] == "tool"],
                         ["parallel-b", "parallel-a"])
        self.assertEqual(check_export(exported).status, "clean")

    def test_incomplete_mode_can_hide_missing_result_as_pending(self):
        value = check_export(convert_fixture("parallel-missing-result.json"),
                             allow_incomplete=True)
        self.assertEqual(value.status, "incomplete")
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.findings, [])
        self.assertEqual([item.call_id for item in value.pending], ["parallel-b"])

    def test_incomplete_mode_does_not_hide_duplicate_history(self):
        value = check_export(convert_fixture("paused-history-duplicate.json"),
                             allow_incomplete=True)
        self.assertEqual(value.exit_code, 1)
        self.assertEqual([item.code for item in value.findings], ["duplicate_call_id"])
        self.assertEqual(value.pending, [])

    def test_separate_valid_snapshots_must_not_be_concatenated(self):
        exported = convert_fixture("paused-history-once.json")
        self.assertEqual(check_export(exported).status, "clean")
        self.assertEqual(check_export(exported).status, "clean")
        flattened = check_export(exported + exported)
        self.assertEqual([item.code for item in flattened.findings],
                         ["duplicate_call_id", "duplicate_result"])


if __name__ == "__main__":
    unittest.main()
