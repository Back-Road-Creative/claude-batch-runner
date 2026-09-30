"""Unit tests for claude_batch_runner.driver — the parallel `claude -p` fan-out.

The subprocess layer is always mocked (monkeypatched `driver._run_bounded` or an
injected `call=`); no test ever spawns a real `claude` process.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time

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
    """Stub driver._run_bounded (the subprocess seam) with a success envelope; return where cmd/kwargs land."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"], captured["kwargs"] = cmd, kwargs
        return _proc(stdout=json.dumps(_envelope({})))

    monkeypatch.setattr(driver, "_run_bounded", fake_run)
    return captured


# --- call_agent ---------------------------------------------------------------


def test_call_agent_success_builds_command_and_returns_envelope(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, timeout):
        captured["cmd"], captured["timeout"] = cmd, timeout
        return _proc(stdout=json.dumps(_envelope({"score": 7})))

    monkeypatch.setattr(driver, "_run_bounded", fake_run)
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

    monkeypatch.setattr(driver, "_run_bounded", fake_run)
    driver.call_agent("health-gaps", "analyze", None, 5.0)
    assert "--json-schema" not in captured["cmd"]


def test_call_agent_nonzero_exit_raises_with_stderr(monkeypatch):
    monkeypatch.setattr(driver, "_run_bounded", lambda *a, **k: _proc(returncode=1, stderr="boom"))
    with pytest.raises(driver.AgentError, match=r"exit 1: boom"):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)


def test_call_agent_is_error_envelope_with_exit_zero_raises(monkeypatch):
    stdout = json.dumps(
        {"is_error": True, "terminal_reason": "max_budget_usd", "errors": ["budget exhausted"]}
    )
    monkeypatch.setattr(driver, "_run_bounded", lambda *a, **k: _proc(stdout=stdout))
    with pytest.raises(driver.AgentError, match=r"terminal_reason=max_budget_usd"):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)


def test_call_agent_timeout_raises(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=600)

    monkeypatch.setattr(driver, "_run_bounded", fake_run)
    with pytest.raises(driver.AgentError, match=r"timed out"):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)


def test_call_agent_non_json_stdout_raises(monkeypatch):
    monkeypatch.setattr(driver, "_run_bounded", lambda *a, **k: _proc(stdout="not json"))
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

    monkeypatch.setattr(driver, "_run_bounded", never)
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


# --- process-tree lifetime (POSIX) --------------------------------------------
#
# These tests never run a real `claude`. A tiny stand-in script named `claude`
# is put first on PATH; it starts one grandchild, records its pid, prints a
# partial line, and then sleeps. Every process a test starts is killed in
# `finally`, whatever the assertions did.

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")

_FAKE_CLAUDE = """#!{python}
import os, subprocess, sys, time
mode = os.environ.get("FAKE_CLAUDE_MODE", "hang")
if mode == "ok":
    print('{{"is_error": false, "result": "ok"}}')
    sys.exit(0)
gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
with open(os.environ["FAKE_CLAUDE_PIDFILE"], "w") as fh:
    fh.write(str(gc.pid))
print("partial-out", flush=True)
print("partial-err", file=sys.stderr, flush=True)
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


def _wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _read_pid(pidfile) -> int | None:
    try:
        return int(pidfile.read_text())
    except (FileNotFoundError, ValueError):
        return None


@pytest.fixture
def fake_claude(monkeypatch, tmp_path):
    """Put the stand-in `claude` on PATH; kill the grandchild it records on exit."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "claude"
    script.write_text(_FAKE_CLAUDE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    pidfile = tmp_path / "grandchild.pid"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_PIDFILE", str(pidfile))
    monkeypatch.setattr(driver, "TERM_GRACE_S", 0.5)
    yield pidfile
    pid = _read_pid(pidfile)
    if pid is not None:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


@posix_only
def test_call_agent_timeout_kills_the_grandchild_too(monkeypatch, fake_claude):
    monkeypatch.setattr(driver, "DEFAULT_AGENT_TIMEOUT_S", 3)
    with pytest.raises(driver.AgentError, match=r"timed out") as excinfo:
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)
    gc = _read_pid(fake_claude)
    assert gc is not None, "the stand-in never recorded its grandchild"
    assert _wait_for(lambda: not _alive(gc)), "grandchild survived the timeout"
    # partial logs are retained, on the exception and in its message
    assert "partial-out" in excinfo.value.stdout
    assert "partial-err" in excinfo.value.stderr
    assert "partial-err" in str(excinfo.value)


@posix_only
def test_direct_call_cancellation_kills_the_tree(monkeypatch, fake_claude):
    real = subprocess.Popen.communicate
    calls = {"n": 0}

    def cancelling(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            assert _wait_for(lambda: _read_pid(fake_claude) is not None)
            raise KeyboardInterrupt
        return real(self, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "communicate", cancelling)
    with pytest.raises(KeyboardInterrupt):
        driver.call_agent("health-gaps", "p", SCHEMA, 5.0)
    gc = _read_pid(fake_claude)
    assert _wait_for(lambda: not _alive(gc)), "grandchild survived cancellation"


@posix_only
def test_fan_out_interrupt_kills_every_live_tree(monkeypatch, fake_claude):
    def interrupt(futures):
        assert _wait_for(lambda: _read_pid(fake_claude) is not None)
        raise KeyboardInterrupt
        yield  # pragma: no cover - makes this a generator like as_completed

    monkeypatch.setattr(driver.concurrent.futures, "as_completed", interrupt)
    with pytest.raises(KeyboardInterrupt):
        driver.fan_out({"a": _task()}, max_workers=1)
    gc = _read_pid(fake_claude)
    assert _wait_for(lambda: not _alive(gc)), "grandchild survived a fan_out interrupt"
    assert not driver._LIVE, "the live-process registry leaked an entry"


@posix_only
def test_normal_completion_is_unchanged(monkeypatch, fake_claude):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    assert driver.call_agent("health-gaps", "p", None, 5.0) == {"is_error": False, "result": "ok"}
    assert not driver._LIVE
