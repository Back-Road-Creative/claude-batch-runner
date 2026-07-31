"""`python -m claude_batch_runner --spec <path> [--dry-run]` — plan or run a
campaign. `--dry-run` validates the spec and prints the dispatch plan without
spending anything; without it, the report-only adapter runs end to end via real
`claude` subprocess calls (tests mock them)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claude_batch_runner import deliver
from claude_batch_runner.spec import SpecError, load_campaign
from claude_batch_runner.verify import VerifyError, load_rubric


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude_batch_runner", description=__doc__)
    parser.add_argument("--spec", required=True, help="campaign spec file (JSON)")
    parser.add_argument("--dry-run", action="store_true", help="print the dispatch plan only")
    args = parser.parse_args(argv)
    name = Path(args.spec).stem
    try:
        c = load_campaign(args.spec)
        rubric = load_rubric(c.verify.rubric) if c.verify else None
    except (SpecError, VerifyError) as e:
        print(f"spec error: {e}", file=sys.stderr)
        return 2
    if not args.dry_run:
        try:
            res = deliver.run(c, name)
        except (SpecError, VerifyError, NotImplementedError) as e:
            print(f"deliver error: {e}", file=sys.stderr)
            return 2
        flagged = sum(u.outcome == "FLAGGED" for u in res.units)
        print(f"campaign {name}: {len(res.units)} units, FLAGGED {flagged}, {res.duration_s:.1f}s")
        print(f"report: {res.report_path}")
        return 0
    esc = "none"
    if c.escalate:
        a = c.escalate.advisor
        esc = f"when {c.escalate.condition!r} -> {a.agent} (tier={a.tier}, effort={a.effort})"
    required = ", ".join(c.schema.get("required") or []) or "-"
    print(f"campaign spec: {args.spec} [valid] — {len(c.units)} unit(s) from {c.worklist}")
    print(f"worker: {c.worker.agent} (tier={c.worker.tier}, effort={c.worker.effort})")
    print(f"parallelism: {c.parallelism}\nescalate: {esc}")
    print(f"schema: {c.schema.get('type', 'any')}, required keys: {required}")
    vr = "none"
    if c.verify and rubric:
        vr = (
            f"rubric {rubric.name} v{rubric.version} ({len(rubric.criteria)} criteria), "
            f"grader {c.verify.agent} (tier={c.verify.tier}, effort={c.verify.effort})"
        )
    print(f"verify: {vr}")
    print(f"deliver: {c.deliver} — report: {deliver.report_path(c, name)}")
    print("dry run only: no agents dispatched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
