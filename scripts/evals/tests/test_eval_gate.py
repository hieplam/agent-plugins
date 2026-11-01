"""Unit tests for scripts/evals/eval_gate.py (the eval gate for tick.md and drive.md).

Stdlib `unittest` only and no `claude -p` calls: the gate reads two finished
benchmark.json dicts, so every test builds them in memory.

    python3 -m unittest discover -s scripts/evals/tests -t . -p test_eval_gate.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EVALS_DIR.parents[1]
SKILL_DIR = REPO_ROOT / "plugins" / "epic-loop" / "skills" / "epic-loop"

_spec = importlib.util.spec_from_file_location("eval_gate", EVALS_DIR / "eval_gate.py")
eval_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_gate)


def bench(per_eval: dict, *, ungraded: dict | None = None) -> dict:
    """A benchmark.json dict. per_eval maps eval id -> (passed, total) of with_skill runs.

    `ungraded` maps eval id -> number of extra runs whose grader failed.
    """
    runs = []
    for eval_id, (passed, total) in per_eval.items():
        for i in range(total):
            runs.append({"skill_name": "epic-loop", "eval_id": eval_id, "eval_name": f"case-{eval_id}",
                         "configuration": "with_skill", "arm": "clean",
                         "result": {"passed": 1 if i < passed else 0, "total": 1}})
        for _ in range((ungraded or {}).get(eval_id, 0)):
            runs.append({"skill_name": "epic-loop", "eval_id": eval_id, "eval_name": f"case-{eval_id}",
                         "configuration": "with_skill", "arm": "clean",
                         "result": {"ungraded": 1, "total": 0}})
    return {"runs": runs}


def seven(first: tuple[int, int] = (5, 5), rest: tuple[int, int] = (5, 5)) -> dict:
    return {i: (first if i == 1 else rest) for i in range(1, 8)}


def run_cli(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = eval_gate.main(argv)
    return code, out.getvalue()


class Decide(unittest.TestCase):
    def verdict(self, base: dict, cand: dict) -> str:
        return eval_gate.decide(bench(base), bench(cand))["verdict"]

    def test_equal_passes(self):
        self.assertEqual(self.verdict(seven(), seven()), "pass")

    def test_better_passes(self):
        self.assertEqual(self.verdict(seven((2, 5)), seven((5, 5))), "pass")

    def test_five_of_five_to_two_of_five_is_refused(self):
        self.assertEqual(self.verdict(seven((5, 5)), seven((2, 5))), "refuse")

    def test_three_of_five_to_zero_of_five_is_refused(self):
        self.assertEqual(self.verdict(seven((3, 5)), seven((0, 5))), "refuse")

    def test_three_of_five_to_one_of_five_is_noise(self):
        self.assertEqual(self.verdict(seven((3, 5)), seven((1, 5))), "pass")

    def test_a_baseline_failure_is_no_reason_to_refuse(self):
        self.assertEqual(self.verdict(seven((0, 5)), seven((0, 5))), "pass")

    def test_one_worse_case_among_seven_is_refused(self):
        base, cand = seven(), seven()
        cand[4] = (2, 5)
        result = eval_gate.decide(bench(base), bench(cand))
        self.assertEqual(result["verdict"], "refuse")
        self.assertEqual([i for i, c in result["cases"].items() if c["status"] == "WORSE"], [4])

    def test_under_five_graded_runs_is_an_error(self):
        base = seven()
        base[3] = (4, 4)
        self.assertEqual(self.verdict(base, seven()), "error")

    def test_ungraded_runs_are_skipped(self):
        # 5 graded + 2 ungraded counts as 5 runs; 4 graded + 1 ungraded counts as 4 and is an error.
        ok = eval_gate.decide(bench(seven(), ungraded={2: 2}), bench(seven()))
        self.assertEqual(ok["verdict"], "pass")
        self.assertEqual(ok["cases"][2]["baseline"], [5, 5])
        short = eval_gate.decide(bench(seven(rest=(5, 5)) | {2: (4, 4)}, ungraded={2: 1}), bench(seven()))
        self.assertEqual(short["verdict"], "error")

    def test_a_case_on_one_side_only_is_an_error(self):
        cand = seven()
        del cand[6]
        self.assertEqual(self.verdict(seven(), cand), "error")


class VerifyResult(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eval-gate-r7-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def copy_skill(self) -> Path:
        dest = self.tmp / "skill"
        (dest / "references").mkdir(parents=True)
        (dest / "evals").mkdir()
        for name in ("tick.md", "drive.md"):
            shutil.copy(SKILL_DIR / "references" / name, dest / "references" / name)
        shutil.copy(SKILL_DIR / "evals" / "gate-result.json", dest / "evals" / "gate-result.json")
        return dest

    def test_verify_result_refuses_after_one_character_is_added(self):
        skill = self.copy_skill()
        with open(skill / "references" / "tick.md", "a", encoding="utf-8", newline="") as f:
            f.write("x")
        code, out = run_cli(["verify-result", "--skill-dir", str(skill)])
        self.assertEqual(code, 1)
        self.assertIn("refuse: references/tick.md changed since the gate ran", out)

    def test_verify_result_passes_on_the_committed_tree(self):
        code, out = run_cli(["verify-result", "--skill-dir", str(SKILL_DIR)])
        self.assertEqual((code, out.strip()), (0, "verify-result: ok"))


class WriteResult(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eval-gate-r7-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_bench(self, name: str, per_eval: dict) -> str:
        path = self.tmp / name
        path.write_text(json.dumps(bench(per_eval)))
        return str(path)

    def test_write_result_writes_no_file_on_refuse(self):
        base = self.write_bench("base.json", seven((5, 5)))
        cand = self.write_bench("cand.json", seven((2, 5)))
        out_file = self.tmp / "gate-result.json"
        code, _ = run_cli(["write-result", "--baseline", base, "--candidate", cand,
                           "--skill-dir", str(SKILL_DIR), "--out", str(out_file)])
        self.assertEqual(code, 1)
        self.assertFalse(out_file.exists())


if __name__ == "__main__":
    unittest.main()
