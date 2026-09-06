"""Unit tests for scripts/evals/run_evals.py.

Stdlib `unittest` only (host python3 is 3.9.6) and no `claude -p` calls: every
function under test is pure, which is the point of the pure-core split.

    python3 -m unittest discover -s scripts/evals/tests -t .
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

EVALS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EVALS_DIR.parents[1]

_spec = importlib.util.spec_from_file_location("run_evals", EVALS_DIR / "run_evals.py")
run_evals = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_evals)


class SubjectResolution(unittest.TestCase):
    """Every fixture in the repo must resolve to a subject that actually exists.

    The `explaining` fixture used to resolve to `plugins/explaining` (the plugin
    root, no SKILL.md there), so its with_skill leg silently compared baseline to
    baseline. Discovery goes through the runner's own discover_evals_json() so a
    future fixture placed wrongly fails here without anyone updating a list.
    """

    def test_every_evals_json_resolves_to_a_real_subject(self):
        paths = run_evals.discover_evals_json()
        self.assertGreaterEqual(len(paths), 4, "fixture discovery found suspiciously few files")
        for evals_path in paths:
            with self.subTest(evals=str(evals_path.relative_to(REPO_ROOT))):
                data = json.loads(evals_path.read_text())
                kind, skill_dir, agents_dir = run_evals.derive_kind_and_dirs(
                    evals_path, data.get("kind"))
                if kind == "skill":
                    self.assertTrue(
                        (skill_dir / "SKILL.md").is_file(),
                        f"{data['skill_name']}: resolved skill_dir {skill_dir} has no SKILL.md",
                    )
                else:
                    self.assertTrue(
                        agents_dir.is_dir(),
                        f"{data['skill_name']}: resolved agents_dir {agents_dir} does not exist",
                    )


class FixtureSourceResolution(unittest.TestCase):
    def test_resolves_repo_relative_path(self):
        resolved = run_evals.resolve_fixture_source("plugins/explaining/README.md", REPO_ROOT)
        self.assertTrue(resolved.is_file())
        self.assertTrue(str(resolved).startswith(str(REPO_ROOT)))

    def test_rejects_absolute_path(self):
        with self.assertRaises(ValueError):
            run_evals.resolve_fixture_source("/etc/passwd", REPO_ROOT)

    def test_rejects_escape_above_repo_root(self):
        with self.assertRaises(ValueError):
            run_evals.resolve_fixture_source("../../../etc/passwd", REPO_ROOT)

    def test_rejects_non_string_source(self):
        """A plausible authoring typo (e.g. "source": 123) must raise the ValueError
        run_case's guard already names, not a bare TypeError from os.path.isabs()."""
        with self.assertRaises(ValueError):
            run_evals.resolve_fixture_source(123, REPO_ROOT)

    def test_rejects_symlink_loop(self):
        """A symlink loop reachable from `source` must raise, not hang/crash uncaught
        with a Python-version-specific exception type (RuntimeError on 3.9, OSError
        with errno ELOOP on 3.10+)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a", root / "b"
            try:
                a.symlink_to(b)
                b.symlink_to(a)
            except (OSError, NotImplementedError):
                self.skipTest("platform cannot create symlinks")
            with self.assertRaises((RuntimeError, OSError)):
                run_evals.resolve_fixture_source("a/x", root)


class MaterializeFilesWithSource(unittest.TestCase):
    def test_writes_repo_file_contents_into_scratch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            written = run_evals.materialize_files(
                scratch, [{"path": "explaining-README.md",
                           "source": "plugins/explaining/README.md"}])
            self.assertEqual(written, ["explaining-README.md"])
            self.assertEqual(
                (scratch / "explaining-README.md").read_text(),
                (REPO_ROOT / "plugins/explaining/README.md").read_text(),
            )

    def test_source_and_content_are_mutually_exclusive(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_evals.materialize_files(
                    Path(tmp),
                    [{"path": "a.md", "source": "plugins/explaining/README.md", "content": "x"}])

    def test_missing_source_file_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                run_evals.materialize_files(
                    Path(tmp), [{"path": "a.md", "source": "plugins/nope/nothing.md"}])


class RunCaseFixtureSetupGuard(unittest.TestCase):
    """run_case's fixture-setup guard must swallow ANY fixture-setup exception into
    the {"error": ...} signal, never let one escape.

    run_case is driven by pool.map(execute, jobs) inside a ThreadPoolExecutor with no
    enclosing handler up to main(), and benchmark.json is only written after that loop
    completes — an exception escaping run_case discards every already-completed case's
    results, each backed by a real, paid `claude -p` subprocess call. A narrow except
    clause that misses a plausible fixture-authoring mistake (a non-string `source`, a
    symlink loop reachable from `source`) turns one bad case into a total-loss batch.
    """

    def test_non_string_source_becomes_setup_error_not_a_raised_exception(self):
        case = {"id": 1, "name": "x", "prompt": "p", "expected_output": "e",
                "files": [{"path": "a.md", "source": 123}]}
        result = run_evals.run_case(
            case, "skill", None, None, "with_skill", timeout=1, exec_model=None,
            grader_model=None, out_dir=Path("/tmp/unused-run-case-guard-test"), verbose=False)
        self.assertIn("error", result)
        self.assertIn("fixture setup failed", result["error"])

    def test_symlink_loop_source_becomes_setup_error_not_a_raised_exception(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a", root / "b"
            try:
                a.symlink_to(b)
                b.symlink_to(a)
            except (OSError, NotImplementedError):
                self.skipTest("platform cannot create symlinks")

            original_repo_root = run_evals.REPO_ROOT
            run_evals.REPO_ROOT = root
            try:
                case = {"id": 2, "name": "y", "prompt": "p", "expected_output": "e",
                        "files": [{"path": "a.md", "source": "a/x"}]}
                result = run_evals.run_case(
                    case, "skill", None, None, "with_skill", timeout=1, exec_model=None,
                    grader_model=None, out_dir=Path("/tmp/unused-run-case-guard-test"),
                    verbose=False)
            finally:
                run_evals.REPO_ROOT = original_repo_root

            self.assertIn("error", result)
            self.assertIn("fixture setup failed", result["error"])


class RunCaseCheckSetupGuard(unittest.TestCase):
    """The check machinery (plan_checks/run_checks) and collect_artifacts must not let
    an exception escape run_case either, exactly like the fixture/memory guards above —
    the same total-batch-loss failure mode (pool.map with no enclosing handler up to
    main(), benchmark.json written only after the whole loop finishes), one call
    further down: a malformed `checks` entry (missing "command", or one that resolves
    to an empty/blank command, or one with an unbalanced quote) or an absolute
    `artifacts` glob pattern must degrade into this case's {"error": ...} setup-error
    signal rather than raise past the already-paid `claude -p` executor call.
    """

    @staticmethod
    def _fake_ok_run(*args, **kwargs):
        return {"ok": True, "events": [], "error": None, "wall_seconds": 0.01}

    def _run_with_case(self, case):
        """Drive run_case for kind='skill' against a throwaway skill dir (a real
        SKILL.md is required upstream of the check-setup guard, at
        `parse_frontmatter(skill_dir / "SKILL.md")`), with run_claude stubbed so no
        real `claude -p` subprocess is spawned."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp)
            (skill_dir / "SKILL.md").write_text("---\nname: throwaway\n---\nbody\n")
            with mock.patch.object(run_evals, "run_claude", side_effect=self._fake_ok_run):
                return run_evals.run_case(
                    case, "skill", skill_dir, None, "with_skill", timeout=1,
                    exec_model=None, grader_model=None,
                    out_dir=Path("/tmp/unused-run-case-check-guard-test"), verbose=False)

    def test_missing_command_key_becomes_setup_error_not_a_raised_exception(self):
        case = {"id": 1, "name": "x", "prompt": "p", "expected_output": "e",
                "checks": [{"name": "c"}]}
        result = self._run_with_case(case)
        self.assertIn("error", result)
        self.assertIn("check setup failed", result["error"])

    def test_empty_resolving_command_becomes_setup_error_not_a_raised_exception(self):
        case = {"id": 2, "name": "y", "prompt": "p", "expected_output": "e",
                "checks": [{"name": "c", "command": ""}]}
        result = self._run_with_case(case)
        self.assertIn("error", result)
        self.assertIn("check setup failed", result["error"])

    def test_unbalanced_quote_command_becomes_setup_error_not_a_raised_exception(self):
        case = {"id": 3, "name": "z", "prompt": "p", "expected_output": "e",
                "checks": [{"name": "c", "command": "echo it's broken"}]}
        result = self._run_with_case(case)
        self.assertIn("error", result)
        self.assertIn("check setup failed", result["error"])

    def test_absolute_artifact_pattern_becomes_setup_error_not_a_raised_exception(self):
        case = {"id": 4, "name": "w", "prompt": "p", "expected_output": "e",
                "artifacts": ["/x.html"]}
        result = self._run_with_case(case)
        self.assertIn("error", result)
        self.assertIn("check setup failed", result["error"])


class ArmPlanning(unittest.TestCase):
    def test_mem_arm_runs_with_skill_leg_only(self):
        self.assertEqual(
            run_evals.plan_arm_configurations("mem", ("with_skill", "without_skill")),
            ("with_skill",))

    def test_clean_arm_runs_both_legs(self):
        self.assertEqual(
            run_evals.plan_arm_configurations("clean", ("with_skill", "without_skill")),
            ("with_skill", "without_skill"))

    def test_clean_arm_asserts_no_memory_and_writes_none(self):
        plan = run_evals.plan_memory_files("clean", Path("/x/CLAUDE.md"))
        self.assertEqual(plan["memory_files"], [])
        self.assertTrue(plan["assert_no_memory"])

    def test_mem_arm_writes_the_fixture_as_claude_md(self):
        plan = run_evals.plan_memory_files("mem", Path("/x/CLAUDE.md"))
        self.assertEqual(plan["memory_files"], [("CLAUDE.md", Path("/x/CLAUDE.md"))])
        self.assertFalse(plan["assert_no_memory"])

    def test_mem_arm_without_a_fixture_is_skipped_with_a_note(self):
        jobs, notes = run_evals.plan_jobs(
            cases=[{"id": 1}], configurations=("with_skill", "without_skill"),
            arms=("clean", "mem"), runs=1, has_memory_fixture=False)
        self.assertEqual([j["arm"] for j in jobs], ["clean", "clean"])
        self.assertEqual(len(notes), 1)
        self.assertIn("memory_fixture", notes[0])

    def test_both_arms_produce_three_cells_when_a_fixture_exists(self):
        jobs, notes = run_evals.plan_jobs(
            cases=[{"id": 1}], configurations=("with_skill", "without_skill"),
            arms=("clean", "mem"), runs=1, has_memory_fixture=True)
        self.assertEqual(
            sorted((j["arm"], j["configuration"]) for j in jobs),
            [("clean", "with_skill"), ("clean", "without_skill"), ("mem", "with_skill")])
        self.assertEqual(notes, [])

    def test_runs_multiplies_every_cell(self):
        jobs, _ = run_evals.plan_jobs(
            cases=[{"id": 1}], configurations=("with_skill",),
            arms=("clean",), runs=3, has_memory_fixture=False)
        self.assertEqual([j["run_idx"] for j in jobs], [0, 1, 2])

    def test_mem_arm_with_only_without_skill_requested_is_skipped_with_a_note(self):
        # The mem arm only ever runs the with_skill leg (plan_arm_configurations).
        # Requesting --mode without_skill together with --arm mem intersects to
        # ZERO configurations for the mem arm even though a memory_fixture exists —
        # this must be an honest, noted skip (like the no-fixture case), never a
        # silent zero-job vanish.
        jobs, notes = run_evals.plan_jobs(
            cases=[{"id": 1}], configurations=("without_skill",),
            arms=("mem",), runs=1, has_memory_fixture=True)
        self.assertEqual(jobs, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("mem", notes[0])
        self.assertIn("with_skill", notes[0])


class ArmRollup(unittest.TestCase):
    @staticmethod
    def _run(arm, pass_rate, configuration="with_skill"):
        return {"arm": arm, "configuration": configuration,
                "result": {"pass_rate": pass_rate, "passed": int(pass_rate), "ungraded": 0,
                            "total": 1, "time_seconds": 1.0, "tokens": 10}}

    def test_delta_is_mem_minus_clean_on_the_with_skill_leg(self):
        by_arm = run_evals.summarize_with_skill_by_arm(
            [self._run("clean", 1.0), self._run("mem", 0.0),
             self._run("clean", 0.0, "without_skill")])
        self.assertEqual(by_arm["clean"]["pass_rate"]["mean"], 1.0)
        self.assertEqual(by_arm["mem"]["pass_rate"]["mean"], 0.0)
        delta = run_evals.arm_delta(by_arm)
        self.assertEqual(delta["pass_rate"], "-1.00")

    def test_delta_is_none_when_one_arm_is_missing(self):
        by_arm = run_evals.summarize_with_skill_by_arm([self._run("clean", 1.0)])
        self.assertIsNone(run_evals.arm_delta(by_arm))


class CompareArmFiltering(unittest.TestCase):
    def setUp(self):
        _cspec = importlib.util.spec_from_file_location(
            "compare", EVALS_DIR / "compare.py")
        self.compare = importlib.util.module_from_spec(_cspec)
        _cspec.loader.exec_module(self.compare)

    @staticmethod
    def _bench(runs):
        return {"runs": runs}

    @staticmethod
    def _run(arm, passed):
        entry = {"skill_name": "s", "eval_id": 1, "eval_name": "n", "agent": None,
                  "configuration": "with_skill", "result": {"passed": passed}}
        if arm is not None:
            entry["arm"] = arm
        return entry

    def test_filters_to_the_requested_arm(self):
        bench = self._bench([self._run("clean", True), self._run("mem", False)])
        clean = self.compare.index_runs(bench, "with_skill", "clean")
        self.assertEqual(list(clean.values()), [[True]])
        mem = self.compare.index_runs(bench, "with_skill", "mem")
        self.assertEqual(list(mem.values()), [[False]])

    def test_a_run_with_no_arm_key_counts_as_clean(self):
        bench = self._bench([self._run(None, True)])
        self.assertEqual(
            list(self.compare.index_runs(bench, "with_skill", "clean").values()), [[True]])


class CheckOutcomes(unittest.TestCase):
    def test_zero_is_pass(self):
        self.assertEqual(run_evals.classify_check_outcome(0), run_evals.CHECK_PASS)

    def test_one_is_a_behavioral_fail(self):
        self.assertEqual(run_evals.classify_check_outcome(1), run_evals.CHECK_FAIL)

    def test_two_is_could_not_check_not_a_fail(self):
        self.assertEqual(run_evals.classify_check_outcome(2), run_evals.CHECK_UNGRADED)

    def test_any_other_code_is_could_not_check(self):
        for rc in (3, 127, -9):
            self.assertEqual(run_evals.classify_check_outcome(rc), run_evals.CHECK_UNGRADED)


class CheckPlanning(unittest.TestCase):
    def test_substitutes_skill_dir_and_scratch(self):
        planned = run_evals.plan_checks(
            {"checks": [{"name": "c", "command": "bun {skill_dir}/v.ts --out {scratch}/x"}]},
            Path("/s/kill"), Path("/tmp/scr"))
        self.assertEqual(planned, [{"name": "c",
                                     "argv": ["bun", "/s/kill/v.ts", "--out", "/tmp/scr/x"]}])

    def test_no_checks_declared_plans_nothing(self):
        self.assertEqual(run_evals.plan_checks({}, Path("/s"), Path("/t")), [])

    def test_glob_token_is_left_literal_for_the_check_to_expand(self):
        planned = run_evals.plan_checks(
            {"checks": [{"name": "c", "command": "bun v.ts --html-glob *.html"}]},
            Path("/s"), Path("/t"))
        self.assertEqual(planned[0]["argv"][-1], "*.html")

    def test_skill_dir_with_a_space_stays_one_argv_token(self):
        """Plain str.replace before shlex.split (no quoting) tokenizes a space-bearing
        skill_dir/scratch path into wrong argv, so the check runs the wrong command —
        likely a FileNotFoundError silently misclassified CHECK_UNGRADED, masking a
        real pass/fail. The substituted path must stay one argv token."""
        planned = run_evals.plan_checks(
            {"checks": [{"name": "c", "command": "bun {skill_dir}/v.ts"}]},
            Path("/Users/John Doe/skill"), Path("/tmp/scr"))
        self.assertEqual(planned, [{"name": "c",
                                     "argv": ["bun", "/Users/John Doe/skill/v.ts"]}])


class ArtifactCollection(unittest.TestCase):
    def test_copies_matching_files_and_ignores_the_rest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            scratch, dest = Path(tmp) / "s", Path(tmp) / "d"
            (scratch / "sub").mkdir(parents=True)
            (scratch / "a.html").write_text("A")
            (scratch / "b.txt").write_text("B")
            collected = run_evals.collect_artifacts(scratch, ["*.html"], dest)
            self.assertEqual(collected, ["a.html"])
            self.assertEqual((dest / "a.html").read_text(), "A")
            self.assertFalse((dest / "b.txt").exists())

    def test_no_patterns_collects_nothing_and_creates_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            scratch, dest = Path(tmp) / "s", Path(tmp) / "d"
            scratch.mkdir()
            self.assertEqual(run_evals.collect_artifacts(scratch, [], dest), [])
            self.assertFalse(dest.exists())

    def test_dot_dot_escaping_pattern_never_writes_outside_dest(self):
        """`target = dest / src.relative_to(scratch)` is lexical (no resolution), so a
        pattern containing ".." (a case-authoring typo, e.g. "../*.html") yields a
        src whose relative_to(scratch) is "../"-prefixed and copies SILENTLY outside
        dest. Mirrors resolve_fixture_source's repo-root confinement: collect_artifacts
        must never write outside dest."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scratch = root / "run" / "scratch"
            scratch.mkdir(parents=True)
            outside_file = root / "run" / "secret.html"
            outside_file.write_text("LEAKED")
            dest = scratch / "artifacts"

            collected = run_evals.collect_artifacts(scratch, ["../*.html"], dest)

            self.assertEqual(collected, [])
            # No file exists anywhere under root, other than the original
            # pre-existing outside_file itself, that isn't confined to dest.
            for path in root.rglob("*"):
                if path.is_file() and path.resolve() != outside_file.resolve():
                    self.assertTrue(
                        str(path.resolve()).startswith(str(dest.resolve()) + "/")
                        or path.resolve() == dest.resolve(),
                        f"artifact landed outside dest: {path}")


MEMORY_FIXTURE = (REPO_ROOT
                  / "plugins/explaining/skills/explaining/evals/memory-fixture/CLAUDE.md")

# The mem arm measures whether realistic ambient memory SUPPRESSES the behavior. If the
# fixture names the vocabulary of the behavior it would seed it instead, and the arm
# would measure the fixture rather than the skill. Following the zero-lexical-overlap
# meta-test kept alongside this fixture in its origin repo.
BANNED_VOCABULARY = ("mermaid", "diagram", "illustration", "illustrate", "chart",
                     "graph", "visual", "html", "render", "draw", "picture", "image")


class MemoryFixture(unittest.TestCase):
    def test_exists(self):
        self.assertTrue(MEMORY_FIXTURE.is_file(), f"missing fixture: {MEMORY_FIXTURE}")

    def test_never_mentions_the_illustration_vocabulary(self):
        import re as _re
        text = MEMORY_FIXTURE.read_text().lower()
        hits = [w for w in BANNED_VOCABULARY if _re.search(rf"\b{w}\w*", text)]
        self.assertEqual(hits, [], f"memory fixture leaks the answer: {hits}")

    def test_is_substantial_enough_to_be_realistic_ambient_memory(self):
        self.assertGreater(len(MEMORY_FIXTURE.read_text().split()), 80)


EXPLAINING_EVALS = (REPO_ROOT
                    / "plugins/explaining/skills/explaining/evals/evals.json")


class ExplainingIllustrationCase(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(EXPLAINING_EVALS.read_text())
        self.case = next(c for c in self.data["evals"]
                          if c["name"] == "tribe-overall-flow-illustrated")

    def test_fixture_declares_its_memory_fixture(self):
        self.assertEqual(self.data["memory_fixture"], "memory-fixture/CLAUDE.md")
        self.assertTrue(
            (EXPLAINING_EVALS.parent / self.data["memory_fixture"]).is_file())

    def test_uses_the_real_tribe_readme_by_source_not_an_inlined_copy(self):
        self.assertEqual(self.case["files"],
                          [{"path": "tribe-README.md",
                            "source": "plugins/explaining/skills/explaining/"
                                      "evals/fixtures/tribe-README.md"}])

    def test_prompt_never_asks_for_the_artifact(self):
        prompt = self.case["prompt"].lower()
        for word in ("diagram", "mermaid", "html", "chart", "picture", "image",
                      "illustrate", "illustration", "draw", "visual", "render"):
            self.assertNotIn(word, prompt,
                              f"prompt leaks the behavior under test: {word!r}")

    def test_declares_a_machine_check_and_collects_the_artifact(self):
        self.assertEqual(len(self.case["checks"]), 1)
        command = self.case["checks"][0]["command"]
        self.assertIn("{skill_dir}", command)
        self.assertIn("validate-mermaid.ts", command)
        self.assertEqual(self.case["artifacts"], ["*.html"])

    def test_the_planned_check_argv_points_at_a_real_script(self):
        _, skill_dir, _ = run_evals.derive_kind_and_dirs(
            EXPLAINING_EVALS, self.data.get("kind"))
        planned = run_evals.plan_checks(self.case, skill_dir, Path("/tmp/scratch"))
        self.assertTrue(Path(planned[0]["argv"][1]).is_file(),
                         f"check points at a missing script: {planned[0]['argv']}")

    def test_existing_cases_are_untouched(self):
        self.assertEqual([c["id"] for c in self.data["evals"]][:3], [1, 2, 3])

    def test_expected_output_is_gradeable_from_the_transcript_alone(self):
        """The grader (run_evals.grade()) only ever sees parsed["transcript"] and
        parsed["final_result"] — extract_metrics() never captures a tool_result's
        content, so text read from tribe-README.md via the agent's Read call never
        reaches the grader (GRADER_INSTRUCTIONS even says "you have no tools —
        judge only from the text given below"). expected_output must not ask the
        grader to fact-check claims against a source it is never shown (F22); it
        must still require the deterministic .html/mermaid artifact."""
        expected_output = self.case["expected_output"]
        self.assertNotIn("anchored in what tribe-README.md", expected_output)
        self.assertIn("self-contained .html file", expected_output)
        self.assertIn('class="mermaid"', expected_output)


if __name__ == "__main__":
    unittest.main()
