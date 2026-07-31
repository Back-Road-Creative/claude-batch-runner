---
name: finding-validity
version: 1.0.0
applies_to: a single finding produced by an automated code-review or audit agent
criteria_count: 5
---

# Finding validity rubric

Grade one finding at a time — five criteria, PASS or FAIL each, every verdict
backed by concrete evidence. Do not grade a whole report as a single unit: an
average is exactly the thing that lets a bad finding ride along with good ones.

A rubric is parsed by `claude_batch_runner.verify.load_rubric`, which requires
the frontmatter above and one `## N. <id>` section per criterion, with
`criteria_count` matching the number of sections.

## 1. file-line-cited
**Statement:** the claim points at a specific location, not a vague area.
**PASS:** the finding names a file path and a line number or line range.
**FAIL:** the finding refers to "the config", "somewhere in the request path",
or a whole directory, with no line-level pointer.
**Evidence:** the file:line pair as written in the finding.

## 2. one-line-verify-command
**Statement:** the claim is checkable with one shell command in under a minute.
**PASS:** a single command (grep, git log, a test invocation, …) run against
the current tree reproduces the claimed fact.
**FAIL:** no such single command exists, or running the given command
contradicts the claim.
**Evidence:** the exact command text and its actual output.

## 3. severity-justified
**Statement:** the assigned severity is backed by a concrete failure scenario.
**PASS:** the severity label is paired with a scenario stating what breaks, for
whom, and what triggers it.
**FAIL:** severity is asserted with no scenario, or the scenario is generic
("could cause issues", "may be a problem").
**Evidence:** quote the scenario sentence next to the severity label.

## 4. not-already-fixed
**Statement:** the issue still exists at the commit the finding describes.
**PASS:** re-running the verify command from criterion 2 against current HEAD
still shows the issue present.
**FAIL:** a commit already landed that resolves the claim — check the file's
current content and the history for the relevant keyword.
**Evidence:** the re-run command output.

## 5. not-a-known-limitation
**Statement:** the finding is not a restatement of a documented, accepted
non-goal.
**PASS:** the finding is new, or it presents evidence that should change a
previously accepted decision.
**FAIL:** the finding restates a limitation already documented as by-design (in
a README caveat, a design doc's non-goals, or a prior report) with no new
evidence.
**Evidence:** a search command over the project's own docs and its result — a
hit that matches the finding fails it; no hit passes.
