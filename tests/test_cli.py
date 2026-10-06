import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, data=None):
    return subprocess.run([sys.executable, "-m", "agent_trace_check", *args],
                          input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=ROOT, check=False)


class CLITests(unittest.TestCase):
    def test_clean_example(self):
        process = cli("examples/clean.jsonl")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn(b"CLEAN", process.stdout)

    def test_findings_example(self):
        process = cli("examples/broken.jsonl", "--json")
        self.assertEqual(process.returncode, 1)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "findings")
        self.assertEqual(process.stderr, b"")

    def test_incomplete_example(self):
        process = cli("examples/incomplete.jsonl", "--allow-incomplete", "--json")
        self.assertEqual(process.returncode, 0)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["summary"]["pending"], 1)

    def test_chat_example(self):
        self.assertEqual(cli("examples/chat-messages.jsonl", "--format", "chat-messages").returncode, 0)

    def test_stdin(self):
        process = cli("-", "--json", data=b'{"type":"trace_end"}\n')
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["summary"]["records"], 1)

    def test_stdin_unicode(self):
        process = cli("-", "--json", data='{"type":"tool_call","call_id":"调用","name":"查询"}\n'.encode())
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(process.stdout)["findings"][0]["call_id"], "调用")

    def test_invalid_json_exit_two(self):
        process = cli("-", "--json", data=b'not-json\n')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "invalid")

    def test_invalid_utf8_stdin(self):
        process = cli("-", "--json", data=b'\xff\n')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["errors"][0]["code"], "input_error")

    def test_invalid_utf8_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.jsonl"
            path.write_bytes(b"\xff")
            process = cli(str(path), "--json")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "invalid")

    def test_missing_file_exit_two(self):
        process = cli("no-such-input.jsonl", "--json")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["errors"][0]["code"], "input_error")

    def test_bad_usage_exit_two(self):
        process = cli("-", "--format", "unknown")
        self.assertEqual(process.returncode, 2)
        self.assertIn(b"usage:", process.stderr)

    def test_version(self):
        process = cli("--version")
        self.assertEqual(process.returncode, 0)
        self.assertIn(b"0.1.0", process.stdout)

    def test_directory_is_input_error(self):
        process = cli("examples", "--json")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "invalid")

    @unittest.skipUnless(os.name == "posix", "requires POSIX file descriptors")
    def test_closed_stdout_pipe_exits_without_traceback(self):
        reader, writer = os.pipe()
        os.close(reader)
        try:
            process = subprocess.run(
                [sys.executable, "-m", "agent_trace_check", "examples/clean.jsonl"],
                stdout=writer, stderr=subprocess.PIPE, cwd=ROOT, check=False)
        finally:
            os.close(writer)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stderr, b"")


if __name__ == "__main__":
    unittest.main()
