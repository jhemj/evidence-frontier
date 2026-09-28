# Frontier — public architecture review snapshot

This is a code-only review snapshot, not a completed forensic product or an
accepted incident report. It has no parent commit or imported working-tree
history. Real evidence, runtime data, local configuration, operational logs,
incident-specific reports and model transcripts are intentionally absent.

Start with [the architecture questions](docs/PRO_ARCHITECTURE_REVIEW.md).
The questions and source map are in Korean. The goal is a flexible, defensible
local-LLM forensic agent with minimal user intervention, not case-specific rules.

## Offline synthetic tests

Python 3.12 or 3.13 is supported. Install the locked development dependencies,
then run `python -m pytest -q`. Worker/parser tests also require the locked worker
dependencies. UI logic tests require Node.js on PATH or `FRONTIER_TEST_NODE`.
Tests use synthetic fixtures and must not require real case data.

Optional probe scripts are source code, not test results. They require explicitly
provided inputs; no private inputs or live service configuration are included.
Do not run live model or worker probes merely to review the architecture.

Review priorities: immutable provenance, bounded context and resumable review,
contrary-evidence retention, agent-chosen discriminating checks, meaningful
completion criteria, relevance versus fact confidence, and usable reports.

See `REVIEW_VALIDATION.md` for the exact snapshot verification scope.
