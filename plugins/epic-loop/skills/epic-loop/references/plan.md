# Plan: turn a medium or large task into an epic a loop can run

You are done when all of these are true:

- The epic and every task exist on GitHub, linked in order, and each task carries its own Verify block.
- The epic carries a one-line loop prompt.
- `E status <epic>` shows the order you meant.
- The owner knows where they come in.

`E` stands for `node "<skill dir>/scripts/epic.mjs"`.

## 1. Size and shape

- **One task is one PR** that a single session can finish and verify on its own: an hour or a few of agent work.
  Doing several units in one PR is how mistakes slip through. One unit, one PR.
- **Several tasks make an epic.** Use **phases** when there are many tasks, or two stages with a checkpoint between them
  (for example: local network access first, remote access later). Each phase is a sub-issue of the epic with its own
  spec and plan, and its tasks are sections of that plan.
- **Read the repository first**: its CLAUDE.md and docs, where specs and plans live, and how earlier epics look
  (`gh issue list --search "Epic in:title" --state all`). Copy the conventions that already exist.
- **No GitHub repository** (no remote, or `gh` cannot reach it): say so, and ask the owner where the epic should
  live. Never create a repository yourself.

## 2. Open the epic early

Create the epic issue (`gh issue create`) as soon as the work is clear enough to name. Put in it:

- what the work delivers (the features, not the motive, unless the owner wants the why written down);
- the findings so far and the draft goals;
- the steps still ahead (brainstorm, spec and plan, preview, build, acceptance).

From then on the state lives in that issue, not in this session: a compaction or a crash loses nothing.

## 3. Let the owner decide what is theirs

Some choices belong to the owner: look and feel, names, scope, and trade-offs that cost something. Take them to the
owner before the spec, as options they can see (mockups and examples first, terms after). Show them together with
the draft shape of the epic, one line per phase and task (what it delivers, what proves it, what it waits for), so
the owner answers with the whole picture in view. Decide everything else yourself, and say what you decided.
Approvals that come later in the work, such as a preview or an acceptance test, become tasks of their own (step 4).

## 4. Spec and plan, merged before the issues

Write them where the repository keeps them (for example `docs/specs/<date>-<topic>.md` and
`docs/plans/<date>-<topic>.md`), one pair per phase. The plan contains:

- **"How to read this plan"**: the plan file wins over the copy in an issue, and where the evidence goes (for example
  an evidence log, `docs/plans/<date>-<topic>-evidence.md`, with one `## <task id>` section per task).
- **One section per task**, headed `## <ID> - <title>`, with IDs such as L0, L1, L2 per phase. Each section lists the
  files it touches, its steps, and its Verify block:
  - **Goal:** the outcome a user sees, stated against the artifact the owner ratified ("the session page matches
    `preview.html`"), not the means ("styled with the tokens").
  - **Red:** the command run before building, and the failure it shows. Write "not applicable" plus the reason when the
    task has no code to go red.
  - **Green:** the command run after building, and its literal expected output.
  - **Stub check:** why doing nothing, or a stub, fails Green. If a stub would pass, Green verifies nothing: change it.
  - **Ratchet:** a number that a committed tool measures before and after, and the direction it may move.

  The oracle must be the same kind of thing as the claim. A visual claim is compared side by side with the reference.
  A behaviour is checked by an end-to-end run. A performance claim needs a measurement. A feature's Green includes its
  unit tests and its end-to-end tests.
- **A first task that measures the baseline**, when the plan moves numbers.
- **Owner gates as tasks of their own**, marked `loop:needs-human` in the plan. Put "Owner approval of the <x>
  preview" right after each preview task, and "Owner acceptance" at the end of each phase. The loop stops at a gate
  until the owner closes it.

Merge the docs through the repository's normal PR flow before you create the tasks, because the issues link to the
files on the default branch.

## 5. Create the tasks and their links

Write a manifest in your scratch directory. Run `E create <manifest> --dry-run`, read the plan it prints, then run it
again without `--dry-run`. `create` prints `{"epic": <n>, "issues": {"<key>": <number>, ...}}`.

```json
{
  "repo": "owner/name",
  "labels": ["network-access"],
  "epic": { "number": 46 },
  "phases": [
    {
      "key": "P1",
      "title": "Phase 1: local network access",
      "spec": "docs/specs/2026-10-21-lan-access.md",
      "plan": "docs/plans/2026-10-21-lan-access.md",
      "evidence": "docs/plans/2026-10-21-lan-access-evidence.md",
      "tasks": [
        { "key": "L0", "title": "L0 - Baseline numbers", "section": "L0" },
        { "key": "L1", "title": "L1 - Command-line flags", "section": "L1" },
        { "key": "L2", "title": "L2 - Owner approval of the phone preview", "section": "L2", "needsHuman": true }
      ]
    }
  ]
}
```

- **`epic`**: `{ "number": <n> }` attaches the tasks to the epic you opened in step 2. To create a new epic instead,
  use `{ "title": "...", "body": "<file>" }`.
- **`section`** copies the plan's `## L0 ...` section verbatim, under a generated English header: which phase and epic
  the task belongs to, links to the spec and plan, and what "done" means.
- **`body`** gives your own text instead of the header, from a file path relative to the manifest. With both `body`
  and `section`, the section follows your text. Use `body` when the repository's issues are written in another language.
- **Paths**: `spec`, `plan` and `evidence` are relative to the repository, and are read from the current checkout.
- **Order**: by default each task is blocked by the one before it (across phases), and each phase by the phase before
  it. `"blockedBy": ["L1", "L2"]` replaces that for one task, and `"blockedBy": []` lets it start at any time. Use
  these for work that can run in parallel.
- **Without phases**, put `"tasks"` at the top level of the manifest.
- **Numbers in text**: any body may write `{{#KEY}}`. It becomes the issue number once every issue exists.

## 6. The epic's body

Update the epic (`gh issue edit <e> --body-file <file>`) so that a person sees the plan at a glance:

- the phases table: phase issue, spec, plan, and the tasks in order, with the owner gates marked;
- how the state is kept: the labels, the `loop-attempt:` comments, the evidence log;
- who approves what, and anything the owner delegated (only if they said so);
- a **Loop prompt** section with the suggested settings and this prompt, and nothing more:

  ```text
  Run one tick of the epic loop on epic #<e> of <owner/name>, using the epic-loop skill. If that skill is not available, change nothing and reply SKILL MISSING.
  ```

  Suggested settings for Castraferrum:
  - repository: the main checkout;
  - schedule: every 30 minutes;
  - at most one run at a time;
  - timeout: longer than a task takes (for example 4 h), but shorter than the 6 h after which a claim counts as stale;
  - model and effort: chosen by how hard the tasks are.

  Keep the workflow out of the prompt (worktrees, PR rules, test commands, "one task per run"). The skill and the
  repository's CLAUDE.md carry it. Keep goals like "until no issues are left" out too: a run that reads a goal tries to
  finish the whole epic by itself.

## 7. Check, then report

- `E status <e>` shows every task in the order you meant: the first one `ready`, the gates `needs human`.
- `E next <e> --dry-run` takes the first task.
- Report to the owner:
  - the epic's link;
  - a table of phases and their tasks in order, with the owner gates in bold;
  - the decisions they should know about;
  - the loop prompt;
  - what they can look at now, or "nothing to see yet, because ...".
