import itertools
import json
import unittest

from agent_trace_check.core import check_lines, parse_record, RecordError
from agent_trace_check.__main__ import render_human


def call(cid="a", trace=None):
    event = {"type": "tool_call", "call_id": cid, "name": "lookup"}
    if trace is not None:
        event["trace_id"] = trace
    return event


def result(cid="a", trace=None):
    event = {"type": "tool_result", "call_id": cid}
    if trace is not None:
        event["trace_id"] = trace
    return event


def end(status="completed", trace=None):
    event = {"type": "trace_end", "status": status}
    if trace is not None:
        event["trace_id"] = trace
    return event


def check(*events, **kwargs):
    return check_lines([json.dumps(event) for event in events], **kwargs)


def codes(report):
    return [item.code for item in report.findings]


def message_call(cid="a"):
    return {"id": cid, "type": "function", "function": {"name": "lookup", "arguments": "{}"}}


def assistant(*calls):
    return {"role": "assistant", "tool_calls": list(calls)}


def tool(cid="a", content="ok"):
    return {"role": "tool", "tool_call_id": cid, "content": content}


class LifecycleTests(unittest.TestCase):
    def test_clean_single_call(self):
        report = check(call(), result())
        self.assertEqual(report.status, "clean")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.matched_results, 1)

    def test_all_parallel_completion_orders(self):
        for order in itertools.permutations("abc"):
            with self.subTest(order=order):
                report = check(*(call(cid) for cid in "abc"), *(result(cid) for cid in order))
                self.assertEqual(report.exit_code, 0)
                self.assertEqual(report.matched_results, 3)

    def test_multiple_sequential_calls(self):
        report = check(call("a"), result("a"), call("b"), result("b"))
        self.assertEqual(report.exit_code, 0)

    def test_active_duplicate_id(self):
        report = check(call(), call(), result())
        self.assertEqual(codes(report), ["duplicate_call_id"])
        self.assertEqual(report.findings[0].related_line, 1)

    def test_sequential_reuse_same_trace_is_duplicate(self):
        report = check(call(), result(), call(), result())
        self.assertEqual(codes(report), ["duplicate_call_id", "duplicate_result"])

    def test_reuse_across_traces_is_valid(self):
        report = check(call(trace="one"), result(trace="one"),
                       call(trace="two"), result(trace="two"))
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.traces, 2)

    def test_interleaved_traces_isolate_ids(self):
        report = check(call(trace="one"), call(trace="two"),
                       result(trace="two"), result(trace="one"))
        self.assertEqual(report.exit_code, 0)

    def test_result_in_wrong_trace(self):
        report = check(call(trace="one"), result(trace="two"))
        self.assertEqual(codes(report), ["orphan_result", "missing_result"])

    def test_orphan_result(self):
        self.assertEqual(codes(check(result())), ["orphan_result"])

    def test_result_before_call_not_retroactively_matched(self):
        self.assertEqual(codes(check(result(), call())), ["orphan_result", "missing_result"])

    def test_repeated_orphans_stay_orphans(self):
        self.assertEqual(codes(check(result(), result())), ["orphan_result", "orphan_result"])

    def test_duplicate_result(self):
        report = check(call(), result(), result())
        self.assertEqual(codes(report), ["duplicate_result"])
        self.assertEqual(report.findings[0].related_line, 2)

    def test_missing_result_at_eof(self):
        report = check(call())
        self.assertEqual(codes(report), ["missing_result"])
        self.assertEqual(report.exit_code, 1)

    def test_allow_incomplete(self):
        report = check(call(), allow_incomplete=True)
        self.assertEqual(report.status, "incomplete")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual([item.code for item in report.pending], ["pending_call"])

    def test_partial_parallel_completion(self):
        report = check(call("a"), call("b"), result("b"), allow_incomplete=True)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual([item.call_id for item in report.pending], ["a"])
        self.assertEqual(report.matched_results, 1)

    def test_allow_incomplete_does_not_hide_other_findings(self):
        report = check(call(), result("unknown"), allow_incomplete=True)
        self.assertEqual(report.status, "findings")
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(len(report.pending), 1)

    def test_clean_explicit_end(self):
        self.assertEqual(check(call(), result(), end()).exit_code, 0)

    def test_explicit_end_still_requires_results(self):
        report = check(call(), end(), allow_incomplete=True)
        self.assertEqual(codes(report), ["missing_result"])
        self.assertEqual(len(report.pending), 0)
        self.assertEqual(report.findings[0].related_line, 2)

    def test_interruption_is_explicit(self):
        report = check(call(), end("interrupted"), allow_incomplete=True)
        self.assertEqual(codes(report), ["interrupted_trace", "missing_result"])

    def test_interruption_without_pending_calls(self):
        self.assertEqual(codes(check(end("interrupted"))), ["interrupted_trace"])

    def test_no_interruption_inference_from_eof(self):
        self.assertNotIn("interrupted_trace", codes(check(call())))

    def test_event_after_end(self):
        report = check(end(), call())
        self.assertEqual(codes(report), ["event_after_trace_end"])
        self.assertEqual(report.findings[0].related_line, 1)

    def test_duplicate_end(self):
        self.assertEqual(codes(check(end(), end())), ["event_after_trace_end"])

    def test_ending_one_trace_does_not_close_another(self):
        report = check(call(trace="one"), call(trace="two"), end(trace="one"), allow_incomplete=True)
        self.assertEqual([item.trace_id for item in report.findings], ["one"])
        self.assertEqual([item.trace_id for item in report.pending], ["two"])

    def test_json_report_is_deterministic(self):
        events = [call("z"), call("a"), result("bad")]
        self.assertEqual(check(*events).to_dict(), check(*events).to_dict())
        self.assertEqual(check(*events).to_dict()["report_version"], 1)


class ParsingTests(unittest.TestCase):
    def test_blank_lines_keep_physical_line_numbers(self):
        report = check_lines(["\n", json.dumps(call()), "  "])
        self.assertEqual(report.records, 1)
        self.assertEqual(report.findings[0].line, 2)

    def test_empty_input_is_clean_zero_coverage(self):
        report = check_lines([])
        self.assertEqual(report.status, "clean")
        self.assertEqual(report.calls, 0)

    def test_invalid_json_continues(self):
        report = check_lines(["{broken", json.dumps(call()), json.dumps(result())])
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.matched_results, 1)
        self.assertEqual(report.errors[0].code, "malformed_json")

    def test_nonobject_records_rejected(self):
        for value in [[], None, 4, True, "text"]:
            with self.subTest(value=value):
                self.assertEqual(check(value).exit_code, 2)

    def test_required_strings_rejected(self):
        for key in ["call_id", "name", "trace_id"]:
            for bad in [None, "", "  ", 1, [], {}]:
                event = call()
                event[key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assertEqual(check(event).exit_code, 2)

    def test_unknown_event_type_is_error(self):
        self.assertEqual(check({"type": "tool_resut", "call_id": "a"}).exit_code, 2)

    def test_bad_end_status_is_error(self):
        self.assertEqual(check(end("cancelled")).exit_code, 2)

    def test_duplicate_json_keys_rejected(self):
        report = check_lines(['{"type":"tool_result","call_id":"a","call_id":"b"}'])
        self.assertEqual(report.exit_code, 2)
        self.assertIn("duplicate key", report.errors[0].message)

    def test_nonfinite_json_numbers_rejected_even_in_metadata(self):
        for token in ["NaN", "Infinity", "-Infinity"]:
            report = check_lines(['{"type":"trace_end","extra":' + token + '}'])
            self.assertEqual(report.exit_code, 2)

    def test_unknown_metadata_allowed(self):
        event = call()
        event["arguments"] = {"anything": True}
        self.assertEqual(check(event, result()).exit_code, 0)

    def test_error_priority_over_findings(self):
        report = check(call(), {"type": "invalid"})
        self.assertEqual(report.status, "invalid")
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(codes(report), ["missing_result"])

    def test_unknown_format_rejected(self):
        with self.assertRaises(ValueError):
            check_lines([], input_format="anything")

    def test_json_nested_beyond_parser_limit(self):
        report = check_lines(['{"extra":' + '[' * 2000 + '0' + ']' * 2000 + '}'])
        self.assertEqual(report.exit_code, 2)

    def test_payload_not_in_diagnostics(self):
        event = call()
        event["arguments"] = "PRIVATE_PAYLOAD_ABC"
        report = check(event)
        self.assertNotIn("PRIVATE_PAYLOAD_ABC", json.dumps(report.to_dict()))
        bad = check_lines(['{"secret": "PRIVATE_PAYLOAD_ABC", invalid}'])
        self.assertNotIn("PRIVATE_PAYLOAD_ABC", json.dumps(bad.to_dict()))

    def test_human_diagnostics_escape_terminal_control_characters(self):
        report = check(call("\x1b[31m\nsecret"))
        text = render_human(report)
        self.assertNotIn("\x1b", text)
        self.assertIn("\\u001b", text)
        self.assertIn("\\n", text)


class ChatMessageTests(unittest.TestCase):
    def test_complete_chat_message_pair(self):
        report = check(assistant(message_call()), tool(), input_format="chat-messages")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.events, 2)

    def test_parallel_calls_in_one_message(self):
        report = check(assistant(message_call("a"), message_call("b")), tool("b"), tool("a"), input_format="chat-messages")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.calls, 2)

    def test_context_messages_ignored(self):
        report = check(*({"role": role, "content": "text"} for role in ["system", "developer", "user", "assistant"]), input_format="chat-messages")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.ignored_messages, 4)

    def test_null_calls_ignored(self):
        report = check({"role": "assistant", "tool_calls": None}, input_format="chat-messages")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.ignored_messages, 1)

    def test_text_content_parts(self):
        report = check(assistant(message_call()), tool(content=[{"type": "text", "text": "ok"}]), input_format="chat-messages")
        self.assertEqual(report.exit_code, 0)

    def test_empty_result_content_is_valid(self):
        self.assertEqual(check(assistant(message_call()), tool(content=""), input_format="chat-messages").exit_code, 0)

    def test_bad_tool_content_rejected(self):
        for content in [None, 3, {}, [{"type": "image_url", "text": "x"}], [{"type": "text", "text": 3}]]:
            with self.subTest(content=content):
                self.assertEqual(check(tool(content=content), input_format="chat-messages").exit_code, 2)

    def test_tool_content_required(self):
        event = tool()
        del event["content"]
        self.assertEqual(check(event, input_format="chat-messages").exit_code, 2)

    def test_arguments_not_parsed_or_executed(self):
        event = message_call()
        event["function"]["arguments"] = "not json; arbitrary payload"
        self.assertEqual(check(assistant(event), tool(), input_format="chat-messages").exit_code, 0)

    def test_nonstring_arguments_rejected(self):
        event = message_call()
        event["function"]["arguments"] = {}
        self.assertEqual(check(assistant(event), input_format="chat-messages").exit_code, 2)

    def test_malformed_batch_is_atomic(self):
        report = check(assistant(message_call(), {"type": "custom", "id": "b"}), input_format="chat-messages")
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.calls, 0)
        self.assertEqual(report.findings, [])

    def test_unsupported_custom_call_is_error(self):
        report = check(assistant({"type": "custom", "id": "x", "custom": {"name": "shell", "input": "pwd"}}), input_format="chat-messages")
        self.assertEqual(report.exit_code, 2)

    def test_legacy_function_call_is_error(self):
        self.assertEqual(check({"role": "assistant", "function_call": {}}, input_format="chat-messages").exit_code, 2)

    def test_null_legacy_function_field_is_absent(self):
        event = assistant(message_call())
        event["function_call"] = None
        self.assertEqual(check(event, tool(), input_format="chat-messages").exit_code, 0)
        self.assertEqual(check({"role": "assistant", "function_call": None}, input_format="chat-messages").exit_code, 0)

    def test_nonassistant_tool_calls_rejected(self):
        self.assertEqual(check({"role": "user", "tool_calls": []}, input_format="chat-messages").exit_code, 2)

    def test_legacy_function_role_is_error(self):
        self.assertEqual(check({"role": "function", "content": "ok"}, input_format="chat-messages").exit_code, 2)

    def test_response_envelopes_and_deltas_rejected(self):
        for event in [{"choices": [{"message": assistant(message_call())}]}, {"delta": {"tool_calls": []}}]:
            self.assertEqual(check(event, input_format="chat-messages").exit_code, 2)

    def test_trace_extension_isolates_runs(self):
        events = []
        for trace in ["first", "second"]:
            for event in [assistant(message_call()), tool()]:
                event["trace_id"] = trace
                events.append(event)
        self.assertEqual(check(*events, input_format="chat-messages").exit_code, 0)

    def test_bad_call_shapes(self):
        for bad in [None, [], {}, {"type": "function"}, {"type": "function", "id": "a", "function": []}]:
            self.assertEqual(check(assistant(bad), input_format="chat-messages").exit_code, 2)

    def test_tool_calls_requires_list(self):
        self.assertEqual(check({"role": "assistant", "tool_calls": {}}, input_format="chat-messages").exit_code, 2)


if __name__ == "__main__":
    unittest.main()
