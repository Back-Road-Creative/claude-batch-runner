"""Parallel `claude -p` subprocess driver.

One driver shared by every code-orchestrated fan-out, instead of a copy per
pipeline. Each unit of work is its own `claude -p --agent` process; results
come back as the CLI's JSON result envelope, with schema-validated output on
`structured_output` when the caller supplies a JSON schema. Parallel fan-out
also lets the CLI reuse its cached system context across sibling calls.

Authentication is whatever the `claude` CLI on PATH is already configured to
use — this module never reads, sets, or forwards credentials of its own.

Import surface:

  AgentTask             one unit of fan-out work
  call_agent            single `claude -p --agent` subprocess call
  fan_out               parallel fan-out over {key: AgentTask}
  agent_options         the optional call_agent kwargs, minus whatever is unset
  AgentError            raised on any non-success or malformed result
  agent_failure_detail  envelope-aware failure description

Process lifetime. On POSIX each `claude` runs in its own session (so its pid is
also its process-group id), and a timeout, an exception while waiting, or an
interrupt of `fan_out` sends SIGTERM to that whole group, waits up to
`TERM_GRACE_S`, then SIGKILLs whatever is left: the CLI's own helpers and
anything they spawned go with it. A descendant that deliberately starts its own
session or group is outside this, and on Windows (no POSIX groups) only the
direct child is killed. A normal exit is unchanged.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_AGENT_TIMEOUT_S = int(os.environ.get("AGENT_TIMEOUT_S", "600"))
# Seconds between SIGTERM and SIGKILL when tearing down a process tree.
TERM_GRACE_S = float(os.environ.get("AGENT_TERM_GRACE_S", "5"))

_POSIX = os.name == "posix"
_LIVE: dict[int, subprocess.Popen[str]] = {}  # pid -> running child, for interrupt cleanup
_LIVE_LOCK = threading.Lock()
_PARTIAL_TAIL = 400  # characters of partial stdout/stderr quoted in a timeout message


class AgentError(RuntimeError):
    """Raised when an agent invocation fails or returns malformed output.

    A timeout also carries what the process had written before it was killed, on
    `.stdout` and `.stderr` (empty strings otherwise).
    """

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_trees(procs: list[subprocess.Popen[str]]) -> None:
    """Stop each child and everything in its process group; always reaps the child.

    SIGTERM every group, give them `TERM_GRACE_S` in total to empty, SIGKILL the
    stragglers, then reap. Windows has no POSIX groups: the direct child is
    killed and its descendants are not.
    """
    if not _POSIX:
        for proc in procs:
            proc.kill()
            proc.wait()
        return
    for proc in procs:
        _signal_group(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + TERM_GRACE_S
    pending = list(procs)
    while pending and time.monotonic() < deadline:
        # poll() reaps a leader that has exited; a zombie leader keeps its group "alive"
        pending = [p for p in pending if (p.poll(), _group_alive(p.pid))[1]]
        if pending:
            time.sleep(0.02)
    for proc in pending:
        _signal_group(proc.pid, signal.SIGKILL)
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # unkillable (D state); do not hang the caller
            pass


def _terminate_live() -> None:
    """Tear down every child `_run_bounded` currently has running."""
    with _LIVE_LOCK:
        procs = list(_LIVE.values())
    if procs:
        _terminate_trees(procs)


def _close_pipes(proc: subprocess.Popen[str]) -> None:
    for pipe in (proc.stdout, proc.stderr, proc.stdin):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _run_bounded(
    cmd: list[str], *, timeout: float, **where: Any
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run(cmd, capture_output=True, text=True, timeout=...)` that owns the tree.

    Same result, but the child leads its own session (POSIX), so on timeout or on
    any exception while waiting (a KeyboardInterrupt included) the whole group is
    terminated, not only the direct child. Raises `subprocess.TimeoutExpired`
    carrying whatever output the child wrote first.
    """
    extra: dict[str, Any] = {"start_new_session": True} if _POSIX else {}
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **extra, **where
    )
    with _LIVE_LOCK:
        _LIVE[proc.pid] = proc
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_trees([proc])
            try:  # the pipes are closed now; collect what was buffered
                out, err = proc.communicate(timeout=TERM_GRACE_S)
            except subprocess.TimeoutExpired:  # a descendant escaped the group and holds them
                out = err = ""
            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
        except BaseException:
            _terminate_trees([proc])
            raise
    finally:
        with _LIVE_LOCK:
            _LIVE.pop(proc.pid, None)
        _close_pipes(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


@dataclasses.dataclass(frozen=True)
class AgentTask:
    """One unit of fan-out work: which agent, what prompt, what contract."""

    agent: str
    prompt: str
    schema: dict[str, Any] | None
    budget_usd: float


def agent_failure_detail(proc: subprocess.CompletedProcess[str]) -> str | None:
    """Describe a failed `claude -p` run; None when it succeeded.

    Budget exhaustion arrives as a JSON envelope on STDOUT (`is_error`,
    `terminal_reason`, `errors[]`) with STDERR EMPTY, so a stderr-only message
    renders "agent exited 1:" with nothing after it; and `is_error` can arrive
    with exit 0, so the exit code alone is not proof of success.
    """
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        envelope = None
    if not isinstance(envelope, dict):
        envelope = None
    if proc.returncode == 0 and not (envelope or {}).get("is_error"):
        return None
    if envelope is None:
        detail = (proc.stderr or "").strip()[:400] or "(no stderr, no JSON envelope on stdout)"
        return f"exit {proc.returncode}: {detail}"
    bits = [f"{k}={envelope[k]}" for k in ("subtype", "terminal_reason") if envelope.get(k)]
    errors = envelope.get("errors") or []
    if errors:
        errors = errors if isinstance(errors, list) else [errors]
        bits.append("errors=" + "; ".join(str(e) for e in errors))
    return f"exit {proc.returncode}, " + (", ".join(bits) or "is_error=True")[:600]


def agent_options(
    cwd: str | Path | None = None, permission_mode: str | None = None
) -> dict[str, Any]:
    """The optional `call_agent` keyword arguments, minus whatever is unset.

    Empty when both are None, so a caller's own injected `call=` written against
    the four-positional-argument signature keeps working untouched.
    """
    given = (("cwd", cwd), ("permission_mode", permission_mode))
    return {k: v for k, v in given if v is not None}


def call_agent(
    agent_name: str,
    prompt: str,
    schema: dict[str, Any] | None,
    budget_usd: float,
    *,
    cwd: str | Path | None = None,
    permission_mode: str | None = None,
) -> dict[str, Any]:
    """Subprocess `claude -p --agent <name>` and return the result envelope.

    When `schema` is provided, output is coerced into envelope["structured_output"].
    Raises AgentError on any non-success result or parse failure.

    `cwd` runs the subprocess in that directory — checked to exist first, so an
    unusable path raises before a process starts (and inside `fan_out` becomes
    that unit's `_error` row rather than taking the batch down). `permission_mode`
    is handed straight to `claude --permission-mode`; the accepted modes are the
    CLI's vocabulary, not the runner's. Both default to None, and when omitted
    the subprocess call is exactly what it was without them.
    """
    if cwd is not None and not Path(cwd).is_dir():
        raise AgentError(f"agent {agent_name}: cwd is not an existing directory: {cwd}")
    cmd = [
        "claude",
        "-p",
        prompt,
        "--agent",
        agent_name,
        "--output-format",
        "json",
        "--max-budget-usd",
        str(budget_usd),
        "--no-session-persistence",
    ]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    if permission_mode is not None:
        cmd += ["--permission-mode", permission_mode]
    where = {"cwd": cwd} if cwd is not None else {}  # unset: not even cwd=None
    try:
        proc = _run_bounded(cmd, timeout=DEFAULT_AGENT_TIMEOUT_S, **where)
    except subprocess.TimeoutExpired as e:
        out, err = _text(e.output), _text(e.stderr)
        msg = f"agent {agent_name} timed out after {DEFAULT_AGENT_TIMEOUT_S}s: {e}"
        if err.strip() or out.strip():
            msg += (
                f" (partial stderr: {err.strip()[-_PARTIAL_TAIL:]!r};"
                f" partial stdout: {out.strip()[-_PARTIAL_TAIL:]!r})"
            )
        raise AgentError(msg, stdout=out, stderr=err) from e
    detail = agent_failure_detail(proc)
    if detail is not None:
        raise AgentError(f"agent {agent_name} failed: {detail}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AgentError(f"agent {agent_name} returned non-JSON: {e}") from e
    return envelope


def fan_out(
    tasks: dict[str, AgentTask],
    *,
    call: Any = call_agent,
    max_workers: int | None = None,
    cwd: str | Path | None = None,
    permission_mode: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Parallel subprocess fan-out over independent agent tasks.

    Returns: {key: envelope}.  Tasks that fail are reported with a synthetic
    envelope carrying {"_error": "<msg>"} so the orchestrator can surface
    coverage gaps without aborting the run.  `max_workers` defaults to
    len(tasks) — full-width fan-out; pass it to cap concurrency.

    `cwd` and `permission_mode` apply to every task (see `call_agent`); omit
    them and `call` is invoked with the same four positional arguments as before.

    If the wait is interrupted, every `claude` process tree this module has running is
    terminated before the exception propagates (see the module docstring).
    """
    if not tasks:
        return {}
    opts = agent_options(cwd, permission_mode)
    results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers or len(tasks)) as pool:
        futures = {
            pool.submit(call, task.agent, task.prompt, task.schema, task.budget_usd, **opts): key
            for key, task in tasks.items()
        }
        try:
            for fut in concurrent.futures.as_completed(futures):
                key = futures[fut]
                try:
                    results[key] = fut.result()
                except AgentError as e:
                    results[key] = {"_error": str(e)}
        except BaseException:
            # Cancelled (Ctrl-C included): children run in their own sessions now, so the
            # terminal's SIGINT no longer reaches them. Drop unstarted tasks and stop the
            # live ones, or the pool's shutdown would wait out a full timeout.
            pool.shutdown(wait=False, cancel_futures=True)
            _terminate_live()
            raise
    return results
