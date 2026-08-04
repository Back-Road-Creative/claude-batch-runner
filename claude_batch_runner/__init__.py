"""claude_batch_runner — code-orchestrated campaign runner for agent fan-out.

Four pieces, each usable on its own:

  driver   parallel `claude -p --agent` subprocess fan-out
  spec     declarative JSON campaign spec with boundary validation
  verify   rubric-driven outcome grader with a bounded revision loop
  deliver  campaign execution + a metered markdown report

Public API is re-exported here.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

from claude_batch_runner.driver import (
    DEFAULT_AGENT_TIMEOUT_S,
    AgentError,
    AgentTask,
    agent_failure_detail,
    agent_options,
    call_agent,
    fan_out,
)
from claude_batch_runner.spec import (
    DEFAULT_TIERS,
    Campaign,
    Escalate,
    SpecError,
    Tiers,
    Verify,
    Worker,
    load_campaign,
)
from claude_batch_runner.verify import VerifyError, load_rubric, verify_unit

__all__ = [
    "DEFAULT_AGENT_TIMEOUT_S",
    "DEFAULT_TIERS",
    "AgentError",
    "AgentTask",
    "Campaign",
    "Escalate",
    "SpecError",
    "Tiers",
    "Verify",
    "VerifyError",
    "Worker",
    "agent_failure_detail",
    "agent_options",
    "call_agent",
    "fan_out",
    "load_campaign",
    "load_rubric",
    "verify_unit",
]

# Derived, never written by hand. `pyproject.toml` is the single source of
# truth — release.yml already cross-checks it against the git tag — and a
# second literal here drifted the moment 0.1.1 bumped one and not the other.
try:
    __version__ = _installed_version("claude-batch-runner")
except PackageNotFoundError:  # source checkout, never pip-installed
    __version__ = "0.0.0.dev0"
