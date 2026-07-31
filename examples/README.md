# Examples

Four things live here, in rough order of how useful they are when you're
starting out.

## 1. A runnable campaign

`demo-campaign.json` + `demo-worklist.jsonl` + `rubrics/finding-validity.md`.

Validate it without spending anything:

```bash
python -m claude_batch_runner --spec examples/demo-campaign.json --dry-run
```

Drop `--dry-run` to actually run it. That dispatches agents and costs money, and
it needs the three agents in `agents/` installed first.

## 2. Agent definitions

`agents/` holds the three agents the demo campaign names:

| File | Role in the campaign |
|---|---|
| `doc-reviewer.md` | the cheap `worker` — one unit in, one finding out |
| `batch-advisor.md` | the `escalate.advisor` — redoes units the worker was unsure about |
| `batch-verifier.md` | the `verify.agent` — grades output against the rubric |

`health-check/agents/` holds the eight the worked example needs.

These are examples, not a framework. Install them wherever your Claude Code
setup keeps agent definitions (commonly `.claude/agents/` in a project, or the
user-level equivalent), or point the spec at agents you already have. The
runner only ever passes the name through to `claude -p --agent <name>`; it does
not read these files.

## 3. A complete worked application

`health-check/` — a project health check built the way this library is meant to
be used.

```bash
pip install -e .          # health-check.py imports claude_batch_runner
python examples/health-check/health-check.py --cwd /path/to/project
python examples/health-check/health-check.py --categories security,flaws --cwd .
```

What it does: scan the project once with a shell script, fan seven category
agents over that one briefing in parallel, score their findings with a weighted
shell scorer, ask one strategy agent for a verdict, and write a markdown report
to a path that never collides with an earlier run.

What's worth stealing from it:

- **Control flow is code.** The controller decides what runs, in what order,
  and what to do when an agent dies. No model chooses any of that.
- **One scan, seven agents.** `project-scan.sh` runs once; its output is pasted
  into every category prompt. Seven agents do not each rediscover the layout.
- **The arithmetic is a shell script.** `compute-health-score.sh` does the
  weighted average. Asking a model to average seven numbers is how you get a
  different answer each run.
- **A dead agent is a visible gap.** A category whose agent fails becomes a
  "coverage gap" row in the report, not a missing section nobody notices.
- **Reports never overwrite.** `next-report-path.sh` returns
  `DATE-health.md`, then `-2`, then `-3`.

`--report-dir` controls where reports go (default `./health-reports`).

## 4. Two workflow programs, for reading

`workflows/tiered-wave.js` and `workflows/tiered-audit-loop.js`.

**Neither runs standalone.** They are not Node scripts. They are programs for an
agent-workflow host that supplies `agent()`, `parallel()`, `pipeline()`,
`phase()`, `log()`, and `budget` as ambient globals, and `node file.js` will
fail. Each file says so at the top.

They're included because they show the same two ideas this library implements,
in a form short enough to read in one sitting:

- **tiered-wave** — a cheap executor handles every unit; only units the executor
  flags as hard or low-confidence go to an expensive advisor. Most of the batch
  bills at the cheap rate. That is what `escalate.condition` does in a campaign
  spec.
- **tiered-audit-loop** — cheap finders sweep repeatedly until two consecutive
  rounds turn up nothing new, and every fresh finding must survive an
  adversarial verifier that is asked to *refute* it. That is what `verify` plus
  a rubric does in a campaign spec.

Each has one example environment in its `CFG` map. Add your own keys.
