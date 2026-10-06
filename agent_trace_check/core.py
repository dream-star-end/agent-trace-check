"""Parsing and lifecycle checks. No tool content is executed or reported."""

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Iterable


DEFAULT_TRACE = "default"
FORMATS = ("normalized", "chat-messages")


@dataclass(frozen=True)
class Event:
    kind: str
    trace_id: str
    line: int
    call_id: str | None = None
    name: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    line: int | None = None
    trace_id: str | None = None
    call_id: str | None = None
    related_line: int | None = None


@dataclass
class Report:
    records: int = 0
    events: int = 0
    ignored_messages: int = 0
    calls: int = 0
    results: int = 0
    matched_results: int = 0
    traces: int = 0
    findings: list[Diagnostic] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)
    pending: list[Diagnostic] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 2 if self.errors else 1 if self.findings else 0

    @property
    def status(self) -> str:
        if self.errors:
            return "invalid"
        if self.findings:
            return "findings"
        return "incomplete" if self.pending else "clean"

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": 1,
            "status": self.status,
            "exit_code": self.exit_code,
            "summary": {
                "records": self.records,
                "events": self.events,
                "ignored_messages": self.ignored_messages,
                "traces": self.traces,
                "calls": self.calls,
                "results": self.results,
                "matched_results": self.matched_results,
                "findings": len(self.findings),
                "errors": len(self.errors),
                "pending": len(self.pending),
            },
            "findings": [asdict(item) for item in self.findings],
            "errors": [asdict(item) for item in self.errors],
            "pending": [asdict(item) for item in self.pending],
        }


class RecordError(ValueError):
    """An invalid or unsupported input record, with payload-free diagnostics."""


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{field_name} must be a nonempty string")
    return value


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecordError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _no_constant(_: str) -> None:
    raise RecordError("NaN and Infinity are not valid JSON")


def parse_record(text: str, line: int, input_format: str) -> list[Event]:
    """Parse one whole record atomically; reject unsupported tool variants."""
    if input_format not in FORMATS:
        raise ValueError("unsupported input format")
    value = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                       parse_constant=_no_constant)
    if not isinstance(value, dict):
        raise RecordError("each line must be a JSON object")
    trace = _string(value.get("trace_id", DEFAULT_TRACE), "trace_id")
    if input_format == "normalized":
        kind = value.get("type")
        if kind == "tool_call":
            return [Event(kind, trace, line,
                          _string(value.get("call_id"), "call_id"),
                          _string(value.get("name"), "name"))]
        if kind == "tool_result":
            return [Event(kind, trace, line,
                          _string(value.get("call_id"), "call_id"))]
        if kind == "trace_end":
            status = value.get("status", "completed")
            if status not in ("completed", "interrupted"):
                raise RecordError("trace_end status must be completed or interrupted")
            return [Event(kind, trace, line, status=status)]
        raise RecordError("type must be tool_call, tool_result, or trace_end")

    # Intentionally narrow: complete message objects, not API response envelopes
    # or streamed deltas. The trace_id field is this adapter's local extension.
    role = value.get("role")
    if role not in ("system", "developer", "user", "assistant", "tool"):
        raise RecordError("unsupported or missing message role")
    if value.get("function_call") is not None or role != "assistant" and "tool_calls" in value:
        raise RecordError("legacy function_call or misplaced tool_calls is unsupported")
    if role == "tool":
        call_id = _string(value.get("tool_call_id"), "tool_call_id")
        content = value.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                    raise RecordError("tool content parts must be text objects")
        elif not isinstance(content, str):
            raise RecordError("tool content must be a string or a list of text parts")
        return [Event("tool_result", trace, line, call_id)]
    if role != "assistant" or value.get("tool_calls") is None:
        return []
    calls = value["tool_calls"]
    if not isinstance(calls, list):
        raise RecordError("tool_calls must be a list or null")
    events = []
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise RecordError("only function tool_calls are supported")
        function = call.get("function")
        if not isinstance(function, dict):
            raise RecordError("tool call function must be an object")
        if not isinstance(function.get("arguments"), str):
            raise RecordError("function.arguments must be a string (not parsed)")
        events.append(Event("tool_call", trace, line,
                            _string(call.get("id"), "tool_calls.id"),
                            _string(function.get("name"), "function.name")))
    return events


@dataclass
class Trace:
    calls: dict[str, Event] = field(default_factory=dict)
    results: dict[str, Event] = field(default_factory=dict)
    pending: dict[str, Event] = field(default_factory=dict)
    end: Event | None = None


def check_lines(lines: Iterable[str], *, input_format: str = "normalized",
                allow_incomplete: bool = False) -> Report:
    """Check a finite snapshot in file order. Blank lines are ignored.

    A malformed record is skipped atomically. Continue collecting diagnostics,
    but status 'invalid' and exit code 2 always take precedence.
    """
    if input_format not in FORMATS:
        raise ValueError("unsupported input format")
    report = Report()
    traces: dict[str, Trace] = {}

    def finding(code: str, message: str, event: Event,
                related_line: int | None = None) -> None:
        report.findings.append(Diagnostic(code, message, event.line,
                                          event.trace_id, event.call_id, related_line))

    for line_number, text in enumerate(lines, 1):
        if not text.strip():
            continue
        report.records += 1
        try:
            events = parse_record(text, line_number, input_format)
        except json.JSONDecodeError as exc:
            report.errors.append(Diagnostic("malformed_json",
                f"invalid JSON at column {exc.colno}", line_number))
            continue
        except (RecordError, RecursionError, ValueError) as exc:
            message = str(exc) if isinstance(exc, RecordError) else "JSON nesting or number exceeds parser limits"
            report.errors.append(Diagnostic("malformed_record", message, line_number))
            continue
        if not events:
            report.ignored_messages += 1
        for event in events:
            report.events += 1
            trace = traces.setdefault(event.trace_id, Trace())
            if trace.end is not None:
                finding("event_after_trace_end", "event appears after this trace ended",
                        event, trace.end.line)
                continue
            if event.kind == "tool_call":
                report.calls += 1
                if event.call_id in trace.calls:
                    finding("duplicate_call_id", "call ID is already used in this trace",
                            event, trace.calls[event.call_id].line)
                    continue
                trace.calls[event.call_id] = event
                trace.pending[event.call_id] = event
            elif event.kind == "tool_result":
                report.results += 1
                if event.call_id not in trace.calls:
                    finding("orphan_result", "result has no preceding call in this trace", event)
                elif event.call_id in trace.results:
                    finding("duplicate_result", "call already has a result",
                            event, trace.results[event.call_id].line)
                else:
                    trace.results[event.call_id] = event
                    del trace.pending[event.call_id]
                    report.matched_results += 1
            else:
                trace.end = event
                if event.status == "interrupted":
                    finding("interrupted_trace", "trace explicitly ended as interrupted", event)
                for call in trace.pending.values():
                    finding("missing_result", "trace ended before the call received a result",
                            call, event.line)
                trace.pending.clear()

    report.traces = len(traces)
    for trace in traces.values():
        for call in trace.pending.values():
            diagnostic = Diagnostic(
                "pending_call" if allow_incomplete else "missing_result",
                "call is pending at end of snapshot" if allow_incomplete
                else "no result before end of input", call.line, call.trace_id, call.call_id)
            (report.pending if allow_incomplete else report.findings).append(diagnostic)
    return report
