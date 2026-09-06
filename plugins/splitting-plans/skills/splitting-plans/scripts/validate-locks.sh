#!/usr/bin/env bash
# validate-locks.sh — mechanically check the AVAILABLE -> LOCKED -> DONE/BLOCKED gate across a
# splitting-plans bundle folder, instead of re-deriving lock consistency by prose reasoning
# every time a cook or the head chef reads the status board.
#
# Checks, per sub-plan bundle (`<N>-<title>.md` files with the §4.1 YAML frontmatter — bundle
# discovery matches any run of one-or-more leading digits, not a fixed two-digit width, so
# `1-foo.md` and a hypothetical `100-foo.md` (past bundle 99) are both picked up; the folder's
# non-bundle `README.md` orchestrator, which has no leading digit, is never picked up as a
# bundle):
#   - status is one of AVAILABLE | LOCKED | DONE | BLOCKED
#   - LOCKED requires non-empty locked_by + locked_at; a lock older than the staleness
#     threshold (default 30 minutes, same number the tribe's Shaman<->Warchief heartbeat uses)
#     is flagged stale
#   - AVAILABLE requires empty locked_by/locked_at (never been picked up)
#   - LOCKED/DONE requires every listed prereq bundle to itself be DONE
#   - owns_files is disjoint across all bundles (no two bundles claim the same file)
#
# Output: prints a JSON summary on stdout (only). Logs go to stderr.
# Exit codes: 0 = ran successfully (regardless of pass/fail); 2 = setup error.
#
# Usage:
#   validate-locks.sh <bundles-dir> [--threshold-minutes N]

set -euo pipefail

LOG() { printf '[validate-locks] %s\n' "$*" >&2; }
DIE() { LOG "ERROR: $*"; exit 2; }

BUNDLES_DIR=""
THRESHOLD_MINUTES=30
while [[ $# -gt 0 ]]; do
  case "$1" in
    --threshold-minutes) THRESHOLD_MINUTES="$2"; shift 2 ;;
    -h|--help)           sed -n '2,17p' "$0"; exit 0 ;;
    -*)                   DIE "unknown flag: $1" ;;
    *)
      if [[ -n "$BUNDLES_DIR" ]]; then DIE "unexpected extra argument: $1"; fi
      BUNDLES_DIR="$1"; shift ;;
  esac
done

[[ -n "$BUNDLES_DIR" ]] || DIE "usage: validate-locks.sh <bundles-dir> [--threshold-minutes N]"
[[ -d "$BUNDLES_DIR" ]] || DIE "bundles directory not found: $BUNDLES_DIR"
[[ -r "$BUNDLES_DIR" ]] || DIE "bundles directory not readable (permission denied?): $BUNDLES_DIR"
[[ "$THRESHOLD_MINUTES" =~ ^[0-9]+(\.[0-9]+)?$ ]] || DIE "invalid --threshold-minutes: '$THRESHOLD_MINUTES' (must be a non-negative number)"
command -v python3 >/dev/null 2>&1 || DIE "python3 is required but not on PATH"

python3 - "$BUNDLES_DIR" "$THRESHOLD_MINUTES" <<'PY'
import json, os, re, sys
from datetime import datetime, timezone

bundles_dir, threshold_minutes = sys.argv[1], float(sys.argv[2])

VALID_STATUSES = {"AVAILABLE", "LOCKED", "DONE", "BLOCKED"}

def parse_ts(raw):
    s = (raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def parse_frontmatter(path):
    """Minimal parser for the flat + one-level-list YAML frontmatter shape used by
    splitting-plans sub-plan templates (SKILL.md §4.1). Not a general YAML parser."""
    # A bundle file is an externally-supplied artifact (written by another cook/process), so an
    # I/O surprise (permission denied, vanished mid-scan, etc.) is a realistic per-file
    # condition, not a self-inflicted one. Report it the same way a malformed-frontmatter file
    # is reported — via parse_errors, so one unreadable bundle doesn't crash the whole run and
    # hide every other bundle's status — instead of letting an OSError traceback escape with
    # exit 1 and no JSON on stdout.
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return None, f"cannot read file: {e}"

    if not lines or lines[0].strip() != "---":
        return None, "missing frontmatter (no leading '---')"

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, "missing frontmatter (no closing '---')"

    def clean(raw):
        """Return (indent, comment-stripped-stripped-text) for a raw frontmatter line, or
        None if the line is blank/comment-only."""
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        if " #" in stripped:
            stripped = stripped.split(" #", 1)[0].rstrip()
        indent = len(line) - len(line.lstrip())
        return indent, stripped

    block = lines[1:end]
    fm = {}
    i, n = 0, len(block)
    while i < n:
        cur = clean(block[i])
        if cur is None:
            i += 1
            continue
        indent, stripped = cur

        # An orphan `- item` with no preceding scalar-turned-list key: ignore.
        if stripped.startswith("- "):
            i += 1
            continue

        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", stripped)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()

        if val == "":
            # Value-less scalar (`locked_by:`) vs. start of a nested list (`owns_files:`
            # followed by `- item`) look identical on this line alone. Disambiguate by
            # looking ahead: only a `- `-prefixed line indented deeper than this key
            # makes it a list; anything else (including EOF, a same/lower-indent line,
            # e.g. the next key) means the scalar's value is empty ("", falsy) rather
            # than the truthy `[]` a naive read would produce.
            items = []
            j = i + 1
            while j < n:
                nxt = clean(block[j])
                if nxt is None:
                    j += 1
                    continue
                nxt_indent, nxt_stripped = nxt
                if nxt_stripped.startswith("- ") and nxt_indent > indent:
                    items.append(nxt_stripped[2:].strip().strip('"\''))
                    j += 1
                    continue
                break
            if items:
                fm[key] = items
                i = j
                continue
            fm[key] = ""
            i += 1
            continue

        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            fm[key] = [v.strip().strip('"\'') for v in inner.split(",") if v.strip()] if inner else []
        else:
            fm[key] = val.strip('"\'')
        i += 1
    return fm, None

# Bundle filename shape per SKILL.md §4.1: one-or-more leading digits, a dash, a title, `.md`.
# A fixed-width glob like `[0-9][0-9]-*.md` would silently exclude `1-foo.md` (not zero-padded)
# or a hypothetical `100-foo.md` (past bundle 99) — either would drop straight to a false-clean
# `noop`/undercounted verdict rather than a loud failure, so match any digit-count instead.
BUNDLE_NAME_RE = re.compile(r"^\d+-.*\.md$")
try:
    all_names = os.listdir(bundles_dir)
except OSError as e:
    print(json.dumps({
        "bundles_dir": os.path.abspath(bundles_dir) if os.path.isdir(bundles_dir) else bundles_dir,
        "threshold_minutes": threshold_minutes,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "bundle_count": 0,
        "bundles": [],
        "parse_errors": [{"file": None, "error": f"cannot list bundles directory: {e}"}],
        "violations": [],
        "verdict": "error",
    }, indent=2))
    sys.exit(0)
bundle_paths = sorted(
    (os.path.join(bundles_dir, name) for name in all_names if BUNDLE_NAME_RE.match(name)),
    key=lambda p: (int(re.match(r"^(\d+)-", os.path.basename(p)).group(1)), os.path.basename(p)),
)
now = datetime.now(timezone.utc)

# `entries` keeps every parsed file (even ones sharing a `bundle:` id with another file), so a
# duplicate id can be flagged as its own violation instead of one file silently shadowing
# another. `bundles` is a first-seen-wins lookup by id, used only to resolve prereq references.
entries = []
bundles = {}
violations = []
parse_errors = []

for path in bundle_paths:
    name = os.path.basename(path)
    fm, err = parse_frontmatter(path)
    if err:
        parse_errors.append({"file": name, "error": err})
        continue
    bundle_id = str(fm.get("bundle", "")).strip() or os.path.splitext(name)[0]
    entries.append({"bundle": bundle_id, "file": name, "fm": fm})
    bundles.setdefault(bundle_id, {"file": name, "fm": fm})

by_id = {}
for entry in entries:
    by_id.setdefault(entry["bundle"], []).append(entry["file"])
for bundle_id, names in by_id.items():
    if len(names) > 1:
        violations.append({"bundle": bundle_id, "file": None, "type": "duplicate_bundle_id",
                            "detail": f"bundle id {bundle_id!r} declared by multiple files: {sorted(names)} "
                                       "— only one is used for prereq resolution, fix the `bundle:` field"})

for entry in entries:
    bundle_id, fm, name = entry["bundle"], entry["fm"], entry["file"]
    status = str(fm.get("status", "")).strip()
    locked_by = str(fm.get("locked_by", "")).strip()
    locked_at_raw = str(fm.get("locked_at", "")).strip()
    prereqs = fm.get("prereqs", [])
    if isinstance(prereqs, str):
        prereqs = [prereqs] if prereqs else []

    if status not in VALID_STATUSES:
        violations.append({"bundle": bundle_id, "file": name, "type": "invalid_status",
                            "detail": f"status={status!r} not in {sorted(VALID_STATUSES)}"})
        continue

    if status == "LOCKED":
        if not locked_by or not locked_at_raw:
            violations.append({"bundle": bundle_id, "file": name, "type": "incomplete_lock",
                                "detail": "LOCKED requires non-empty locked_by and locked_at"})
        else:
            locked_at = parse_ts(locked_at_raw)
            if locked_at is None:
                violations.append({"bundle": bundle_id, "file": name, "type": "unparseable_locked_at",
                                    "detail": f"locked_at={locked_at_raw!r}"})
            else:
                age_minutes = round((now - locked_at).total_seconds() / 60, 2)
                if age_minutes > threshold_minutes:
                    violations.append({"bundle": bundle_id, "file": name, "type": "stale_lock",
                                        "detail": f"locked_by={locked_by!r} locked_at={locked_at_raw!r} "
                                                   f"age_minutes={age_minutes} threshold_minutes={threshold_minutes}"})

    if status == "AVAILABLE" and (locked_by or locked_at_raw):
        violations.append({"bundle": bundle_id, "file": name, "type": "available_but_locked_fields_set",
                            "detail": f"locked_by={locked_by!r} locked_at={locked_at_raw!r}"})

    if status in ("LOCKED", "DONE"):
        for prereq in prereqs:
            prereq_entry = bundles.get(str(prereq).strip())
            if prereq_entry is None:
                violations.append({"bundle": bundle_id, "file": name, "type": "unknown_prereq",
                                    "detail": f"prereq={prereq!r} has no matching bundle file"})
                continue
            prereq_status = str(prereq_entry["fm"].get("status", "")).strip()
            if prereq_status != "DONE":
                violations.append({"bundle": bundle_id, "file": name, "type": "prereq_not_done",
                                    "detail": f"prereq={prereq!r} status={prereq_status!r} "
                                               f"(must be DONE before {bundle_id} can be {status})"})

# owns_files disjointness across ALL bundle files (including duplicate-id ones) regardless of status
owner_of = {}
for entry in entries:
    owns = entry["fm"].get("owns_files", [])
    if isinstance(owns, str):
        owns = [owns] if owns else []
    for f in owns:
        owner_of.setdefault(f, []).append(entry["file"])

for f, owners in owner_of.items():
    if len(set(owners)) > 1:
        violations.append({"bundle": None, "file": None, "type": "owns_files_overlap",
                            "detail": f"file={f!r} claimed by files {sorted(set(owners))}"})

if parse_errors:
    verdict = "error"
elif not entries:
    verdict = "noop"
elif violations:
    verdict = "fail"
else:
    verdict = "pass"

print(json.dumps({
    "bundles_dir": os.path.abspath(bundles_dir),
    "threshold_minutes": threshold_minutes,
    "checked_at": now.isoformat(),
    "bundle_count": len(entries),
    "bundles": [{"bundle": e["bundle"], "file": e["file"], "status": e["fm"].get("status")} for e in entries],
    "parse_errors": parse_errors,
    "violations": violations,
    "verdict": verdict,
}, indent=2))
PY
