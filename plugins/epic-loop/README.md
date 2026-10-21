# epic-loop

A skill for work that is bigger than one PR. The work becomes a GitHub **epic** whose issues are linked into a graph,
so that a session starting cold can read, from GitHub alone, which task is done, which is in progress, which comes
next and which waits for whom. A loop then runs one short prompt again and again, and every run is a fresh session
that does exactly one task and ends.

| Path | What it is |
| --- | --- |
| `skills/epic-loop/SKILL.md` | when to load it, the graph contract, the script's commands |
| `skills/epic-loop/references/plan.md` | **Plan**: turn a medium or large task into an epic a loop can run |
| `skills/epic-loop/references/tick.md` | **Tick**: one run of the loop, at most one task |
| `skills/epic-loop/references/drive.md` | **Drive**: this session runs `/loop`, one background subagent per task |
| `skills/epic-loop/scripts/epic.mjs` | reads and writes the graph (`status`, `next`, `claim`, `release`, `check`, `link`, `create`) |
| `skills/epic-loop/scripts/tests/` | `node --test` suite against a fake `gh` (no network) |
| `skills/epic-loop/evals/` | harness cases for `scripts/evals/run_evals.py`, with fake-GitHub fixtures |

## The graph

- **Epic** = the parent issue. **Phase** (optional) = a sub-issue of the epic with its own spec and plan.
  **Task** = an issue with no sub-issues: one PR, with its own Verify block (Goal, Red, Green, Stub check, Ratchet).
- **Order** = GitHub's "blocked by" links, then the sub-issue order. A blocker or label on a phase applies to its tasks.
- **State** = the issue: closed is done; `loop:in-progress` means a run holds it (one task per epic at a time);
  `loop:needs-human` marks an owner gate or a stuck task, which no loop takes. Each claim adds a `loop-attempt: <k>`
  comment; after 3 attempts the task goes to a human.
- **Done** = closed by a merged PR that says `Closes #<n>` and has a `## Verify` section with Red, Green and Stub check.
- **How** to build and ship is the repository's business (its CLAUDE.md), never the skill's or the loop prompt's.

A run's decision comes from `epic.mjs next <epic>`, one JSON line: `take` (claim it, do it, ship it), `skip`
(another run holds a task), `wait` (an owner gate is next), `blocked` (open blockers outside the epic), or `done`
(it closes the finished phases and the epic).

## Install

```
/plugin marketplace add hieplam/agent-plugins
/plugin install epic-loop@agent-plugins
```

or, from a checkout, `./install.sh epic-loop` (links `skills/epic-loop/` into `~/.claude/skills/`).

Needs the GitHub CLI `gh`, logged in, on a repository with sub-issues and issue dependencies (GitHub has both), and
Node 18 or later for `epic.mjs`.

## Use

**Plan.** Ask for the work as you normally would ("add user accounts: sign-up, login, password reset, an admin
page, full tests"). The skill loads for anything bigger than one PR. It asks you the decisions that are yours, gets
the spec and plan merged, creates the epic, phases and tasks with every link (`epic.mjs create`), and puts a loop
prompt in the epic.

**Loop.** The prompt in the epic is one line; the workflow lives in the skill and in the repository's CLAUDE.md:

```text
Run one tick of the epic loop on epic #<e> of <owner/name>, using the epic-loop skill. If that skill is not available, change nothing and reply SKILL MISSING.
```

Run it every 30 minutes with at most one run at a time, for example in Castraferrum or with `claude -p` on a
schedule. Give each run a timeout longer than a task takes and shorter than 6 hours (after 6 hours a claim counts as
stale). Each run ends with one JSON line, for example `{"epic":46,"issue":48,"result":"DONE","pr":"...","progress":"1/14 done","note":"..."}`.

**Drive.** "Drive epic #46 until it is done" makes the current session run `/loop`: it checks each finished task
by machine, then hands the next one to one background subagent.

**Make it the default.** Claude loads a skill when its description matches the task, and the description is written
to load before planning anything bigger than one PR. To make that a hard rule in every project, add this line to
`~/.claude/CLAUDE.md`:

```markdown
Work bigger than one PR (several PRs, phases or sessions): load the epic-loop skill before planning it.
```

## Tests and evals

```bash
# the script, against a fake gh (34 tests, no network, no tokens)
node --test plugins/epic-loop/skills/epic-loop/scripts/tests/epic.test.mjs

# with and without the skill, through the repo harness (spends real tokens)
python3 scripts/evals/run_evals.py --evals plugins/epic-loop/skills/epic-loop/evals/evals.json
```

The tick cases point `epic.mjs` at the fake `gh` (`EPIC_LOOP_GH_MODULE`) and a fixture world in the scratch
directory, so they run anywhere, change nothing real, and check the reply's last line and that the world is
unchanged. The trigger cases are graded on the transcript.

## Evidence (2026-10-21)

- **Script:** the suite went from 0/33 (written first, before `epic.mjs` existed) to 34/34. Seven deliberate
  mutations (ranking, inherited blockers, stale window, phase closing, older attempt comments, the Verify words,
  one task at a time) each made at least one test fail.
- **Real epics, read-only:** on a 14-task, two-phase epic `next --dry-run` takes the first task; on an 18-task epic
  with only the owner's acceptance left it waits for the owner; on an epic whose tasks are labelled `frozen` it
  waits instead of taking a frozen task.
- **Fresh sessions on a real repository:** a dry-run tick named the right task and changed nothing (40 issues
  compared before and after); the same prompt with skills disabled (`--safe-mode`) answered `SKILL MISSING` and
  ran no tool; a real tick on the waiting epic answered `WAITING` and wrote nothing.
- **Loading:** with the first description, a multi-PR feature request loaded the skill in 0 of 3 sessions; with the
  current one, in 7 of 7 (two repositories, with and without shell tools). A typo fix, a one-PR bug fix and a
  question loaded it in 0 of 3.
- **Harness** (`run_evals.py`, executor opus, one run per case): with the skill 5/5 cases pass, without it 1/5
  (only the "do not load it for a typo" case, which a baseline passes by definition). Mean time 22.8 s with the
  skill, 8.8 s without.

## Origin

Distilled from two weeks of sessions in which the owner described this loop by hand again and again: "each run reads
the epic, finds the task it can do, skips if a task is in progress", "the issues must link to each other", "the loop
only runs a prompt on a schedule; the project's CLAUDE.md decides the workflow". It also folds in one incident: a run
whose prompt ended with "Goal: no issues left" ran for 70 minutes, started four background agents and scheduled its
own wake-ups, so `tick.md` now tells every run that it is one turn of the loop.
