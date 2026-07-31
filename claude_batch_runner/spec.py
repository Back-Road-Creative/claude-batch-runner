"""Campaign spec model + loader.

A campaign is one JSON file describing what to run, over what, and how the
result is judged. Boundary validation happens once, here: unknown fields,
missing fields, and bad types are rejected with field-named SpecErrors before
a single token is spent. `verify` parses into a Verify config consumed by
claude_batch_runner.verify; `escalate` and `deliver` are consumed by
claude_batch_runner.deliver.

The model-tier vocabulary is caller-supplied (see `Tiers`) — it is a property
of whatever agent runtime you point this at, not of the runner.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

EFFORTS = ("low", "medium", "high", "xhigh")
DELIVER_MODES = ("pr-dispatch", "report-only")
JSON_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")
FIELDS = ("worklist", "worker", "parallelism", "escalate", "schema", "verify", "deliver", "report")


class SpecError(ValueError):
    """Raised when a campaign spec or worklist fails boundary validation."""


@dataclasses.dataclass(frozen=True)
class Tiers:
    """The model-tier vocabulary a spec may name, plus the roles it fills.

    `names` is the accepted set. `cheapest` is the tier a verify/judge step is
    forbidden to run on — grading your own cheap output with the same cheap
    model is how a campaign passes itself. `executor` is the default tier for
    workers, `advisor` the default for escalation advisors and graders.
    """

    names: tuple[str, ...]
    cheapest: str
    executor: str
    advisor: str

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError("Tiers.names must not be empty")
        for role in ("cheapest", "executor", "advisor"):
            if (value := getattr(self, role)) not in self.names:
                raise ValueError(f"Tiers.{role}={value!r} is not one of {self.names}")


DEFAULT_TIERS = Tiers(
    names=("haiku", "sonnet", "opus"), cheapest="haiku", executor="sonnet", advisor="opus"
)


@dataclasses.dataclass(frozen=True)
class Worker:
    agent: str
    tier: str = DEFAULT_TIERS.executor  # cheap executor tier by default
    effort: str = "low"


@dataclasses.dataclass(frozen=True)
class Escalate:
    condition: str
    advisor: Worker  # stronger tier, consulted only when `condition` trips


@dataclasses.dataclass(frozen=True)
class Verify:
    rubric: Path
    agent: str
    tier: str = DEFAULT_TIERS.advisor  # the judge; the cheapest tier is rejected at parse
    effort: str = "high"
    max_revisions: int = 1  # worker re-invocations before a failing unit is FLAGGED


@dataclasses.dataclass(frozen=True)
class Campaign:
    worklist: Path
    units: tuple[Any, ...]
    worker: Worker
    parallelism: int
    schema: dict[str, Any]
    escalate: Escalate | None
    verify: Verify | None
    deliver: str
    report: str


def _parse_worker(raw: Any, where: str, tiers: Tiers, tier: str, effort: str) -> Worker:
    if not isinstance(raw, dict) or set(raw) - {"agent", "tier", "effort"}:
        raise SpecError(f"{where} must be an object with fields: agent, tier, effort")
    agent, tier, effort = raw.get("agent"), raw.get("tier", tier), raw.get("effort", effort)
    if not isinstance(agent, str) or not agent:
        raise SpecError(f"{where}.agent must be a non-empty registered agent name")
    if tier not in tiers.names:
        raise SpecError(f"{where}.tier must be one of {tiers.names}, got {tier!r}")
    if effort not in EFFORTS:
        raise SpecError(f"{where}.effort must be one of {EFFORTS}, got {effort!r}")
    return Worker(agent, tier, effort)


def _validate_schema(node: Any, where: str = "schema") -> None:
    if not isinstance(node, dict):
        raise SpecError(f"{where} must be a JSON-schema object, got {type(node).__name__}")
    declared = node.get("type")
    types = declared if isinstance(declared, list) else [declared] if declared else []
    if any(t not in JSON_TYPES for t in types):
        raise SpecError(f"{where}.type: invalid JSON-schema type(s) in {declared!r}")
    props = node.get("properties", {})
    if not isinstance(props, dict):
        raise SpecError(f"{where}.properties must be an object")
    for name, sub in props.items():
        _validate_schema(sub, f"{where}.properties.{name}")
    required = node.get("required", [])
    if not isinstance(required, list) or any(not isinstance(r, str) for r in required):
        raise SpecError(f"{where}.required must be a list of strings")


def _parse_verify(raw: Any, spec_dir: Path, tiers: Tiers) -> Verify:
    allowed = ("rubric", "agent", "tier", "effort", "max_revisions")
    if not isinstance(raw, dict) or set(raw) - set(allowed):
        raise SpecError(f"verify must be an object with fields: {', '.join(allowed)}")
    base = {"agent": raw.get("agent")} | {k: raw[k] for k in ("tier", "effort") if k in raw}
    grader = _parse_worker(base, "verify", tiers, tiers.advisor, "high")
    if grader.tier == tiers.cheapest:
        raise SpecError(f"verify.tier: verify never runs the cheapest tier ({tiers.cheapest})")
    if not isinstance(rel := raw.get("rubric"), str) or not rel:
        raise SpecError("verify.rubric must be a rubric .md path")
    # resolve spec-relative first, then each ancestor (covers repo-root-relative)
    hits = (b / rel for b in (spec_dir, *spec_dir.resolve().parents) if (b / rel).is_file())
    if (rubric := next(hits, None)) is None:
        raise SpecError(f"verify.rubric file not found relative to spec: {rel}")
    max_rev = raw.get("max_revisions", 1)
    if not isinstance(max_rev, int) or isinstance(max_rev, bool) or max_rev < 0:
        raise SpecError(f"verify.max_revisions must be an integer >= 0, got {max_rev!r}")
    return Verify(rubric.resolve(), grader.agent, grader.tier, grader.effort, max_rev)


def load_campaign(path: str | Path, *, tiers: Tiers = DEFAULT_TIERS) -> Campaign:
    """Parse + validate a campaign spec; load and count its worklist.

    `tiers` is the model-tier vocabulary the spec may name; pass your own to
    match whatever tiers your agent runtime actually exposes.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SpecError(f"cannot parse spec {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SpecError(f"spec must be a single object, got {type(raw).__name__}")
    if unknown := set(raw) - set(FIELDS):
        raise SpecError(f"unknown spec field(s): {', '.join(sorted(unknown))}")
    if missing := [f for f in ("worklist", "worker", "schema") if f not in raw]:
        raise SpecError(f"missing required spec field(s): {', '.join(missing)}")
    worker = _parse_worker(raw["worker"], "worker", tiers, tiers.executor, "low")
    schema = raw["schema"]
    _validate_schema(schema)
    if not isinstance(raw["worklist"], str):
        raise SpecError("worklist must be a path string")
    worklist = (path.parent / raw["worklist"]).resolve()  # relative to the spec file
    if not worklist.is_file():
        raise SpecError(f"worklist file not found: {worklist}")
    units = []
    for line in worklist.read_text().splitlines():
        if line := line.strip():
            try:
                units.append(json.loads(line))  # one JSON value per line ...
            except json.JSONDecodeError:
                units.append(line)  # ... or a plain-line unit (a path, a URL)
    if not units:
        raise SpecError(f"worklist is empty: {worklist}")
    parallelism = raw.get("parallelism", min(len(units), 4))
    if not isinstance(parallelism, int) or isinstance(parallelism, bool) or parallelism < 1:
        raise SpecError(f"parallelism must be a positive integer, got {parallelism!r}")
    escalate = None
    if (esc := raw.get("escalate")) is not None:
        if not isinstance(esc, dict) or set(esc) - {"condition", "advisor"}:
            raise SpecError("escalate must be an object with fields: condition, advisor")
        if not isinstance(esc.get("condition"), str) or not esc["condition"]:
            raise SpecError("escalate.condition must be a non-empty string")
        advisor = _parse_worker(
            esc.get("advisor"), "escalate.advisor", tiers, tiers.advisor, "high"
        )
        escalate = Escalate(condition=esc["condition"], advisor=advisor)
    verify = None
    if raw.get("verify") is not None:
        verify = _parse_verify(raw["verify"], path.parent, tiers)
    deliver = raw.get("deliver", "report-only")
    if deliver not in DELIVER_MODES:
        raise SpecError(f"deliver must be one of {DELIVER_MODES}, got {deliver!r}")
    report = raw.get("report", "reports/{date}-{campaign}.md")
    if not isinstance(report, str) or not report:
        raise SpecError("report must be a non-empty output path template")
    units = tuple(units)
    return Campaign(worklist, units, worker, parallelism, schema, escalate, verify, deliver, report)
