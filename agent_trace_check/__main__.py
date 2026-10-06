"""Run with python -m agent_trace_check. Reads only local files or stdin."""

import argparse
import json
import os
import sys

from . import __version__
from .core import Diagnostic, FORMATS, Report, check_lines


def render_human(report: Report) -> str:
    lines = [
        f"{report.status.upper()}: {report.calls} calls, {report.matched_results} matched results; "
        f"{len(report.findings)} findings, {len(report.errors)} input errors, "
        f"{len(report.pending)} pending calls",
    ]
    for group in (report.errors, report.findings, report.pending):
        for item in group:
            location = f"line {item.line}" if item.line is not None else "input"
            if item.trace_id is not None:
                location += " trace=" + json.dumps(item.trace_id, ensure_ascii=True)
            if item.call_id is not None:
                location += " call=" + json.dumps(item.call_id, ensure_ascii=True)
            if item.related_line is not None:
                location += f" related_line={item.related_line}"
            lines.append(f"- {item.code}: {location}: {item.message}")
    if report.errors:
        lines.append("Input was not fully validated; findings may be incomplete.")
    if report.pending:
        lines.append("Pending calls are allowed only because --allow-incomplete was set.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline lifecycle checks for normalized JSONL or complete Chat Completions messages.",
        epilog="Exit: 0 no findings (pending allowed); 1 lifecycle findings; 2 invalid input/usage.")
    parser.add_argument("input", help="UTF-8 JSONL file, or - for stdin")
    parser.add_argument("--format", choices=FORMATS, default="normalized",
                        help="input format (default: normalized)")
    parser.add_argument("--json", action="store_true", help="machine-readable report to stdout")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="treat unmatched calls at EOF as pending, not missing")
    parser.add_argument("--version", action="version", version=f"agent-trace-check {__version__}")
    args = parser.parse_args(argv)
    try:
        if args.input == "-":
            # Decode explicitly, so piped data follows the file UTF-8 contract.
            import io
            stream = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="strict")
            report = check_lines(stream, input_format=args.format,
                                 allow_incomplete=args.allow_incomplete)
        else:
            with open(args.input, encoding="utf-8", errors="strict") as stream:
                report = check_lines(stream, input_format=args.format,
                                     allow_incomplete=args.allow_incomplete)
    except UnicodeError:
        report = Report(errors=[Diagnostic("input_error", "input is not valid UTF-8")])
    except OSError:
        report = Report(errors=[Diagnostic("input_error", "input could not be read; check path and permissions")])
    output = json.dumps(report.to_dict(), ensure_ascii=True, indent=2) if args.json else render_human(report)
    try:
        print(output, flush=True)
    except BrokenPipeError:
        # Avoid a second failing flush during interpreter shutdown.
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        return 2
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
