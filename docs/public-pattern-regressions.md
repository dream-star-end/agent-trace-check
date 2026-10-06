# Public-pattern regression validation

Validated on 2026-10-06 with Python 3.12.14. These tests use synthetic message
structures inspired by public bug reports. They do not require a live product
installation, API request, credentials, private user data, or copied issue logs.

## Sources and synthetic models

- [CherryHQ/cherry-studio #14128](https://github.com/CherryHQ/cherry-studio/issues/14128):
  the report's message structure contains two function calls using the same ID,
  followed by two tool results for that ID. The regression preserves this
  relationship using new identifiers, tool names, arguments, and result text.
  It does not model the report's other provider/Responses compatibility problems.
- [anomalyco/opencode #24090](https://github.com/anomalyco/opencode/issues/24090):
  the reporter describes replayed assistant messages losing their tool calls
  while corresponding tool-result messages remain. A two-message synthetic
  fixture represents one such orphan. It does not reproduce the application's
  internal serialization or establish why data was lost.

Both reports were closed as not planned when checked. They are evidence that
these failure patterns have been reported, not proof that current releases are
broken. This validates structural detection only, not upstream end-to-end
reproduction, a diagnosis of root cause, or evidence of adoption.

## Expected versus actual

All input fixtures are in `tests/fixtures/public_patterns/`; use
`--format chat-messages`. All expectations below matched actual results.

| Fixture | Expected findings/status | Actual exit |
| --- | --- | --- |
| `cherry_duplicate_ids.jsonl` | `duplicate_call_id`, `duplicate_result` | 1 |
| `cherry_distinct_parallel_ids.jsonl` | Clean; two distinct IDs, same tool, reverse-order results | 0 |
| `cherry_deduplicated.jsonl` | Clean; one call and one result | 0 |
| `opencode_missing_assistant_calls.jsonl` | `orphan_result` | 1 |
| `opencode_restored_assistant_calls.jsonl` | Clean; matching assistant call restored | 0 |
| `parallel_snapshot_incomplete.jsonl` | `missing_result` in complete-input mode | 1 |
| Same fixture with `--allow-incomplete` | Incomplete; one pending call and no findings | 0 |
| `replayed_snapshots_flattened.jsonl` | `duplicate_call_id`, `duplicate_result` under one-trace policy | 1 |
| `replayed_snapshots_scoped.jsonl` | Clean; one trace per request snapshot | 0 |

No input-format errors occurred. Allowing incomplete input does not suppress
orphan or duplicate diagnostics. The duplicate-call fixture produces diagnostics
at line 1 (two calls in one message) and line 3 (the second result). The missing
assistant-calls fixture identifies the orphan at line 2.

## False-positive boundary: replayed snapshots

A request logger may record the full conversation history for every API request.
Two valid snapshots can therefore repeat the same earlier call/result pair.
Concatenating those snapshots into a single trace looks like duplicate events
to this checker. The flattened fixture intentionally demonstrates this scope
hazard; it is not claimed to be a bug in either cited project.

Check each complete request's message history separately. Alternatively, add a
unique local `trace_id` to every message within each request snapshot, using the
same value for its calls and results. The scoped control confirms that this
avoids the apparent duplicate. This does not validate how an application moves
from one request snapshot to the next.

No false positives were observed in the correctly scoped repaired, parallel,
and incomplete controls. That is a limited result for these cases, not a
precision estimate for real-world logs. The checker cannot distinguish a
logging omission from an actual missing execution result.

## Re-run

From the project root:

```sh
python3 -m unittest discover -s tests -p test_public_patterns.py -v
python3 -m agent_trace_check tests/fixtures/public_patterns/cherry_duplicate_ids.jsonl --format chat-messages --json
python3 -m agent_trace_check tests/fixtures/public_patterns/opencode_missing_assistant_calls.jsonl --format chat-messages --json
```

The regression module contains 12 tests, including six CLI cases within one
parameterized test. They all passed. The complete suite now contains 86 tests;
local verification is recorded in [VERIFICATION.txt](../VERIFICATION.txt).

## 中文摘要

本次增加的是参考公开问题结构编写的合成回归用例，不是原始用户日志，也不是对上游
产品的完整复现。两个问题报告中的主要结构分别是“相同 ID 的重复调用及重复结果”
和“assistant 调用字段丢失但 tool 结果仍存在”，检查器均识别出预期问题。
修正后的用例、不同 ID 的并行乱序返回、允许未完成的快照均得到预期结果。

一个需要明确说明的边界：连续记录的完整请求历史会重复包含先前调用，不能直接拼成
一个 trace。应逐份检查，或给每份完整请求快照分配独立的 trace_id，否则会产生看似
重复调用的提示。没有验证上游当前版本是否仍存在原报告的问题。
