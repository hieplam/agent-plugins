---
name: epic-loop
description: >-
  Load this skill BEFORE you plan or start any task bigger than one PR (a feature with several parts, a
  migration, a redesign, anything that needs several PRs, phases or sessions), even when the user never says "epic"
  or "issue" and even if you could plan it inline. It is the owner's way of working: the work becomes a GitHub epic
  whose issues are linked (sub-issues; "blocked by" for order; loop:in-progress and loop:needs-human labels for
  state), so a loop of fresh agent sessions can do it one task per run, each task with its own Verify block. Also
  load it for loop prompts ("run one tick of the epic loop on epic #N", "do the next task of epic #N"), to drive an
  epic to done with subagents or /loop, and whenever you create, link, claim, release or report on epic, story or
  task issues (Jira-style epics and stories on GitHub). The owner ratifies a clear goal once, at the start; after
  that the loop verifies itself, decides by standing rules and files what it finds as blocking issues instead of
  asking. Skip it for a fix that fits in one PR.
---

# Epic loop

Work bigger than one PR runs as an **epic**: a GitHub issue whose sub-issues are its tasks, linked so that a session
starting cold can read, from GitHub alone, what is done, what is being done, what comes next and what waits for
whom. A loop then runs one short prompt again and again. Every run is a new session that does one task and ends. This
is the owner's standard way of working on medium and large tasks, so prompts no longer spell it out.

## The approach: ratify once, then a goal-anchored loop runs to done

Read [references/goal-loop.md](references/goal-loop.md) before you plan or drive. In short:

1. **Ratify.** At the very beginning the owner ratifies the goal: outcome rows against a reference they approved, each
   with its oracle, threshold and ratchet, plus the standing rules and the acceptance bar. This is the only human
   phase. If the reference has to be made first, approving it is the last human gate.
2. **Anchor.** Every task and every issue added later names the goal row it serves. Anything that threatens no goal
   row is a follow-up outside the epic.
3. **Verify yourself.** Each task has a Verify block that `E check` enforces. The coordinator re-runs Green rather than
   trusting reports. A final verify task re-proves every goal row with one committed tool, and an agent acceptance
   task does what the owner would have done.
4. **Discovery becomes an issue, never a question.** A red CI, a flaky test, a real bug, a records gap, a visible
   defect: each becomes an issue with evidence and a Verify block, linked to block the task it threatens
   (`E link ... --blocks`, or `E release ... --found`). The loop keeps going and fixes it first.
5. **Decide by standing rules,** record each decision, and ask a person only for what only a person has
   (credentials, hardware, money).
6. **Run independent work in parallel** once the "blocked by" links follow the real dependencies.

## Pick the job

| The request | Job | Read first |
|---|---|---|
| New work that needs more than one PR, or "create an epic", "split this into issues", "plan it so the loop can do it" | **Plan** it as an epic | [references/goal-loop.md](references/goal-loop.md), then [references/plan.md](references/plan.md) |
| A loop prompt: "run one tick of the epic loop on epic #N", "do the next task of epic #N" | **Tick**: at most one task, then end | [references/tick.md](references/tick.md) |
| "drive epic #N until it is done", "use /loop, one subagent per issue", "finish the epic as soon as possible" | **Drive** the loop from this session | [references/goal-loop.md](references/goal-loop.md), then [references/drive.md](references/drive.md) |
| "where is epic #N", "what is next" | `E status <epic>`, then report (below) | |

Read the reference in full before acting. It is short, and each step is there because skipping it caused a problem before.
Work that fits in one PR and one session needs no epic: do it directly, or as a single issue.

## The graph

- **Epic** = the parent issue. **Phase** (optional) = a sub-issue of the epic with its own spec and plan. **Task** = an
  issue with no sub-issues: one PR's worth of work, with its own Verify block (Goal, Red, Green, Stub check, Ratchet).
- **Order** = "blocked by" links (GitHub issue dependencies), then the sub-issue order on the parent. A task never
  starts while a blocker is open. A blocker or label on a phase applies to every task in that phase.
- **State** lives on the issues. Closed means done. `loop:in-progress` means a run holds the task. A loop tick runs
  one task at a time; a driving session may run several ready tasks side by side (`--parallel`). `loop:needs-human`
  marks a ratification gate, or a task that needs something only a person has (credentials, hardware, money). No
  loop takes it, and it is never used for a sign-off after ratification. Any other open task is ready or blocked.
  Every claim adds a `loop-attempt: <k>` comment. After 3 failed attempts the task moves to `loop:needs-human`, but an
  attempt that ended by filing a real defect (`release --found`) does not count.
- **Done** means closed by a merged PR that says `Closes #<n>` and has a `## Verify` section with Red, Green and Stub
  check, each with its result.
- **How** a task gets built and shipped (worktrees, branches, test commands, language, PR and merge rules) belongs to
  the repository's CLAUDE.md and the owner's memory. Follow those. Neither this skill nor a loop prompt restates
  them, because the loop only runs a prompt and the project's own setup decides the workflow.

## The script

`scripts/epic.mjs` (Node 18 or later, with `gh` logged in) does every read and write of the graph, so no run has to
guess. Below, `E` stands for `node "<this skill's directory>/scripts/epic.mjs"`. Run it inside the repository, or
add `-R owner/name`.

| Command | What it does |
|---|---|
| `E status <epic>` | Prints the tree with every task's state, and what `next` would do. |
| `E next <epic> [--dry-run] [--parallel]` | Prints the decision of one run as one JSON line: `take`, `skip`, `wait`, `blocked` or `done`. Without `--dry-run` it also closes phases and the epic once all their sub-issues are closed. With `--parallel` it takes a ready task even while others are in progress, and lists every ready task (`ready`) and the tasks in progress (`inProgress`). |
| `E claim <task> --epic <epic> [--note "..."] [--parallel]` | Labels the task `loop:in-progress` and comments `loop-attempt: <k>`. Refuses unless the task is ready and, without `--parallel`, nothing else in the epic is in progress. |
| `E release <task> --reason "..." [--needs-human] [--found <n,...>]` | Puts the task back in the queue, or hands it to a human. Reopens it if it was closed. `--found` names the issues this attempt filed: each joins the epic and blocks the task, and the attempt does not count toward the limit. |
| `E check <task> [--dry-run] [--evidence <file> --heading "## <id>"]` | Checks the task is really done. On PASS it removes the label and closes any parent that just finished. |
| `E link <issue> [--parent <n>] [--blocked-by <n,...>] [--blocks <n,...>]` | Adds an issue to the graph, for example one found partway through. A link that already exists is reported as `(already)`, not an error. |
| `E unlink <issue> [--blocked-by <n,...>] [--blocks <n,...>]` | Removes "blocked by" links, to rewire a plan's convenience chain to the real dependencies. |
| `E create <manifest.json> [--dry-run]` | Creates an epic, or phases and tasks under an existing one, with every link (see references/plan.md). |

Exit codes: 0 means ok, 1 means refused or not done, 2 means bad usage or a GitHub error. The script also reads
labels that a repository may already use: the older loop labels `loop:dang-lam` and `loop:can-nguoi`, `in-progress`
(counts as taken), and `frozen`, `on-hold` and `blocked` (count as held). It only ever writes the two loop labels,
and keeps a repository's older names when it has them. Tests: `node --test scripts/tests/epic.test.mjs`.

## Reporting to the owner

Whenever a task finishes, or the owner asks how an epic is going, report two things:

1. **Where the whole epic stands**: done, in progress, next, and what blocks what. Use a short table, not the
   Red/Green of one task.
2. **What they can look at now**: a link they can open directly (PR with screenshots, a preview page, a running
   URL), or "nothing to see yet, because ...". Name the owner gates waiting for them; after ratification there
   should be none.
3. **The decisions made for them** under the standing rules, and the issues the loop added (one line each: what and
   which goal row). Report decisions, not questions.
