"""claude_batch_runner.spec + --dry-run CLI tests. Never spawns a real `claude` process."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from claude_batch_runner import spec as bspec

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_SPEC = REPO_ROOT / "examples" / "demo-campaign.json"


def _spec_dict(**over):
    base = {"worklist": "wl.jsonl", "worker": {"agent": "a"}, "schema": {"type": "object"}}
    base.update(over)
    return base


def _write(tmp_path, sp=None, worklist='{"x": 1}\n'):
    (tmp_path / "wl.jsonl").write_text(worklist)
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(_spec_dict() if sp is None else sp))
    return path


def test_demo_spec_loads():
    c = bspec.load_campaign(DEMO_SPEC)
    assert len(c.units) == 3
    assert c.parallelism == 2
    assert c.worker == bspec.Worker(agent="doc-reviewer", tier="sonnet", effort="low")
    assert c.escalate.advisor.tier == "opus" and c.escalate.advisor.effort == "high"
    assert c.deliver == "report-only" and c.verify is not None


def test_defaults_cheap_tier_and_parallelism(tmp_path):
    c = bspec.load_campaign(_write(tmp_path, worklist="u1\nu2\n"))
    assert c.parallelism == 2  # min(#units, 4)
    assert c.worker.tier == "sonnet" and c.worker.effort == "low"
    assert c.escalate is None and c.verify is None and c.deliver == "report-only"
    wl9 = "".join(f"u{i}\n" for i in range(9))
    assert bspec.load_campaign(_write(tmp_path, worklist=wl9)).parallelism == 4
    mixed = bspec.load_campaign(_write(tmp_path, worklist='{"a": 1}\n\nplain-unit\n'))
    assert mixed.units == ({"a": 1}, "plain-unit")  # JSON lines parse; plain lines stay raw


@pytest.mark.parametrize(
    ("sp", "match"),
    [
        (_spec_dict(bogus=1), "unknown spec field.*bogus"),
        ({k: v for k, v in _spec_dict().items() if k != "worker"}, "missing required.*worker"),
        (_spec_dict(worker={"agent": ""}), "worker.agent"),
        (_spec_dict(worker={"agent": "a", "tier": "nonesuch"}), "worker.tier"),
        (_spec_dict(parallelism="four"), "parallelism must be a positive integer"),
        (_spec_dict(deliver="email"), "deliver must be one of"),
        (_spec_dict(schema={"type": "nope"}), "invalid JSON-schema type"),
        (_spec_dict(schema={"type": "object", "required": "x"}), "schema.required"),
        (_spec_dict(escalate={"condition": "x"}), "escalate.advisor"),
        (_spec_dict(verify="rubric.md"), "verify must be an object"),
    ],
)
def test_invalid_specs_reject_with_clear_message(tmp_path, sp, match):
    with pytest.raises(bspec.SpecError, match=match):
        bspec.load_campaign(_write(tmp_path, sp=sp))


def test_missing_and_empty_worklist_reject(tmp_path):
    (tmp_path / "campaign.json").write_text(json.dumps(_spec_dict()))
    with pytest.raises(bspec.SpecError, match="worklist file not found"):
        bspec.load_campaign(tmp_path / "campaign.json")
    with pytest.raises(bspec.SpecError, match="worklist is empty"):
        bspec.load_campaign(_write(tmp_path, worklist="\n"))


# --- caller-supplied tier vocabulary ------------------------------------------


def test_tiers_vocabulary_is_caller_supplied(tmp_path):
    tiers = bspec.Tiers(names=("small", "large"), cheapest="small", executor="small", advisor="large")
    sp = _spec_dict(worker={"agent": "a", "tier": "large"})
    c = bspec.load_campaign(_write(tmp_path, sp=sp), tiers=tiers)
    assert c.worker.tier == "large"
    assert bspec.load_campaign(_write(tmp_path), tiers=tiers).worker.tier == "small"  # executor
    with pytest.raises(bspec.SpecError, match=r"worker.tier must be one of \('small', 'large'\)"):
        bspec.load_campaign(_write(tmp_path, sp=_spec_dict(worker={"agent": "a", "tier": "opus"})),
                            tiers=tiers)


def test_tiers_roles_must_name_real_tiers():
    with pytest.raises(ValueError, match="Tiers.advisor='huge' is not one of"):
        bspec.Tiers(names=("small",), cheapest="small", executor="small", advisor="huge")
    with pytest.raises(ValueError, match="Tiers.names must not be empty"):
        bspec.Tiers(names=(), cheapest="x", executor="x", advisor="x")


# --- CLI ----------------------------------------------------------------------


def _cli(*args):
    cmd = [sys.executable, "-m", "claude_batch_runner", *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60)


def test_dry_run_prints_plan_with_adapter_and_report_path():
    proc = _cli("--spec", "examples/demo-campaign.json", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "3 unit(s)" in proc.stdout and "parallelism: 2" in proc.stdout
    assert "doc-reviewer (tier=sonnet, effort=low)" in proc.stdout
    assert "rubric finding-validity v1.0.0 (5 criteria)" in proc.stdout
    assert "grader batch-verifier (tier=opus, effort=high)" in proc.stdout
    assert "deliver: report-only — report: reports/" in proc.stdout
    assert "no agents dispatched" in proc.stdout  # execution paths live in test_deliver
