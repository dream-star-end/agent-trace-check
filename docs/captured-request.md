# Check a captured Chat Completions request

Use this recipe when a logger has saved **one complete outbound request body**
as a UTF-8 JSON object containing a `messages` array. A small standard-library
script exports those messages as JSONL for the existing `--format chat-messages`
checker. No installation, provider SDK, new adapter, or network call is needed.

## 1. Capture one complete request safely

Use your application's existing request logging or debug hook at the point
where it sends a Chat Completions-shaped body. Save one request object as
`request.json`; the top-level `messages` array must contain the complete history
actually sent for that request, in its original order. An omitted tool message
in a partial log is indistinguishable from an omitted result in the request.

If the response uses streaming (`"stream": true`), still capture that complete
outbound request body. **Do not save or concatenate response SSE events, tool
call argument fragments, `delta` objects, or `choices` envelopes for this
recipe.** It does not reconstruct streamed responses or extract nested logger
envelopes. A saved JSON array alone also needs a different export step.

Keep real captures local and access-restricted. They may contain prompts,
credentials, personal information, proprietary code, arguments, and tool output.
Do not add authorization headers, cookies, or tokens to the capture. Before
sharing anything, replace private content and identifiers with synthetic values,
preserving the call/result relationships. The converter is **not a redactor**:
it copies message contents into another local file. Use a trusted, bounded file;
it reads and serializes the complete snapshot in memory without a size limit.
Checker reports omit payload bodies but still include call/trace identifiers;
review those identifiers before sharing a report too.

## 2. Export, then check

From the project root, using Python 3.10+:

```sh
python3 examples/request_to_jsonl.py request.json request-messages.jsonl && python3 -m agent_trace_check request-messages.jsonl --format chat-messages --json
```

Use a new output filename each time. The converter refuses to overwrite an
existing file, including the input. `&&` runs the checker only if conversion
succeeds; do not check a stale or partial output after an export error. The
converter returns 0 for a successful export and 2 for an error. The checker
returns 0 for no findings, 1 for lifecycle findings, or 2 for invalid input.

On POSIX systems, new output files are created exclusively with owner-only
permissions (`0600`, further restricted by the process umask), before any payload
is written. Existing files and their permissions are never changed; existing
symlinks and hard-link aliases are refused too. Windows access depends on the
destination directory's ACL; the POSIX mode does not establish owner-only access
there. On Windows, choose a folder with an appropriately restricted ACL.

Conversion validates that the top level is an object, `messages` is an array,
and every array element is an object. Malformed JSON, duplicate object keys,
NaN/Infinity, and parser-limit failures are rejected. These checks finish before
the output file is created. The converter preserves message order and fields,
escapes embedded newlines, and removes the request envelope; it does not validate
the model, tools definition, or the full API schema. The checker then validates
its documented subset of complete function-tool messages. Diagnostic line N in
the JSONL corresponds to `messages[N - 1]`, not the original JSON file's line N.

Check `summary.calls`, `summary.matched_results`, and `summary.errors` as well as
the exit code. By default, an empty `messages` array or text-only history can
return `clean` with zero calls. That does not establish tool-call coverage.
If your scenario is supposed to invoke a tool, optionally add `--require-calls`
when checking the successfully exported file:

```sh
python3 -m agent_trace_check request-messages.jsonl --format chat-messages --require-calls --json
```

Zero captured calls then produce `errors[].code: "no_tool_calls"`, status
`invalid`, and exit 2. Leave the flag off when no tool calls are expected. It
requires at least one valid processed call across the whole input; a pending
call qualifies, so the flag does not prove complete capture or complete results.
See [the CI guard's boundaries](../README.md#use-as-a-ci-check).

## 3. Try the synthetic request shapes

All four request files in `examples/requests/` are newly authored, anonymized
structures. Model names, tool names, IDs, arguments, and results are synthetic.
They are not copied production logs and should not be sent to an API.

For example, run the missing-result shape:

```sh
python3 examples/request_to_jsonl.py examples/requests/parallel-missing-result.json parallel-missing-result.jsonl && python3 -m agent_trace_check parallel-missing-result.jsonl --format chat-messages
```

Expected output (exit 1):

```text
FINDINGS: 2 calls, 1 matched results; 1 findings, 0 input errors, 0 pending calls
- missing_result: line 2 trace="default" call="parallel-b": no result before end of input
```

The repaired control includes both results, in reverse completion order:

```sh
python3 examples/request_to_jsonl.py examples/requests/parallel-complete.json parallel-complete.jsonl && python3 -m agent_trace_check parallel-complete.jsonl --format chat-messages
```

Expected output (exit 0):

```text
CLEAN: 2 calls, 2 matched results; 0 findings, 0 input errors, 0 pending calls
```

The paused-history shape repeats the same assistant call inside one request:

```sh
python3 examples/request_to_jsonl.py examples/requests/paused-history-duplicate.json paused-history-duplicate.jsonl && python3 -m agent_trace_check paused-history-duplicate.jsonl --format chat-messages
```

Expected output (exit 1):

```text
FINDINGS: 2 calls, 1 matched results; 1 findings, 0 input errors, 0 pending calls
- duplicate_call_id: line 4 trace="default" call="paused-a" related_line=2: call ID is already used in this trace
```

The repaired control contains that turn once:

```sh
python3 examples/request_to_jsonl.py examples/requests/paused-history-once.json paused-history-once.jsonl && python3 -m agent_trace_check paused-history-once.jsonl --format chat-messages
```

Expected output (exit 0):

```text
CLEAN: 1 calls, 1 matched results; 0 findings, 0 input errors, 0 pending calls
```

These controls demonstrate matching behavior, not instructions to repair real
histories by fabricating results or deleting messages. Investigate the application
and capture boundary before deciding how to fix an actual request.

## Sources and what these checks establish

- [OmniRoute #13170](https://github.com/diegosouzapw/OmniRoute/issues/13170)
  reported a request history where one of two parallel calls lacked a result.
  `parallel-missing-result.json` models that relationship. The related
  [fix #13174](https://github.com/diegosouzapw/OmniRoute/pull/13174) merged on
  September 29, 2026.
- [Agno #10086](https://github.com/agno-agi/agno/issues/10086) reported that
  resuming a paused run with background streaming duplicated the current turn
  into its own history. `paused-history-duplicate.json` models two copies of one
  call and one result. The related [fix #10597](https://github.com/agno-agi/agno/pull/10597)
  merged on September 30, 2026.

Sources and merge status were checked on October 6, 2026. These historical
reports motivate useful request shapes; they are not claims of current upstream
bugs. No upstream application was run, no original transcript was validated,
and no adoption or provider acceptance is established by these synthetic tests.

For the duplicate shape, this checker reports `duplicate_call_id`, not an extra
`missing_result`: its per-trace ID convention accepts the first call and matches
the single result to that ID. It does not assign one result to a specific repeated
occurrence or enforce all provider adjacency rules. `clean` only means the
supported lifecycle checks found nothing; it does not guarantee API validity,
successful tools, or a correct answer.

## Keep request boundaries intact

Check every request snapshot separately. A logger may repeat the full prior
history in every request. Concatenating those snapshots into one JSONL trace
creates apparent duplicate calls and results even when each request is valid.
This is different from the paused-history fixture, where duplication is already
present **inside a single captured request**. See the existing
[snapshot-boundary controls](public-pattern-regressions.md#false-positive-boundary-replayed-snapshots).

For these complete-history examples, use the default missing-result check.
`--allow-incomplete` is for an intentionally unfinished snapshot: it can turn a
missing EOF result into an allowed pending call. It cannot prove that an outbound
request was complete or repair a request rejected by a provider.

## Re-run the validation

```sh
python3 -m unittest discover -s tests -p test_captured_request.py -v
python3 -m unittest discover -s tests -v
python3 -m compileall -q agent_trace_check tests examples/request_to_jsonl.py
```

Local Python 3.12.14 validation on October 7, 2026 passed all 26 recipe tests and
all 128 tests in the complete suite, including the 16 merged CI-guard tests.
The four documented fixture commands also matched their shown output and exit
codes. The optional `--require-calls` command was checked with both tool-bearing
and zero-call exports. See [validation record](captured-request-verification.txt).
