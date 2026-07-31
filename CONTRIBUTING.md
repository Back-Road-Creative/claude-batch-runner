# Contributing to claude-batch-runner

Thanks for your interest. This guide covers how to propose a change and get it
merged.

## Reporting

- **Bugs and features:** open an issue. For a bug, include the campaign spec (or
  a reduced version of it), what you expected, and what happened.
- **Security vulnerabilities:** do **not** open a public issue — follow
  [SECURITY.md](SECURITY.md).

## Development setup

Python 3.11 or newer. No runtime dependencies; `pytest` is the only dev one.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

You do **not** need the `claude` CLI to develop or test. Every agent call in the
suite is mocked or injected, so the tests run offline and cost nothing. You need
the CLI only to run a real campaign.

## The gate

CI runs the suite on Python 3.11, 3.12, and 3.13. Run it locally first:

```bash
python -m pytest -q
```

## What a change should look like

- **Tests first for a bug fix.** Add a test that fails before your change and
  passes after. Never delete or weaken a test to make the suite pass.
- **No real subprocess in tests.** If a new test would spawn `claude`, that is
  a design problem in the code under test — thread a `call=` injection point
  through instead. This rule is what keeps CI free and offline.
- **Validate at the boundary.** New spec fields belong in `spec.py`, rejected
  with a field-named `SpecError` before anything is dispatched. A check that
  can happen at parse time should not happen at run time.
- **Docs land in the same change.** A new spec field or public function updates
  the README in the same commit, not in a follow-up.
- **Keep the dependency list empty.** Standard library only. A new runtime
  dependency needs a strong argument in the PR description.

## Commits and pull requests

- **Conventional commits** — `feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
  `chore:`.
- One logical change per PR, with a short summary and the test output in the
  description.
- **Pin GitHub Actions to a version tag or SHA**, never a moving reference.

## Changelog

Add your entry under an `## Unreleased` heading in
[CHANGELOG.md](CHANGELOG.md), in the same PR.

## Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
