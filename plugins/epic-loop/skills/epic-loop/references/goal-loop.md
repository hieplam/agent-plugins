# The goal-anchored loop: ratify once, then run to the goal without asking

This is how the owner wants every epic to run. The owner ratifies a clear, realistic goal **once, at the start**.
From then on the loop runs on its own:
- it verifies its own work;
- it decides by standing rules;
- it turns everything it finds between the code and the goal into an issue that blocks the right task.

New issues are how the loop keeps going without stopping to ask. The epic ends when every goal's oracle passes, not
when someone signs it off. Read this before you plan (references/plan.md) or drive (references/drive.md) an epic; a tick
(references/tick.md) follows the parts that apply to one run.

Why: in castraferrum epic 63 (the UI uplift), the owner said, after approving the design: "you verify your self, dont
invoking me anymore, I already approved the design". The epic grew from 38 planned issues to 67, and every added issue
came from this approach. They were all real and none was a question:
- a red CI on main;
- three preview-against-app data differences;
- four flaky tests and a deadlock;
- a records gap;
- two real bugs found by the final verify task (a picker that saved the wrong folder, a test that failed every night
  from 22:00 to 24:00);
- nineteen visible defects found by the agent's own acceptance.

The owner's verdict on the approach: "This improvement loop without-human is exactly what i want."

## 1. Ratify: the only human phase, before the loop starts

Nothing below works without a ratified goal. Get these from the owner, written in the epic body and the spec, before
any build task is created:

- **Goal rows.** Each row is an outcome a user sees, stated against an artifact the owner ratified (a design, a
  preview, an API contract, a rule): "every screen matches the approved preview", never "styled with the tokens".
  Each row names:
  - its **oracle**, of the same kind as the claim: visual means side by side with the reference, behaviour means an
    end-to-end run, performance means a measurement;
  - its **threshold** (for example, a screen pair is at most 0.1 % off);
  - its **ratchet**: a number a committed tool measures before and after.
- **Realistic.** An agent on this machine must be able to run every oracle. If a check seems to need a person (a
  real phone, a pair of eyes), turn it into a check an agent can run before ratification:
  - a committed Playwright journey;
  - device emulation;
  - a relay on the LAN address;
  - the compare tool, plus reading each image.

  The real-hardware part becomes a follow-up outside the epic.
- **The reference exists.** If the reference has to be made first (a design system, a preview), making it is the first
  phase, and the owner's approval of it is the **last human gate**. Everything after that gate runs without the owner.
- **Standing rules.** These are how the loop decides what the owner would otherwise be asked (section 4). The owner
  ratifies them with the goal and may change the defaults.
- **The acceptance bar and the scope.** What "done" means, for example: every goal row's oracle passes on main, and
  an agent acceptance finds zero open defects against the reference. Also what is outside the scope: a finding
  outside it becomes a follow-up issue, not a blocker.

The epic body then states it plainly: "after <last gate>, the owner is not asked to verify or decide anything".

## 2. Anchor: every issue serves a goal row

The loop may add as many issues as it needs. Each one says which goal row it protects. An issue that threatens no
goal row is a follow-up outside the epic: it is real work, but it does not block. This rule makes "keep adding
issues" lead to the goal instead of to endless polish. When it is unclear, ask whether the acceptance bar would fail
without the fix.

## 3. Verify yourself, at three levels

1. **Every task** carries its Verify block: Goal, Red, Green, Stub check, Ratchet. `E check` refuses "done" without a
   merged PR that closes the task and has a `## Verify` section.
2. **The coordinator trusts no report.** It runs the real `E check`, re-runs the one or two Green commands that matter,
   and puts one evidence image next to its reference. In epic 63 this caught:
   - a re-run that passed only because of a half-built tree;
   - a goal-check script that printed usage and exited 0 when run without its manifest.
3. **The end re-proves everything.**
   - A **verify task** re-runs every goal row with one committed tool (a goal manifest plus `goal:check`). It also
     runs the epic-wide `E check --dry-run` on every closed task.
   - An **acceptance task** does what the owner would have done, as an agent: a committed journey through the real
     build, the compare page, and every image opened and judged one by one. Each defect becomes an issue (section 5).

## 4. Decide by standing rules, and record the decision

These defaults worked. The epic body may replace them at ratification.

- **The reference and the app disagree on sample data:** the app's honest behaviour wins. The reference is corrected
  and re-shot, and the decision log (for example `design/APPROVED.md`) records it.
- **The system has no rule for what a task needs** (a design gap): extend the system inside its own rules, draw it in
  the reference, record it, and go on. Do not wait.
- **A plan command no longer fits the shipped tree:** use the equivalent that proves the same goal, and record why.
  Never weaken what a goal proves.
- **A test is flaky:** fix the root cause, never with retries, longer sleeps or looser assertions. A test that depends
  on the wall clock pins its clock.
- **Another process on the machine skews a run** (a heavy job in another session): wait until the machine is quiet,
  re-run once, and record both runs.
- **Never weaken or delete a test** to make Green pass.

## 5. Discovery becomes an issue, never a question

Whatever stands between the code and a goal row becomes an issue with:
- its evidence (literal output, a measurement, the image);
- the goal row it threatens;
- its own Verify block.

Then it goes into the graph so that it is fixed first:

    E link <new> --parent <epic> --blocks <task,...>             # found while planning or coordinating
    E release <task> --reason "<one line>" --found <new,...>     # found by the run that was doing <task>

`--found` puts the new issue into the epic, makes it block the task, and does not count the attempt toward the
attempt limit. The attempt found a defect; it did not fail the task. This covers:
- a red CI on main;
- a flaky test or a deadlock;
- a real bug that a check exposed;
- a records gap (a closing PR that does not link its issue);
- a tool bug;
- every visible defect from acceptance.

A fixer that sweeps for more of its own kind files what it finds the same way.

**Never:** an "owner decides" issue, `loop:needs-human` for a sign-off, or a task left waiting on a question.

## 6. When a person really is needed

Use `loop:needs-human` (and `E release --needs-human`) only for what only a person has:
- credentials or a login;
- hardware the agent cannot reach;
- spending money;
- an action on the owner's own apps or data outside the repository;
- a ratified goal that turns out impossible or contradictory. Then propose the change, and only that goal row waits.

Ask once, with the options and a recommendation, and keep every other lane running.

## 7. Speed: run independent work in parallel

- Plans chain tasks for convenience. Before driving, check each "blocked by" against what the task really needs, and
  rewire to the real dependencies with `E unlink` and `E link`.
- `E next <e> --parallel` lists every ready task. `E claim <n> --epic <e> --parallel` claims one while others run. Run
  one subagent per lane.
- Lanes must not collide on anything shared:
  - its own test database (in castraferrum, the name must end in `_test`), port and scratch folder;
  - heavy runs (Playwright, full verify) capped by machine-wide slots;
  - rebase and take the union before merging;
  - re-shoot a conflicting binary shot instead of merging it by hand.
- Group a batch of defects into lanes by code area, so that lanes rarely touch the same file.
- See references/drive.md for the coordinator's side.
