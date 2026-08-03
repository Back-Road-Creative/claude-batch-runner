"""claude_batch_runner — code-orchestrated campaign runner for agent fan-out.

Four pieces, each usable on its own:

  driver   parallel `claude -p --agent` subprocess fan-out
  spec     declarative JSON campaign spec with boundary validation
  verify   rubric-driven outcome grader with a bounded revision loop
  deliver  campaign execution + a metered markdown report

Public API is re-exported here.
"""

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

__version__ = "0.1.0"
