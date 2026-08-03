"""Delivery adapters: execute a validated campaign and deliver results.

`run` picks the spec's `deliver` adapter: `report-only` (worker fan-out at
spec parallelism -> escalate -> verify -> campaign report; no git operations)
or the `pr-dispatch` stub, which raises with its designed contract rather than
pretending to work (PR_DISPATCH_CONTRACT). A real run subprocesses the
`claude` CLI on PATH; tests inject `call=` and never spawn a process.

Metering: per-tier call counts are exact; token totals read each result
envelope's `usage` when the CLI provides one, and the report says how many
envelopes carried none rather than quietly under-reporting."""

from __future__ import annotations

import datetime
import json
import re
import time
from operator import eq, ge, gt, le, lt, ne
from collections import Counter, namedtuple
from pathlib import Path
from typing import Any

from claude_batch_runner import driver
from claude_batch_runner.spec import Campaign, SpecError
from claude_batch_runner.verify import FLAGGED, VerifyError, frame, load_rubric, verify_unit

UNIT_BUDGET_USD = 5.0  # per worker/advisor call; graders use verify.GRADER_BUDGET_USD
_OPS = {"<": lt, "<=": le, ">": gt, ">=": ge, "==": eq, "!=": ne}
_CONDITION = re.compile(r"(\w+)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)")
PR_DISPATCH_CONTRACT = (
    "deliver=pr-dispatch is a stub, and raises rather than half-running. Designed contract: "
    "one worktree + branch per unit off the default branch; each PR under a fixed diff cap; the "
    "DISPATCHER, never a worker, runs the suite unpiped and reads `0 failed` before commit; "
    "commits refuse when `git rev-parse --show-toplevel` != the assigned worktree; no two units "
    "share a checkout. Implement it against live report-only campaigns, not in the abstract."
)
UnitReport = namedtuple("UnitReport", "key label outcome verdict revisions tokens note")
CampaignResult = namedtuple("CampaignResult", "units report_path duration_s")


def report_path(campaign: Campaign, name: str) -> Path:
    return Path(campaign.report.format(date=datetime.date.today().isoformat(), campaign=name))


def run(
    campaign: Campaign,
    name: str,
    *,
    call: Any = None,
    cwd: str | Path | None = None,
    permission_mode: str | None = None,
) -> CampaignResult:
    """Execute `campaign` per its deliver adapter; `name` keys the report path.

    `cwd` and `permission_mode` (see `driver.call_agent`) confine every call the
    campaign makes — worker, advisor, and grader alike, so an escalation cannot
    step outside the sandbox its worker ran in. Omitted, the run is unchanged.
    """
    if campaign.deliver == "pr-dispatch":
        raise NotImplementedError(PR_DISPATCH_CONTRACT)
    opts = driver.agent_options(cwd, permission_mode)
    return _report_only(campaign, name, call or driver.call_agent, opts)


def _report_only(campaign: Campaign, name: str, call: Any, opts: dict[str, Any]) -> CampaignResult:
    start, cond = time.monotonic(), None
    if campaign.escalate:  # upstream gate: a bad condition fails before any tokens burn
        raw = campaign.escalate.condition
        if (m := _CONDITION.fullmatch(raw.strip())) is None:
            raise SpecError(f"escalate.condition must be '<field> <op> <number>', got {raw!r}")
        cond = (m.group(1), _OPS[m.group(2)], float(m.group(3)))
    rubric = load_rubric(campaign.verify.rubric) if campaign.verify else None
    meter = {"calls": Counter(), "tokens": Counter(), "unmetered": 0}
    extras = (campaign.escalate.advisor if campaign.escalate else None, campaign.verify)
    tiers = {p.agent: p.tier for p in (campaign.worker, *extras) if p}
    tasks = {
        f"u{i:02d}": driver.AgentTask(
            campaign.worker.agent,
            "Process this work unit; return output matching the required schema.\n"
            + frame("WORK UNIT", u if isinstance(u, str) else json.dumps(u, indent=2)),
            campaign.schema,
            UNIT_BUDGET_USD,
        )
        for i, u in enumerate(campaign.units, 1)
    }
    envelopes = driver.fan_out(tasks, call=call, max_workers=campaign.parallelism, **opts)
    units = tuple(
        _finish_unit(
            key, u, tasks[key], envelopes[key], campaign, cond, rubric, meter, tiers, call, opts
        )
        for key, u in zip(tasks, campaign.units)
    )
    duration = time.monotonic() - start
    return CampaignResult(units, _write_report(campaign, name, units, meter, duration), duration)


def _finish_unit(key, unit, task, envelope, campaign, cond, rubric, meter, tiers, call, opts):
    label = (unit if isinstance(unit, str) else json.dumps(unit)).replace("|", "\\|")[:48]
    acc = {"tokens": 0, "metered": False}

    def tally(tier: str, env: Any) -> None:  # cost metering: count the call, read its usage
        meter["calls"][tier] += 1
        usage = env.get("usage") if isinstance(env, dict) else None
        if not isinstance(usage, dict):
            meter["unmetered"] += 1
            return
        toks = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        meter["tokens"][tier] += toks
        acc["tokens"], acc["metered"] = acc["tokens"] + toks, True

    def done(outcome, verdict, revisions, note):  # tokens None = no usage seen for this unit
        toks = acc["tokens"] if acc["metered"] else None
        return UnitReport(key, label, outcome, verdict, revisions, toks, note)

    def metered(agent, prompt, schema, budget):  # verify-pass calls: grader + worker revisions
        env = call(agent, prompt, schema, budget, **opts)
        tally(tiers.get(agent, "?"), env)
        return env

    tally(campaign.worker.tier, envelope)
    if "_error" in envelope:
        return done(FLAGGED, "-", 0, f"worker error: {envelope['_error']}")
    output, outcome, note = envelope.get("structured_output", envelope.get("result")), "ok", ""
    value = output.get(cond[0]) if cond and isinstance(output, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and cond[1](value, cond[2]):
        advisor, condition = campaign.escalate.advisor, campaign.escalate.condition
        draft = output if isinstance(output, str) else json.dumps(output, indent=2)
        prompt = (
            f"{task.prompt}\n\nA cheap-tier worker draft tripped the escalation condition "
            f"{condition!r}. Produce the authoritative output.\n" + frame("WORKER DRAFT", draft)
        )
        try:
            adv_env = call(advisor.agent, prompt, campaign.schema, UNIT_BUDGET_USD, **opts)
        except driver.AgentError as e:
            return done(FLAGGED, "-", 0, f"advisor error: {e}")
        tally(advisor.tier, adv_env)
        output, outcome = adv_env.get("structured_output", adv_env.get("result")), "escalated"
        note = f"advisor ran: {condition}"
    if rubric is None:
        return done(outcome, "-", 0, note)
    try:
        v = verify_unit(rubric, campaign.verify, task, output, call=metered)
    except (VerifyError, driver.AgentError) as e:
        return done(FLAGGED, "-", 0, f"verify error: {e}")
    if v.status == FLAGGED:
        fails = ", ".join(g["id"] for g in v.grades if g["verdict"] == "FAIL")
        return done(FLAGGED, FLAGGED, v.revisions, f"failed criteria: {fails}")
    return done("revised" if v.revisions else outcome, v.status, v.revisions, note)


def _write_report(campaign, name, units, meter, duration_s) -> Path:
    path = report_path(campaign, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    n, w = Counter(u.outcome for u in units), campaign.worker
    summary = (
        f"{len(units)} unit(s) — ok {n['ok']} · escalated {n['escalated']} · "
        f"revised {n['revised']} · FLAGGED {n[FLAGGED]}"
    )
    calls, tokens = sum(meter["calls"].values()), sum(meter["tokens"].values())
    rows = "\n".join(
        f"| {u.key} | {u.label} | {u.outcome} | {u.verdict} | {u.revisions} "
        f"| {'-' if u.tokens is None else u.tokens} | {u.note or '-'} |"
        for u in units
    )
    cost = "\n".join(
        f"| {t} | {meter['calls'][t]} | {meter['tokens'][t]} |" for t in sorted(meter["calls"])
    )
    gap = (
        f"\n- {meter['unmetered']}/{calls} envelope(s) carried no `usage` — call counts are "
        "exact, token totals partial."
    ) * bool(meter["unmetered"])
    by_tier = ", ".join(f"{t} {c}" for t, c in sorted(meter["calls"].items()))
    path.write_text(
        f"# Campaign report: {name}\n\n"
        f"- {datetime.date.today().isoformat()} · report-only · wall-clock {duration_s:.1f}s · "
        f"worker {w.agent} ({w.tier}/{w.effort}) · parallelism {campaign.parallelism}\n"
        f"- units: {summary}\n\n"
        "## Units\n\n| unit | input | outcome | verify | revisions | tokens | note |\n"
        f"|---|---|---|---|---|---|---|\n{rows}\n\n"
        f"## Cost\n\n| tier | calls | tokens |\n|---|---|---|\n{cost}\n\n"
        f"- total: {calls} call(s), {tokens} token(s) metered{gap}\n\n"
        "## Handoff\n\n```\n"
        f"campaign {name}: {summary}\n"
        f"cost: {calls} call(s) ({by_tier}), {tokens} token(s) metered, "
        f"{duration_s:.0f}s wall-clock\n"
        f"next: review FLAGGED rows in {path}, then re-run\n"
        "```\n"
    )
    return path
