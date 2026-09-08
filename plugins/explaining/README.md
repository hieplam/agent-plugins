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

## The output style (`output-styles/todd-way.md`)

The skill fires when Claude judges a task to be explanatory. **Todd way** is the same guidance
made unconditional: an [output style](https://code.claude.com/docs/en/output-styles) is a
Markdown file whose body Claude Code appends to the system prompt for every turn of the main
conversation, so the rules hold even on a turn the skill would not have triggered on.

It is a *combination*, not a port. Exactly one output style is active at a time, so selecting
`Todd way` replaces the built-in **Concise** style rather than stacking with it — which is why
Concise's six rules are folded in verbatim alongside the skill's five. It sets
`keep-coding-instructions: true`, because the reader is still doing software engineering; only
the response shape changes.

**The seam.** The two halves genuinely conflict: Concise says cut, and the explaining rules say
define every unfamiliar term and ground every claim. Leaving that unresolved would make the
model guess per turn, so the style states the resolution up front — two registers, and one rule
decides which applies. Operational output (command results, status, a direct answer to a direct
question) takes Concise. Explanatory output takes the explaining rules, **and** keeps Concise's
rules 1, 2, 4 and 6; only "short by default" yields, because a definition the reader needs is
not padding, while a preamble still is. `evals/evals.json` measures both ends and the seam
itself — see below.

**Tooling discovery.** Being a system-prompt fragment, the style has no stable relative path to
the skill's `scripts/` and `references/`, so it discovers them
(`~/.claude/skills/explaining` from a symlink install, else the plugin cache) and carries a
fallback for each when the plugin is absent: an inline HTML template for the illustration, and
an inline copy of the blind-reader brief. The discovery line uses `find` rather than a shell
glob because a non-matching glob aborts the whole command under zsh — measured, not theorized.
The inlined brief is pinned byte-identical to `references/blind-reader-brief.md` by
`test_inlined_blind_reader_brief_is_identical_to_the_shipped_template`, since a drifted copy
would otherwise fail `check-review-log.ts` far from the edit that caused it.

`install.sh` links it into `~/.claude/output-styles/`. Select it with `/config` → **Output
style**, or set `"outputStyle": "Todd way"`; it takes effect after `/clear`, since the system
prompt is read once per session. Subagents are unaffected — they run their own system prompt,
which is also why the blind-reader review still works: the reader never inherits the style.

## The eval fixture for the style (`evals/evals.json`)

Separate from the skill's own fixture (`skills/explaining/evals/evals.json`, below) so a
regression in either is still attributable. It declares `kind: "output-style"`, which the
repo-wide harness supports by writing both halves of a real selection into the scratch project
scope — the style file *and* `{"outputStyle": …}` in `.claude/settings.json`. Five cases:

| # | Case | What it pins |
| --- | --- | --- |
| 1 | `operational-register-stays-terse` | The expensive failure mode of an always-on explanatory style: firing on an operational question. Machine-checked — no `.html`, no `.review.jsonl`, no `explanation.md` may exist afterwards. |
| 2 | `explanatory-register-terms-introduced-and-claims-grounded` | The term-discipline + grounding pair still lands (the skill's case 1, reused), now with the reply-level term check. |
| 3 | `seam-explanatory-depth-without-losing-concision` | Both halves at once: depth *and* no preamble, no recap — with the reply-level term check that the first run showed was missing. |
| 4 | `multi-actor-flow-illustrated` | The rendered HTML diagram, checked by `validate-mermaid.ts`. |
| 5 | `long-explanation-gets-a-blind-reader-review` | The review actually ran, checked by `check-review-log.ts`; the reply that points at the file does not re-drop the file's jargon. |
| 6 | `first-sentence-leads-with-the-answer-without-bare-jargon` | "Lead with the result" pulling MVCC / dead tuples / xmin horizon into sentence one with no lead-in — the seam gap measured on 2026-09-08. |
| 7 | `error-message-question-is-explanatory-in-disguise` | "What does this mean?" on a Postgres FATAL line: the register decision. A bare "raise `max_connections`, add PgBouncer" answers the surface and fails. |
| 8 | `pr-description-introduces-its-own-terms` | A PR description for a real diff (backoff + jitter + idempotency key + circuit breaker): the register the style names explicitly, where models write for the author. |
| 9 | `doc-comment-names-the-behaviour-not-a-label` | B4 on a Go function that logs and swallows: the comment must say what happens, never "best-effort" — machine-checked — while the chat reply stays operational. |
| 10 | `non-english-reply-introduces-borrowed-english-terms` | A Vietnamese question; the reply borrows "hash ring", "virtual node", "hotspot" from English and must gloss them in Vietnamese (`--cues 'là,tức là,…'`). |
| 11 | `acronym-heavy-domain-expands-every-acronym` | mTLS in a service mesh: SPIFFE / SVID / SDS / xDS sprayed bare is the default; plus the multi-actor diagram. |
| 12 | `senior-audience-no-padding-but-new-terms-still-introduced` | The over-correction guard: a senior .NET reader gets no `async`/`await` primer, yet `ExecutionContext` and `sync-over-async` still get a lead-in. |

Every explanatory case carries a machine check on the **reply text** itself
(`check-term-discipline.ts --reply {reply} --terms …`), because the first run showed the LLM
grader alone cannot hold the line: it passed a baseline reply with ten bare terms as
"contextualized in place". The fixture's top-level `oracle` states, once, what "introduced"
means and which direction of error is by design; the per-case rubrics inherit it.

It reuses the skill fixture's `memory-fixture/CLAUDE.md` by relative path rather than copying
it, so the ambient-memory arm cannot drift between the two suites.

### Measured, 2026-09-08, first run (cases 1 and 3, clean arm, n=1)

| Case | With `Todd way` | Baseline (`--safe-mode`) |
| --- | --- | --- |
| 1 — operational stays terse | PASS, 7.1s, 43k tokens | PASS, 7.4s, 31k tokens |
| 3 — the seam | PASS, **545s, 457k tokens** | PASS, 15.1s, 16k tokens |

Case 1 is a guard, not a differentiator: a two-value config lookup is already terse at baseline,
so what it proves is the absence of a regression — the always-on explanatory rules did not fire
on an operational question, machine-confirmed by the no-artifacts check.

Case 3 is the finding, and it is a **cost** finding, not a correctness one. The style passed and
the answer was good; it cost 36× the wall-clock and 29× the tokens of the same question without
it. The transcript names the cause exactly: the answer crossed B5's 600-word threshold, so the
model read the brief from `~/.claude/skills/explaining/references/blind-reader-brief.md`,
dispatched a `sonnet` blind reader, logged round 1, fixed its findings, dispatched a second
reader, and reported `PASS after 2 round(s)` — every step working as written. It also rendered
an `index-write-path.html` diagram under B1.

That is the trade the style makes, and it is worth stating plainly because the threshold means
it fires on ordinary conversational "why" questions, not just on deliverables: **B5 was designed
for a skill that only runs when invoked, and an always-on style invokes it far more often.** The
knob is B5's `When` clause — raise the word threshold, or gate it on an explicit file
deliverable rather than a word count. Both narrow the rule, so neither is applied here by
default.

**What that first run did not show, and what it hid.** Both arms PASSED case 3 — the baseline
too. Its reply dropped MVCC, HOT update, latch, vacuum, dead tuples, bloat, planner, seq scan
and full-page images with no lead-in, and the grader accepted them as "contextualized in
place". The styled reply was no better on that axis: its first sentence opened with "the
write-ahead log, the buffer pool, and vacuum" bare, and the reply summarising
`explanation.md` re-dropped nine of the eleven listed terms the file had defined. A suite that
passes both arms is measuring nothing. Three things changed as a result: the reply is now on
disk for checks (`{reply}`), `check-term-discipline.ts` gates every explanatory case before the
grader, and B2 gained two paragraphs — the first sentence is not exempt, and every channel the
reader sees (the reply, not only the file) carries the lead-ins.

### Measured, 2026-09-08/09, second run (all 12 cases, clean arm)

Both arms once; styled legs whose verdict changed with a checker or style fix were re-run
(cases 2, 3, 5, 6, 7, 8, 11, 12), and four styled transcripts whose machine check had failed
under an earlier checker but passes the final one were graded from their saved transcript
with the harness's own grader rather than re-executed (2, 7, 10, 12). Verdict = machine
checks, then the LLM grader. "Bare" = listed terms used without a lead-in, as the final
`check-term-discipline.ts` counts them, out of listed terms used.

| # | Case | Baseline | Bare | `Todd way` | Bare |
| --- | --- | --- | --- | --- | --- |
| 1 | operational stays terse | PASS | — | PASS | — |
| 2 | JetStream acks | FAIL (term floor) | 5/12 | PASS | 3/11 |
| 3 | the seam (index on a hot table) | FAIL (term floor) | 10/11 | PASS | 2/8 |
| 4 | multi-actor flow illustrated | FAIL (no diagram) | — | PASS | — |
| 5 | WAL, blind-reader review | FAIL (no review log) | 4/7 | PASS | 2/6 |
| 6 | first sentence, Postgres bloat | FAIL (term floor) | 7/10 | PASS | 3/12 |
| 7 | error message, explanatory in disguise | FAIL (term floor) | 3/4 | PASS | 1/5 |
| 8 | PR description | FAIL (term floor) | 3/6 | **FAIL** (term floor) | 4/7 |
| 9 | doc comment, no "best-effort" | FAIL ("on a best-effort basis") | — | PASS | — |
| 10 | Vietnamese, borrowed terms | PASS | 1/1 | PASS | 1/1 |
| 11 | mTLS acronyms | FAIL (no diagram; 5/10 bare) | 5/10 | PASS | 2/7 |
| 12 | senior audience | FAIL (term floor) | 2/2 | PASS | 1/1 |

Nine of twelve cases now separate the arms; cases 1 and 10 are guards the baseline already
meets, and case 8 is a styled failure. The suite went from "both arms pass" to a
discriminating one, and the numbers say what the style does on jargon: a baseline reply leaves
roughly two thirds of the listed terms it uses bare, a styled one roughly one fifth, and the
styled first sentence is plain words in every case where it was measured.

**What still fails, honestly.** Case 8 (a PR description) opens in plain words, then its
second sentence lists "idempotency key, exponential backoff, circuit breaker, Retry-After" by
name before the sections below define each one — a genuine first-use miss, and the natural
shape of a PR summary. Two second runs disagreed with the first: case 7's re-run dropped
"idle in transaction" and "backend process" bare (2 of 5, floor allows 1), and the first run
of case 6 left "dead tuple, bloat, pg_repack" bare (3 of 8, allows 2). Everything here is
n=1 or n=2; treat the table as directional, and re-run with `--runs 3` before trusting a
change to B2 or to the floor.

**Cost.** A styled explanatory leg takes 4–9 minutes and roughly half a million tokens, the
baseline 15–40 seconds, because B1 renders a diagram and B5 runs one to three blind-reader
rounds; the same trade the first run measured on case 3.

Run it yourself before changing that clause; a first run needs a raised timeout, since the
harness default of 420s is shorter than a two-round review:

```bash
python3 scripts/evals/run_evals.py --evals plugins/explaining/evals/evals.json --eval-id 3 --timeout 1200
```

```bash
python3 scripts/evals/run_evals.py --evals plugins/explaining/evals/evals.json
python3 scripts/evals/run_evals.py --evals plugins/explaining/evals/evals.json --eval-id 1,3
```

## The four scripts (`skills/explaining/scripts/`)

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
- **`check-term-discipline.ts`** — reads one reply (the harness hands it `{reply}`) and a list
  of terms, and decides per term: `UNUSED` (fine — avoiding jargon is term discipline too),
  `DEFINED` (the sentence of first use carries a definitional cue attached to the term: a
  colon, a does-verb saying what it does ("`VACUUM FULL` rewrites the table"), a dash,
  parenthesis or appositive comma within two words, a copula within three, "called" / "known
  as" / a predicative copula before it ("a modified page is *dirty*"), or the term opening a
  parenthesis after a word — `write-ahead log (WAL)`, `(`setting`, default 3)`), or
  `UNDEFINED`. Fenced code is not prose; a heading or bold lead-in is read with the sentence
  after it; an all-caps term matches case-sensitively so `HOT` never hits "hot pages"; `--cues`
  adds another language's cue words (`là`, `tức là`, `gọi là`). It is a **floor**: it refuses
  a reply whose bare terms exceed `max(--max-undefined, floor(--max-undefined-ratio × used))`
  — the fixture uses `1` and `0.34`, so a mass bare drop (a baseline reply leaves 60–100 % of
  the terms it uses bare) fails and a single miss on a long reply goes to the grader. Exit
  `0` / `1` / `2` as the others. Its oracle is the comment at the top of the file:
  under-flagging is by design, over-flagging is a bug. Its tests carry the real baseline reply
  the grader passed (refused) and a disciplined rewrite (accepted).
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
