#!/usr/bin/env python3
"""Eval gate for the epic-loop files that steer a run: references/tick.md and references/drive.md.

A change of either file must come with a committed result (evals/gate-result.json)
that says: the candidate is not worse than the base on the skill's evals, and the
result was made for exactly these file contents.

Commands
--------
  check --baseline B --candidate C
      Print one line per eval, then the verdict. Exit 0 pass, 1 refuse, 2 error.
  write-result --baseline B --candidate C --out FILE [--skill-dir DIR]
      Write the gate result, only on a pass. Nothing is written on refuse or error.
  verify-result [--skill-dir DIR]
      Exit 1 when a guarded file changed since the gate ran, or the result is
      missing or not a pass. Needs no benchmark and no model call.

The statistics (spec 4, S6)
---------------------------
Each eval id has `bp` passes of `bt` graded runs at the baseline and `cp` of `ct`
at the candidate. A case is WORSE when the candidate rate is lower AND the chance
of a split this bad by luck is under 0.10. That chance is the hypergeometric
P(X <= cp) with N = bt + ct runs, K = bp + cp passes, and ct runs drawn. At 5 runs
a side: 5/5 -> 2/5 and 3/5 -> 0/5 are refused, 3/5 -> 1/5 is noise. A baseline
failure is no reason to refuse: 0/5 -> 0/5 passes. One WORSE case refuses the whole
gate, because an average hides a single lost behaviour.

A case with fewer than `min_runs` graded runs on a side, or present on one side
only, is an error: the gate cannot judge it. `ungraded` runs are skipped, as in
compare.py.

Hashes are taken over the file text with CRLF changed to LF, so a Windows
checkout with autocrlf gives the same hash as a Linux one.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from math import comb
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parents[1]
DEFAULT_SKILL_DIR = REPO_ROOT / "plugins" / "epic-loop" / "skills" / "epic-loop"
GUARDED = ("references/tick.md", "references/drive.md")
RESULT_REL = "evals/gate-result.json"
ALPHA = 0.10
RESULT_VERSION = 1

_spec = importlib.util.spec_from_file_location("compare", EVALS_DIR / "compare.py")
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


def side_counts(bench: dict) -> dict:
    """eval id -> [passed, graded] for the with_skill leg, clean arm; ungraded runs skipped."""
    out: dict = {}
    for (_skill, eval_id, _name, _agent), results in compare.index_runs(bench, "with_skill", "clean").items():
        p, t = out.get(eval_id, [0, 0])
        out[eval_id] = [p + sum(results), t + len(results)]
    return out


def chance_at_most(cp: int, ct: int, bp: int, bt: int) -> float:
    """Hypergeometric P(X <= cp): the chance that ct drawn runs hold at most cp passes by luck."""
    n, k = bt + ct, bp + cp
    lowest = max(0, ct - (n - k))
    total = comb(n, ct)
    return sum(comb(k, x) * comb(n - k, ct - x) for x in range(lowest, cp + 1)) / total


def decide(base: dict, cand: dict, min_runs: int = 5) -> dict:
    """Pure. Two benchmark.json dicts in, a verdict and one row per eval id out."""
    b, c = side_counts(base), side_counts(cand)
    cases: dict = {}
    for eval_id in sorted(set(b) | set(c), key=lambda i: (str(type(i)), i)):
        if eval_id not in b or eval_id not in c:
            side = "baseline" if eval_id not in b else "candidate"
            cases[eval_id] = {"baseline": b.get(eval_id), "candidate": c.get(eval_id), "status": "error",
                              "reason": f"no graded runs on the {side} side"}
            continue
        (bp, bt), (cp, ct) = b[eval_id], c[eval_id]
        row = {"baseline": [bp, bt], "candidate": [cp, ct]}
        if bt < min_runs or ct < min_runs:
            row.update(status="error", reason=f"under {min_runs} graded runs on a side")
        elif cp * bt < bp * ct and chance_at_most(cp, ct, bp, bt) < ALPHA:
            row.update(status="WORSE")
        else:
            row.update(status="ok")
        cases[eval_id] = row
    statuses = {r["status"] for r in cases.values()}
    verdict = "error" if "error" in statuses or not cases else "refuse" if "WORSE" in statuses else "pass"
    pooled = {"baseline": [0, 0], "candidate": [0, 0]}
    for r in cases.values():
        for name in pooled:
            if r[name]:
                pooled[name][0] += r[name][0]
                pooled[name][1] += r[name][1]
    return {"verdict": verdict, "cases": cases, "pooled": pooled}


def format_report(result: dict) -> str:
    lines = []
    for eval_id, r in result["cases"].items():
        bs = "none" if not r["baseline"] else f"{r['baseline'][0]}/{r['baseline'][1]}"
        cs = "none" if not r["candidate"] else f"{r['candidate'][0]}/{r['candidate'][1]}"
        tail = r["status"] if r["status"] != "error" else f"error: {r['reason']}"
        lines.append(f"eval {eval_id} {bs} -> {cs} {tail}")
    lines.append(f"verdict: {result['verdict']}")
    return "\n".join(lines)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def exit_code(verdict: str) -> int:
    return {"pass": 0, "refuse": 1}.get(verdict, 2)


def load_pair(args) -> tuple:
    return compare.load(Path(args.baseline)), compare.load(Path(args.candidate))


def cmd_check(args) -> int:
    result = decide(*load_pair(args))
    print(format_report(result))
    return exit_code(result["verdict"])


def cmd_write_result(args) -> int:
    result = decide(*load_pair(args))
    print(format_report(result))
    if result["verdict"] != "pass":
        return exit_code(result["verdict"])
    skill = Path(args.skill_dir)
    runs = min(min(r["baseline"][1], r["candidate"][1]) for r in result["cases"].values())
    doc = {
        "v": RESULT_VERSION,
        "files": {rel: file_hash(skill / rel) for rel in GUARDED},
        "runs": runs,
        "cases": {str(i): {"baseline": r["baseline"], "candidate": r["candidate"]}
                  for i, r in result["cases"].items()},
        "pooled": result["pooled"],
        "verdict": "pass",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.name}")
    return 0


def cmd_verify_result(args) -> int:
    skill = Path(args.skill_dir)
    path = skill / RESULT_REL
    if not path.exists():
        print(f"refuse: no gate result at {RESULT_REL}")
        return 1
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"refuse: {RESULT_REL} is not valid JSON")
        return 1
    refused = False
    if doc.get("verdict") != "pass":
        print(f"refuse: the gate result in {RESULT_REL} is not a pass")
        refused = True
    recorded = doc.get("files", {})
    for rel in GUARDED:
        target = skill / rel
        if rel not in recorded or not target.exists() or file_hash(target) != recorded[rel]:
            print(f"refuse: {rel} changed since the gate ran")
            refused = True
    if refused:
        return 1
    print("verify-result: ok")
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("write-result", cmd_write_result)):
        p = sub.add_parser(name)
        p.add_argument("--baseline", required=True, help="benchmark.json of the base")
        p.add_argument("--candidate", required=True, help="benchmark.json of the change")
        if name == "write-result":
            p.add_argument("--out", required=True)
            p.add_argument("--skill-dir", default=str(DEFAULT_SKILL_DIR))
        p.set_defaults(fn=fn)
    p = sub.add_parser("verify-result")
    p.add_argument("--skill-dir", default=str(DEFAULT_SKILL_DIR))
    p.set_defaults(fn=cmd_verify_result)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
