# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.1 - 2026-08-03

### Added

- Windows installer, built and attached to a `v*` tag's GitHub release by
  `.github/workflows/release.yml`: a PyInstaller one-file executable wrapped in
  an Inno Setup installer (`installer/`) that installs into Program Files, adds
  itself to `PATH`, and registers an uninstaller. It does not bundle the Claude
  Code CLI and it is not code-signed; both are stated in the installer and the
  README.

## 0.1.0

First public release.

### Added

- **Parallel `claude -p` driver** (`claude_batch_runner.driver`) — `AgentTask`,
  `call_agent`, and `fan_out`. One subprocess per unit of work, schema-validated
  structured output, and an envelope-aware failure description that catches the
  case where the CLI reports an error with exit code 0. A failed task returns an
  `{"_error": ...}` envelope so a coverage gap is visible instead of missing.
- **Declarative campaign spec** (`claude_batch_runner.spec`) — one JSON file
  describing worklist, worker, parallelism, escalation, output schema,
  verification, delivery, and report path. Boundary validation rejects unknown
  fields, missing fields, and bad types with field-named `SpecError`s before any
  agent is dispatched.
- **Caller-supplied model-tier vocabulary** (`Tiers`) — tier names and the
  cheapest/executor/advisor roles are configuration, not constants, so the
  runner is not tied to one provider's tier list.
- **Rubric verification** (`claude_batch_runner.verify`) — markdown rubric
  parsing with a `criteria_count` integrity check, a grader that must return
  PASS/FAIL plus evidence for every criterion, a bounded revision loop, and a
  `FLAGGED` terminal state. The verify step refuses to run on the cheapest tier.
- **Data-only framing** for every piece of model-produced content that reaches
  another model, so reviewed text cannot act as instructions to its reviewer.
- **Report-only delivery adapter** (`claude_batch_runner.deliver`) — executes a
  campaign end to end and writes a markdown report with per-unit outcomes and a
  cost table metering calls and tokens per tier, including an explicit note when
  a result envelope carried no usage data.
- **CLI** — `python -m claude_batch_runner --spec <path> [--dry-run]`;
  `--dry-run` validates and prints the dispatch plan without spending anything.
- **Worked example** (`examples/health-check/`) — a code-orchestrated project
  health check that fans seven category agents out in parallel, scores their
  findings with a weighted shell scorer, and writes a report to a
  non-colliding path.
- **Example agent definitions, rubric, campaign, and worklist**, plus two
  JavaScript workflow programs demonstrating the same tiered escalation pattern
  under an agent-workflow host.

### Known limitations

- `deliver: "pr-dispatch"` is a stub; it raises with its designed contract.
- Escalation conditions are a single `<field> <op> <number>` comparison.
- A campaign that fails partway through cannot be resumed.
