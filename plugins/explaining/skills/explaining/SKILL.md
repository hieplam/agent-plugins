---
name: explaining
description: Use when producing any prose whose main job is to make a reader understand something — explaining a concept, answering a "why/how does X work" question, writing research notes, design docs, architecture or code documentation, teaching-style answers, PR descriptions of non-trivial changes, or blog drafts — even when the user doesn't say "explain". Applies in any output language. Not for terse operational output (command results, status lines, checklists).
---

# Explaining

## Overview

Default LLM technical prose imitates expert-to-expert register: terms appear without introduction and claims stay abstract. These two rules replace that default. They are the pair that won an isolated A/B eval against baseline and against each rule alone — apply **both** to any explanatory prose you produce.

## Rule 1 — Term discipline: define before use

Any new concept, technology, or technical term must be briefly defined or contextualized the first time it is introduced. Never drop a new term mid-explanation without an introductory lead-in. If a term needs a whole paragraph to define, define it *before* the section that depends on it, not inside.

## Rule 2 — Grounding: anchor every abstract claim

Ground every claim with truth, code or fact. Pair each abstract or general statement with at least one of:

- a code snippet that demonstrates it,
- a concrete worked example, or
- a verifiable fact/source.

If you cannot ground a claim, mark it explicitly as unverified/opinion or delete it. Prefer showing the artifact first, then explaining it — the artifact carries its own context.

## Rule 3 — Named a concept instead of the behaviour

For example: "Best-effort" is a label from the OKRA frame. At the code, a reader has no referent for it — it sounds like it means something but says nothing checkable.

Avoid: // Best-effort: persists the snapshot; on ANY failure it logs (no PII) and swallows — it NEVER throws.
Follow:  // Never throws: persists the snapshot, or logs and swallows on failure.

## Rule 4 — Illustrate a flow instead of narrating it

**When.** A flow with multiple actors or conditional paths gets a diagram. Linear prose, a single-actor sequence, or a list of facts does not — a diagram there is noise.

**What.** A mermaid diagram, rendered into one self-contained HTML file written to disk. A fenced code block alone is not the deliverable: it renders in some clients and not others.

**How.** Run both from the directory where you wrote the file (the `--diagram`/`--out`/`--html-glob` paths are relative). Build the file with `bun scripts/render-illustration.ts --title T --diagram D.mmd --out out.html`, then validate it with `bun scripts/validate-mermaid.ts --html-glob out.html`. The diagram must sit in an element with `class="mermaid"` (the renderer does this).

**The three validator outcomes.** Exit `0`: ship it. Exit `1` with a printed hint: fix the diagram using the hint and re-validate. Exit `1` with no hint (`0 diagram(s) found`): either no HTML artifact was found, or one was found but has no `class="mermaid"` element (or that element is empty) — confirm the render step ran and the HTML file has a non-empty rendered diagram in a `class="mermaid"` element, then re-validate. Exit `2`: the validator could not run (no dependency, no network) — ship the file anyway and say the diagram is unvalidated. A validator that cannot run is not a failing diagram.

**Offer it.** The file on disk is the deliverable; offering it is a best-effort second step. Use an MCP preview or download tool when one exists, otherwise state the path.

**Mermaid safe syntax:**

- Write dotted link ends in full: `-.-x` and `-.-o`, never an abbreviated form.
- Wrap a label in double quotes when it contains `(` `)` `[` `]` `{` `}` `|` or `"`, or when it starts with `/` or `\`.
- Write a literal double quote inside a quoted label as `#quot;`.

## Rule 5 — Blind-reader review before delivery

You cannot see what a reader lacks, because you have the context that makes every jump feel
smooth. The self-check below is you grading your own homework; this rule is the part a reader
does.

**When.** The deliverable is a file on disk (HTML or markdown), or the explanatory prose runs to
600 words or more. Shorter answers keep the self-check alone.

**Draft to disk first.** Write the complete draft to a file — the artifact itself, or
`explanation.md` in the working directory when the deliverable is prose. The review runs on the
file, never on pasted text: the path is the reader's entire input, and that is what keeps the
reader blind.

**Dispatch one blind reader per round.** A fresh subagent — never a fork of this session, never
this session itself — briefed with `references/blind-reader-brief.md` from this skill directory,
its three slots filled in: the file path, the audience in one short phrase, and the language.
Reader model: `sonnet` by default. Dispatch it with `run_in_background: false` and wait for its
reply: this skill runs in headless sessions, where a backgrounded reader never returns a verdict.
Nothing else crosses into that brief: not the user's request, not your sources, not your
reasoning, not the draft text inline, not an earlier round's findings. A reader that has been
told what the draft was supposed to say can no longer tell you what it actually says.

**Log every round, and open the record before you dispatch.** The log is a file named after the
draft with `.review.jsonl` appended (a draft at `explanation.md` logs to
`explanation.md.review.jsonl`). Each round is two writes, in this order:

1. **Open** — before dispatching the reader, append one JSON object carrying `round` and `brief`
   (the rendered brief, verbatim).
2. **Complete** — when the reader returns, rewrite that same line with `findings`, `block_count`,
   `verdict` and `author_action` filled in.

Opening first is not bookkeeping taste. It is what makes a missing log impossible: if the log has
no record for a round, no reader was dispatched for it.

```json
{"round": 1, "reader_model": "sonnet", "brief": "the rendered brief, verbatim", "findings": [{"severity": "BLOCK", "location": "the quoted phrase", "issue": "what broke, in the reader's words"}], "block_count": 1, "verdict": "FAIL", "author_action": "what you changed before the next round"}
```

`block_count` is the number of `BLOCK` findings and `verdict` is `PASS` exactly when that count
is zero. Check the log with
`bun scripts/check-review-log.ts --prompt "the request you were given"` from the directory
holding the draft: exit `0` means the review is well-formed, exit `1` names what is wrong, exit
`2` means the checker itself could not run.

**Fix and loop. Round 3 is the last round. After its verdict, stop — even on FAIL.** Fix every
`BLOCK` finding, rewrite the file, and dispatch a NEW reader — fresh context again. Stop at
`READER: PASS`, or when round 3 returns its verdict, whichever comes first. There is no fourth
round; the checker fails a log that records one. A `NIT` may be dismissed; record the one-clause
reason in the log.

**Say how it ended.** The final answer carries exactly one line about the review, never silence:

- `Blind-reader review: PASS after 2 round(s)`
- `Blind-reader review: ended at cap with 1 open BLOCK finding(s)` followed by the list

**Degrade only after a dispatch has actually failed.** Skipping the review is permitted only
after you attempted to dispatch a reader and that attempt itself failed. Believing that no
dispatch tool exists is not a permitted reason — attempt the dispatch and let the failure be the
evidence. When it fails, write the round 0 record to the log first:

```json
{"round": 0, "brief": "the rendered brief, verbatim", "findings": [], "block_count": 0, "verdict": "FAIL", "author_action": "dispatch failed: the failure text, verbatim"}
```

then keep the self-check and say so in one line:
`Blind-reader review: skipped (dispatch failed, round 0 in the review log carries the error).`

A round 0 record belongs to the session that failed to dispatch. If a later session retries the
review on the same draft, delete the stale round 0 line before opening round 1: the retry
supersedes it, and a log that still carries round 0 reads to the checker as a review that never
dispatched a reader, whatever else the log holds.

## Self-check before finishing

Scan the draft once and fix:

1. Any technical term at first use without a lead-in or definition? (Rule 1)
2. Any abstract claim with no code/example/fact anchor and no "unverified" marker? (Rule 2)
3. Any multi-actor or conditional flow that got narrated instead of drawn? (Rule 4)
4. Did the blind-reader review run to a verdict, and did the answer say how it ended? (Rule 5)

## Evidence — why exactly these two rules

A/B eval, 2026-07-18: 5 arms × isolated `claude -p` runs (`--setting-sources project`, no CLAUDE.md leakage), blind-graded by a separate model on countable metrics. On clean runs, baseline scored **12.18 undefined terms per 1k words**; this rule pair scored **4.06 (−67%)**. Each rule alone lost to the pair: term discipline alone doubled over-explanation (M3 1.25 vs the pair's 0.75); the grounding line alone did not move the undefined-term rate (11.17 ≈ baseline) though it directionally helped grounding ratio. An added reader-model line ("Reader: senior .NET dev…") *regressed* undefined terms to 10.04 and grew output +18% — excluded. Persona framing ("act as a meticulous instructor") is excluded per Zheng et al. 2024 (arXiv 2311.10054): personas in system prompts don't improve task performance.

Model transfer (measured, not assumed): the −67% effect was measured on Opus 4.8. A same-design transfer grid on Fable 5 found its *baseline* already near the rules' level (M1 8.92 baseline vs 8.20 with rules — within noise); there the pair mainly narrowed variance (±3.49 → ±1.34) with no measured harm. Expect the largest gains on models/subagents with jargon-heavy defaults, and consistency gains elsewhere.

Known limits: n=4 clean runs/arm (directional, not statistical); the pair still over-explains slightly vs no-rules baseline (M3 0.75 vs 0.25). Regression fixtures: `evals/evals.json` — re-run with the repo eval harness before editing these rules, and keep a rule only if the numbers still back it.
