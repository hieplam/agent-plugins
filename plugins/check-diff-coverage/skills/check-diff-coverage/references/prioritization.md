# Prioritising uncovered diff lines

When `measure.sh` returns `verdict: "fail"` (or "warn" and you want to push to "pass"), you must decide *which* uncovered lines to write tests for. Don't just go top-to-bottom of the file list — uncovered lines have very different value.

## The triage question

For every uncovered block, ask in order:

1. **Should this code exist at all?**
   If it's defensive paranoia (a null check on something that can't be null, a catch that swallows then re-throws, a fallback for a path that never fires) — **delete it**. Deletion lowers the denominator and the percentage in one move, with less risk than an unread test.

2. **Is it pure logic, or wiring?**
   Pure logic (calculations, transformations, branching on inputs) → write a test. High value.
   Wiring (DI registration, builder calls, simple delegations) → low value to test directly; cover via the integration test that already exists.

3. **What happens if this line is wrong in production?**
   Money / data corruption / security → must be tested even at <20%.
   Cosmetic / log message / dev-only path → fine to leave uncovered.

## Priority order — test these first

| Priority | Pattern | Why |
|---|---|---|
| 🔴 High | New conditional / branch (`if`, `switch`, `?:`) | Each branch is a behaviour. Untested branches are silent landmines. |
| 🔴 High | New error/exception path (`throw`, `catch`, error return) | Almost never hit by manual testing. Most likely to break in prod. |
| 🔴 High | Anything touching money, auth, contracts, or external systems | Blast radius is large. |
| 🟡 Medium | New public method with non-trivial body | Forms the API surface; tests document intent. |
| 🟡 Medium | New validation / guard clauses | Tests pin down the contract. |
| 🟢 Low | New private helper called by tested code | The caller's test usually covers it. |
| ⚪ Skip | DI registration, ctor wiring, options binders | Integration tests cover this. Direct unit tests are noise. |
| ⚪ Skip | Trivial getters, ToString, generated code | Test budget better spent elsewhere. |
| ⚪ Delete | Defensive code with no observable effect | Removing is cleaner than testing. |

## Reading the diff-cover JSON

`measure.sh` writes `.coverage-diff/diff-cover.json`. Each entry under `src_stats` looks like:

```json
"src/Contracts.Api/PartnerLedHandler.cs": {
  "percent_covered": 42.0,
  "violation_lines": [45, 46, 47, 88, 89, 92, 110, 111]
}
```

Cross-reference the line numbers with the file content to classify each block by the table above. Group consecutive uncovered lines into a single "block" — they're usually one behaviour.

## How many tests, and how big

- One test per behaviour, not one per line. A 6-line `if/else` branch is one test.
- Keep tests small and focused — explicit inputs, one assertion per concern. See `~/.claude/rules/test-first.md`.
- Match the existing test style of the project. Don't introduce a new test framework or convention to fix coverage.

## When you're done with a round

After adding tests, rerun `measure.sh --skip-tests` is **not** valid here — you wrote new test code, you need to actually run it. Use plain `measure.sh` to re-execute and re-measure.

## When deletion is the right call

You're allowed to delete uncovered code if:

- It's clearly dead (no caller, found via LSP `find-references`).
- It's unreachable (e.g. `if (false)`, branch after a `throw`).
- It's defensive code that duplicates a guarantee already enforced upstream (a non-nullable parameter that's `null`-checked anyway).

Tell the user when you delete code, and *why* — don't slip it in silently. A deletion is a behaviour change.
