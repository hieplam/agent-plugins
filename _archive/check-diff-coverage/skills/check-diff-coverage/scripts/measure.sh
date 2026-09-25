#!/usr/bin/env bash
# measure.sh — run tests with coverage, then compute uncovered diff vs base branch.
#
# Output: prints a JSON summary on stdout (only). Logs go to stderr.
# Exit codes: 0 = ran successfully (regardless of pass/fail); 2 = setup error.
#
# Usage:
#   measure.sh                       # auto-detect base branch (master or main)
#   measure.sh --base origin/master  # explicit
#   measure.sh --skip-tests          # reuse existing coverage file (faster iteration)

set -euo pipefail

LOG() { printf '[measure] %s\n' "$*" >&2; }
DIE() { LOG "ERROR: $*"; exit 2; }

BASE_BRANCH=""
SKIP_TESTS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)        BASE_BRANCH="$2"; shift 2 ;;
    --skip-tests)  SKIP_TESTS=1; shift ;;
    -h|--help)     sed -n '2,10p' "$0"; exit 0 ;;
    *)             DIE "unknown arg: $1" ;;
  esac
done

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || DIE "not in a git repo"
cd "$REPO_ROOT"

# ---------- resolve base branch ----------
if [[ -z "$BASE_BRANCH" ]]; then
  if   git rev-parse --verify --quiet origin/master >/dev/null; then BASE_BRANCH="origin/master"
  elif git rev-parse --verify --quiet origin/main   >/dev/null; then BASE_BRANCH="origin/main"
  elif git rev-parse --verify --quiet master        >/dev/null; then BASE_BRANCH="master"
  elif git rev-parse --verify --quiet main          >/dev/null; then BASE_BRANCH="main"
  else DIE "no master/main branch found; pass --base"
  fi
fi
LOG "base branch: $BASE_BRANCH"

# ---------- detect language ----------
LANGUAGE=""
if   compgen -G "*.sln" > /dev/null || find . -maxdepth 4 -name "*.csproj" -print -quit 2>/dev/null | grep -q .; then
  LANGUAGE="dotnet"
elif [[ -f "go.mod" ]] || find . -maxdepth 4 -name "go.mod" -print -quit 2>/dev/null | grep -q .; then
  LANGUAGE="go"
else
  DIE "could not detect .NET (.csproj/.sln) or Go (go.mod) in $REPO_ROOT"
fi
LOG "language: $LANGUAGE"

# ---------- ensure tools ----------
ensure_diff_cover() {
  if command -v diff-cover >/dev/null 2>&1; then return 0; fi
  LOG "installing diff-cover (pip --user)…"
  python3 -m pip install --user --quiet diff-cover || DIE "failed to install diff-cover"
  USER_BIN=$(python3 -c 'import site, os; print(os.path.join(site.USER_BASE, "bin"))')
  export PATH="$USER_BIN:$PATH"
  command -v diff-cover >/dev/null 2>&1 || DIE "diff-cover installed but not on PATH ($USER_BIN)"
}

ensure_gocover_cobertura() {
  if command -v gocover-cobertura >/dev/null 2>&1; then return 0; fi
  LOG "installing gocover-cobertura…"
  go install github.com/boumenot/gocover-cobertura@latest || DIE "failed to install gocover-cobertura"
  GOBIN="$(go env GOPATH)/bin"
  export PATH="$GOBIN:$PATH"
  command -v gocover-cobertura >/dev/null 2>&1 || DIE "gocover-cobertura installed but not on PATH"
}

# ---------- workspace ----------
OUT_DIR="$REPO_ROOT/.coverage-diff"
mkdir -p "$OUT_DIR"
COVERAGE_XML="$OUT_DIR/coverage.cobertura.xml"
DIFF_JSON="$OUT_DIR/diff-cover.json"
DIFF_HTML="$OUT_DIR/diff-cover.html"

# ---------- run coverage ----------
run_dotnet_coverage() {
  if [[ $SKIP_TESTS -eq 1 && -f "$COVERAGE_XML" ]]; then
    LOG "--skip-tests: reusing $COVERAGE_XML"; return
  fi
  LOG "running: dotnet test --collect:\"XPlat Code Coverage\""
  rm -rf "$OUT_DIR/dotnet-results"
  dotnet test --collect:"XPlat Code Coverage" \
              --results-directory "$OUT_DIR/dotnet-results" \
              --nologo --verbosity quiet >&2 \
    || LOG "tests reported failures (continuing — coverage data still produced)"
  local found
  found=$(find "$OUT_DIR/dotnet-results" -name "coverage.cobertura.xml" -type f 2>/dev/null | head -n 1)
  [[ -n "$found" ]] || DIE "no coverage.cobertura.xml produced"
  cp "$found" "$COVERAGE_XML"
}

run_go_coverage() {
  if [[ $SKIP_TESTS -eq 1 && -f "$COVERAGE_XML" ]]; then
    LOG "--skip-tests: reusing $COVERAGE_XML"; return
  fi
  ensure_gocover_cobertura
  LOG "running: go test ./... -coverprofile"
  local profile="$OUT_DIR/coverage.out"
  go test ./... -coverprofile="$profile" -covermode=atomic >&2 \
    || LOG "tests reported failures (continuing — coverage data still produced)"
  [[ -f "$profile" ]] || DIE "no coverage.out produced"
  gocover-cobertura < "$profile" > "$COVERAGE_XML" || DIE "gocover-cobertura conversion failed"
}

case "$LANGUAGE" in
  dotnet) run_dotnet_coverage ;;
  go)     run_go_coverage ;;
esac

# ---------- run diff-cover ----------
ensure_diff_cover
LOG "computing diff coverage vs $BASE_BRANCH"
diff-cover "$COVERAGE_XML" \
  --compare-branch="$BASE_BRANCH" \
  --json-report "$DIFF_JSON" \
  --html-report "$DIFF_HTML" \
  >/dev/null 2>&1 || true   # non-zero when threshold fails; we read JSON instead

[[ -f "$DIFF_JSON" ]] || DIE "diff-cover did not produce JSON report"

# ---------- emit summary JSON ----------
python3 - "$DIFF_JSON" "$COVERAGE_XML" "$DIFF_HTML" "$LANGUAGE" "$BASE_BRANCH" <<'PY'
import json, sys, os

diff_json, cov_xml, diff_html, language, base = sys.argv[1:6]
with open(diff_json) as f:
    d = json.load(f)

total      = d.get("total_num_lines", 0)
violations = d.get("total_num_violations", 0)
covered    = max(total - violations, 0)
uncov_pct  = 0.0 if total == 0 else round(100 * violations / total, 2)

if total == 0:           verdict = "noop"
elif uncov_pct <= 10:    verdict = "pass"
elif uncov_pct <= 20:    verdict = "warn"
else:                    verdict = "fail"

files = []
src = d.get("src_stats", {})
if isinstance(src, dict):
    for path, stats in src.items():
        files.append({
            "path": path,
            "total_lines": stats.get("total_num_lines", 0),
            "uncovered_lines": stats.get("violation_lines", []),
            "percent_covered": stats.get("percent_covered", 0),
        })

print(json.dumps({
    "language":         language,
    "base_branch":      base,
    "coverage_xml":     os.path.abspath(cov_xml),
    "diff_report_json": os.path.abspath(diff_json),
    "diff_report_html": os.path.abspath(diff_html),
    "total_diff_lines": total,
    "covered_diff":     covered,
    "uncovered_diff":   violations,
    "uncovered_pct":    uncov_pct,
    "verdict":          verdict,
    "files":            files,
}, indent=2))
PY
