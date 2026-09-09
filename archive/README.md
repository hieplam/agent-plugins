# archive

Retired components, kept for their evidence rather than for use.

`archive/` sits deliberately **outside `plugins/`**. Two mechanisms depend on that:

- `install.sh` only walks `plugins/*/`, so nothing here can be installed by accident.
- `run_evals.py --all` discovers fixtures by globbing `plugins/**/evals/evals.json`, so an
  archived fixture cannot silently re-enter the suite and split the evidence with its
  replacement.

`tests/test_install.py` and `scripts/evals/tests/test_run_evals.py` both assert those two
facts, so moving a directory back under `plugins/` fails the suite rather than surprising
someone later.

| Path | Retired | Replaced by | Why it is kept |
| --- | --- | --- | --- |
| `skills/explaining/` | 2026-09-09 | the `Todd way` output style, `plugins/explaining/output-styles/todd-way.md` | its Evidence section holds the A/B numbers that justify the term-discipline and grounding rules, measured against *this* wording; the style's text has since been amended, so the numbers do not transfer verbatim |

## Resurrecting something

The archived `explaining` fixture's paths were repointed at the live plugin
(`plugins/explaining/evals/fixtures/`, `plugins/explaining/tools/scripts/`) when it moved, so
it still runs where it sits:

```bash
python3 scripts/evals/run_evals.py --evals archive/skills/explaining/evals/evals.json
```

Its `evals-case4.test.ts` moved with it and is **not** run by `bun test`, which now runs from
`plugins/explaining/tools/scripts/`. Frozen tests are documentation, not a gate.
