# Drive: this session runs the loop, subagents do the tasks

Use this when the owner asks this session to take an epic to done ("drive the issues until the epic is done", "use
/loop, one subagent per issue", "finish the epic as soon as possible") instead of running a loop in Castraferrum.
Read references/goal-loop.md first: after ratification you never bring the owner a question or a sign-off. You verify,
you decide by the standing rules, and you turn what you find into issues that block the right task.

This session coordinates and checks. A subagent does each task in its own context, so this session grows by a few
thousand tokens per task instead of by a whole task. In one measured epic, the coordinating session took about 13k
tokens per round while each subagent used about 256k.

For a long epic, prefer a Castraferrum loop: every run is a fresh session, so nothing grows. Drive when the console is
not available, or when the owner wants this session to watch.

`E` stands for `node "<skill dir>/scripts/epic.mjs"`.

## Start

1. `E status <e>`. Check that the epic body records the ratified goal, the standing rules and the acceptance bar. If
   one is missing, that is the one thing to take to the owner, before anything runs.
2. **Rewire to the real dependencies.** For each "blocked by" link, ask whether the task really needs that blocker's
   output (its files, its screen, its data). Remove the convenience links with `E unlink <n> --blocked-by <m>`, and
   add the real ones with `E link`. Then `E next <e> --dry-run --parallel` lists what can start now.
3. **Lane isolation,** when tasks will run side by side. Give each lane its own resources:
   - a test database, a port, and a scratch folder;
   - a machine-wide cap on heavy runs (two mkdir-lock slots worked on a 16 GB machine).

   Write these rules once into a shared file that every brief points to. Tell lanes to rebase and take the union before
   merging, and to re-shoot a conflicting binary instead of merging it by hand.
4. Start `/loop` without an interval, so that it paces itself, with this prompt filled in:

       Drive epic #<e> of <owner/name> with the epic-loop skill (references/drive.md): every ready task in parallel, each done by one background subagent. This session only coordinates and checks.

   Say "one task at a time" instead when the owner asks for it or the machine cannot take parallel lanes.

## On each wake

1. Run `git pull` in the main checkout, then `E status <e>`.
2. **For each task in progress:**
   - Its subagent is still running (no completion notification yet): nothing.
   - Its subagent has reported, or there is no subagent because this session restarted: look at the issue, its branch,
     its worktree and its PR first. A subagent stopped by a restart often left finished work (a PR with green CI, or
     uncommitted files). Resume it with SendMessage (its transcript is kept). Do not start a new one.
   - **Verify, never trust:** run `E check <n>` (with `--evidence`/`--heading` when the plan keeps a log).
     - **PASS:** re-run yourself the one or two Green commands that matter most, from a clean build, and compare the
       numbers. For visual work, open at least one evidence image next to its reference. If they do not match, run
       `E release <n> --reason "<what differs>"`.
     - **FAIL, or the subagent reported BLOCKED:** read why.
       - It filed a defect that stops it: `E release <n> --reason "<why>" --found <new,...>`. The attempt does not
         count, and the defect is next.
       - It needs a plan fix: file the correction as an issue and release with `--found`.
       - Otherwise: `E release <n> --reason "<why>"`.
       - Use `--needs-human` only for what only a person has (goal-loop.md, section 6).
     - A PR that is open with green CI but not merged: tell its subagent to merge, or verify and merge it yourself
       when the subagent is gone.
3. **Start what is ready.** `E next <e> --parallel` (or plain `E next <e>` in one-task mode).
   - **`take`:** for each task in `ready`, run `E claim <n> --epic <e> --parallel`, then start ONE subagent per task
     with the brief below (the Agent tool, with `run_in_background: true`). Model: as the owner prefers; otherwise
     sonnet when the plan spells the task out, opus when it needs design or judgment.
   - **A batch of findings** (for example the defects of an acceptance task): group them into lanes by code area, one
     subagent and one PR per lane, so that lanes rarely touch the same files.
   - **`wait`:** the only gate left is ratification, so report it. **`done`:** report. **`blocked`:** a blocker
     outside the epic, so bring it in (`E link <it> --parent <e>`) if it threatens a goal row.
4. **After every wake that changed something,** tell the owner in a few lines:
   - which tasks passed, failed or were released;
   - the issues the loop added, and which goal row each one serves;
   - the decisions made under the standing rules;
   - the epic's progress before -> after;
   - what they can look at (the PR link, a preview, a screenshot).

   Report decisions, never questions.

## Rules

- Never do a task's work in this session. Coordinating, verifying, filing issues and rewiring links are your work.
- Run tasks in parallel only when their "blocked by" links say they are independent, and their lanes are isolated.
- Every issue you or a subagent adds names the goal row it serves; anything else is a follow-up outside the epic.
- A tool bug becomes a new issue under the epic that blocks the affected tasks. Do not change the loop's tools in the
  middle of the loop; it is fixed next, through a PR like any task.
- Watch the machine. Before heavy runs, check for heavy jobs from other sessions; if one is running, wait and say so.
  Keep services the tests need (a database container) running, and restart them after a reboot. Never close the
  owner's own apps without asking.
- All the state is on GitHub. After a compaction or a restart, start `/loop` again with the same prompt; `E status`
  says where you are.

## The brief

Give it to the subagent word for word, filled in. Add the lane line when lanes run in parallel.

    You are doing ONE task of epic #<e> in <owner/name>: issue #<n> "<title>". It is already claimed for you
    (label loop:in-progress). Do not claim, release or start any other task, and do not schedule anything.
    Read <skill dir>/references/goal-loop.md and <skill dir>/references/tick.md, and follow tick.md steps 3 to 5
    for #<n>. To check your own work, run `node "<skill dir>/scripts/epic.mjs" check <n> --dry-run`; the
    coordinator runs the real check.
    The owner is never asked anything: decide by the standing rules in the epic's body and record each decision.
    Anything you find that stands between the code and a goal row becomes an issue (evidence, goal row, Verify
    block) linked under the epic to block what it threatens; name it in your report.
    Commit and push early: the session may restart.
    [Lane: <name>, resources <db / port / folder>; read <shared rules file> first.]
    If you cannot finish: merge nothing, push your work to its branch, comment on the issue what is done and what
    blocks it, and report BLOCKED with the issues you filed. The coordinator releases the task.
    End your report with the JSON line of tick.md step 7.
