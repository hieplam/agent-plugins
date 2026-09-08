# Repo-wide eval harness

Runs the `evals/evals.json` files that already sit next to some skills/plugins in this
repo and actually executes them — mirroring the official `skill-creator` eval loop
(`evals.json` → isolated-subagent run → `grading.json` with evidence → `benchmark.json`
with/without comparison) instead of inventing a new one.

## Why this exists

`refactor-for-testability/skills/refactor-for-testability/evals/evals.json` shipped with
3 well-formed cases and no runner. `run_evals.py` is that runner, and it applies to any
`plugins/**/evals/evals.json` in this shape — currently `check-diff-coverage`,
`splitting-plans`, `explaining`, and `refactor-for-testability`.

## evals.json shape

```json
{
  "skill_name": "example-skill",
  "kind": "skill",                    // "skill" (default, omit-able) | "agent" | "output-style"
  "memory_fixture": "memory-fixture/CLAUDE.md", // OPTIONAL, top-level — CLAUDE.md the --arm mem axis writes to scratch/
  "evals": [
    {
      "id": 1,
      "name": "short-slug",
      "agent": "hunter",              // only for kind: "agent" — which agents/<name>.md to test
      "style": "todd-way",            // only for kind: "output-style" — which output-styles/<name>.md to test
      "prompt": "the task given to the model",
      "expected_output": "prose description of correct behavior — the grading rubric",
      "files": [
        { "path": "a.md", "source": "plugins/explaining/README.md" } // OPTIONAL per-entry — a repo-relative file read verbatim, instead of an inlined "content"
      ],
      "checks": [                     // OPTIONAL — machine commands whose exit code decides pass/fail/ungraded before any LLM grader runs
        { "name": "html-mermaid-parses", "command": "bun {skill_dir}/scripts/validate-mermaid.ts --html-glob *.html" }
      ],
      "artifacts": ["*.html"]         // OPTIONAL — glob patterns preserved from the scratch dir as evidence before it is deleted
    }
  ]
}
```

`kind: "skill"` cases test a `SKILL.md`-based skill (the evals.json lives at
`<skill-dir>/evals/evals.json`); `kind: "agent"` cases test a plugin's agent
definitions per case (the evals.json lives at `plugins/<plugin>/evals/evals.json`, each
case names which `agents/<name>.md` it targets).

`kind: "output-style"` cases test a plugin's output styles. The evals.json lives at
`plugins/<plugin>/evals/evals.json` like an agent fixture, each case names which
`output-styles/<name>.md` it targets, and `{skill_dir}` in a check resolves to the
**plugin root** — so a check can reach sibling tooling as
`{skill_dir}/skills/<name>/scripts/…` without a second placeholder.

A check has three placeholders: `{skill_dir}` (above), `{scratch}` (the executor's cwd) and
`{reply}` — the executor's **final reply**, written to `<scratch>/.eval/reply.md` right before
the checks run. Without it a check can only judge the files the executor chose to leave
behind, never the text the user actually reads; with it a check can gate term discipline, a
required closing line, or an opening preamble deterministically before the LLM grader gets a
say. The dot-directory keeps it invisible to `artifacts` globs and to absence checks such as
`! ls *.md`. Each placeholder is substituted as one shell-quoted argv token, so use it as a
direct argument (`--reply {reply}`), not inside a nested `sh -c '…'` string, when the path
might carry a space.

An output style is *selected*, not made available: the file alone changes nothing. So
`install_output_style()` writes both halves of the real shape a user's selection
produces — the style at `.claude/output-styles/<file>.md` **and**
`{"outputStyle": "<name>"}` in `.claude/settings.json`, which the with_skill leg's
`--setting-sources project` is what loads. The style name is the file name unless the
frontmatter sets `name:`, matching Claude Code's own precedence. Verified empirically
that a project-scope style set this way does reach the system prompt under
`--setting-sources project --strict-mcp-config`.

The with/without comparison is unusually clean for this kind: a style modifies the
system prompt on **every** turn, so there is nothing to trigger and no way for the
executor to decline it, and the `without_skill` leg's `--safe-mode` disables project
settings entirely. Same model, same prompt, style or no style.

## What it does

For each case, up to two isolated `claude -p` subprocesses run (never the current
session — a genuinely clean context per skill-creator's own `run_eval.py` pattern):

- **with_skill** — the skill/agent under test is made available for that one process
  only: skill cases have `install_skill()` copy the ENTIRE real skill directory
  (`SKILL.md` + `references/`, `scripts/`, assets — not just the frontmatter
  `description:`) into the scratch cwd's project scope under
  `.claude/skills/<name>/`, exactly like `skill-creator`'s own eval loop installs the
  skill under test, so the skill can genuinely trigger (or fail to) via the normal
  Skill tool and exercise its real body, not dead-end at a one-line stub; agent cases
  pass the named agent's real frontmatter + body straight through `--agents`/`--agent`,
  independent of whether it happens to be symlink-installed locally.
- **without_skill** — a `claude -p --safe-mode` call: nothing registered *and* every
  user/project-scope customization (CLAUDE.md, skills, plugins, hooks, MCP servers,
  custom commands/agents) disabled. Plain `claude -p` alone isn't a clean baseline when
  the skill under test is also symlink-installed at user scope (the normal state when
  dogfooding this repo's own marketplace) — it would fire anyway and collapse the
  comparison. `--safe-mode` is what actually makes this the baseline.

Both configurations' token usage and wall-clock duration come straight from
`claude -p --output-format stream-json --verbose`'s own `result` event (`duration_ms`,
`usage`, `total_cost_usd`) — no separate instrumentation. `stream-json` (one JSON
object per stdout line), not plain `json`, is used deliberately: plain
`--output-format json` silently collapses to a single bare `result` object (no
assistant/tool-call events at all) whenever `--setting-sources` excludes `"user"` —
exactly what the `with_skill` leg's isolation flags do — which would otherwise starve
every `with_skill` grading verdict of transcript/tool-call evidence. A second, tool-less `claude -p` call
(the grader) scores the transcript against `expected_output` and writes `grading.json`
with evidence. Everything rolls up into one `benchmark.json` per invocation.

### UNGRADED: a harness failure is not an agent failure

Grading is itself a `claude -p` subprocess call, and that call can fail the same way any
subprocess can: it can time out, exit non-zero, or hand back a reply that isn't valid
JSON (including one cut off mid-object — a truncated reply). When that happens the
*harness* failed to produce a verdict — nothing about the agent's actual behavior was
judged. Scoring that as `passed: false` would silently corrupt every pass-rate read off
the suite, so `grade()` reports a third outcome instead: `{"ungraded": true, "evidence":
"<why>"}`.

An ungraded run is excluded from the pass/total denominator wherever results roll up:

- a case's own `result` dict reports `"total": 0` (not `1`) with `"ungraded": 1` when
  its only run was ungraded — never a fake `"passed": 0` read as a FAIL;
- its `grading.json` expectation entry carries `"ungraded": true` instead of
  `"passed": false`;
- the per-configuration roll-up in `benchmark.json`'s `run_summary` (`with_skill` /
  `without_skill`) computes `pass_rate` only over graded runs and reports the excluded
  count as `"ungraded"` alongside it;
- `compare.py` (below) treats an ungraded run as a **missing sample** — the same
  treatment as a case present on only one side of a comparison — never as a fail; folding
  it in as a fail could manufacture a `CONFIRMED` regression out of nothing but a grader
  hiccup.

This only reads skill/agent files and shells out to `claude -p` in a scratch temp
directory per case — it never edits a skill's or agent's runtime files.

### Arms: clean vs mem

The default arm is `clean`: the scratch cwd carries no `CLAUDE.md`, and the harness
asserts none is present rather than merely skipping the write. The `mem` arm writes the
fixture's declared top-level `memory_fixture` to `scratch/CLAUDE.md`, which
`--setting-sources project` loads for the `with_skill` leg. **The `mem` arm runs the
`with_skill` leg only** — the `without_skill` baseline is `claude -p --safe-mode`, and
`--safe-mode` disables `CLAUDE.md` entirely, so a "mem baseline" leg would silently be a
clean baseline wearing a mem label. A fixture that declares no `memory_fixture` gets an
honest skip note in `benchmark.json`'s `notes` (never a clean cell run and mislabelled
`mem`). The mem-vs-clean pass-rate delta is recorded as `run_summary.arm_delta`; it is
**reported, never gated** — a negative delta (ambient memory suppressing a behavior) is
a real finding to publish, not a failure to hide.

### Machine checks

A case's optional `checks` list runs after the executor and before any LLM grader call,
and its exit code decides the outcome directly: `0` is a pass (the LLM grader still
runs, scoring the transcript against `expected_output` as usual); `1` is a behavioral
FAIL owned by the agent, and the grader is skipped entirely (the machine already has the
verdict); any other exit code — a missing dependency, no network, a crash — is
UNGRADED, routed through the exact same harness-failure machinery as an ungraded LLM
grader call (excluded from the pass/total denominator, never scored as an agent
failure). A case's optional `artifacts` glob patterns are copied out of the scratch dir
before it is deleted, so a check's target file (or a human reviewer) has something to
inspect after the run. The per-run output path now carries an arm segment:
`<skill_name>/eval-<id>-<name>/<arm>/<configuration>/run-<N>/`.

## Usage

```bash
# One evals.json
scripts/evals/run_evals.py --evals plugins/splitting-plans/skills/splitting-plans/evals/evals.json

# Every evals.json in the repo
scripts/evals/run_evals.py --all

# Just prove the harness runs, cheaply, before committing to a full pass
scripts/evals/run_evals.py --all --mode with_skill --eval-id 1 --exec-model haiku --grader-model haiku

# See what would run without spending anything
scripts/evals/run_evals.py --all --dry-run

# Include the mem arm too (only fixtures with a memory_fixture actually run it)
scripts/evals/run_evals.py --all --arm both --dry-run
```

Key flags: `--mode {both,with_skill,without_skill}`, `--arm {clean,mem,both}` (default
`clean` — see Arms: clean vs mem above), `--runs N` (repeats per configuration, for
variance), `--timeout SECONDS` (per `claude -p` call), `--exec-model`
/ `--grader-model` (default: your configured model — pin to something cheap for a smoke
pass), `--eval-id 1,3` (restrict to specific case ids).

Run the harness's own unit tests (no `claude -p` calls, no cost) with:

```bash
python3 -m unittest discover -s scripts/evals/tests -t .
```

**Executor model resolution (`kind: "agent"` cases only).** An explicit `--exec-model`
used to be the *only* way to pick the executor's model, so every agent-kind case ran on
whatever one model the caller chose — even though production dispatches each agent on
the model its own frontmatter names (e.g. an agent that declares
`model: opus`). Benchmarking that agent on `sonnet` measures a model production never
runs it on, so any regression conclusion drawn from that run would not transfer.
Precedence, highest first:

1. **`--exec-model`**, if given — overrides everything (still the way to run a cheap
   smoke pass on `haiku` without touching any agent's frontmatter).
2. **The subject agent's frontmatter `model:` value** — a concrete value (`opus`,
   `sonnet`, `haiku`) is passed to the executor as `--model`.
3. **`inherit`** (Claude Code's own frontmatter convention for "use the caller's model"),
   or a missing `model:` key — resolves to the harness default (no `--model` flag passed
   at all), same as giving no model today.

The model actually used is recorded per run in `result.model` in `benchmark.json`, so a
benchmark is self-describing about which model each case ran on. `--grader-model` is
unaffected by any of this — the grader's model is always either `--grader-model` or,
failing that, `--exec-model`/default, exactly as before.

Output lands in `scripts/evals/runs/<UTC-timestamp>/` by default (git-ignored —
reproducible from `evals.json` + this script, not checked in), or under `--out-dir PATH`
(any path, including outside the repo, e.g. a CI artifacts dir): per-case, per-run
`transcript.md` / `metrics.json` / `grading.json` under
`<skill_name>/eval-<id>-<name>/<arm>/<configuration>/run-<N>/` (1-based `run-<N>`,
always present even for the default `--runs 1`, so repeats and arms never overwrite
each other's evidence), plus one rolled-up `benchmark.json` at the run root.

## Cost note

Each case with `--mode both` spends 4 `claude -p` calls (2 executors + 2 graders), and
executor calls have full tool access — they really do the task, so a slow/tool-heavy
case can run minutes and real tokens. Pin `--exec-model`/`--grader-model` to a cheap
model and use `--eval-id` to scope a smoke pass before running the full suite.

---

## Prompt-tuning mode: A/B one prompt against itself

The with/without benchmark above answers *"does this agent beat vanilla Claude?"*. Editing an
agent's prompt asks a different question — *"did my edit change how it behaves?"* — and that
needs the **same cases run against two versions of the same prompt**. Three pieces do that.

### 1. `prompt_size.py` — the objective metric

What you are trying to move. Bytes and lines are exact; the token column is an explicitly
labelled estimate (no local tokenizer, no network call), which is fine because trimming steers
on *deltas and rank order*, not absolute token counts.

```bash
scripts/evals/prompt_size.py                        # this repo's agents
scripts/evals/prompt_size.py --baseline plugins/research-to-blog/agents --candidate /tmp/cand/agents
```

### 2. `--agents-dir` — the A/B axis

`run_evals.py` used to read agents only from `<plugin>/agents`. Point it at a candidate copy to
run the identical cases against edited prompts:

```bash
cp -r plugins/research-to-blog/agents /tmp/cand-agents
# ... trim /tmp/cand-agents/research-to-blog.md ...

scripts/evals/run_evals.py --evals plugins/<plugin>/evals/evals.json \
  --mode with_skill --runs 3 --jobs 5 --exec-model sonnet \
  --agents-dir /tmp/cand-agents --label trimmed \
  --out-dir scripts/evals/runs/trimmed
```

Each `benchmark.json` records a **sha256 + char count per subject prompt**, so a comparison can
prove the two runs really measured different text rather than trusting the `--label`.

### 3. `compare.py` — the regression tripwire

```bash
scripts/evals/compare.py \
  --baseline scripts/evals/baselines/<name>/benchmark.json \
  --candidate scripts/evals/runs/trimmed/benchmark.json
```

It reports prompt-size delta and pass rate **per agent** (a suite-wide average would bury one
agent's regression under twenty unrelated passes), and splits losses into two buckets that must
not be conflated:

| Bucket | Meaning | Exit |
|---|---|---|
| `CONFIRMED` | passed **every** baseline run, failed **every** candidate run | **1** |
| `UNSTABLE` | flipped, but inconsistently across runs | 0, always printed |

At `--runs 1` every flip is `UNSTABLE` by construction — one sample cannot separate a regression
from model variance. Use `--runs 3` before trusting a trim.

An `ungraded` run (see UNGRADED above) is never one of these two runs of `[true, false, ...]`
per case — `compare.py` drops it before pairing baseline against candidate, the same as a case
present on only one side of the comparison. Treating it as a fail instead could manufacture a
`CONFIRMED` regression (or hide a real one inside `UNSTABLE`) out of nothing but a grader
subprocess that happened to time out.

### Baselines are checked in

`runs/` is git-ignored (regenerable), but a reference `benchmark.json` committed under
`scripts/evals/baselines/<name>/` is what a trim compares against — a trim needs a stable
"before", and re-measuring the baseline every time would let both sides of the comparison
drift at once.

This repo ships no baseline yet. Record one before the first trim: run the suite with
`--runs 3` on production models and commit its `benchmark.json` under
`scripts/evals/baselines/<date>-<label>/` with a README stating the models, run count and
known-invalid cases.

## Two harness bugs this mode required fixing

Both produced **false FAILs** — harness artifacts scored as agent defects. Any regression signal
read off the suite before these fixes was unreliable.

1. **`case["files"]` was parsed but never written to disk.** A case whose prompt named a file ran
   against an empty directory; the agent correctly reported `BLOCKED`, and the grader scored that
   FAIL against a rubric describing behavior on a real file. `materialize_files()` now writes
   fixtures (with a path-traversal guard) before the executor starts.
2. **The executor ran under the default permission mode.** Under `-p` there is no way to answer an
   approval prompt, so **every write was denied** — one observed executor even tried `dd` to get
   around it before giving up. `--permission-mode` (default `bypassPermissions`) fixes it.

> **Security note on the default.** `bypassPermissions` gives each executor unattended tool access.
> Every executor runs in a fresh `mkdtemp` scratch dir that the harness deletes afterwards, with
> user-scope settings and MCP servers already excluded — so the blast radius is that throwaway
> directory. If that trade is wrong for your machine, pass `--permission-mode default`, and expect
> every fixture-carrying case to fail spuriously.

Verified end-to-end: eval 2 went **FAIL -> PASS** on identical prompt text once both were fixed.

## Speed

`--jobs N` (default 4) runs executions concurrently — they are independent subprocesses in
separate temp dirs. Serially a 30-case suite is over an hour, which is slow enough that a gate
stops getting run.
