# Explaining

A skill that makes explanatory prose (design docs, PR descriptions, teaching-style
answers, architecture write-ups) readable by someone without the writer's context, and
turns a multi-actor or conditional flow into a real, renderable diagram instead of
narration nobody can follow.

## The five rules (`skills/explaining/SKILL.md`)

1. **Term discipline: define before use.** Any new concept, technology, or technical
   term must be briefly defined or contextualized the first time it appears — never
   dropped mid-explanation with no lead-in.
2. **Grounding: anchor every abstract claim.** Every abstract or general statement is
   paired with a code snippet, a concrete worked example, or a verifiable fact/source;
   an ungroundable claim is marked unverified or deleted.
3. **Name a concept instead of the behaviour.** A vague label ("best-effort") tells a
   reader nothing checkable; name the actual behavior instead.
4. **Illustrate a flow instead of narrating it.** A flow with multiple actors or
   conditional paths gets a mermaid diagram, rendered into one self-contained HTML file
   written to disk — a fenced code block alone is not the deliverable, since it renders
   in some clients and not others.
5. **Blind-reader review before delivery.** Before a file-on-disk deliverable or an
   explanation of 600 words or more is handed over, the draft goes to disk and one fresh
   subagent reads it with no other context — its brief carries the path, the audience and the
   language and nothing else. It reports what it could not follow as `BLOCK` or `NIT`; the
   author fixes every `BLOCK` and re-dispatches a new reader, at most three rounds, logging
   every round next to the draft, and the answer always says how the review ended. Each round's
   log record is opened before the reader is dispatched and completed when it returns; the
   review degrades to the self-check only after an attempted dispatch actually fails, which is
   recorded as round 0 in the log.

Rules 1 and 2 are the pair that won an isolated A/B eval against baseline and against
each rule alone (see `SKILL.md`'s Evidence section for the numbers). Rule 4 is enforced
by the two rendering scripts below plus a machine check in this skill's own eval case. Rule
5 is enforced by `check-review-log.ts`, also below.

## The three scripts (`skills/explaining/scripts/`)

- **`validate-mermaid.ts`** — validates mermaid diagram source against the real
  `mermaid.parse()` parser (via a `jsdom` shim), not by LLM opinion. Exits `0` when
  every diagram in the target HTML parses, `1` when at least one does not (with a
  printed remediation hint mapped from the real mermaid error string) or when no
  diagram artifact is found at all, and `2` when the parser itself could not run (no
  dependency, no network) — a validator that cannot run is not a failing diagram, so
  `2` is a distinct, non-blocking outcome from `1`.
- **`render-illustration.ts`** — renders one self-contained HTML document from a
  mermaid diagram: the diagram sits inside a `<div class="mermaid">` element (what
  `validate-mermaid.ts` looks for), mermaid itself loads from a CDN at view time
  (`@11`, the same major the validator parses with), and light/dark is handled via CSS
  custom properties plus a `prefers-color-scheme: dark` media query.
- **`check-review-log.ts`** — reads the `*.review.jsonl` log Rule 5 leaves next to a draft and
  decides whether the review really happened: rounds present and consecutive, never more than
  three, terminated by a `PASS` or by the cap, every rendered brief reproducing the shipped
  template, and no run of 12 or more words shared between a brief and the original request (the
  context-isolation seal, made machine-checkable). Exits `0` when the log is sound, `1` when it
  is not, and `2` when the checker itself could not run — the same three-outcome vocabulary the
  eval harness reads. `--require-catch` additionally demands that round 1 found something and
  round 2 found less, which is how the "the reader actually catches things" evidence is tallied
  after a run rather than gated during it. The 12-word overlap check is word-based, so any
  prompt normalizing to fewer than 12 word tokens is out of contract for leak detection (ruling
  R2) — scripts with no whitespace word boundaries (e.g. Japanese, Chinese) are the motivating
  case, but a short prompt in any language hits the same condition: the checker prints a
  `WARN: prompt-leak detection not applicable` line and continues rather than passing silently.

Both rendering scripts (`validate-mermaid.ts` and `render-illustration.ts`) are `bun` CLIs, run
from the directory where the diagram/output files live (their path flags — `--diagram`,
`--out`, `--html-glob`, `--file` — are relative to `cwd`).

This `scripts/` directory is **skill-local** (`plugins/explaining/skills/explaining/scripts/`),
which matters for installability: `install.sh` symlinks a skill's whole directory into
`~/.claude/skills/<name>/` (`scripts/` included), so these three scripts install
automatically with no installer change. A *plugin-level* `scripts/` — directly under
`plugins/explaining/`, not under a `skills/<name>/` — is a different case: `install.sh`'s
whitelist recognizes that name too, but only to skip it ("repo-invoked, NOT installed"),
never to link it. See `ref-plugin-layout`'s How section for the golden plugin layout.

## The blind-reader brief template (`skills/explaining/references/blind-reader-brief.md`)

Rule 5's brief is rendered from `references/blind-reader-brief.md`, which ships inside the
skill directory (`skills/explaining/`, not a plugin-level `scripts/`) so the eval harness
installs it with the skill along with everything else the directory carries. Its three slots
— `artifact_path`, `audience`, `language` — are the only values allowed to reach the blind
reader; nothing else (the user's request, the author's reasoning, an earlier round's
findings) may cross into the rendered text. The reader model is a documented knob that
defaults to `sonnet`.

## On-demand dependency install

`validate-mermaid.ts` depends on `mermaid` and `jsdom` (`package.json` +
`bun.lock`, committed; `node_modules/` is git-ignored — see `scripts/.gitignore`). If
the dependency isn't installed yet, the script runs `bun install --cwd <script dir>`
once, on demand, and retries the import; if that also fails (no network, no `bun`), the
parser is reported `unavailable` and the validator exits `2` rather than crashing or
misreporting a diagram as invalid.

## Eval fixture

The skill's regression fixture lives at `skills/explaining/evals/evals.json` (next to
`SKILL.md`, per `ref-evals-fixture`), with its ambient-memory fixture at
`skills/explaining/evals/memory-fixture/CLAUDE.md`. Run it with
`scripts/evals/run_evals.py --evals plugins/explaining/skills/explaining/evals/evals.json`
from the repo root — see `scripts/evals/README.md` for the harness's own flags
(`--arm`, `--dry-run`, etc.).
