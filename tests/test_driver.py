"""Unit tests for claude_batch_runner.driver — the parallel `claude -p` fan-out.

The subprocess layer is always mocked (monkeypatched `subprocess.run` or an
injected `call=`); no test ever spawns a real `claude` process.
"""

from __future__ import annotations

import json
import subprocess
import threading

import pytest

from claude_batch_runner import driver

SCHEMA = {"type": "object", "properties": {"score": {"type": "number"}}, "required": ["score"]}


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _envelope(structured: dict) -> dict:
    return {"is_error": False, "result": "ok", "structured_output": structured}


def _task(agent: str = "health-gaps", budget: float = 5.0) -> driver.AgentTask:
    return driver.AgentTask(agent=agent, prompt="p", schema=SCHEMA, budget_usd=budget)


def _capture_run(monkeypatch) -> dict:
    """Stub subprocess.run with a success envelope; return where cmd/kwargs land."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"], captured["kwargs"] = cmd, kwargs
        return _proc(stdout=json.dumps(_envelope({})))

    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    return captured


# --- call_agent ---------------------------------------------------------------


def test_call_agent_success_builds_command_and_returns_envelope(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"], captured["timeout"] = cmd, timeout
        return _proc(stdout=json.dumps(_envelope({"score": 7})))

    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    env = driver.call_agent("health-gaps", "analyze", SCHEMA, 5.0)
    assert env["structured_output"] == {"score": 7}
    cmd = captured["cmd"]
    assert cmd[:3] == ["claude", "-p", "analyze"]
    assert cmd[cmd.index("--agent") + 1] == "health-gaps"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "5.0"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    assert captured["timeout"] == driver.DEFAULT_AGENT_TIMEOUT_S


def test_call_agent_omits_json_schema_when_none(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _proc(stdout=json.dumps(_envelope({})))

    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    driver.call_agent("health-gaps", "analyze", None, 5.0)
    assert "--json-schema" not in captured["cmd"]


def test_call_agent_nonzero_exit_raises_with_stderr(monkeypatch):
    monkeypatch.setattr(
        driver.subprocess, "run", lambda *a, **k: _proc(returncode=1, stderr="boom")
    )
    with pytest.raises(driver.AgentError, match=r"exit 1: boom"):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)


def test_call_agent_is_error_envelope_with_exit_zero_raises(monkeypatch):
    stdout = json.dumps(
        {"is_error": True, "terminal_reason": "max_budget_usd", "errors": ["budget exhausted"]}
    )
    monkeypatch.setattr(driver.subprocess, "run", lambda *a, **k: _proc(stdout=stdout))
    with pytest.raises(driver.AgentError, match=r"terminal_reason=max_budget_usd"):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)


def test_call_agent_timeout_raises(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=600)

    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    with pytest.raises(driver.AgentError, match=r"timed out"):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)


def test_call_agent_non_json_stdout_raises(monkeypatch):
    monkeypatch.setattr(driver.subprocess, "run", lambda *a, **k: _proc(stdout="not json"))
    with pytest.raises(driver.AgentError, match=r"non-JSON"):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)


# --- call_agent: cwd + permission_mode ----------------------------------------


def test_call_agent_omitting_options_leaves_the_call_unchanged(monkeypatch):
    captured = _capture_run(monkeypatch)
    driver.call_agent("health-gaps", "analyze", SCHEMA, 5.0)
    assert "--permission-mode" not in captured["cmd"]
    assert "cwd" not in captured["kwargs"]  # not even cwd=None — today's call, byte for byte


def test_call_agent_cwd_reaches_the_subprocess(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)
    driver.call_agent("health-gaps", "analyze", SCHEMA, 5.0, cwd=tmp_path)
    assert captured["kwargs"]["cwd"] == tmp_path


def test_call_agent_bad_cwd_raises_before_any_dispatch(monkeypatch, tmp_path):
    def never(*a, **k):
        raise AssertionError("no subprocess may start for an unusable cwd")

    monkeypatch.setattr(driver.subprocess, "run", never)
    (not_a_dir := tmp_path / "f.txt").write_text("x")
    for bad in (tmp_path / "nope", not_a_dir):
        with pytest.raises(driver.AgentError, match=r"cwd is not an existing directory"):
            driver.call_agent("health-gaps", "p", SCHEMA, 5.0, cwd=bad)


def test_call_agent_permission_mode_appears_in_argv(monkeypatch):
    captured = _capture_run(monkeypatch)
    driver.call_agent("health-gaps", "p", SCHEMA, 5.0, permission_mode="acceptEdits")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert "cwd" not in captured["kwargs"]


def test_call_agent_both_options_together(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)
    driver.call_agent("health-gaps", "p", None, 5.0, cwd=tmp_path, permission_mode="plan")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--permission-mode") + 1] == "plan"
    assert captured["kwargs"]["cwd"] == tmp_path


# --- agent_failure_detail -----------------------------------------------------


def test_agent_failure_detail_success_is_none():
    assert driver.agent_failure_detail(_proc(stdout=json.dumps(_envelope({})))) is None


def test_agent_failure_detail_no_output_placeholder():
    assert "(no stderr, no JSON envelope on stdout)" in driver.agent_failure_detail(
        _proc(returncode=1)
    )


def test_agent_failure_detail_joins_envelope_errors():
    stdout = json.dumps({"is_error": True, "subtype": "error", "errors": ["a", "b"]})
    detail = driver.agent_failure_detail(_proc(returncode=1, stdout=stdout))
    assert "subtype=error" in detail
    assert "errors=a; b" in detail


# --- fan_out ------------------------------------------------------------------


def test_fan_out_full_width_parallel_and_keys_results():
    barrier = threading.Barrier(3, timeout=10)  # deadlocks unless all 3 run concurrently

    def fake_call(agent, prompt, schema, budget):
        barrier.wait()
        return _envelope({"agent": agent, "budget": budget})

    tasks = {k: _task(agent=f"health-{k}", budget=float(i)) for i, k in enumerate("abc")}
    out = driver.fan_out(tasks, call=fake_call)
    assert set(out) == {"a", "b", "c"}
    assert out["b"]["structured_output"] == {"agent": "health-b", "budget": 1.0}


def test_fan_out_empty_returns_empty():
    assert driver.fan_out({}) == {}


def test_fan_out_converts_agent_error_to_coverage_gap_envelope():
    def flaky(agent, prompt, schema, budget):
        if agent == "health-bad":
            raise driver.AgentError("budget exceeded")
        return _envelope({"ok": True})

    out = driver.fan_out(
        {"good": _task(agent="health-good"), "bad": _task(agent="health-bad")}, call=flaky
    )
    assert out["bad"] == {"_error": "budget exceeded"}
    assert out["good"]["structured_output"] == {"ok": True}


def test_fan_out_non_agent_error_propagates():
    def broken(agent, prompt, schema, budget):
        raise ValueError("driver bug")

    with pytest.raises(ValueError, match="driver bug"):
        driver.fan_out({"x": _task()}, call=broken)


def test_fan_out_forwards_options_and_passes_none_when_unset(tmp_path):
    seen: list = []

    def fake_call(agent, prompt, schema, budget, **opts):
        seen.append(opts)
        return _envelope({})

    driver.fan_out({"a": _task()}, call=fake_call)
    driver.fan_out({"a": _task()}, call=fake_call, cwd=tmp_path, permission_mode="plan")
    assert seen == [{}, {"cwd": tmp_path, "permission_mode": "plan"}]


def test_fan_out_max_workers_caps_concurrency():
    lock, state = threading.Lock(), {"active": 0, "peak": 0}

    def counting(agent, prompt, schema, budget):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        threading.Event().wait(0.01)
        with lock:
            state["active"] -= 1
        return _envelope({})

    tasks = {k: _task(agent=f"health-{k}") for k in "abc"}
    driver.fan_out(tasks, call=counting, max_workers=1)
    assert state["peak"] == 1
