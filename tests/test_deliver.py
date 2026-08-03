"""claude_batch_runner.deliver tests — report-only end-to-end; agent calls always mocked."""

import json

import pytest

from claude_batch_runner import __main__ as cli, deliver, driver, spec as bspec

RUBRIC = (
    "---\nname: mini\nversion: 1.0.0\napplies_to: tests\ncriteria_count: 1\n---\n\n"
    "## 1. has-path\n**Statement:** names a path. **PASS:** present. **FAIL:** absent.\n"
)
OUTS = {"alpha": 0.9, "beta": 0.3, "gamma": 0.9}  # unit -> confidence; finding = initial
FULL = {
    "escalate": {"condition": "confidence < 0.6", "advisor": {"agent": "adv", "tier": "opus"}},
    "verify": {"rubric": "r.md", "agent": "v"},
}


def fake_call(agent, prompt, schema, budget):
    if "delta" in prompt:  # delta's worker dies -> fan_out coverage-gap envelope
        raise driver.AgentError("budget exceeded")
    if agent == "v":  # grader: gamma drafts (G, G2) always fail has-path
        verdict = "FAIL" if '"G' in prompt else "PASS"
        out = {"grades": [{"id": "has-path", "verdict": verdict, "evidence": "e"}]}
    elif agent == "adv":  # advisor envelope carries NO usage -> partial metering
        return {"structured_output": {"finding": "B2", "confidence": 0.9}}
    elif "FAILED CRITERIA" in prompt:  # worker revision (gamma)
        out = {"finding": "G2", "confidence": 0.9}
    else:
        u = next(k for k in OUTS if k in prompt)
        out = {"finding": u[0].upper(), "confidence": OUTS[u]}
    return {"structured_output": out, "usage": {"input_tokens": 100, "output_tokens": 50}}


def _campaign(tmp_path, units, extra=None):
    (tmp_path / "r.md").write_text(RUBRIC)
    (tmp_path / "wl.jsonl").write_text("".join(f"{u}\n" for u in units))
    sp = {"worklist": "wl.jsonl", "worker": {"agent": "w", "tier": "sonnet"}}
    sp |= {"schema": {"type": "object"}, "report": "out/report.md"} | (extra or {})
    (tmp_path / "c.json").write_text(json.dumps(sp))
    return bspec.load_campaign(tmp_path / "c.json")


def test_report_only_end_to_end_and_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    c = _campaign(tmp_path, ["alpha", "beta", "gamma", "delta"], FULL)
    res = deliver.run(c, "demo", call=fake_call)
    # alpha ok; beta escalates (0.3 < 0.6); gamma revises, still fails; delta: worker error
    expect = [("ok", "PASS"), ("escalated", "PASS"), ("FLAGGED", "FLAGGED"), ("FLAGGED", "-")]
    assert [(u.outcome, u.verdict) for u in res.units] == expect
    text = res.report_path.read_text()
    assert "ok 1 · escalated 1 · revised 0 · FLAGGED 2" in text
    assert "| u03 | gamma | FLAGGED | FLAGGED | 1 | 600 | failed criteria: has-path |" in text
    assert "worker error: budget exceeded" in text
    # sonnet: 4 workers + 1 revision; opus: advisor + 4 graders; 150 tok/call; 2/10 unmetered
    assert "| sonnet | 5 | 600 |" in text and "| opus | 5 | 600 |" in text
    assert "total: 10 call(s), 1200 token(s) metered" in text
    assert "2/10 envelope(s) carried no `usage`" in text and "token totals partial" in text
    assert "## Handoff" in text and "campaign demo:" in text
    monkeypatch.setattr(driver, "call_agent", fake_call)  # CLI end-to-end, still mocked
    assert cli.main(["--spec", str(tmp_path / "c.json")]) == 0
    assert "FLAGGED 2" in capsys.readouterr().out


def test_pr_dispatch_stub_and_condition_gate(tmp_path):
    contract = "(?s)worktree.*diff cap.*unpiped.*rev-parse --show-toplevel"
    with pytest.raises(NotImplementedError, match=contract):
        deliver.run(_campaign(tmp_path, ["u"], {"deliver": "pr-dispatch"}), "d")
    assert cli.main(["--spec", str(tmp_path / "c.json")]) == 2  # the CLI surfaces the stub
    bad = {"escalate": {"condition": "vibes are off", "advisor": {"agent": "adv"}}}
    with pytest.raises(bspec.SpecError, match="escalate.condition"):
        deliver.run(_campaign(tmp_path, ["u"], bad), "d", call=lambda *a: pytest.fail("no call"))


def test_run_confines_worker_advisor_and_grader_alike(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen: list = []

    def confined(agent, prompt, schema, budget, **opts):
        seen.append((agent, opts))
        return fake_call(agent, prompt, schema, budget)

    c = _campaign(tmp_path, ["beta"], FULL)  # beta escalates: worker -> advisor -> grader
    deliver.run(c, "demo", call=confined, cwd=tmp_path, permission_mode="plan")
    assert {agent for agent, _ in seen} == {"w", "adv", "v"}
    assert all(opts == {"cwd": tmp_path, "permission_mode": "plan"} for _, opts in seen)


def test_run_without_options_keeps_the_four_argument_call_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = _campaign(tmp_path, ["beta"], FULL)  # fake_call accepts exactly four positionals
    assert [u.outcome for u in deliver.run(c, "demo", call=fake_call).units] == ["escalated"]
