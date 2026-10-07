"""Export one saved Chat Completions request's messages, using only stdlib.

This example does not capture traffic, redact content, repair messages, or add
a provider adapter. Read docs/captured-request.md before using private data.
"""

import argparse
import json
import os
from pathlib import Path
import sys


class RequestError(ValueError):
    """A payload-free explanation of an invalid request snapshot."""


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RequestError("JSON object contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise RequestError("NaN and Infinity are not valid JSON")


def request_to_jsonl(text: str) -> str:
    """Validate the whole snapshot before producing any output records."""
    try:
        request = json.loads(text, object_pairs_hook=_unique_keys,
                             parse_constant=_reject_constant)
        if not isinstance(request, dict):
            raise RequestError("request must be a top-level JSON object")
        messages = request.get("messages")
        if not isinstance(messages, list):
            raise RequestError("request.messages must be an array")
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise RequestError(f"request.messages[{index}] must be an object")
        # Preserve every message and its order. JSON string newlines are escaped
        # so each message occupies exactly one physical line. The checker, not
        # this converter, validates supported roles and tool-call fields.
        return "".join(json.dumps(message, ensure_ascii=True, allow_nan=False)
                       + "\n" for message in messages)
    except RequestError:
        raise
    except json.JSONDecodeError as exc:
        raise RequestError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}") from None
    except (ValueError, RecursionError, OverflowError):
        raise RequestError("JSON nesting or number exceeds supported limits") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export messages from one saved JSON request into a new JSONL file.",
        epilog="Exit: 0 exported; 2 invalid input, file error, or usage error.")
    parser.add_argument("request", type=Path, help="UTF-8 JSON request object")
    parser.add_argument("output", type=Path, help="new output file (never overwritten)")
    args = parser.parse_args(argv)
    try:
        text = args.request.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("request_to_jsonl: could not read input as a UTF-8 file", file=sys.stderr)
        return 2
    try:
        exported = request_to_jsonl(text)
    except RequestError as exc:
        print(f"request_to_jsonl: {exc}", file=sys.stderr)
        return 2
    descriptor = None
    try:
        # Restrict POSIX permissions at creation, before any payload is written.
        # O_EXCL refuses existing files, including symlinks and hard-link aliases.
        # On Windows, access also depends on the destination directory's ACL.
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None  # The stream now owns and closes the descriptor.
        with stream:
            stream.write(exported)
    except (OSError, UnicodeError):
        print("request_to_jsonl: could not write output; use a new writable filename",
              file=sys.stderr)
        return 2
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
