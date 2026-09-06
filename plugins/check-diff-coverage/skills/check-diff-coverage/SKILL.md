---
name: check-diff-coverage
description: Use this skill whenever there is doubt about whether recent code changes are adequately tested — before opening a PR, before claiming a task is complete, after implementing a feature or refactor, or as a mid-stream sanity check. Measures the percentage of changed lines (vs master/main) that no test exercises — the "uncovered diff" — and drives a remediation loop to bring it under 20% (target ≤10%). Trigger on phrases like "check coverage", "review my changes", "ready to PR?", "have I written enough tests?", "is this well-tested?", "did I cover all the cases?", "uncovered diff", "diff coverage". Also trigger proactively before declaring work complete on any task that produced non-trivial code changes — and self-trigger any time you have just changed code and want to verify the new behaviour is covered. Works on .NET (csproj/sln) and Go (go.mod) repos. Acts as a quality gate, not a hard wall — escalates to the user when stuck rather than blocking.
---

# Check Diff Coverage

A coverage gate for *recent changes*. The principle: total coverage % is historical debt; what matters for *this PR* or *this task* is whether the lines you just changed have tests. This skill measures that and helps push it into a healthy range.

## When to invoke

Use this skill at any point where you (or the user) want to verify that *new or changed* code is adequately tested. Concretely:

- **Before opening a PR** — the most obvious moment, but not the only one.
- **Before claiming a task is complete** — after implementing a feature, fixing a bug, or finishing a refactor, and before telling the user "done." A passing test suite proves what you tested; this skill proves what you *didn't*.
- **Mid-task sanity check** — after a meaningful chunk of code change, when you want to know whether you're keeping up with tests or accumulating untested code.
- **After a big-bang refactor** — refactors often quietly add untested code (new abstractions, error paths, defensive guards). This skill surfaces the gap.
- **When the user expresses doubt** — phrases like "is this well-tested?", "did I cover everything?", "are we good to merge?".

You can self-trigger this skill — the user does not have to ask. If you have just made code changes and you cannot confidently answer "yes, the new behaviour is tested," run the skill before reporting completion. It is cheap to run and is the difference between "I think it's fine" and "I checked."

## Thresholds

| Uncovered diff % | Verdict | Action |
|---|---|---|
| ≤ 10% | **pass**  | Report and stop. The PR is well-tested. |
| 10 – 20% | **warn**  | Show the uncovered list. Recommend (don't enforce) writing tests for high-priority blocks. Ask the user if they want to push it down or accept. |
| > 20% | **fail**  | Enter the remediation loop below. |
| 0 changed lines | **noop** | Nothing to measure. Tell the user. |

The user's stated standard: ≤10% is good, ≤20% is acceptable. Above 20% means the diff carries real untested risk.

## Workflow

### 1. Measure

Run the bundled measure script. It detects the language (`.NET` or `Go`), runs tests with coverage, and computes diff coverage vs the base branch.

```bash
bash ~/.claude/skills/check-diff-coverage/scripts/measure.sh
```

Optional flags:
- `--base origin/master` — explicit base branch (auto-detected by default).
- `--skip-tests` — reuse the existing coverage XML; only valid when no source/test files have changed since the last run.

The script prints a JSON summary on stdout. Read every field — especially `verdict`, `uncovered_pct`, and `files[].uncovered_lines`.

### 2. Branch on the verdict

- **`pass`** — Tell the user the verdict, the percentage, and stop.
- **`noop`** — Tell the user there are no diff lines in covered files vs the base branch and stop. (Often this means they're not on a feature branch, or all changes are in non-tested files like docs.)
- **`warn`** — Show the per-file uncovered breakdown. Read `references/prioritization.md`. Surface the top 2–3 high-priority uncovered blocks and ask the user if they'd like you to add tests, accept the warning, or stop.
- **`fail`** — Enter the remediation loop.

### 3. Remediation loop (only when `verdict == "fail"`)

Two phases. **Phase A** runs before **Phase B**. Auto-commit after each successful round (a round counts as successful when `measure.sh` produces a new, lower `uncovered_pct`). If a round increases the percentage, do **not** commit — investigate.

#### Phase A — add tests (max 3 rounds)

For each round:

1. Read `references/prioritization.md` if you have not already in this session.
2. Pick the highest-priority uncovered block from the JSON. Group consecutive uncovered lines into a single block — they're usually one behaviour.
3. Read the surrounding code so you understand the behaviour (use LSP `go-to-definition` / `find-references` over Grep where available — see the user's global CLAUDE.md).
4. Decide: **test it, or delete it?** Use the prioritization table. If deletion is correct, do that instead of writing a no-op test.
5. Write a test that follows the existing test conventions of the project. Do not introduce a new framework or test style. Match what's already there.
6. Run the test to confirm it passes.
7. Re-run `measure.sh`.
8. If `uncovered_pct` dropped: auto-commit with `[<branch-name>] test: add tests for <what you tested>` (per the user's git conventions in `~/.claude/rules/git-conventions.md` — branch name in square brackets, imperative mood, no Co-Authored-By trailer).
9. If verdict is now `pass` or `warn`, exit the loop.
10. If still `fail`, continue to next round (up to 3).

After 3 rounds without reaching `pass`/`warn`, switch to Phase B.

#### Phase B — refactor for testability (max 2 rounds)

When tests aren't moving the needle, the code shape is the problem. Read `references/refactor-bridge.md` and hand off to the `refactor-for-testability` skill with a focused brief on a single uncovered block.

For each refactor round:

1. Pick the most stubborn uncovered block.
2. Identify the testability problem (ambient state, hidden deps, mixed concerns — see the bridge doc).
3. Use the `refactor-for-testability` skill with that brief.
4. Land the test that the refactor was meant to enable. **A refactor without the unblocked test is just churn — do not commit a refactor with no new test.**
5. Re-run `measure.sh`.
6. Auto-commit the refactor + test together with `[<branch-name>] refactor: <what you changed> for testability`.
7. If verdict reaches `pass`/`warn`, exit.

After 2 refactor rounds without reaching `pass`/`warn`, **stop and ask the user**.

### 4. Stopping condition — STOP and ask

When 3 add-tests rounds + 2 refactor rounds have not reached `pass` or `warn`, stop. Do not keep trying. Hand back to the user with:

- Current `uncovered_pct` and which files/lines are still uncovered.
- A short summary of what was tried (what tests were added, what was refactored).
- A recommendation, picking from:
  - **Split the PR**: land the testable parts now, isolate the hard part for a follow-up.
  - **Accept the gap with rationale**: e.g. "this is a thin adapter over a third-party SDK — better covered at integration level than unit level."
  - **Bigger redesign needed**: out of scope for a coverage gate.
- An explicit "what would you like to do?" — let the user decide.

This skill is a gate, not a wall. Its job is to surface the right question and propose options, not to block forever on an arbitrary percentage.

## Output style

Each round, give the user a single concise update:

```
Round 1 (add tests): 34% → 22% uncovered diff
  - Added 2 tests for PartnerLedHandler error paths (lines 88–92, 110–115)
  - Committed: [refactor-integration-test] test: add tests for PartnerLedHandler error paths
  - Still 'fail' — entering round 2.
```

Don't dump the full JSON or HTML report unless the user asks. The HTML report is at `.coverage-diff/diff-cover.html` if they want to look themselves.

## Things to avoid

- **Don't write tests that execute lines without asserting anything** just to bump the metric. Coverage gaming defeats the point of the gate. Better to delete the code or accept the gap.
- **Don't introduce a new test framework** to fix coverage. Match the project's existing style.
- **Don't refactor unrelated code in passing.** Stay scoped to the uncovered blocks you're addressing.
- **Don't silently delete code.** When deletion is the right call, tell the user *why* — it's a behaviour change.
- **Don't keep grinding past the stopping condition.** If 3+2 rounds haven't worked, the answer isn't "another round."
- **Don't run this skill on the base branch itself** — there's no diff. If `verdict == "noop"` and the user looks confused, double-check `git status` / `git rev-parse --abbrev-ref HEAD`.

## When this skill should NOT trigger

- The user is investigating a bug — use `superpowers:systematic-debugging` instead. (Run this skill *after* the fix lands, to verify the fix is itself tested.)
- The user is asking about *total* coverage of the codebase — that's a different (and less useful) metric. Explain the distinction and offer this skill if their real concern is whether *recent changes* are tested.
- The repo isn't .NET or Go — this skill's bundled tooling won't work.
- There's no diff vs `master`/`main` (e.g. fresh checkout, base branch itself). The script returns `noop`; tell the user and stop.
- The change is purely non-code (docs, README, config-only) — there's nothing to test, the script will report `noop`.
- There are no committed changes vs `master`/`main` (e.g. fresh checkout). The script will return `noop`.

## Files in this skill

- `scripts/measure.sh` — runs tests, produces `coverage.cobertura.xml`, runs `diff-cover`, emits JSON summary. Auto-installs `diff-cover` (pip) and `gocover-cobertura` (go install) on first run.
- `references/prioritization.md` — how to pick which uncovered block to test next, and when deletion beats testing.
- `references/refactor-bridge.md` — how to hand off to the `refactor-for-testability` skill when tests alone aren't moving the metric.
