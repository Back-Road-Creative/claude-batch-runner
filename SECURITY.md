# Security Policy

## Supported versions

Only the latest released tag receives fixes. Pin a released `v*` tag; `main` is
unstable.

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do **not** open a public
issue for a security report.

- Open a private advisory via GitHub's **Security → Report a vulnerability**
  tab on this repository (Private Vulnerability Reporting).
- If that tab is unavailable to you, contact the maintainer through the address
  in this project's package metadata (`pyproject.toml`), with
  `claude-batch-runner security` in the subject.

Please include the affected version or commit, a description of the issue and
its impact, reproduction steps, and any suggested remediation.

## What to expect

- Acknowledgement within 5 business days.
- An initial assessment and severity triage within 10 business days.
- Coordinated disclosure: we agree a timeline with you before any public
  write-up, and credit reporters who want it.

## Scope and threat model

In scope: the code under `claude_batch_runner/` and the CI workflow under
`.github/workflows/`.

Two things are worth stating plainly, because they shape what counts as a
vulnerability here:

**This library executes a subprocess you configure.** A campaign spec names an
agent and supplies a prompt; the runner passes both to the `claude` CLI. A spec
is therefore as trusted as a script — **do not run a campaign spec, worklist, or
rubric from an untrusted source.** Treat "a malicious spec causes a malicious
agent invocation" as working as designed, not as a vulnerability.

**Content flowing through the runner is framed as data, not instructions.**
Worker output, work units, and failed criteria are wrapped in an explicit
data-only frame before they reach a grader or advisor
(`claude_batch_runner.verify.frame`). This reduces the chance that text inside a
reviewed artifact steers the model reviewing it. It is a mitigation, not a
guarantee — no prompt-level framing is one. A framing bypass that reliably makes
a grader follow instructions from graded content **is** in scope, and worth
reporting.

Out of scope: vulnerabilities in the `claude` CLI itself (report those to its
maintainers), and the content of whatever a model produces when you run a
campaign.
