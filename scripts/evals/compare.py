#!/usr/bin/env python3
"""Compare two benchmark.json runs — the regression tripwire for a prompt trim.

Why this exists
---------------
`run_evals.py`'s own roll-up answers "does the agent beat vanilla Claude?"
(with_skill vs without_skill). A prompt-trim run asks a different question:
"did editing this prompt change how the agent behaves?" That is a comparison
between two runs of the SAME cases against DIFFERENT prompt text, and nothing
in the harness read it until now.

What counts as a breach
-----------------------
A case that passed at baseline and fails on the candidate. Not a suite-average
dip: averages hide a single agent's regression behind twenty other cases, and
the whole point of a tripwire is that it halts on the specific loss.

Sampling noise is reported, never hidden
----------------------------------------
A single graded run is one sample of a stochastic model, so one flipped case at
`--runs 1` is not proof of a regression. This script grades each case by how many
of its N runs passed and separates two outcomes it would be dishonest to merge:

  CONFIRMED   passed every baseline run, failed every candidate run
  UNSTABLE    flipped, but inconsistently across runs — the case is noisy, and
              at --runs 1 EVERY flip lands here, because one sample cannot tell
              a regression from variance

Exit codes: 0 clean, 1 regression (CONFIRMED present), 2 setup error.
UNSTABLE alone exits 0 but always prints — it means "re-run with more --runs",
not "you are fine".

Usage
-----
    scripts/evals/compare.py --baseline runs/A/benchmark.json \\
                             --candidate runs/B/benchmark.json
    scripts/evals/compare.py --baseline A/benchmark.json \\
                             --candidate B/benchmark.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"ERROR: no such benchmark.json: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: {path} is not valid JSON: {e}")


def index_runs(bench: dict, configuration: str, arm: str = "clean") -> dict[tuple, list[bool]]:
    """(skill, eval_id, eval_name, agent) -> [passed?] across that case's runs.

    A run whose result is `ungraded` (run_evals.py's grader subprocess failed or
    returned unparseable JSON — a harness failure, not a verdict) is skipped
    entirely rather than folded in as `bool(None)` -> False. Reading it as a fail
    would misattribute a harness outage to the agent/prompt being compared, and
    could even manufacture a CONFIRMED regression out of a grader hiccup. It is
    a missing sample, exactly like a case present in only one of the two runs.

    Filtered on `arm` (default "clean", mirroring the runner's own default) so a
    single benchmark.json holding both the clean and mem arms never blends a
    case's clean and mem with_skill runs into one average — that would turn a
    mem-arm suppression into a fake regression in a prompt A/B that never
    touched the mem arm at all. A run with no "arm" key at all (every
    benchmark.json written before the arm axis existed) counts as "clean", so
    every pre-existing benchmark still compares.
    """
    out: dict[tuple, list[bool]] = defaultdict(list)
    for r in bench.get("runs", []):
        if r.get("configuration") != configuration:
            continue
        if r.get("arm", "clean") != arm:
            continue
        if r.get("result", {}).get("ungraded"):
            continue
        key = (r.get("skill_name"), r.get("eval_id"), r.get("eval_name"), r.get("agent"))
        out[key].append(bool(r.get("result", {}).get("passed")))
    return out


def subjects_of(bench: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in bench.get("metadata", {}).get("evals_run", []):
        out.update(entry.get("subjects", {}) or {})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True, help="benchmark.json for the committed prompts")
    ap.add_argument("--candidate", required=True, help="benchmark.json for the trimmed prompts")
    ap.add_argument("--configuration", default="with_skill",
                    choices=["with_skill", "without_skill"],
                    help="Which leg to compare (default: with_skill — the one a trim moves)")
    ap.add_argument("--arm", default="clean", choices=["clean", "mem"],
                    help="Which ambient-memory arm to compare (default: clean, mirroring the "
                         "runner's own default). A run with no 'arm' key (pre-arm-axis "
                         "benchmark.json) counts as clean.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    base_b, cand_b = load(Path(args.baseline)), load(Path(args.candidate))
    base = index_runs(base_b, args.configuration, args.arm)
    cand = index_runs(cand_b, args.configuration, args.arm)

    if not base or not cand:
        print(f"ERROR: no '{args.configuration}' runs in "
              f"{'baseline' if not base else 'candidate'} — nothing to compare.", file=sys.stderr)
        return 2

    b_subj_pre, c_subj_pre = subjects_of(base_b), subjects_of(cand_b)
    confirmed, unstable, improved, control_flips = [], [], [], []
    per_agent: dict[str, dict] = defaultdict(lambda: {"b_pass": 0, "b_tot": 0,
                                                       "c_pass": 0, "c_tot": 0})
    only_in_one = sorted({k[:3] for k in set(base) ^ set(cand)})

    for key in sorted(set(base) & set(cand), key=lambda k: (str(k[0]), k[1] or 0)):
        _, eval_id, eval_name, agent = key
        b, c = base[key], cand[key]
        b_rate, c_rate = sum(b) / len(b), sum(c) / len(c)
        a = agent or "(skill)"
        per_agent[a]["b_pass"] += sum(b); per_agent[a]["b_tot"] += len(b)
        per_agent[a]["c_pass"] += sum(c); per_agent[a]["c_tot"] += len(c)

        # A verdict can only be attributed to a prompt edit if the prompt actually
        # changed. An agent whose text is byte-identical across the two runs is a
        # CONTROL: any flip it shows is run-to-run variance by construction, and
        # measuring that gives an empirical noise floor to judge the rest against.
        unchanged = (b_subj_pre.get(a, {}).get("sha256")
                     == c_subj_pre.get(a, {}).get("sha256") is not None)
        row = {"eval_id": eval_id, "eval_name": eval_name, "agent": a,
               "baseline": f"{sum(b)}/{len(b)}", "candidate": f"{sum(c)}/{len(c)}",
               "prompt_unchanged": unchanged}

        if c_rate != b_rate and unchanged:
            # Cannot be caused by the edit — record it as noise, never as a verdict.
            control_flips.append(row)
        elif b_rate == 1.0 and c_rate == 0.0 and len(b) >= 2 and len(c) >= 2:
            # CONFIRMED needs repeats on BOTH sides. With a single run per side a
            # 1/1 -> 0/1 flip is one sample against one sample, which cannot
            # distinguish a regression from variance no matter how clean it looks.
            confirmed.append(row)
        elif c_rate < b_rate:
            unstable.append(row)
        elif c_rate > b_rate:
            improved.append(row)

    b_subj, c_subj = b_subj_pre, c_subj_pre
    size_rows = []
    for name in sorted(set(b_subj) | set(c_subj)):
        bc = b_subj.get(name, {}).get("chars", 0)
        cc = c_subj.get(name, {}).get("chars", 0)
        same_hash = (b_subj.get(name, {}).get("sha256")
                     == c_subj.get(name, {}).get("sha256") is not None)
        size_rows.append({"agent": name, "baseline_chars": bc, "candidate_chars": cc,
                          "delta_chars": cc - bc,
                          "pct": round(100.0 * (cc - bc) / bc, 1) if bc else None,
                          "unchanged": same_hash})

    report = {
        "baseline_label": base_b.get("metadata", {}).get("label"),
        "candidate_label": cand_b.get("metadata", {}).get("label"),
        "configuration": args.configuration,
        "arm": args.arm,
        "cases_compared": len(set(base) & set(cand)),
        "cases_only_in_one_run": [list(x) for x in only_in_one],
        "confirmed_regressions": confirmed,
        "unstable_flips": unstable,
        "improvements": improved,
        "control_flips": control_flips,
        "runs_per_side": {"baseline": max((len(v) for v in base.values()), default=0),
                          "candidate": max((len(v) for v in cand.values()), default=0)},
        "per_agent": {a: {"baseline": f"{v['b_pass']}/{v['b_tot']}",
                          "candidate": f"{v['c_pass']}/{v['c_tot']}"}
                      for a, v in sorted(per_agent.items())},
        "prompt_size": size_rows,
        "verdict": "REGRESSION" if confirmed else
                   ("REVIEW" if (unstable or control_flips) else "CLEAN"),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 1 if confirmed else 0

    bl = report["baseline_label"] or "(unlabeled)"
    cl = report["candidate_label"] or "(unlabeled)"
    print(f"Baseline : {bl}  ({args.baseline})")
    print(f"Candidate: {cl}  ({args.candidate})")
    print(f"Configuration: {args.configuration}   Arm: {args.arm}   "
          f"cases compared: {report['cases_compared']}\n")

    if size_rows:
        print("Prompt size")
        print(f"  {'agent':<12} {'base':>9} {'cand':>9} {'delta':>10} {'pct':>8}")
        for r in size_rows:
            note = "  (identical text)" if r["unchanged"] else ""
            pct = f"{r['pct']:+.1f}%" if r["pct"] is not None else "n/a"
            print(f"  {r['agent']:<12} {r['baseline_chars']:>9,} {r['candidate_chars']:>9,} "
                  f"{r['delta_chars']:>+10,} {pct:>8}{note}")
        bt = sum(r["baseline_chars"] for r in size_rows)
        ct = sum(r["candidate_chars"] for r in size_rows)
        print(f"  {'TOTAL':<12} {bt:>9,} {ct:>9,} {ct - bt:>+10,} "
              f"{(100.0 * (ct - bt) / bt if bt else 0):>+7.1f}%\n")

    print("Pass rate by agent")
    for a, v in report["per_agent"].items():
        flag = ""
        bp, bt_ = (int(x) for x in v["baseline"].split("/"))
        cp, ct_ = (int(x) for x in v["candidate"].split("/"))
        if bt_ and ct_ and (cp / ct_) < (bp / bt_):
            flag = "   <-- down"
        print(f"  {a:<12} baseline {v['baseline']:>7}   candidate {v['candidate']:>7}{flag}")
    print()

    if confirmed:
        print(f"CONFIRMED REGRESSIONS ({len(confirmed)}) — passed every baseline run, "
              f"failed every candidate run:")
        for r in confirmed:
            print(f"  [{r['agent']}] eval {r['eval_id']}: {r['eval_name']}"
                  f"  {r['baseline']} -> {r['candidate']}")
        print()
    if unstable:
        print(f"UNSTABLE ({len(unstable)}) — flipped, but not consistently. At --runs 1 every "
              f"flip lands here;\nre-run these with --runs 3 to tell a regression from variance:")
        for r in unstable:
            print(f"  [{r['agent']}] eval {r['eval_id']}: {r['eval_name']}"
                  f"  {r['baseline']} -> {r['candidate']}")
        print()
    if improved:
        print(f"IMPROVED ({len(improved)}):")
        for r in improved:
            print(f"  [{r['agent']}] eval {r['eval_id']}: {r['eval_name']}"
                  f"  {r['baseline']} -> {r['candidate']}")
        print()
    if control_flips:
        print(f"CONTROL FLIPS ({len(control_flips)}) — the NOISE FLOOR. These agents' prompts are\n"
              f"byte-identical across both runs, so these flips CANNOT be caused by the edit:")
        for r in control_flips:
            print(f"  [{r['agent']}] eval {r['eval_id']}: {r['eval_name']}"
                  f"  {r['baseline']} -> {r['candidate']}")
        print("  Treat any same-sized effect on the EDITED prompt as indistinguishable from this.\n")
    if only_in_one:
        print(f"NOT COMPARED ({len(only_in_one)}) — present in only one run "
              f"(a --eval-id mismatch, or a case that errored):")
        for skill, eid, name in only_in_one:
            print(f"  {skill} eval {eid}: {name}")
        print()

    rps = report["runs_per_side"]
    single = rps["baseline"] < 2 or rps["candidate"] < 2
    print(f"VERDICT: {report['verdict']}"
          f"   (runs per side: baseline {rps['baseline']}, candidate {rps['candidate']})")
    if report["verdict"] == "REGRESSION":
        print("  The trim broke behavior the committed prompt held. Restore what was cut.")
    elif report["verdict"] == "REVIEW":
        print("  No CONFIRMED regression. Flips are present but not yet separable from variance.")
    else:
        print("  No case that passed at baseline fails on the candidate.")
    if single:
        print("  NOTE: with <2 runs per side nothing can be CONFIRMED — one sample cannot be")
        print("        distinguished from variance. Re-run with --runs 3 to decide.")
    return 1 if confirmed else 0


if __name__ == "__main__":
    sys.exit(main())
