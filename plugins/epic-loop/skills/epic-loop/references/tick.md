# Tick: one run of the loop

A run is one turn of a loop that something else keeps alive: Castraferrum, a cron job, or a session that drives the
epic (references/drive.md). A run does at most one task, then ends. The next run is a fresh session that remembers
nothing, so everything that matters is on GitHub before you reply: labels, comments, the PR.

**Why only one task.** A run that takes on more (a second task, background agents, a wake-up timer) becomes an
orchestrator that nobody watches and nobody can stop. In one real case, a run whose prompt ended with "Goal: no issues
left" worked for 70 minutes. It started four background agents and scheduled its own wake-ups, and the loop around it
never got another turn. The schedule is the loop; this run is one turn of it.

Below, `E` stands for `node "<skill dir>/scripts/epic.mjs"`. Run it in the repository's main checkout, or add `-R owner/name`.

## 1. Decide

    E next <epic>

It prints one JSON line. Act on its `action` and nothing else:

| `action` | What to do | Result |
|---|---|---|
| `take` | Go on to step 2 with `issue`. | after step 5 or 6 |
| `skip`, `stale: false` | Nothing: another run or a person holds `issue`. | `SKIP` |
| `skip`, `stale: true` | The claim is older than the stale window (6 h). Look for signs of life: an open PR for the issue updated within that window (`gh pr list --search "<n> in:body" --state open --json number,updatedAt`), or recent commits on its branch. If there are none, the run that claimed it died: `E release <n> --reason "stale claim: no activity since <since>"`. Either way, take nothing else in this run. | `SKIP` |
| `wait` | Nothing. The listed issues wait for a person (`loop:needs-human`). | `WAITING` |
| `blocked` | Nothing. Say what blocks what (`issues[].blockedBy`). | `BLOCKED` |
| `done` | Nothing. `next` already closed the finished phases and the epic. | `EPIC_DONE` |

A **dry run** ("dry run", "what would the loop do next", "change nothing"): run `E next <epic> --dry-run`, report the
decision, change nothing and stop. Its last line (step 7) has `"result":"DRY_RUN"`, the issue the run would take (or
null), and the decision in `note`.

## 2. Claim

    E claim <n> --epic <epic> [--note "<ratchet baseline, if the task names one>"]

This adds the label `loop:in-progress` and the comment `loop-attempt: <k>`. Exit code 1 means the task is no longer
yours to take. Either another run got there first (reply `SKIP`), or the task ran out of attempts (`gaveUp: true`; it
now waits for a person, so reply `GAVE_UP`). In both cases stop.

## 3. Read, fully

- **The issue and every comment** (`gh issue view <n> --comments`).
  - Earlier `loop-release:` comments say what went wrong before.
  - An earlier attempt may have left a branch, a worktree or an open PR (`git worktree list`, `gh pr list --search
    "<n> in:body"`). Continue that work instead of starting over.
- **The spec and the plan the issue links.** The plan file on the default branch wins over the copy in the issue.
- **The repository's CLAUDE.md**, and what it points to. It says how this repository builds, tests, branches, writes
  commits and PRs, and which language it is written in. Follow it. This skill does not repeat it, and the loop prompt
  must not either.

## 4. Do the task, exactly as the issue says

- **The issue's Verify block is the contract**: Goal, Red, Green, Stub check, Ratchet.
  - Run Red before you build, and keep its literal output.
  - If Red does not fail the way the issue says, the plan is wrong about the starting point. Comment the output on the
    issue, run `E release <n> --reason "Red does not fail as planned: <one line>" --needs-human`, and reply `BLOCKED`.
  - Measure the ratchet number before you build. It may only move in the good direction.
- **Stay inside the task.** Never weaken or delete a test so that Green passes.
- **Something outside the task** (a bug, a broken tool, a wrong doc): do not fix it here. Open an issue for it. If it
  blocks tasks of this epic, put it in the graph so that it gets fixed first:
  `E link <new> --parent <epic> --blocks <task,...>`.
- **Owner gates are not yours to sign.** Approving a preview, accepting a phase and choosing a design belong to the
  owner, unless the epic's body says the owner delegated that approval.
- **Subagents and background commands** are fine for this task's own work. Wait for them: nothing of yours may still
  be running when you reply.

## 5. Ship

- Follow the repository's normal flow (its CLAUDE.md) and open one PR for this task. The PR body has `Closes #<n>` and
  a `## Verify` section with Red, Green, Stub check and their literal results, plus the ratchet before -> after.
- When it is merged, run `E check <n>`. If the plan keeps an evidence log, add
  `--evidence <log> --heading "## <task id>"`.
  - PASS removes the label, and closes a phase or the epic that has just finished.
  - FAIL lists what is missing. Fix it (edit the PR body, add the evidence) and run `check` again.
- If the repository leaves the merge to a person, do not wait for it. Once the PR is open and green, run
  `E release <n> --reason "PR #<m> waits for review" --needs-human` and reply `DONE` with the PR link. The loop
  then waits at this task, and the issue closes itself when they merge the PR.

## 6. When you cannot finish

Merge nothing whose Green did not match. Push the work to its branch, and comment on the issue what is done, what is
left and what blocks it. Then release the task:

- If a later run can retry it: `E release <n> --reason "<one line>"`.
- If it needs a decision, access or hardware that only a person has: add `--needs-human`.

## 7. Last line

End your reply with exactly one JSON line. Castraferrum shows it as the run's summary.

    {"epic":<e>,"issue":<n or null>,"result":"DONE|SKIP|WAITING|BLOCKED|GAVE_UP|RELEASED|EPIC_DONE|DRY_RUN","pr":"<url or null>","progress":"<done>/<total> done","note":"<one sentence>"}

`progress` is the epic's state when you reply: `counts.done` and `counts.total` from `next`, plus the task this run
closed, if any.

## Never, in a run

- Take a second task, or work on a task you did not claim.
- Schedule yourself (`ScheduleWakeup`, `CronCreate`, `/loop`), or reply while your background agents or commands are
  still running.
- Stop, restart or kill the process that runs the loop (on Castraferrum, the console server), or `claude --resume` a
  session whose run is still going. Two processes on one session work on top of each other.
- Change the loop's own tools (this skill, the repository's loop scripts) as a side effect of a task. That change is a
  task of its own.
