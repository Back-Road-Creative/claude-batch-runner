# claude-batch-runner

Run a batch of LLM agent calls the way you'd run a batch job: describe the work
in a file, let code decide what runs and in what order, and get back a report
that tells you what happened and what it cost.

The runner shells out to the [Claude Code](https://docs.claude.com/en/docs/claude-code)
CLI (`claude -p`), one process per unit of work, in parallel. It has no runtime
dependencies beyond the Python standard library.

## Why this exists

The usual way to fan an agent out over fifty files is to ask an agent to do it.
That works until it doesn't: the orchestrating model forgets a file, decides
halfway through that ten is enough, or quietly reports success for a unit that
failed. Control flow written in English is not control flow.

So the loop is code and only the judgement is a model call:

- **The spec is validated before anything runs.** An unknown field, a bad tier
  name, a missing worklist — all rejected with a field-named error, at zero
  cost. A campaign that would fail at unit 40 fails at parse instead.
- **A failed unit is a reported gap, not a silent hole.** A worker that dies
  comes back as a `FLAGGED` row with its error, and the rest of the batch
  continues.
- **Cheap work runs on a cheap model; only the hard units escalate.** You write
  the escalation condition; the runner evaluates it against the worker's own
  output.
- **A verify pass grades output against a rubric you wrote**, gets one bounded
  chance to fix a failure, and flags whatever still fails. It never
  auto-passes, and it refuses to run on the cheapest tier.
- **The report meters itself** — calls per tier, tokens per tier, and an honest
  note when a result carried no usage data.

## Install

Not on PyPI yet — install from a checkout:

```bash
git clone <this repo> && cd claude-batch-runner
pip install .
```

You also need the `claude` CLI on your PATH, already authenticated. The runner
uses whatever authentication the CLI is configured with; it never reads or sets
credentials of its own.

```bash
claude --version   # if this fails, the runner will too
```

Python 3.11 or newer.

### Windows installer

Each release also carries `claude-batch-runner-setup-<version>.exe` on its
[releases page](https://github.com/Back-Road-Creative/claude-batch-runner/releases)
— the runner frozen into one executable, so it needs no Python. It installs
into Program Files, appends that directory to the system `PATH`, and registers
an uninstaller in Add/Remove Programs. Open a *new* terminal afterwards; one
that was already open still holds the old `PATH`.

Two things to know before downloading it.

**Claude Code is not bundled, and the runner cannot do anything without it.**
Every unit of work is a `claude -p` subprocess, so with no `claude` on `PATH`
each unit comes back `FLAGGED` with an error and the campaign accomplishes
nothing. Claude Code is separately versioned and self-updating, so it installs
itself:

```powershell
winget install Anthropic.ClaudeCode
```

The [native installer](https://code.claude.com/docs/en/setup) —
`irm https://claude.ai/install.ps1 | iex` — works too and auto-updates. Confirm
with `claude --version`, then run `claude` once and follow the browser prompts
to sign in. The runner never reads, sets, or forwards credentials of its own;
it inherits whatever that login leaves configured.

**The build is not code-signed.** There is no code-signing certificate for this
project, so Windows cannot show you a publisher. Expect the blue *"Windows
protected your PC"* box — "Microsoft Defender SmartScreen prevented an
unrecognized app from starting" — which runs the installer only after **More
info** → **Run anyway**, and expect your browser to warn during the download.
That is simply what an unsigned binary looks like; it is not evidence the file
is safe. If you would rather not make that call, `pip install .` from a
checkout needs no installer.

## Quick start

A campaign is one JSON file. This one reviews three documentation files:

```json
{
  "worklist": "demo-worklist.jsonl",
  "worker": {"agent": "doc-reviewer", "tier": "sonnet", "effort": "low"},
  "parallelism": 2,
  "escalate": {"condition": "confidence < 0.6",
               "advisor": {"agent": "batch-advisor", "tier": "opus"}},
  "schema": {"type": "object",
             "properties": {"finding": {"type": "string"},
                            "confidence": {"type": "number"}},
             "required": ["finding", "confidence"]},
  "verify": {"rubric": "rubrics/finding-validity.md", "agent": "batch-verifier"},
  "deliver": "report-only",
  "report": "reports/{date}-demo-campaign.md"
}
```

The worklist is one unit per line — a JSON object, or just a bare string:

```
{"path": "README.md", "focus": "install instructions actually work"}
{"path": "CONTRIBUTING.md", "focus": "the listed gate commands exist"}
docs/getting-started.md
```

Check it without spending anything:

```bash
python -m claude_batch_runner --spec examples/demo-campaign.json --dry-run
```

```
campaign spec: examples/demo-campaign.json [valid] — 3 unit(s) from .../demo-worklist.jsonl
worker: doc-reviewer (tier=sonnet, effort=low)
parallelism: 2
escalate: when 'confidence < 0.6' -> batch-advisor (tier=opus, effort=high)
schema: object, required keys: finding, confidence
verify: rubric finding-validity v1.0.0 (5 criteria), grader batch-verifier (tier=opus, effort=high)
deliver: report-only — report: reports/2026-01-01-demo-campaign.md
dry run only: no agents dispatched.
```

Then run it for real by dropping `--dry-run`. That dispatches agents and costs
money — the agents named in the spec (`doc-reviewer`, `batch-advisor`,
`batch-verifier`) must exist in your Claude Code setup first; copies you can
install are in [`examples/agents/`](examples/agents/).

## The campaign spec

| Field | Required | Meaning |
|---|---|---|
| `worklist` | yes | Path to the unit file, relative to the spec. One unit per line: a JSON value, or a plain string. |
| `worker` | yes | `{agent, tier, effort}` — the cheap executor that processes each unit. |
| `schema` | yes | JSON schema every worker result must match. Passed to the CLI as `--json-schema`. |
| `parallelism` | no | Concurrent workers. Default `min(units, 4)`. |
| `escalate` | no | `{condition, advisor}`. When the condition holds on a worker's output, a stronger agent redoes the unit. |
| `verify` | no | `{rubric, agent, tier, effort, max_revisions}`. Grades each unit; see below. |
| `deliver` | no | `report-only` (default) or `pr-dispatch`. |
| `report` | no | Output path template. `{date}` and `{campaign}` are substituted. |

`escalate.condition` is deliberately not an expression language. It is
`<field> <op> <number>` — `confidence < 0.6`, `severity >= 3` — checked against
a numeric field of the worker's own structured output. It is parsed *before*
the batch runs, so a typo costs nothing instead of failing after 39 units.

Anything else is a `SpecError` naming the field.

### Model tiers are yours to define

Tier names are not baked in. The default vocabulary is Claude's three public
tiers, with the roles the runner needs:

```python
from claude_batch_runner import Tiers, load_campaign

tiers = Tiers(names=("small", "large"),
              cheapest="small",     # verify may never run on this
              executor="small",     # default tier for workers
              advisor="large")      # default tier for advisors and graders

campaign = load_campaign("campaign.json", tiers=tiers)
```

## Rubric verification

A rubric is a markdown file with frontmatter and one `## N. <id>` section per
criterion. `criteria_count` must match the number of sections — a rubric that
lost a criterion in an edit fails to load instead of silently grading fewer
things.

```markdown
---
name: finding-validity
version: 1.0.0
applies_to: a single finding produced by an automated code-review agent
criteria_count: 2
---

## 1. file-line-cited
**Statement:** the claim points at a specific location.
**PASS:** names a file path and a line number.
**FAIL:** refers to "the config" with no line-level pointer.

## 2. one-line-verify-command
**Statement:** the claim is checkable with one shell command.
**PASS:** a single command reproduces the claimed fact.
**FAIL:** no such command exists, or it contradicts the claim.
```

The grader returns PASS or FAIL plus concrete evidence for every criterion. A
verdict with no evidence is rejected as malformed. On any FAIL, the *worker* is
re-invoked with the failed criteria attached and gets `max_revisions` attempts
(default 1); if it still fails, the unit is `FLAGGED` in the report. Nothing is
dropped and nothing auto-passes.

Everything the grader reads is wrapped in a data-only frame, so a unit's own
text can't act as instructions to the grader.

## The report

```markdown
# Campaign report: demo-campaign

- 2026-01-01 · report-only · wall-clock 42.3s · worker doc-reviewer (sonnet/low) · parallelism 2
- units: 4 unit(s) — ok 1 · escalated 1 · revised 0 · FLAGGED 2

## Units

| unit | input | outcome | verify | revisions | tokens | note |
|---|---|---|---|---|---|---|
| u01 | README.md | ok | PASS | 0 | 150 | - |
| u02 | CONTRIBUTING.md | escalated | PASS | 0 | 300 | advisor ran: confidence < 0.6 |
| u03 | docs/index.md | FLAGGED | FLAGGED | 1 | 600 | failed criteria: file-line-cited |
| u04 | docs/api.md | FLAGGED | - | 0 | - | worker error: budget exceeded |

## Cost

| tier | calls | tokens |
|---|---|---|
| opus | 5 | 600 |
| sonnet | 5 | 600 |

- total: 10 call(s), 1200 token(s) metered
- 2/10 envelope(s) carried no `usage` — call counts are exact, token totals partial.
```

Call counts are exact. Token totals come from each result envelope's `usage`
when the CLI provides one, and the report says how many envelopes carried none
rather than quietly under-reporting.

## Using the driver on its own

You don't need a campaign spec to use the fan-out:

```python
from claude_batch_runner import AgentTask, fan_out

schema = {"type": "object", "properties": {"score": {"type": "number"}},
          "required": ["score"]}
tasks = {
    name: AgentTask(agent=f"review-{name}", prompt=f"Review the {name} area.",
                    schema=schema, budget_usd=5.0)
    for name in ("api", "storage", "auth")
}

for name, envelope in fan_out(tasks, max_workers=3).items():
    if "_error" in envelope:
        print(f"{name}: FAILED — {envelope['_error']}")
    else:
        print(f"{name}: {envelope['structured_output']['score']}")
```

`fan_out` returns one entry per task, always. A task whose agent failed comes
back as `{"_error": "..."}` instead of vanishing, so a coverage gap is visible
in the results rather than in a count you forgot to check.

`--max-budget-usd` is a computed-cost ceiling that *aborts* the run when the
estimate crosses it. Set it as a runaway-loop stop, generously — tuning it down
just moves the abort onto whichever unit reads the most.

### Confining a run to a directory

`call_agent` takes two optional keyword arguments for running agents somewhere
other than the caller's own directory, on tighter permissions:

```python
from claude_batch_runner import call_agent

envelope = call_agent("doc-reviewer", prompt, schema, 5.0,
                      cwd="/tmp/eval-fixture",   # the subprocess's working directory
                      permission_mode="plan")    # claude --permission-mode
```

`cwd` must be an existing directory. A missing one raises `AgentError` *before*
the subprocess starts, so a bad path inside a fan-out comes back as that unit's
`{"_error": ...}` row rather than taking the batch down. `permission_mode` is
handed straight to the CLI — the accepted modes are its vocabulary, not the
runner's, so a mode the CLI adds later works without a release here.

Both are accepted by `fan_out(tasks, cwd=..., permission_mode=...)`, which
applies them to every task, and by `deliver.run(campaign, name, cwd=...,
permission_mode=...)`, which applies them to every call a campaign makes —
worker, advisor, and grader alike, so an escalation cannot step outside the
sandbox its worker ran in.

Both default to `None`, and omitting them changes nothing: no
`--permission-mode` in the argv, no `cwd` on the subprocess call, and an
injected `call=` written against the four-argument signature is invoked exactly
as before.

## Examples

- [`examples/demo-campaign.json`](examples/) — the campaign above, runnable
  with `--dry-run` out of the box.
- [`examples/agents/`](examples/agents/) — agent definitions for the worker,
  advisor, and rubric grader.
- [`examples/health-check/`](examples/health-check/) — a complete worked
  application. A Python controller fans seven category agents over one project
  in parallel, scores their findings with a weighted shell script, asks one
  strategy agent for a verdict, and writes a report. It is the fullest example
  of the split this library is built around: code owns the loop, models own the
  judgement.
- [`examples/workflows/`](examples/workflows/) — two JavaScript workflow
  programs showing the same tiered patterns. **They do not run standalone** —
  they expect an agent-workflow host that supplies `agent()`, `parallel()`,
  `pipeline()`, `phase()`, `log()`, and `budget` as ambient globals. Read them
  for the design; port the globals if you want to run them.

## Limits

Worth knowing before you adopt this:

- **`deliver: "pr-dispatch"` is a stub.** It raises `NotImplementedError` with
  its designed contract rather than half-running. `report-only` is the working
  adapter.
- **Escalation conditions are one comparison** against one numeric field. No
  boolean logic, no nesting. If you need more, filter your worklist instead.
- **Rubric parsing is deliberately dumb** — frontmatter plus `## N. <id>`
  headings, no markdown AST. Unusual formatting will not parse.
- **Token metering depends on the CLI** returning `usage` on the result
  envelope. When it doesn't, the report says so; it does not guess.
- **The verify pass costs real money.** A campaign with verify enabled makes at
  least two calls per unit, the second on a stronger tier. That is the point,
  but budget for it.
- **Concurrency is threads around subprocesses**, so `parallelism` is bounded
  by what your machine and your rate limits tolerate, not by the GIL.
- **No resume.** A campaign that dies mid-run starts over. Split large
  worklists.

## Development

```bash
git clone <this repo>
cd claude-batch-runner
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

The test suite never spawns a real `claude` process — every agent call is
mocked or injected — so it runs offline, in CI, and costs nothing. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
