# Project memory

## Build & test
- `make build` compiles the service; `make test` runs the unit suite (Go, testify).
  CI blocks merge on any failure.
- Run `make lint` before pushing — `golangci-lint` catches most style issues locally,
  faster than waiting on CI to report the same thing.
- Integration tests live under `test/integration/` and need
  `docker compose up -d postgres redis` running first.
- Flaky tests get quarantined in `test/quarantine/`, not deleted. File a ticket, don't
  just skip and forget.

## Answers
- Keep responses short. A couple of sentences, not five. If a question has a yes/no
  answer, lead with it.
- Skip restating the question back to me before answering. Get to the point.
- Prefer a code snippet over a wall of prose when a snippet answers the question
  directly.
- If you're not sure, say so in one line and ask a single clarifying question — don't
  guess and bury the guess in a long explanation.

## Release notes
- Every PR that touches `api/` needs a one-line entry in `CHANGELOG.md` under
  `Unreleased`. No entry, no merge.
- Tag releases as `vMAJOR.MINOR.PATCH`; breaking changes bump MAJOR and get their own
  migration note at the top of the entry.
- Deploys go out Tuesday and Thursday only, never on a Friday.
- Roll back first, investigate second, if a deploy causes an error-rate spike.

## Review habits
- Reviewers: check for missing error handling before anything else. Swallowed errors
  are the most common bug in this codebase.
- Flag any function over 40 lines for a possible split.
- Don't nitpick naming on a first pass; focus on correctness and test coverage first,
  polish later.
- Prefer small PRs. Anything over 400 lines gets asked to split into smaller pieces.
- Two approvals required for anything touching `auth/` or `billing/`.

## Misc
- Auth service owns rate limiting; don't add it anywhere else in the stack.
- Use structured logging (`log.WithFields`), never `fmt.Println`, in production code
  paths.
- The staging environment resets nightly at 02:00 UTC — don't rely on state persisting
  there between runs.
- Feature flags default to off; flip them in `config/flags.yaml`, never hardcode a
  bypass.
