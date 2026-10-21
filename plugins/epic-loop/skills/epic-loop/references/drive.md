# Drive: this session runs the loop, subagents do the tasks

Use this when the owner asks this session to take an epic to done ("drive the issues until the epic is done", "use
/loop, one subagent per issue") instead of running a loop in Castraferrum.

This session coordinates and checks. A subagent does each task in its own context, so this session grows by a few
thousand tokens per task instead of by a whole task. In one measured epic, the coordinating session took about 13k
tokens per round while each subagent used about 256k.

For a long epic, prefer a Castraferrum loop: every run is a fresh session, so nothing grows. Drive when the console is
not available, or when the owner wants this session to watch.

`E` stands for `node "<skill dir>/scripts/epic.mjs"`.

## Start

Start `/loop` without an interval, so that it paces itself, with this prompt filled in:

    Drive epic #<e> of <owner/name> with the epic-loop skill (references/drive.md): one task at a time, each done by one background subagent. This session only coordinates and checks.

## On each wake

1. Run `git pull` in the main checkout, then `E status <e>`.
2. **A task is in progress:**
   - Its subagent is still running (no completion notification yet): `ScheduleWakeup` 1800 s, nothing else.
   - Its subagent has reported (its last line), or there is no subagent because this session restarted: run
     `E check <n>`. Do not take the subagent's report on trust.
     - **PASS**: re-run yourself the one or two Green commands that matter most, and compare the numbers. For visual
       work, open at least one evidence image next to its reference. If they match, go to step 3. If they do not,
       run `E release <n> --reason "<what differs>"`.
     - **FAIL, or the subagent reported BLOCKED**: run `E release <n> --reason "<why>"`. Add `--needs-human` when the
       task needs the owner. If the plan is wrong about the starting point, fix the plan through a PR before the task is
       handed out again.
3. **Run `E next <e>`.**
   - **`take`**: run `E claim <n> --epic <e>`, then start ONE subagent with the brief below (the Agent tool, with
     `run_in_background: true`).
     - Model: sonnet when the plan spells the task out, opus when the task needs design or judgment, haiku for
       research only. Set the effort by how hard the task is.
     - Then `ScheduleWakeup` 1800 s as a safety net. The completion notification usually wakes you first.
   - **`wait`, `blocked` or `done`**: stop the loop (`ScheduleWakeup` with `stop: true`) and report.
4. **After every wake that changed something**, tell the owner in two or three lines: which task, whether it passed,
   failed or was released, and the epic's progress before -> after. When a task passes, also say what they can look
   at (the PR link, a preview, a screenshot).

## Rules

- Never do a task in this session, and never run two tasks at once.
- Do not change the loop's tools in the middle of the loop. A tool bug becomes a new issue under the epic that blocks
  the affected tasks (`E link <new> --parent <e> --blocks <n,...>`), so it is fixed next, through a PR like any task.
- All the state is on GitHub. After a compaction or a restart, start `/loop` again with the same prompt; `E status`
  says where you are.

## The brief

Give it to the subagent word for word, filled in:

    You are doing ONE task of epic #<e> in <owner/name>: issue #<n> "<title>". It is already claimed for you
    (label loop:in-progress). Do not claim, release or start any other task, and do not schedule anything.
    Read <skill dir>/references/tick.md and follow its steps 3 to 5 for #<n>. To check your own work, run
    `node "<skill dir>/scripts/epic.mjs" check <n> --dry-run`; the coordinator runs the real check.
    If you cannot finish: merge nothing, push your work to its branch, comment on the issue what is done and what
    blocks it, and report BLOCKED. The coordinator releases the task.
    End your report with the JSON line of tick.md step 7.
