# agent-trace-check

Find broken tool-call/result pairs in agent logs before digging through a full
trace. Version 0.1.0 is an early, offline command-line tool built with the Python
standard library. No account, API key, model call, runtime network access, or
package installation is needed. Intended for Python 3.10+; locally tested on
Python 3.12.

It finds duplicate call IDs, results without a preceding call, duplicate results,
missing results, explicitly interrupted traces, and events after a trace ended.
It validates its documented record format, not tool execution or answer quality.
All included data is synthetic.

## Quick start

Download the source or clone the repository, open a terminal inside the project
folder, and use your Python executable
(`python3` below; on some systems use `python` or `py`):

```sh
python3 -m agent_trace_check examples/clean.jsonl
python3 -m agent_trace_check examples/broken.jsonl --json
python3 -m agent_trace_check examples/incomplete.jsonl --allow-incomplete
python3 -m agent_trace_check examples/chat-messages.jsonl --format chat-messages
python3 -m unittest discover -s tests -v
```

Use `-` instead of a filename to read standard input. `--json` writes a JSON
report to standard output. Otherwise output is plain text. Nothing is sent
anywhere by this checker. There is no package-registry release. Run from the
project root; a similarly named registry package is not part of this project.

Exit codes:

- `0`: no lifecycle findings or input errors. With `--allow-incomplete`, this
  can include pending calls; the report says `incomplete`, never `clean`.
- `1`: one or more lifecycle findings.
- `2`: malformed/unsupported input, unreadable/non-UTF-8 input, command-line
  usage error, or broken output pipe. Input errors take priority over findings.
  Argument usage errors are printed to stderr, even with `--json`.

## What a failure looks like

```text
FINDINGS: 2 calls, 0 matched results; 4 findings, 0 input errors, 0 pending calls
- duplicate_call_id: line 2 trace="demo" call="a" related_line=1: call ID is already used in this trace
- orphan_result: line 3 trace="demo" call="unknown": result has no preceding call in this trace
- interrupted_trace: line 4 trace="demo": trace explicitly ended as interrupted
- missing_result: line 1 trace="demo" call="a" related_line=4: trace ended before the call received a result
```

This is the output for `examples/broken.jsonl`. Use `--json` when another
script needs to consume the diagnostics. For a running workflow, take a bounded
snapshot and use `--allow-incomplete`; this tool does not tail a live log.

## Normalized JSONL schema (version 1)

One complete JSON object per UTF-8 line, in recorded event order. Blank lines
are ignored but still count toward diagnostic line numbers. No JSON array,
multiline objects, byte-order mark, comments, duplicate JSON keys, or NaN/Infinity.

```jsonl
{"type":"tool_call","trace_id":"run-1","call_id":"a","name":"lookup"}
{"type":"tool_result","trace_id":"run-1","call_id":"a"}
{"type":"trace_end","trace_id":"run-1","status":"completed"}
```

Fields:

- All events: `type` must be `tool_call`, `tool_result`, or `trace_end`.
  `trace_id` is an optional nonempty string, defaulting to the literal `default`.
- `tool_call`: requires nonempty strings `call_id` and `name`.
- `tool_result`: requires nonempty string `call_id`. A result payload is optional
  because this checker only needs the lifecycle relationship.
- `trace_end`: optional `status`, either `completed` (default) or `interrupted`.
  This is a terminal boundary for this `trace_id`.
- Additional fields, including arguments, content, timestamps, and metadata,
  are ignored after JSON parsing. Relevant strings cannot be whitespace-only;
  nonempty identifiers are compared exactly without trimming or case folding.

Call IDs are unique within one trace, including after a result has arrived.
Sequential reuse in the same trace is a `duplicate_call_id`; use a different
`trace_id` for a different run. Explicit `trace_id: "default"` and omitted IDs
refer to the same trace. A duplicate call does not open another pending call;
any result refers to the first accepted call. A second result is therefore a
`duplicate_result`. This is the checker's strict convention, not a claim that
all logging frameworks impose the same uniqueness scope.

Parallel calls can finish in any order. Each result must follow its call in the
file; timestamps are not sorted. Traces may interleave, but results never match
across traces. An orphan result is not retroactively matched by a later call.
Repeated orphan results are each reported as `orphan_result`.

At EOF, unmatched calls are `missing_result` by default. `--allow-incomplete`
puts them in a separate `pending` list instead; it does not follow a growing
file or infer that a process crashed. An explicit `trace_end` always requires
all results, including with `--allow-incomplete`. `interrupted` always produces
an `interrupted_trace` finding, even if all calls returned. Intentional
cancellation has no dedicated event type in this prototype.

Malformed records are rejected atomically, then checking continues. If one
call in a multi-call message is malformed, none of that message's calls is
accepted. Input errors mean the overall report is `invalid`; later findings
may be secondary consequences of skipped records. A file read/decoding failure
returns an input-error report without partial counts.

## Chat Completions message adapter

`--format chat-messages` accepts one complete message object per line:

```jsonl
{"role":"assistant","tool_calls":[{"id":"a","type":"function","function":{"name":"lookup","arguments":"{}"}}]}
{"role":"tool","tool_call_id":"a","content":"synthetic result"}
```

The adapter maps assistant `tool_calls` to call events and `role: "tool"`
messages to result events. It supports function tools only. Function arguments
must be a string but are never parsed or executed. Tool content must be a string
or a list of text content parts. Modern messages may contain
`function_call: null`; non-null legacy function calls are unsupported.

System, developer, user, and assistant messages without tool calls are ignored.
The adapter is not a full API message validator; content and other unrelated
fields are not validated. A local optional `trace_id` on each message has the
same meaning as in normalized input. All messages belonging to a run, including
tool results, must use that same value. It is an adapter extension, not an
OpenAI API field. Each complete multi-call assistant message is one record
but can produce several events.

This input mode does not accept whole `messages` arrays, response envelopes,
`choices`, streaming chunks/deltas, custom tools, Responses API events, legacy
`role: "function"` messages, or other provider formats. Export complete
messages as JSONL yourself, or map your logger to the normalized schema.
There is no automatic format detection and no provider SDK dependency.

If your logger saves the full conversation history for every request, check each
request snapshot separately. Concatenating repeated histories under one trace
can produce misleading duplicate findings. Alternatively, give each snapshot a
distinct local `trace_id`, consistently applied to every call and result message
in that snapshot. The checker cannot infer this boundary for you.

Reference for the supported function-call/message fields:
[OpenAI Chat Completions API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
(checked 2026-10-06). This adapter deliberately implements only the subset above.

## Reports and boundaries

JSON reports have `report_version: 1`, `status` (`clean`, `incomplete`,
`findings`, or `invalid`), `exit_code`, a `summary`, and separate `findings`,
`errors`, and `pending` lists. Each diagnostic includes a stable code, message,
physical line, trace/call IDs when known, and a related line when useful.
A missing result at an explicit end points to the call line, with the end line
as `related_line`.

Summary counters count nonblank input records and parsed lifecycle events.
`calls`/`results` count observed call/result events before their trace's end,
including duplicates. `matched_results` counts accepted matches only.
Events after a terminal boundary are reported but not processed as calls or
results. `traces` counts traces with lifecycle events; ignored chat messages do
not create traces. Empty input or input containing only ignored messages is
`clean` with zero calls, which does not establish that a workload was captured.

Arguments and result bodies are omitted from reports. Identifiers are included
and could contain private data; review/redact reports before sharing them.
Terminal control characters in identifiers are escaped. This is not a secret
scanner, security audit, or permission checker.

The parser reads one line at a time, retains lifecycle state and diagnostics in
memory, and has no enforced input-size limit. Use trusted, bounded exports;
resource-exhaustion resistance and very large traces are not tested. It does
not check timeouts, retries, ordering rules for subsequent assistant messages,
API acceptance, cancellation semantics, argument schema validity, tool success,
latency/cost, or model quality. An error result still completes a call.
There is no evidence of real-world adoption or validation against live provider
exports yet.

## Development and contributions

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q agent_trace_check tests
```

The local verification passes 86 tests on Python 3.12. CI is configured
to exercise Python 3.10–3.14 on Linux, plus Python 3.12 on Windows and macOS.
A configured matrix is not proof of a passing run; check the actual workflow
results for the commit you use.

The suite includes synthetic regressions inspired by two public issue patterns,
with repaired and parallel-call controls. See
[public-pattern regressions](docs/public-pattern-regressions.md) for sources,
expected diagnostics, and the repeated-history boundary. These cases are not
end-to-end reproductions or claims about current upstream releases.

Useful contributions include small synthetic reproductions of missed lifecycle
bugs, tests against safely redacted exports, clearer diagnostics, and narrowly
specified adapters. See [CONTRIBUTING.md](CONTRIBUTING.md) for scope and checks.
Please remove credentials, personal information, and proprietary content before
sharing any trace or report.

## Project status and AI assistance

This is an early implementation. It has synthetic test coverage; it has not yet
been validated against production workloads. The initial code, tests, and
documentation were developed with AI assistance. Please review the behavior and
limitations for your own use case. No affiliation with or endorsement by any
model provider is implied.

## License

[MIT](LICENSE). The project contains no vendored third-party source code and has
no third-party Python runtime dependencies. The Python interpreter and external
CI actions retain their own licenses.

---

# 中文说明

这是一个离线的 Agent 工具调用日志检查工具，帮助定位调用与结果不匹配的问题。
使用 Python 标准库，无需账号、API 密钥、模型调用或安装运行依赖。下载源码或
克隆仓库后进入项目目录，运行上面的示例命令即可。
面向 Python 3.10+，当前在 Python 3.12 上测试。示例全部为合成数据。

它检查：重复调用 ID、找不到先前调用的结果、重复结果、缺失结果、明确标记的中断，
以及 trace 已结束后仍出现的事件。它不判断工具是否真的执行成功，也不判断模型回答质量。

默认格式是 UTF-8 JSONL，每行一个完整对象：

- `tool_call`：必填 `call_id`、`name`，均为非空字符串
- `tool_result`：必填 `call_id`，结果正文可省略
- `trace_end`：`status` 为 `completed` 或 `interrupted`，默认 `completed`
- `trace_id` 可选，默认字面值 `default`；同一轮运行的所有事件应使用相同值

同一 trace 内，调用 ID 在返回结果后也不能重复使用。独立运行应使用不同的
`trace_id`。并行调用可以乱序返回结果，但结果必须出现在对应调用之后。
只按文件顺序检查，不按时间戳排序，也不会把不同 trace 的事件相互配对。

`--allow-incomplete` 用于尚未结束的日志快照：EOF 时没返回的调用单独列为
`pending`；在没有其他生命周期问题或输入错误时，状态为 `incomplete`，退出码为 0。
它不会持续监控文件。
如果已经有 `trace_end`，缺失结果仍然报错；`interrupted` 总会报告中断。
EOF 本身不是崩溃的证据。

`--format chat-messages` 只支持每行一个完整的 Chat Completions 消息，读取
assistant 的 function `tool_calls` 和 tool 消息的 `tool_call_id`。它不支持
流式增量、完整 API 响应封装、Responses API、自定义工具或所有厂商的格式。
参数字符串不会被执行，也不会被解析成工具输入。如果日志为每次请求记录完整对话历史，
请逐份检查，或给每份快照的所有调用和结果设置同一个独立的 `trace_id`。把重复历史
直接拼成一个 trace，会得到误导性的重复提示。

`--json` 输出机器可读报告。退出码：0 表示无检查发现（可包含允许的 pending），
1 表示生命周期问题，2 表示输入/格式/用法/读取错误或输出管道已关闭。若出现格式错误，该条记录整体
跳过，继续检查；总体报告仍为 `invalid`。空文件会显示零调用，不能证明任务执行正确。

报告不包含参数和结果正文，但会包含 trace 和 call ID；分享前请检查其中是否有
隐私信息。原型没有输入大小限制，请使用可信、有限大小的日志导出。

项目处于早期阶段，采用 MIT 许可证，目前没有软件包注册表发行版。初始代码、测试
和文档由 AI 辅助开发，86 项测试在本地 Python 3.12 上通过，尚未验证生产环境中的
实际效果。欢迎提供最小合成样例、脱敏日志的验证反馈和小范围改进。贡献前请阅读
[CONTRIBUTING.md](CONTRIBUTING.md)，分享日志或报告前请移除敏感信息。
