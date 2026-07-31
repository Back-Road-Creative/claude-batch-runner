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
  AgentError            raised on any non-success or malformed result
  agent_failure_detail  envelope-aware failure description
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import os
import subprocess
from typing import Any

DEFAULT_AGENT_TIMEOUT_S = int(os.environ.get("AGENT_TIMEOUT_S", "600"))


class AgentError(RuntimeError):
    """Raised when an agent invocation fails or returns malformed output."""


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


def call_agent(
    agent_name: str,
    prompt: str,
    schema: dict[str, Any] | None,
    budget_usd: float,
) -> dict[str, Any]:
    """Subprocess `claude -p --agent <name>` and return the result envelope.

    When `schema` is provided, output is coerced into envelope["structured_output"].
    Raises AgentError on any non-success result or parse failure.
    """
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
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_AGENT_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise AgentError(
            f"agent {agent_name} timed out after {DEFAULT_AGENT_TIMEOUT_S}s: {e}"
        ) from e
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
) -> dict[str, dict[str, Any]]:
    """Parallel subprocess fan-out over independent agent tasks.

    Returns: {key: envelope}.  Tasks that fail are reported with a synthetic
    envelope carrying {"_error": "<msg>"} so the orchestrator can surface
    coverage gaps without aborting the run.  `max_workers` defaults to
    len(tasks) — full-width fan-out; pass it to cap concurrency.
    """
    if not tasks:
        return {}
    results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers or len(tasks)) as pool:
        futures = {
            pool.submit(call, task.agent, task.prompt, task.schema, task.budget_usd): key
            for key, task in tasks.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except AgentError as e:
                results[key] = {"_error": str(e)}
    return results
