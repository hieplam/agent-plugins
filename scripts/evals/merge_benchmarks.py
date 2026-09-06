#!/usr/bin/env python3
"""Merge several benchmark.json files into one.

Why this exists
---------------
`compare.py` compares exactly one baseline file to one candidate file, and any
case present in only one side lands in its NOT COMPARED bucket. So when eval
cases are ADDED after a baseline has already been recorded, the choice would
otherwise be to re-run the whole (slow, paid) suite just to cover the new ids.

Merging lets you top up instead: run only the new `--eval-id`s, then fold that
benchmark into the existing one. The result is a single file covering every case,
which is what `compare.py` needs on each side.

Later files win on a `(skill_name, eval_id, configuration, run_number)` collision,
so re-running a case to replace a bad result does the obvious thing. Collisions
are reported, never silent — a surprise overwrite is how a baseline quietly stops
describing the prompts it claims to.

Usage
-----
    scripts/evals/merge_benchmarks.py -o merged/benchmark.json \\
        runs/baseline/benchmark.json runs/baseline-topup/benchmark.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def run_key(r: dict) -> tuple:
    return (r.get("skill_name"), r.get("eval_id"), r.get("configuration"), r.get("run_number"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="benchmark.json files, in precedence order (later wins)")
    ap.add_argument("-o", "--out", required=True, help="Path to write the merged benchmark.json")
    ap.add_argument("--label", default=None, help="Label for the merged result")
    args = ap.parse_args()

    merged: dict[tuple, dict] = {}
    subjects: dict[str, dict] = {}
    evals_run: list[dict] = []
    labels: list[str] = []
    collisions: list[tuple] = []
    metadata: dict = {}

    for raw in args.inputs:
        path = Path(raw)
        if not path.exists():
            print(f"ERROR: no such file: {path}", file=sys.stderr)
            return 2
        try:
            b = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"ERROR: {path} is not valid JSON: {e}", file=sys.stderr)
            return 2

        md = b.get("metadata", {})
        metadata = {**metadata, **{k: v for k, v in md.items() if k != "evals_run"}}
        labels.append(md.get("label") or path.parent.name)
        for entry in md.get("evals_run", []):
            evals_run.append(entry)
            subjects.update(entry.get("subjects", {}) or {})
        for r in b.get("runs", []):
            k = run_key(r)
            if k in merged:
                collisions.append(k)
            merged[k] = r

    if not merged:
        print("ERROR: no runs found across the given files.", file=sys.stderr)
        return 2

    out = {
        "metadata": {**metadata,
                     "label": args.label or " + ".join(labels),
                     "merged_from": [str(Path(p)) for p in args.inputs],
                     # One subjects map for the merged whole, so compare.py can
                     # still read prompt sizes off a merged baseline.
                     "evals_run": evals_run + [{"skill_name": "(merged)", "subjects": subjects}]},
        "runs": sorted(merged.values(), key=lambda r: (str(r.get("skill_name")),
                                                        r.get("eval_id") or 0,
                                                        str(r.get("configuration")),
                                                        r.get("run_number") or 0)),
        "run_summary": {},
        "setup_errors": [],
        "notes": ["merged by merge_benchmarks.py; run_summary not recomputed — use compare.py"],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))

    ids = sorted({r.get("eval_id") for r in out["runs"] if r.get("eval_id") is not None})
    print(f"Merged {len(args.inputs)} file(s) -> {out_path}")
    print(f"  {len(out['runs'])} run(s) across {len(ids)} case id(s): "
          f"{', '.join(str(i) for i in ids)}")
    if collisions:
        print(f"  {len(collisions)} collision(s) resolved by later-file-wins:")
        for skill, eid, conf, rn in sorted(set(collisions)):
            print(f"    {skill} eval {eid} [{conf}] run {rn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
