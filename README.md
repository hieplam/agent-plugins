# agent-plugins

General-purpose Claude Code agents and skills, packaged as installable plugins. The
repo is the single source of truth; `.claude-plugin/marketplace.json` is the authoritative
registry.

> **Origin.** These plugins were copied verbatim from
> [`hieplam/todd-skills`](https://github.com/hieplam/todd-skills) at commit
> `87dc4c4219fa8b0c0ec9c854c2cca051cd08d843`, so that repo can focus on the `tribe` plugin alone. No history was
> rewritten; this repo starts from a single import commit.

## Install

### From any machine (marketplace)

The repo is public, so no clone and no auth are needed:

```
/plugin marketplace add hieplam/agent-plugins
/plugin install explaining@agent-plugins
```

Run those inside a Claude Code session. `explaining@agent-plugins` is
`<plugin>@<marketplace>` — the marketplace is named `agent-plugins`. Only `explaining` is
registered there; the archived plugins are symlink-install only (see below).

### From a checkout (symlink install)

Use this on the machine where you edit the plugins. It symlinks them into `~/.claude`, so an
edit here takes effect in every session immediately — there is no marketplace snapshot to
refresh.

```bash
git clone https://github.com/hieplam/agent-plugins.git
cd agent-plugins
./install.sh --list          # show available plugins and their components
./install.sh explaining      # install named plugins
./install.sh                 # install all live plugins (never the archived ones)
./install.sh _archive/splitting-plans   # opt in to an archived plugin
```

Behaviour: `agents/*.md` link into `~/.claude/agents/`, `skills/<name>/` into
`~/.claude/skills/`, `output-styles/*.md` and any `output-styles/<dir>/` into
`~/.claude/output-styles/`, and `tools/` into `~/.claude/tools/<plugin>/`. It is idempotent (an existing link to this repo is skipped), a
conflicting file is backed up to `<name>.bak.<epoch>` first, and a plugin's own `install.sh`
runs as a post-install hook. `CLAUDE_DIR` overrides the target root (used by `tests/test_install.py`).

`tools/` exists because an output style is a fragment of the system prompt: unlike a `SKILL.md`,
it has no relative path back to the repo, so any script it invokes must sit at a path it can
name literally. `~/.claude/tools/explaining/scripts/validate-mermaid.ts` is such a path.

**Which one do I want?** Marketplace to *use* the plugins anywhere; symlink install to *develop*
them.

## Plugins

| Plugin | Kind | What it does |
| --- | --- | --- |
| `explaining` | output style, tools | The **Todd way** output style: eval-proven rules for explanatory prose (term discipline, grounding, drawn flows, blind-reader review) folded together with the built-in Concise rules, always on. The skill it replaced is frozen under `archive/skills/explaining/`. |

### Archived plugins

Everything else lives in `_archive/<name>/` with the same layout it had under `plugins/`. They
are not in the marketplace and not part of `./install.sh` with no arguments, but each one still
installs on request by its `_archive/` name:

| Install with | What it does |
| --- | --- |
| `./install.sh _archive/research-to-blog` | Turn a session insight or a bare topic into a bilingual EN+VI research note and published blog posts. |
| `./install.sh _archive/splitting-plans` | Split a large plan into isolated, dependency-aware sub-plans for parallel subagents. |
| `./install.sh _archive/check-diff-coverage` | Measure uncovered diff vs main and drive a remediation loop (.NET, Go). |
| `./install.sh _archive/refactor-for-testability` | Reshape untestable code before changing its behaviour. |
| `./install.sh _archive/workflow-journal` | Render each Workflow run to a readable Markdown record. |
| `./install.sh _archive/simple-image-video` | Animate a still image into a short video. |

`./install.sh --list` shows both groups. Their eval fixtures are outside `plugins/`, so
`run_evals.py --all` skips them; run one explicitly with `--evals _archive/<name>/...`.

## Development

```bash
# eval-harness unit tests (stdlib unittest, no API calls, no cost)
python3 -m unittest discover -s scripts/evals/tests -t .

# install.sh tests (runs the real installer against a throwaway CLAUDE_DIR)
python3 -m unittest discover -s tests -t .

# explaining's tooling tests
cd plugins/explaining/tools/scripts && bun install && bun test && cd -

# agent/skill evals (spends real tokens — see scripts/evals/README.md)
python3 scripts/evals/run_evals.py --evals _archive/splitting-plans/skills/splitting-plans/evals/evals.json
```

Every plugin in `plugins/` must be registered in `.claude-plugin/marketplace.json`, and must
follow the directory contract `install.sh` understands: `agents/`, `skills/`, `output-styles/`,
`tools/`, `claude-md/`, `hooks/`, `.claude-plugin/`, `scripts/`, and `evals/` — it warns on
anything else, and `tests/test_install.py` fails if any plugin trips that warning.

`archive/` (retired components kept for their evidence) and `_archive/` (retired plugins that
can still be installed by name) both sit outside `plugins/` on purpose: "install ALL" and the
eval harness's `--all` discovery never see them.

## Output styles

An [output style](https://code.claude.com/docs/en/output-styles) is a Markdown file whose body
is appended to Claude Code's system prompt for **every turn** of the main conversation, so it
changes the default shape of every response rather than waiting to be invoked. A plugin ships
them in `output-styles/`; `install.sh` links each `*.md` into `~/.claude/output-styles/`, and
the file name is the style name unless the frontmatter sets `name:`.

| Style | From | What it does |
| --- | --- | --- |
| `Todd way` | `explaining` | Concise on operational replies; on explanatory ones, terms get defined, claims get grounded, multi-actor flows get drawn as validated HTML, and a long draft goes past a blind reader before delivery. Every HTML page wears the Reading design system (book paper, Caslon, full screen width), linked beside the style. Its scripts arrive via the same plugin's `tools/`. |

Select one with `/config` → **Output style**, or set `"outputStyle": "Todd way"` in a settings
file. It takes effect after `/clear` or in the next session — the system prompt is read once at
session start. Exactly one style is active at a time, so selecting `Todd way` replaces whatever
was selected before; that is why it folds the built-in **Concise** rules in rather than assuming
you still have them.

Output styles are eval-backed the same way skills are — `kind: "output-style"` in
`scripts/evals/README.md`:

```bash
python3 scripts/evals/run_evals.py --evals plugins/explaining/evals/evals.json
```
