# Contributing

Small, reproducible improvements are welcome. Issues and pull requests may be
written in English or Chinese.

## Report a bug

Include:

- Your Python version, operating system, command, and input format
- A minimal synthetic JSONL example
- Expected behavior and the actual exit code/report

Do not upload raw production traces, credentials, personal information, or
proprietary content. The checker omits payload bodies from reports, but IDs may
still be sensitive. Replace them with synthetic IDs before sharing.

## Make a change

1. Open an issue first for new adapters or changes to schema/report semantics.
2. Fork the repository and create a focused branch.
3. Add a regression test that demonstrates the bug or requested behavior.
4. Keep runtime dependencies within the Python standard library and support
   Python 3.10+.
5. Run these commands from the project root:

   ```sh
   python3 -m unittest discover -s tests -v
   python3 -m compileall -q agent_trace_check tests
   ```

6. Update the README if behavior, diagnostics, formats, or limitations change.
7. Open a pull request describing the problem, solution, and checks you ran.
   Mark it as a draft if work or verification remains.

The normalized schema and JSON report are versioned. Treat diagnostic codes,
exit codes, per-trace ID scope, atomic record rejection, and payload-free error
messages as compatibility-sensitive behavior.

## Useful starting points

- Add a minimal test for a missed lifecycle edge case
- Validate the documented format using a redacted real-world export and report
  where adaptation is confusing
- Improve examples or translations
- Propose one well-specified adapter with fixtures and an explicit unsupported
  input policy

Avoid broad provider support claims without fixtures and documented boundaries.
Do not submit copies of private logs or code that you cannot contribute.

## AI-assisted contributions

AI-assisted work is welcome. Disclose material assistance in the pull request,
review the diff, run the tests, and check that any added source or fixtures may
be shared under this project's MIT license. Generated code still needs a
reproducible explanation and tests.

By contributing, you agree to make your contribution available under the
project's MIT license. Keep existing copyright and license notices intact.
