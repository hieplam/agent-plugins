# 🪨 Tribe

> An agent *tribe* for the software development lifecycle. Each agent is a role in a prehistoric hunting tribe — every role answers **exactly one question** and **never steps on another's toes**.

`tribe` is a plugin that bundles 6 agents into a 3-tier hierarchy plus 2 review gates and 1 analysis specialist. The "hunting tribe" metaphor isn't just for fun — it **encodes function into the name**, so that when you read the code/config you can immediately guess what each agent does, when it runs, and how far its authority reaches.

---

## Tribe overview

| Agent | Tier | Question it answers |
|---|---|---|
| 🔮 **Shaman** | Super Lead | *What? / Why?* — what to do, and why |
| 🪓 **Warchief** | Leader | *How?* — how to do it, split work, review, merge |
| 🏹 **Hunter** | Worker | (execution) — turn specs into real artifacts |
| 👣 **Tracker** | Review gate (during dev) | *Does this diff follow our written rules?* |
| 🔪 **Skinner** | Review gate (before "done") | *Is the work actually done?* |
| 🧭 **Scout** | Analysis specialist (before the hunt) | *Where will this code break next?* |

**Basic flow:**

```
Shaman  ──(vision: what/why)──▶  Warchief
                                    │
                     (spec + how, dispatch)
                                    ▼
                                 Hunter ──(implement, commit)──▶ diff
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼ (throughout dev, many times)               ▼ (once, at the end)
           Tracker                                      Skinner
   "does this diff follow the rules?"          "is the work ACTUALLY done?"
        advisory                                 findings (Warchief adjudicates)
              └─────────────────────┬─────────────────────┘
                                    ▼
                          Warchief adjudicates findings, opens the PR, swings the hammer ──▶ merge
                                    │
                          (reports back up) ──▶ Shaman
```

---

## Each agent in detail

### 🔮 Shaman — *Super Lead*

**Answers:** *What?* and *Why?*

**What it actually does:** Generates ideas, sets the direction and the meaning for the whole tribe. The Shaman does **not** pick up a weapon and hunt (no writing code, no detailed tactics). It sees far, interprets "why this is worth doing" and "what needs to be achieved," then hands it off to the Warchief. When a PR is merged, the Warchief reports back up to the Shaman to close the vision → outcome loop. Each approved unit is delegated to the tribe's closed loop (Warchief → Hunter → dual-Skinner audit → PR + merge) as ONE deterministic unit — the Shaman launches it, waits, and verifies SHIPPED; it never reviews diffs, commits, or merges inside the loop.

**Why the name Shaman:** In a tribe, the Shaman (medicine man / seer) is the one who "reads the omens" — interpreting the *meaning* (why) and pointing out the *direction* (what) for the whole tribe, without personally going on the hunt. This fits the role of generating ideas + vision, without touching the *how*.

---

### 🪓 Warchief — *Leader*

**Answers:** *How?*

**What it actually does:** Receives **one idea card at a time** from the Shaman (the Shaman picks — the Warchief never chooses what to build), brainstorms, writes the spec/plan, then **dispatches** work to the Hunter. When the Hunter reports "done," the Warchief orchestrates the audit/review, opens the PR, and is the **only one with the authority to swing the hammer and merge**. At every audit round, alongside the mechanical pre-gate, it also dispatches the **Tracker** against the same range: a `BLOCK` verdict is a red gate — same class as a red pre-gate — routed back to a fixer Hunter with no Skinner dispatched, while the final whole-branch audit's `### Harness gaps` section carries forward, verbatim, as the input to harness-gap reconciliation below. Before every PR it also runs the **debt burn-down gate** (`debt-count.ts --diff`) — a positive delta blocks the PR outright and routes the new hits back to a Hunter — then runs **`debt-backfill.ts`** to open follow-up issues for the blacklist's still-open entries, then closes whatever the snapshot flags `closable`. The burn-down gate, the backfill, and the closable-closing all run **unconditionally on every PR**, whether or not any harness gap was reconciled. After merging, the Warchief reports the result up to the Shaman.

**Why the name Warchief:** The war chief is the one who takes the Shaman's word and turns it into concrete tactics — splitting the party, overseeing the battle, and being the only one to declare "victory" (merge). This fits the role of turning *what/why* into *how* and holding the final decision authority.

---

### 🏹 Hunter — *Worker*

**Answers:** (doesn't answer a strategic question — it **does**)

**What it actually does:** The one who directly hunts — executes the real work per the Warchief's spec under strict TDD, produces the artifact (the kill), commits it, and reports back to the Warchief (who opens the PR). The Hunter makes no strategic decisions; its job is to **bring back the kill exactly as ordered**.

**Why the name Hunter:** Hunting is the tribe's most primal act of "producing a result." The artifact here is **the kill** — a natural metaphor for building/coding a real PR.

---

### 👣 Tracker — *code-reviewer (during development)*

**Answers:** *Does this diff follow our written rules?*

**What it actually does:** The **cheap, frequently-run** gate — meant to run before every commit/PR while developing. The Tracker re-reads **every rule source fresh** (global rules, `CLAUDE.md`, formatter/linter config, C3…), inspects the diff, and attaches a **concrete fix** to each finding. Its verdict is **advisory**: `BLOCK` / `APPROVE-WITH-COMMENTS` / `APPROVE`. While walking the diff it also surfaces **harness gaps** — a diff-anchored, risk-scoped pattern (error handling, concurrency, cleanup, input validation, test presence) that no written rule covers yet — as a separate, read-only, never-judged report section; it is a fact about the rule set, not a violation. It also **grandfathers blacklisted legacy**: reads the open debt entities (the tech-debt blacklist), and a diff occurrence already inside a debt entity's recorded scope gets exactly one non-blocking `tracked in <debt-id>` note instead of a Blocker — a genuinely new occurrence of the same anti-rule still blocks normally.

**Ownership:** The Tracker is the **single source of truth** for *rule/style conformance*. It inspects the **process / the path** — whether the diff is following the trail (the rules).

**Why the name Tracker:** A tracker reads tracks **all along the hunt**, continuously checking whether the party is still on the right trail. Its nature — *walking alongside, checking many times, advisory in tone* ("you've drifted left, correct it") — matches exactly the advisory + recurring role of a code-reviewer during dev.

---

### 🔪 Skinner — *adversarial-reviewer (before "done" / before merge)*

**Answers:** *Is the work that claims to be done actually done?*

**What it actually does:** The **heavy, run-once-at-the-end** gate — before declaring "done" or before merging. The Skinner reconstructs the **requirement contract** (from spec/plan → Jira ticket via `ask-copilot` → PR description), then **RUNS real proof** to verify the implementation against that contract, and **self-refutes its own findings** before reporting them. No Skinner lens holds a verdict: the contract lens reports findings ending `CONTRACT-LENS: N findings`, and its cold-diff partner reports hypotheses ending `COLD-LENS: N hypotheses`. **The Warchief holds the adjudication**, at the **disposition** level: every Critical/Important finding gets exactly one recorded disposition — `CONFIRMED` (routes to a fixer Hunter), `REFUTED` (only with evidence: a `file:line`, command output, or for a `[contract-only]` finding a verbatim contract citation), or `DEBT` (a recorded follow-up, forbidden for Critical findings). Even `CONFIRMED` is a **routing act, not proof** — an individual **finding** is a *falsifiable hypothesis* until the fixer Hunter **reproduces it**, and reports `NOT_REPRODUCED` with evidence when it cannot (see the Hunter's "Fixer mode"). The Warchief's adjudication is the referee; a finding is the claim it referees.

**Ownership:** The Skinner is the **single source of truth** for *done-ness*, and enforces only the done-gating governance. It inspects the **product / the kill** — whether the work is *actually* finished and correct.

**Why the name Skinner:** The one who skins/guts the kill has to **cut it open to actually know** whether the meat is good, whether it's the right animal — matching "you have to RUN the proof to know, not eyeball it." And the result is undeniable: bad meat is bad meat — the findings are undeniable because they are **RUN**, not argued. What happens next — fix it, refute it, or carry it as debt — is the Warchief's call, not the Skinner's.

---

### 🧭 Scout — *code-analyzer (before the hunt / on demand)*

**Answers:** *Where will this code break next, and what shape is inviting it?*

**What it actually does:** Surveys **existing, working code** — no diff required — for the structural and readability problems that invite future bugs. Its method encodes hard-won review lessons: read the recorded decisions (ADRs/C3/docs) *before* the code; sweep at **three altitudes** (line, component structure, design-vs-framework); diff reality against the **simplest from-scratch implementation**; hunt **dead states** (code servicing a state that can never occur) and **hand-rolled framework primitives**; distrust sibling conventions (a pattern repeated N times is still a choice); never satisfice on a requested finding count. Prefers fixes that **delete** code over fixes that add abstraction, and distills every finding into a **rule candidate** — so Scout's findings become Tracker's checklist tomorrow. Read-only for analysis; it also **adjudicates open harness gaps** when the Warchief dispatches it with them — proposing `rule` / `anti-rule` / `debt` / `dismissed` dispositions and, once ratified, authoring them through governance-artifact CLIs only. It never edits, stages, or commits source code, and never hand-writes a registry line or debt file.

**Ownership:** The Scout is the **single source of truth** for *pre-emptive design/structure analysis of working code*. It inspects the **terrain** — where the ground will give way — before anyone commits to a path across it.

**Why the name Scout:** The scout ranges ahead of the hunting party, reads the terrain, and reports hazards **without touching anything** — the party decides the route. That matches an analyzer that finds defect-inviting structure in code that "works fine today" and hands the map back, changing nothing itself.

**Boundary with Tracker/Skinner:** Tracker checks a *diff* against rules already written; Skinner checks *claimed-done work* against its requirement contract. Scout analyzes *standing code* for what no rule covers yet — and feeds new rule candidates back to the rules files Tracker enforces.

---

## The critical boundary: Tracker ≠ Skinner

These two review agents must **never have their roles merged**. The orchestrator (Warchief) calls **both**, but at two different times for two different questions:

| | 👣 **Tracker** | 🔪 **Skinner** |
|---|---|---|
| Question | Does the diff follow the **rules**? | Is the work **actually done**? |
| Inspects | The **process** (path / diff vs rules) | The **product** (kill / done-ness) |
| When it runs | **During dev**, before each commit/PR | **Once, at the end**, before merge |
| Frequency | Often (cheap recurring gate) | Once (expensive final gate) |
| How it works | Reads rules + inspects diff | **RUNS proof** + self-refutes |
| Verdict | **Advisory** (BLOCK / APPROVE-W-COMMENTS / APPROVE) | **Findings** — no lens rules; the Warchief adjudicates (CONFIRMED / REFUTED / DEBT) |
| Weight | Advisory — can have comments and still proceed | Evidence-backed — a CONFIRMED finding **must be fixed and verified, never silently dropped** |

> A normal change should **pass through the Tracker many times during dev**, then **pass through the Skinner exactly once at the end** before the word "done" is spoken.

This split is **encoded right into the metaphor**: *a tracker naturally walks the whole trail* (recurring), while *a skinner naturally works only once after the hunt is over* (one-time, final). You don't need to read the docs to guess which one runs often and which one runs at the gate. Both gates are **advisory / findings-based** in the same sense — neither rules on its own; what differs is **scope**: the Tracker checks the diff against the rules, the Skinner checks the implementation against the full requirement contract, by running proof.

**Boundary with the Warchief:** The Skinner never rules on whether the kill is good or bad — it reports evidence-backed findings; it does **not** swing the merge hammer — merging is always the Warchief's authority. **The Warchief holds the adjudication** (CONFIRMED / REFUTED / DEBT) and acts on it, and the two don't overlap.

---

## Machine-global rules — `rules/`

The tribe ships **standing standards as files**, symlinked into `~/.claude/rules/`, where
Claude Code loads them by their frontmatter contract:

| Rule | `paths:` glob | Loaded |
|---|---|---|
| [`rules/pure-core.md`](rules/pure-core.md) | none | **Every turn** — it governs all production source, so it is never scoped away |
| [`rules/html-illustration.md`](rules/html-illustration.md) | `**/*.html`, `**/*.htm` | Only when an HTML file is in play — zero cost on every other turn |

A rule with **no** `paths:` glob applies generally; a rule **with** one applies only when
its glob matches a file in play. That is the whole progressive-disclosure mechanism: a
standard that must always hold pays the context cost always, and a standard that only
governs one file type pays nothing until that file type appears.

### `pure-core.md` — the design golden standard

The tribe carries **one cross-stack design philosophy** into every codebase it works in:
core logic (calculation, decisions, flow control) stays **pure** — deterministic,
side-effect-free — and every outside-world dependency (database, network, filesystem,
clock, random) enters only through an abstraction injected from the edge. The canonical
text, with the golden pattern and the reviewer severity guide, is
[`rules/pure-core.md`](rules/pure-core.md).

### `html-illustration.md` — the visual output house style

Whenever HTML is authored **because a wall of text cannot carry the point** — an Artifact,
a standalone `.html` written to disk, a rendered mermaid illustration, a report or
dashboard — this rule fixes the structure so it is not re-decided ad hoc each time:
container width, root type scale, reading measure, panel caps, the mermaid SVG scaling
override, theming across both delivery modes, and the mobile reset, all calibrated for a
2560×1440 desktop. It deliberately does **not** fix palette or typeface — those stay
derived per subject, with a latte + green lean when nothing else pulls.

The glob is only the outer filter. The rule opens with a **semantic gate** — apply it when
the page *is* the explanation a human reads, skip it for production application markup,
templates, and test fixtures, which follow the repo's own design system. That second layer
needs model judgment, which is why it lives in the rule body rather than in the glob.

### How each role picks the rules up

Delivery is deliberately **file-based, not prompt-based**, so it works in any repo on the
machine regardless of tech stack:

| Who | When it engages the standard | How it gets it |
|---|---|---|
| 🪓 Warchief | Writing the spec (names the pure core + seams) and the plan (verbatim Purity line in Global Constraints) | Reads `~/.claude/rules/` at intake (Method step 1) |
| 🏹 Hunter | Building every task | Inherits the plan's Global Constraints Purity line in its brief |
| 👣 Tracker | Every review | Already reads every `~/.claude/rules/*.md` fresh — zero prompt change needed |
| 🔪 Skinner | The final done-ness audit | Loads `~/.claude/rules/*.md` in its governance step |

The plugin's `install.sh` hook symlinks `rules/*.md` into `~/.claude/rules/` (idempotent,
backs up conflicting files), so the repo stays the single source of truth
(test: `scripts/tests/test-install-rules.sh`).

---

## Skills — named entry points

The agents above define *roles*; two skills define *invocations* — phrases the owner can
say in any session and get a deterministic dispatch, because a skill's description is
always in the model's context (an eval-backed property: a bare codename with no binding
routes to nothing, see `skills/mammoth-hunt/evals/`):

| Skill | Invocation | What it runs |
|---|---|---|
| [`mammoth-hunt`](skills/mammoth-hunt/SKILL.md) | "Run the Mammoth Hunt", "Tribe workflow", "full tribe", a tribe role assignment | The full chain on **one** piece of work: warchief orchestrates, hunters implement (TDD), **2 skinners** audit, **scout** surveys unwritten conventions, **tracker** gates the diff — the scout/tracker legs ride as standing constraints in the warchief brief, since the warchief's own definition dispatches scout only conditionally |
| [`orchestrate-campaign`](skills/orchestrate-campaign/SKILL.md) | "orchestration", "run these N cards" | A **batch** of roadmap cards unattended via the campaign runner, one consolidated report |

---

## Campaign runner

Alongside the six agents, the plugin ships a **stateless capability script** —
[`scripts/runner/`](scripts/runner/README.md) — that drives a roadmap campaign's outer loop
deterministically: pick the next staged card, run one fresh executor session, script-verify
it shipped, record state, repeat. It hardcodes no repo/model/campaign value (every
environment-specific value is a CLI input) and costs zero LLM tokens itself — only the
sessions it spawns do.

```sh
bun plugins/tribe/scripts/runner/run.ts --repo <target-repo> --model <model> \
  --home <campaign-home> --dry-run
```

See [`scripts/runner/README.md`](scripts/runner/README.md) for the full inputs table, resume
semantics, escalation workflow, and known limitations. `--repo`, `--model`, and `--home` are the
only required flags — every campaign operational artifact (state, answers, escalations, reports,
lock, STOP) resolves to a fixed name under `--home` and is never committed to the target repo.
Every real (non-`--dry-run`) invocation records a `run.json` under `--home` too (see that
README's "Run record" section) — this is what the status viewer below reads.

---

## Campaign watchdog

A **zero-token supervisor script** (card i74, issue #74) — not an LLM session — that launches
or adopts a runner pass, waits out an account-limit death until the log's own reset time, backs
off an upstream overload, relaunches a crash once, and exits **only** when a human must act. The
harness's own "background command exited" notification IS the heartbeat, so
`orchestrate-campaign`'s Stage B launches it **detached** (double-forked, so a harness tool
timeout never reaches it):

```sh
( nohup bun "$runner_dir/run.ts" watchdog \
    --repo <target-repo> \
    --model <model> \
    --home "$(plugins/tribe/scripts/tribe-home.sh <target-repo>)/campaigns/<campaign-slug>" \
    </dev/null >"$(plugins/tribe/scripts/tribe-home.sh <target-repo>)/campaigns/<campaign-slug>/watchdog/launch.log" 2>&1 & )
```

It writes `status.json` (rewritten atomically), `events.jsonl` (append-only), and
`runner-stdout/attempt-<N>.log`, all under `<home>/watchdog/`, and never anything else in the
campaign home (never `campaign-state.json`, `answers.md`, an escalation file):

| Subcommand | Own flags (defaults) | Exit codes |
| --- | --- | --- |
| `run.ts watchdog` | `--follow` (on) \| `--once`, `--stall-minutes` (30), `--max-quota-waits` (6), `--max-overload-backoffs` (5), `--max-crash-relaunches` (1), `--poll-seconds` (30), `--quota-grace-seconds` (30), `--fallback-model <tier>` (off) | `0` done · `1` usage error · `10` needs_human (reason in `status.json`) · `11` running (`--once` only) |

See
[`scripts/runner/README.md`](scripts/runner/README.md#watchdog-card-i74-issue-74) for the full
flag table, exit codes and the frozen action table — this section is only a pointer.

---

## Status viewer

[`scripts/viewer/`](scripts/viewer/) is a read-only local web page — a sibling capability to the
runner, same stack (bun + TS) — that scans `~/.tribe` and answers at a glance: which campaigns
exist, is each runner's process actually alive right now, per-card status, pending escalations,
and the current session's log tail. It never writes anything (no lock, no state, no `gh`/`git`);
it binds `127.0.0.1` only. It has two surfaces:

- **Status page** (`GET /`) — refresh-based, zero client JS: every "refresh" in a browser is
  simply a fresh scan.
- **Live view** (`GET /live`) — while a campaign's session is running, tails the executor's and
  every subagent's transcript over Server-Sent Events, rendered by a small browser client
  (`client/app.js`). The campaign runner starts this surface automatically alongside a real run
  and prints its URL — see [`scripts/runner/README.md`](scripts/runner/README.md#live-viewer).

Start the server by hand with:

```sh
bun plugins/tribe/scripts/viewer/serve.ts [--tribe-root <dir>] [--port <n>]
```

`--tribe-root` defaults to `$HOME/.tribe`; `--port` defaults to `4321`. Then open
`http://127.0.0.1:<port>` (or `curl` it). See [`scripts/viewer/README.md`](scripts/viewer/README.md)
for the full route contract.

## Migrating pre-existing worker reports

Before this effort, campaign worker reports were written to the target repo's own
`.claude/state/<campaign>/reports/` — the exact machine-local exhaust the tribe's own convention
says should never live in a git working tree. [`scripts/migrate-campaign-home.sh`](scripts/migrate-campaign-home.sh)
moves any such pre-existing reports into the new `~/.tribe/<repo-key>/campaigns/<campaign>/reports/`
home (via `tribe-home.sh`, never its own key derivation), refusing to overwrite an existing
destination file and refusing to touch a campaign whose `.runner.lock` is held by a live pid:

```sh
bash plugins/tribe/scripts/migrate-campaign-home.sh <target-repo> [--campaign <slug>] [--dry-run]
```

Old session logs (caller-chosen via `--logs-dir`) are **not** migrated — there is no
deterministic source path to scan; the script prints a reminder listing every campaign it
touched so they can be moved by hand if wanted.

---

## Quick reference

| Agent | Technical role | Question | Authority |
|---|---|---|---|
| 🔮 Shaman | Super Lead / ideation | What? / Why? | Sets direction, receives final report |
| 🪓 Warchief | Leader / orchestrator | How? | Spec, dispatch, open PR, **merge** |
| 🏹 Hunter | Worker / implementer | (execution) | Code, commit, report |
| 👣 Tracker | code-reviewer | Does the diff follow rules? | Advisory (BLOCK/APPROVE) |
| 🔪 Skinner | adversarial-reviewer | Is the work actually done? | Evidence-backed **findings** — Warchief adjudicates (CONFIRMED/REFUTED/DEBT) |
| 🧭 Scout | code-analyzer | Where will this code break next? | Advisory findings + rule candidates; read-only |

---

## Local operational home

The tribe stores its per-campaign **operational state** in a machine-local directory keyed to the
repo — never committed:

```
~/.tribe/<repo-key>/
├── state/      # in-flight per-card resume records (written at intake, read by resume-check.sh)
│   └── <card-slug>.md
├── archive/    # shipped cards, moved here on VERIFY_SHIPPED (by archive-card.sh)
│   └── <card-slug>.md
└── reports/    # Warchief heartbeat/report files
    └── <card-slug>.md
```

`<repo-key>` = the canonical main-worktree path with `/` → `-` (e.g.
`-Users-home-repos-todd-skills`). All linked worktrees of one repo share the same key and
therefore the same home. Derive it via `bash plugins/tribe/scripts/tribe-home.sh [repo-dir]`.

**What lives here vs. in `docs/tribe/`:**
- `~/.tribe/<key>/` — operational runtime data (resume state, reports). Machine-local, never
  committed to git.
- `docs/tribe/` — durable contracts (specs, plans, Decision Log, ideas, `ROADMAP.md`). Always
  committed, always in source control.

To migrate an existing repo's committed `docs/tribe/state/*.md` to the home:

```sh
bash plugins/tribe/scripts/migrate-state.sh [repo-dir]
# then commit the resulting .gitignore change
```

---

*Plugin: `tribe` — Shaman · Warchief · Hunter · Tracker · Skinner · Scout*
