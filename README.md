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
/plugin install splitting-plans@agent-plugins
```

Run those inside a Claude Code session. `splitting-plans@agent-plugins` is
`<plugin>@<marketplace>` — the marketplace is named `agent-plugins`, and any plugin from the
table below works in its place.

### From a checkout (symlink install)

Use this on the machine where you edit the plugins. It symlinks them into `~/.claude`, so an
edit here takes effect in every session immediately — there is no marketplace snapshot to
refresh.

```bash
git clone https://github.com/hieplam/agent-plugins.git
cd agent-plugins
./install.sh --list          # show available plugins and their components
./install.sh splitting-plans # install named plugins
./install.sh                 # install all of them
```

Behaviour: `agents/*.md` link into `~/.claude/agents/`, `skills/<name>/` into
`~/.claude/skills/`, and `output-styles/*.md` into `~/.claude/output-styles/`. It is idempotent (an existing link to this repo is skipped), a conflicting
file is backed up to `<name>.bak.<epoch>` first, and a plugin's own `install.sh` runs as a
post-install hook. `CLAUDE_DIR` overrides the target root (used by the tests).

**Which one do I want?** Marketplace to *use* the plugins anywhere; symlink install to *develop*
them.

## Plugins

| Plugin | Kind | What it does |
| --- | --- | --- |
| `research-to-blog` | agents | Turn a session insight or a bare topic into a bilingual EN+VI research note and published blog posts. |
| `splitting-plans` | skills | Split a large plan into isolated, dependency-aware sub-plans for parallel subagents. |
| `check-diff-coverage` | skills | Measure uncovered diff vs main and drive a remediation loop (.NET, Go). |
| `refactor-for-testability` | skills | Reshape untestable code before changing its behaviour. |
| `workflow-journal` | skills | Render each Workflow run to a readable Markdown record. |
| `simple-image-video` | skills | Animate a still image into a short video. |
| `explaining` | skills, output style | Two eval-proven writing rules (term discipline + grounding) for explanatory prose; refuted rule candidates excluded by A/B data. Ships the **Todd way** output style, which folds those rules plus the built-in Concise rules into one always-on style. |

## Development

```bash
# eval-harness unit tests (stdlib unittest, no API calls, no cost)
python3 -m unittest discover -s scripts/evals/tests -t .

# explaining's script tests
cd plugins/explaining/skills/explaining/scripts && bun install && bun test && cd -

# agent/skill evals (spends real tokens — see scripts/evals/README.md)
python3 scripts/evals/run_evals.py --evals plugins/splitting-plans/skills/splitting-plans/evals/evals.json
```

Every plugin in `plugins/` must be registered in `.claude-plugin/marketplace.json`, and must
follow the directory contract `install.sh` understands: `agents/`, `skills/`, `output-styles/`,
`claude-md/`, `hooks/`, `.claude-plugin/`, `scripts/`, and `evals/` — it warns on anything else.

## Output styles

An [output style](https://code.claude.com/docs/en/output-styles) is a Markdown file whose body
is appended to Claude Code's system prompt for **every turn** of the main conversation, so it
changes the default shape of every response rather than waiting to be invoked. A plugin ships
them in `output-styles/`; `install.sh` links each `*.md` into `~/.claude/output-styles/`, and
the file name is the style name unless the frontmatter sets `name:`.

| Style | From | What it does |
| --- | --- | --- |
| `Todd way` | `explaining` | Concise on operational replies; on explanatory ones, terms get defined, claims get grounded, multi-actor flows get drawn as validated HTML, and a long draft goes past a blind reader before delivery. |

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
