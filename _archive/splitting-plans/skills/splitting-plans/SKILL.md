---
name: splitting-plans
description: Split a large monolithic implementation plan into isolated, dependency-aware sub-plans that parallel subagents can pick up. Use when a plan exceeds ~500 lines, when tasks need clear definitions-of-done with verify/validate steps, or when the user wants to divide-and-conquer a long plan across parallel agents.
---

# Brainstorm — Split a 3000-line Plan into Isolated Parallel Sub-plans

**Source session:** `d833cb7a-7b7b-4dfc-8c4d-9ac71cfd02a8`
**Date:** 2026-05-25 19:41
**Project:** `claude-copilot-sync`
**Spec input:** `docs/superpowers/specs/2026-05-24-claude-copilot-sync-design.md`
**Monolithic plan produced first:** `docs/superpowers/plans/_archive/2026-05-24-claude-copilot-sync.md` (2227 lines, 15 tasks)
**Split result:** `docs/superpowers/plans/2026-05-24-claude-copilot-sync/` (README + 14 sub-plans)

This document captures the brainstormed methodology so it can be lifted into a reusable skill.

---

## 1. The Problem (user's exact framing)

> Current problem: Plans with 3000 lines are hard to track, LLMs easy to forget, bunchs of tasks chain togheter is hard to know the overview.
> So back and forth with me on this, outlines for open questions.
> review each tasks carefuly, each task should isolate at some part (which mean its implementation is sealed, dont have too many dependency outside).
> Each task must have defination of done. Based on that,the output of each task *must* be cleared. Also have the way to verify fist and validate second (based on how is done). At the end of each task, before signoff
>  task is done self-audit the task to makesure it strickly follow defination of done.
> If that dont need any input from outside world, thats oky.But it have input, which mean the task can not be start without input, this means the task is depend on other factors like prerequist task must completed.
> In that case, chain those tasks together. Repeat the loop until bunch of depent task can be isolate.
> Once task(s) can isolate, split to seperate md file sub-plan so that parralel subagent can pick and executed. The main plan must be the one control the task and observe status.
> The ultimate goal: devide and conquer, split burden to main Agent, to sub agents, easier to execute parallel subagents. When do the loop of each task, this is the seconds chance to self audit, and think carefuly
> of each task.

Symptoms:

- Single monolithic plan file overflows subagent context.
- Hard to see overview / dependency shape.
- Hard to assign work in parallel.
- Easy to lose track of which task is done, locked, available, or blocked.
- Hard to audit one task without re-reading the whole plan.

## 2. Design Goals (user's wording, cleaned)

1. **Isolation.** Each task self-contained — implementation sealed, minimal external dependency.
2. **Definition of Done (DoD).** Output of each task must be clear and observable.
3. **Verify first, Validate second.** Two-stage proof: (a) correctness via tests, (b) sanity via syntax/scope/no-drift checks.
4. **Self-audit before sign-off.** Strict check against DoD before flipping status.
5. **Explicit prerequisites.** If a task needs input, the input is named and the task waits until prereqs are DONE.
6. **Chain → isolate → split.** Walk the dependency graph; when a clump of dependent tasks can be isolated together (sealed unit), split it into a sub-plan file. Repeat until graph leaves are independent.
7. **Parallel-pickable sub-plans.** Each isolated unit becomes a separate `.md` file so subagents can self-pick and run concurrently.
8. **Main plan = orchestrator only.** Holds status, dispatch protocol, cross-bundle contracts. Does **not** carry implementation detail.
9. **Loop = second audit chance.** Re-reviewing each task during dispatch is a natural second pass over design quality.

Stated ultimate goal: **divide and conquer — offload burden from main agent to subagents, enable parallel execution.**

## 3. Method — Step by Step

> Sub-sections 3.1 – 3.3 use a transparent worked example: **"make breakfast for 4 people, by a team of cooks in one kitchen."** Nothing in the example is project-specific. The same reasoning maps 1-to-1 onto software tasks (replace "pan" with "file", "cook" with "subagent", "kitchen" with "git working copy").

### 3.1 Example — Build the dependency graph

**Scenario:** 8 tasks to put breakfast on the table.

| Task | Action | Needs (input) |
|---|---|---|
| T1 | gather ingredients from pantry / fridge | — |
| T2 | boil water in kettle | T1 |
| T3 | brew coffee (pour boiled water through grounds) | T2 |
| T4 | toast bread | T1 |
| T5 | fry bacon | T1 |
| T6 | cut fruit | T1 |
| T7 | scramble eggs | T1 |
| T8 | plate everything, place on table | T3, T4, T5, T6, T7 |

Read each task's "needs" column → draw arrows. Result:

```
            ┌── T2 ── T3 ──┐
            │              │
            ├── T4 ────────┤
T1 ─────────┤              │
            ├── T5 ────────├── T8
            │              │
            ├── T6 ────────┤
            │              │
            └── T7 ────────┘
```

Lesson: the graph is mechanically derivable. No subjective judgement — just "what does this task need before it can start?".

### 3.2 Example — Identify parallelism waves

A **wave** = set of tasks whose prereqs are all satisfied at the same moment → runnable concurrently.

Walk the graph layer by layer:

```
Wave A:   T1                          (the only task with no prereqs)
Wave B:   T2, T4, T5, T6, T7          [5 parallel — boil + toast + bacon + fruit + eggs]
Wave C:   T3                          (needs T2 — joins late, even though "next step for coffee")
Wave D:   T8                          (needs everyone)
```

Lessons:

- **Wave membership ≠ task family.** T3 (coffee) is in the "coffee chain" with T2, but it sits in its own wave because its single prereq (T2) finishes after Wave B starts.
- **Max parallelism = widest wave.** Here it is 5 (Wave B). Above that, you cannot speed up by adding more cooks.
- A wave is *not* a bundle. A wave is just a scheduling layer — what can run at the same time.

### 3.3 Example — Decide bundle boundaries (disjoint resources)

Same scenario, now we assign work. Constraint: **two cooks running in parallel must not fight over the same tool.** In software terms: two parallel subagents must not edit the same file.

| Bundle | Owns (tools / counter space) | Task | Wave |
|---|---|---|---|
| 01 — fetch    | pantry, fridge              | T1     | A |
| 02 — kettle   | kettle, burner #1            | T2, T3 | B → C |
| 03 — toast    | toaster                      | T4     | B |
| 04 — bacon    | skillet, burner #2           | T5     | B |
| 05 — fruit    | cutting board, paring knife  | T6     | B |
| 06 — eggs     | mixing bowl, pan, burner #3, spatula | T7 | B |
| 07 — plate    | plates, table                | T8     | D |

Decisions visible in the table:

- **T2 and T3 are bundled together (Bundle 02)** — they share the kettle. Splitting them would mean Bundle 02 hands the kettle to Bundle 03, which is fragile. Easier to keep the coffee chain in one bundle owned by one cook.
- **T4, T5, T6, T7 are separate bundles** even though they all live in Wave B. They use different tools, so they can run in parallel without collision.
- **`owns_tools` = `owns_files` in software.** The rule "no two bundles list the same tool" maps directly to "no two bundles list the same file in `owns_files`".

Translation back to software:

| Kitchen concept | Software concept |
|---|---|
| Tool (pan, board, toaster)        | File (`lib/x.sh`, `tests/x.test.js`) |
| Cook                              | Subagent |
| Kitchen                           | Git working copy / worktree |
| Two cooks fighting over one pan   | Two subagents editing the same file → merge conflict |
| Bundle = set of tools one cook owns | Bundle = set of files one subagent owns (`owns_files` YAML key) |
| Wave = "everything that can start now" | Same — scheduling layer |
| Recipe card per bundle            | One `NN-title.md` sub-plan per bundle |
| Head chef checking the board      | Main agent reading the status board |

End of example.

### 3.4 Promote isolated chains into sub-plan **skeleton** files

For each bundle, write one `<NN>-<title>.md` **skeleton** containing the orchestration scaffolding only — no TDD code yet. TDD steps are filled later by a separate `writing-plans` pass per bundle (see §10 Option B wiring).

Skeleton sections:

- YAML frontmatter (status, locks, prereqs, owns_files)
- Lock protocol (verify prereqs, flip status, commit)
- Inputs / Outputs (explicit)
- Definition of Done (D1..Dn — observable)
- Implementation steps — **empty placeholder** at split time; filled per bundle by `writing-plans` afterwards.
- Verify section header + intended check shape (test runner, taste-test, etc.)
- Validate section header + intended sanity checks
- Self-audit checklist
- Sign-off (flip YAML, commit SHA)

The split skill owns shape (boundaries, contracts, status). The follow-up `writing-plans` per bundle owns substance (code, tests, exact commands).

### 3.5 Keep main plan as orchestrator only

Main `README.md` carries:

- Goal + architecture recap.
- Dispatch protocol.
- Status board (checkbox list pointing at sub-plan files).
- Dependency waves diagram.
- **Cross-bundle contracts table** — every shared identifier (function name, array name, exit code), where defined, where used. Prevents drift across bundles.
- Main-plan self-audit (layering / security / consistency / scope / unverified-claim / failure-mode / reversibility / yagni).

Subagents do **not** read main README. They read only their sub-plan + (optionally) their prereqs' YAML.

## 4. Templates

### 4.1 Sub-plan YAML frontmatter (breakfast example)

```yaml
---
bundle: 02
title: kettle
status: AVAILABLE          # AVAILABLE | LOCKED | DONE | BLOCKED
locked_by: ""              # cook id / short identifier
locked_at: ""              # UTC ISO8601
done_at: ""                # set on completion
prereqs: [01]              # bundles that must be DONE first (01 = fetch ingredients)
owns_files:                # tools this bundle alone uses (real project = files)
  - kettle
  - burner-1
---
```

In a software project, swap `kettle` / `burner-1` for actual files like `lib/parser.js` / `tests/parser.test.js`. The YAML shape is identical.

### 4.2 Sub-plan body skeleton (breakfast example — Bundle 06 eggs)

```markdown
# Bundle 06 — Eggs

**Purpose:** Scramble 8 eggs to soft-set, ready for plating.

## Lock protocol
Verify prereq `01-fetch.md` has `status: DONE` (eggs must be on counter).
Flip this file's YAML → LOCKED, commit atomically, execute.

## Inputs
- 8 eggs in carton (from Bundle 01)
- Butter on counter (from Bundle 01)

## Outputs
- Pan of scrambled eggs, kept warm, ready for plating cook

## Definition of Done
- D1: 8 eggs cracked, no shell fragments in bowl.
- D2: Eggs whisked uniform yellow, no streaks of white visible.
- D3: Curds soft-set (still glossy, not dry), salt + pepper applied.
- D4: Pan held off heat after cook, covered, surface temp ≥ 60 °C at plating time.

## Implementation steps
### Step 1 — set up mise en place
Crack eggs into bowl. Inspect for shell. Whisk 30 seconds.
Expected: uniform colour, no white streaks.

### Step 2 — pre-heat pan
Burner #3 medium-low. 1 tbsp butter. Wait until butter foam subsides.
Expected: butter golden, not brown.

### Step 3 — cook
Pour eggs. Wait 20 s undisturbed. Then fold with spatula every 10 s.
Expected: curds form in 90–120 s total.

### Step 4 — finish
Pull pan when eggs look slightly underdone (residual heat finishes them).
Salt + pepper. Cover. Hold.

### Step 5 — sign-off
Mark this sub-plan DONE in YAML. Announce to plating cook (Bundle 07).

## Verify (correctness — taste / look / temp)
- Visual check: glossy soft curds, not dry.
- Probe thermometer in centre of pan: ≥ 60 °C.
- Taste one curd: seasoned, not bland, no raw-egg slime.

Expected: all three pass.

## Validate (sanity — no errors, no scope drift)
- Only owned tools used: mixing bowl, pan, burner #3, spatula. Toaster untouched, kettle untouched.
- No extra ingredients added (no cheese, no herbs — not in DoD).
- No other cook's pan touched.

Expected: clean inventory check.

## Self-audit (run BEFORE sign-off)
- [ ] Every DoD item D1..D4 met?
- [ ] Verify checks recorded (visual, temp, taste)?
- [ ] Validate inventory clean — only owned tools used?
- [ ] No scope drift (added ingredients, garnish, swapped technique)?
- [ ] Handoff to plating cook acknowledged?

## Sign-off
Edit YAML: `status: DONE`, `done_at: 07:42 UTC`.
Hand off: announce "eggs ready" to plating cook.
```

For software: swap "taste test" for `pytest tests/eggs.test.py`, "visual check" for `eslint`, "probe thermometer" for an integration test, "inventory check" for `git diff --stat`. Shape identical.

### 4.3 Main README skeleton (breakfast example)

```markdown
# Breakfast-for-4 — Main Plan

> Orchestrator file. Cooks do NOT read this. Head chef uses for dispatch + status only.

**Spec:** ../../specs/breakfast-menu.md
**Archived monolithic recipe:** ../_archive/breakfast-for-4-monolith.md

## Goal
Serve hot breakfast for 4 — coffee, toast, bacon, fruit, scrambled eggs — plated together within 25 minutes.

## Architecture (recap)
```
Kitchen
├── pantry / fridge        ← Bundle 01 owns
├── kettle + burner #1     ← Bundle 02 owns (water + coffee)
├── toaster                ← Bundle 03 owns
├── skillet + burner #2    ← Bundle 04 owns (bacon)
├── cutting board + knife  ← Bundle 05 owns (fruit)
├── bowl + pan + burner #3 ← Bundle 06 owns (eggs)
└── plates + table         ← Bundle 07 owns (plating)
```

## Dispatch protocol
Self-picking queue. Each cook reads status board → picks first AVAILABLE bundle with all prereqs DONE → locks (writes name on chalkboard) → executes → marks DONE.

## Bundle index (status board)
- [ ] 01 — fetch    → 01-fetch.md    — prereqs: none
- [ ] 02 — kettle   → 02-kettle.md   — prereqs: 01
- [ ] 03 — toast    → 03-toast.md    — prereqs: 01
- [ ] 04 — bacon    → 04-bacon.md    — prereqs: 01
- [ ] 05 — fruit    → 05-fruit.md    — prereqs: 01
- [ ] 06 — eggs     → 06-eggs.md     — prereqs: 01
- [ ] 07 — plate    → 07-plate.md    — prereqs: 02, 03, 04, 05, 06

## Dependency waves
```
Wave A: 01
Wave B: 02 03 04 05 06   [5 cooks parallel]
Wave C: 07
```

## Cross-bundle contracts
| Identifier | Defined in | Used by |
| salt level (1 tsp / 4 servings) | spec       | 04, 06 |
| serving temp ≥ 60 °C            | spec       | 02, 03, 04, 06, 07 |
| plate count = 4                 | 07         | 01 (portioning) |

## Definition-of-Done template
See per-bundle template in §4.2.

## Self-audit (this main plan)
- [ ] Every bundle owns disjoint tools?
- [ ] Every shared parameter listed in contracts?
- [ ] Waves diagram matches prereqs in each YAML?
- [ ] No bundle reaches across to another's tools?
- [ ] No silently dropped menu item?
- [ ] No item added outside the menu spec?
- [ ] Plating cook's prereq list complete (all five upstream bundles)?
- [ ] Timing realistic (25-min target achievable given longest chain)?
```

## 5. Lock & Dispatch Protocol

### Self-picking prompt (generic)

> Read the status board. Pick the first bundle where `status: AVAILABLE` AND all listed `prereqs` are `DONE`. Lock it (write your name on the chalkboard, atomically). Execute. Sign off.

### Lock flow — kitchen analogy

1. Cook walks up to chalkboard, reads target bundle's status row.
2. For each prereq, glance at that bundle's row — must read `DONE`.
3. Cook writes their name + timestamp next to the target bundle. Status flips `AVAILABLE → LOCKED`.
4. The act of writing is **one stroke of chalk** — atomic, no half-states.
5. If two cooks reach for chalk at once → kitchen rule: whoever finished writing first wins. The other erases and picks another bundle.
6. Cook executes the recipe in their sub-plan.
7. On success: cook flips status to `DONE` with completion timestamp.

### Lock flow — software (git as the chalkboard)

1. Read target sub-plan YAML.
2. For each `prereqs` entry, open that bundle's file and confirm `status: DONE`.
3. Edit target YAML: `status: LOCKED`, `locked_by: <id>`, `locked_at: <UTC>`.
4. `git add <subplan>.md && git commit -m "[<bundle>] lock"` — git commit = the atomic chalk stroke.
5. `git pull --rebase` to detect a racing lock. If another agent already pushed a lock for the same bundle, abort.
6. Execute implementation steps.
7. On success: edit YAML `status: DONE`, `done_at: <UTC>`. Commit.

### Race avoidance

- **Own kitchen per cook** = worktree per subagent (`superpowers:using-git-worktrees`). Each cook works on their own counter; nobody collides at the chalkboard.
- Lock change is intentionally tiny (a single name + timestamp; in git, a one-line YAML diff) → minimal contention.
- `owns_files` (= owned tools) disjointness means parallel cooks never reach for the same pan in the first place.

### Validate the board mechanically

Don't re-derive lock consistency by re-reading every bundle's prose each time — run
`~/.claude/skills/splitting-plans/scripts/validate-locks.sh <bundles-dir>`. It
checks, across all bundles at once: status is a valid value, `LOCKED` has non-empty
`locked_by`/`locked_at` and isn't stale (§11's 30-minute threshold), `AVAILABLE` never carries
lock fields, `LOCKED`/`DONE` bundles have every `prereqs` entry actually `DONE`, and `owns_files`
is disjoint across the whole board. It prints a JSON verdict (`pass`/`fail`/`noop`) plus a
`violations` list — run it before picking a bundle and again before sign-off.

## 6. Cross-bundle Contracts (drift control)

Maintain a table in the main README listing every shared parameter (a setting, a unit, a count, a convention) that more than one bundle depends on, with **defined-in bundle** and **used-by bundles**. Rule: a cook who changes one of these MUST update every dependent sub-plan in the same commit / chalkboard pass.

### Breakfast example

| Shared parameter | Defined in | Used by |
|---|---|---|
| salt level (1 tsp / 4 servings)              | spec        | 04 bacon, 06 eggs |
| serving temperature ≥ 60 °C                  | spec        | 02 kettle, 03 toast, 04 bacon, 06 eggs, 07 plate |
| plate count = 4                              | 07 plate    | 01 fetch (portioning) |
| coffee strength = 1 scoop per cup             | 02 kettle   | 01 fetch (grounds quantity) |
| egg doneness = soft-set, glossy              | 06 eggs     | 07 plate (don't overheat at plating) |

If Bundle 06 decides "actually we want hard-set eggs", it must also update 07's hold-temperature instruction in the same edit — otherwise plating cook over-cooks the already-finished eggs.

The contracts table is the **single source of truth** for inter-bundle interfaces. Once published, the defining bundle's parameter becomes a frozen contract for downstream bundles. (In software: a function signature, an array name, an exit code, a JSON schema — same idea, different vocabulary.)

## 7. Three-stage Quality Gate (Verify + Validate + Self-audit)

Three sequential gates per sub-plan, **all required** before sign-off:

| Gate | Purpose | Breakfast check | Software check |
|---|---|---|---|
| **Verify** | Prove DoD directly | Taste a curd, probe pan temperature, look at colour | Run unit tests, run integration tests |
| **Validate** | Catch silent regression / scope drift | Inventory: only owned tools used? no extra ingredients added? | `bash -n` / linter, `git diff --stat`, TODO grep |
| **Self-audit** | Final eye sweep against DoD checklist | Cook re-reads own DoD, mentally ticks each line before flipping `DONE` | Same — re-read DoD, tick each item, then status flip |

Rule: any gate failing → status stays `LOCKED` (or moves to `BLOCKED` with reason). Never flip to `DONE` on partial.

## 8. Outcome (breakfast example)

What a clean run of the breakfast plan looks like:

- Monolithic recipe: ~200 lines of running prose covering all 8 tasks tangled together.
- Split: 1 main README + 7 sub-plans (one per bundle).
- Largest sub-plan: 07-plate (depends on 5 upstream, needs the full timing diagram).
- Smallest: 01-fetch (just a shopping/inventory list).
- Average: ~40 lines per sub-plan — each fits on one printed recipe card.
- Wave B delivered 5 bundles cooked in parallel: 5 cooks, one kitchen, zero pan collisions because `owns_tools` is disjoint.
- Total wall-clock: time(01) + max(time(02..06)) + time(07). The 5 parallel bundles collapse to the slowest one (bacon at 8 min).

The numbers scale up the same way for software: split a 3000-line plan into N sub-plans, average ~200 lines each, deliver one whole wave per parallel batch.

## 9. Anti-patterns (illustrated with the kitchen)

- **Whole recipe pinned on the main board.** Cook 04 (bacon) ends up reading the entire breakfast recipe to find their bit → defeats the point. Keep the main board orchestration-only; full instructions live on each cook's individual recipe card.
- **Overlapping owned tools.** Bundles 04 and 06 both list "burner #2" → two cooks fight over the same hob. Re-split before service starts.
- **Hidden cross-bundle dependency.** Bundle 04 (bacon) silently relies on the kettle being free at 07:15 — but the kettle is Bundle 02's tool and 02's recipe card never mentions handing it over. Either add a row to the contracts table ("kettle free by 07:15"), or refactor so 04 doesn't need the kettle.
- **No Verify step.** "Eggs are done" with no observable check (taste / temp / look) = unverifiable DoD. Always name the check and its expected result.
- **Self-audit deferred until plating.** Plating cook discovers the bacon was undercooked. Too late. Each cook runs self-audit BEFORE flipping their own card to DONE.
- **Lock-flip mixed with cook step.** Cook 03 starts toasting bread before writing their name on the board. Cook 05 also reaches for the toaster, doesn't see the lock, collision. The lock must precede any work — and in software, that means lock = its own commit.
- **Shared kitchen, no boundaries.** All five Wave-B cooks try to work on the same counter. Give each their own counter (= worktree per subagent) so they never bump elbows or step on each other's status writes.

## 10. Skill name + wiring

**Proposed skill name:** `splitting-plans`

Sibling naming to existing `superpowers:writing-plans` and `superpowers:executing-plans`. Gerund + `plans` pattern. Lives in user-global scope (`~/.claude/skills/splitting-plans/SKILL.md`) without the `superpowers:` namespace, since it's a personal extension, not a fork of the plugin.

### Pipeline position — **Option B (recommended)**

`splitting-plans` runs **before** `writing-plans`. Plan content is authored **once**, never duplicated.

```
brainstorming
    ↓
splitting-plans         ← outline-only pass: task list, dep graph, bundles, waves,
    ↓                     contracts, sub-plan SKELETONS (YAML + section headers, no code yet)
writing-plans  × N      ← one invocation per bundle. Fills TDD code into one
    ↓                     sub-plan at a time. Scope bounded per call.
subagent-driven-development  /  dispatching-parallel-agents
    ↓
using-git-worktrees     (per subagent during execution)
```

### Why Option B (not "monolith first, split later")

- **No duplication.** Plan written exactly once — directly into sub-plan files.
- **No archive needed.** Monolith never exists. `_archive/` not required.
- **Bounded `writing-plans` scope per call.** Each invocation only owns one bundle's files → less context pressure, fewer hallucinations.
- **Cross-bundle picture established upfront** by `splitting-plans` (contracts table, owns_files, waves) → `writing-plans` calls inherit a stable interface to code against.
- **Matches user instinct** — "hook before creating plan".

### Decision rule (when to invoke `splitting-plans`)

> Invoke `splitting-plans` after `brainstorming` if **any** of:
> - projected plan > ~800 lines, OR
> - ≥ 2 independent subsystems / components identified during brainstorming, OR
> - user signals parallel intent ("split this", "parallelise", "runnable by subagents", "too big"), OR
> - ≥ 8 distinct tasks visible in the spec.
>
> Otherwise → straight to `writing-plans` (one plan file, no split).

### Triggers

- During brainstorming → "this is going to be big" signal.
- User says: "split", "parallelise", "too big to track", "fan out to subagents", "isolate tasks".
- Spec describes multiple independent subsystems.

### Required inputs

- Spec path (output of brainstorming).
- Confirmed scope: list of high-level tasks / subsystems.

### Procedure (skill body — Option B flow)

1. Read the spec.
2. Enumerate tasks at coarse grain (one task = one deliverable unit).
3. For each task, list its **inputs** (artifacts / data / functions it needs to exist) and **outputs** (files / artifacts it produces). No code yet.
4. Build the dependency graph from inputs ↔ outputs.
5. Group dependent chains into bundles whose owned resources (`owns_files`) are disjoint.
6. Compute waves (sets sharing prereq frontier).
7. Identify cross-bundle contracts (any name / value / convention used by ≥ 2 bundles).
8. Create folder `docs/superpowers/plans/<date>-<feature>/`.
9. Generate `README.md` (orchestrator: dispatch protocol, status board, waves diagram, contracts table, main-plan self-audit). See §4.3.
10. For each bundle, generate `<NN>-<title>.md` **skeleton** — YAML frontmatter + Lock + Inputs + Outputs + DoD + empty Implementation steps + Verify/Validate/Self-audit/Sign-off sections. No TDD code yet.
11. Run main-plan self-audit (8 checks from §3.5).
12. Hand off to `writing-plans` for **per-bundle TDD content filling** — invoke once per bundle, scope = that bundle only.

### Outputs

- New folder `<date>-<feature>/` with `README.md` + N skeleton sub-plans ready for `writing-plans` to fill.
- Status board ready for `subagent-driven-development` once filling is complete.
- No monolith. No archive.

### Skill dependencies / handoffs

- **Upstream:** `superpowers:brainstorming` (provides spec + scope).
- **Sibling reused during outline pass:** none required.
- **Immediately downstream:** `superpowers:writing-plans` (called N times, one per bundle, to fill TDD steps).
- **Then downstream:** `superpowers:subagent-driven-development`, `superpowers:dispatching-parallel-agents`, `superpowers:using-git-worktrees`, `superpowers:test-driven-development`, `superpowers:verification-before-completion`.

### Skill frontmatter draft

```yaml
---
name: splitting-plans
description: Use AFTER brainstorming and BEFORE writing-plans when the upcoming plan covers multiple independent subsystems, has ≥8 tasks, projects > ~800 lines, or the user signals parallel intent ("split this", "parallelise", "fan out to subagents", "too big to track"). Produces a folder containing a main orchestrator README plus N sub-plan SKELETONS (YAML frontmatter, lock protocol, dependency declarations, DoD sections — no TDD code yet). Each skeleton is then filled by a separate writing-plans invocation per bundle, so plan content is authored exactly once. Hands off to subagent-driven-development / dispatching-parallel-agents for execution.
---
```

## 11. Open Questions (worth surfacing in the skill too)

Phrased in kitchen terms; software equivalent in parentheses.

- **How many tasks per bundle?** Breakfast model: 1 dish per bundle. Software equivalent: usually 1 code file + 1 test file per bundle. Larger dishes (a multi-stage stew = a complex feature) might warrant more.
- **What if there is no chalkboard?** (No git / no shared status mechanism.) Need a substitute: a sticky note per bundle, or a tracker file the kitchen shares. Skill should not assume git.
- **Stale LOCKED — cook walked out mid-shift.** Bundle says LOCKED-BY-Alice-07:10 but Alice never came back. **Committed threshold: 30 minutes.** No status change since `locked_at` for 30 minutes → treat the lock as stale; another cook may take over after marking `BLOCKED` + reason (the same 30-minute no-new-heartbeat threshold the tribe's Shaman↔Warchief liveness check uses — not an example, the number). `~/.claude/skills/splitting-plans/scripts/validate-locks.sh` flags this mechanically as a `stale_lock` violation — don't eyeball `locked_at` by hand.
- **BLOCKED semantics — when does a cook set it vs. just abort?** Recommendation: set `BLOCKED` when the prereq says `DONE` but the actual handoff is missing/spoiled (eggs were marked done but pan is empty). Plain abort (drop the lock, take nothing) is fine for honest race losses.
- **How to rename a shared parameter without breaking dependent bundles?** Recommendation: a rename is its own bundle whose owned scope lists every recipe card that mentions the old name. One cook, one pass, all references updated together.

## 12. Provenance

The methodology in this document was extracted from a real brainstorm + execution cycle. Kept as a footer purely for traceability — not required reading for the skill.

- Source brainstorm session: `d833cb7a-7b7b-4dfc-8c4d-9ac71cfd02a8.jsonl`
- Parallel-execution session (5 bundles in 5 worktrees): `5fba1ee7-4c5c-4d85-a034-6ffa22dbd10d.jsonl`
- Handoff session: `f6d8f857-ff3a-4104-94d9-8afce6fb405c.jsonl`
- Real-world artifacts produced by that run live under `docs/superpowers/plans/` in this repo.
