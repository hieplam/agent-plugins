"""Unit tests for scripts/evals/run_evals.py.

Stdlib `unittest` only (host python3 is 3.9.6) and no `claude -p` calls: every
function under test is pure, which is the point of the pure-core split.

    python3 -m unittest discover -s scripts/evals/tests -t .
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
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
                elif kind == "output-style":
                    for case in data["evals"]:
                        style = skill_dir / "output-styles" / f"{case['style']}.md"
                        self.assertTrue(
                            style.is_file(),
                            f"{data['skill_name']}: case {case['id']} names a missing style {style}",
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

    def test_reply_placeholder_resolves_to_the_hidden_reply_file_in_scratch(self):
        """A check that judges the reply text (term discipline) needs a path to it.
        It lives under a dot-directory so an absence check like `! ls *.md` and an
        `artifacts: ["*.md"]` glob never see it."""
        planned = run_evals.plan_checks(
            {"checks": [{"name": "c", "command": "bun t.ts --reply {reply}"}]},
            Path("/s"), Path("/tmp/scr"))
        self.assertEqual(planned[0]["argv"][-1], "/tmp/scr/.eval/reply.md")
        self.assertEqual(run_evals.reply_path(Path("/tmp/scr")),
                         Path("/tmp/scr/.eval/reply.md"))
        self.assertTrue(run_evals.REPLY_RELPATH.parts[0].startswith("."))


class RunCaseWritesTheReplyBeforeChecks(unittest.TestCase):
    """The executor's final reply must be on disk at `{reply}` BEFORE the checks run,
    or every reply-judging check reads a missing file and is misclassified
    CHECK_UNGRADED (exit 2) — the case silently drops out of the denominator instead
    of failing."""

    @staticmethod
    def _fake_run(*args, **kwargs):
        return {"ok": True, "wall_seconds": 0.01, "error": None, "events": [
            {"type": "result", "result": "The latch is a short lock.", "usage": {}}]}

    def test_check_sees_the_final_reply_text(self):
        # The check FAILS (exit 1) exactly when the reply text is present, which
        # short-circuits the grader — so no real `claude -p` is ever reached.
        case = {"id": 7, "name": "reply", "prompt": "p", "expected_output": "e",
                "checks": [{"name": "sees-reply",
                            "command": "sh -c '! grep -q \"short lock\" {reply}'"}]}
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: throwaway\n---\nbody\n")
            with mock.patch.object(run_evals, "run_claude", side_effect=self._fake_run):
                result = run_evals.run_case(
                    case, "skill", skill_dir, None, "with_skill", timeout=5,
                    exec_model=None, grader_model=None,
                    out_dir=Path(tmp) / "out", verbose=False)
        self.assertNotIn("error", result)
        self.assertEqual(result["result"]["passed"], 0)
        self.assertIn("sees-reply", result["expectations"][0]["evidence"])


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


TODD_WAY_EVALS = REPO_ROOT / "plugins" / "explaining" / "evals" / "evals.json"
TODD_WAY_STYLE = REPO_ROOT / "plugins" / "explaining" / "output-styles" / "todd-way.md"
BRIEF_TEMPLATE = (REPO_ROOT / "plugins" / "explaining" / "skills" / "explaining"
                  / "references" / "blind-reader-brief.md")


def _brief_region(text: str) -> str:
    """The rendered region of the blind-reader brief template, markers excluded."""
    return text.split("<!-- BRIEF-START -->")[1].split("<!-- BRIEF-END -->")[0].strip()


class OutputStyleKind(unittest.TestCase):
    """kind: "output-style" resolves and installs the way a real selection does."""

    def test_resolves_its_second_slot_to_the_plugin_root(self):
        kind, plugin_dir, agents_dir = run_evals.derive_kind_and_dirs(
            TODD_WAY_EVALS, "output-style")
        self.assertEqual(kind, "output-style")
        self.assertIsNone(agents_dir)
        self.assertEqual(plugin_dir, REPO_ROOT / "plugins" / "explaining")
        self.assertTrue((plugin_dir / "output-styles").is_dir())

    def test_install_writes_both_halves_of_the_real_selection(self):
        """A style is SELECTED, not merely made available: the file alone changes
        nothing without the settings key, which is what --setting-sources project
        reads. A fixture writing only the file would measure the baseline and
        label it with_skill."""
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            run_evals.install_output_style(scratch, TODD_WAY_STYLE, "Todd way")
            copied = scratch / ".claude" / "output-styles" / "todd-way.md"
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.read_text(), TODD_WAY_STYLE.read_text())
            settings = json.loads((scratch / ".claude" / "settings.json").read_text())
            self.assertEqual(settings, {"outputStyle": "Todd way"})

    def test_selection_uses_the_frontmatter_name_not_the_file_name(self):
        """Claude Code's precedence: the file name is the style name UNLESS the
        frontmatter sets `name`. todd-way.md sets "Todd way", so a fixture keyed
        on the file stem would select a style that does not exist."""
        fields, _ = run_evals.parse_frontmatter(TODD_WAY_STYLE)
        self.assertEqual(fields.get("name"), "Todd way")
        self.assertNotEqual(fields.get("name"), TODD_WAY_STYLE.stem)

    def test_subject_digests_records_the_style_file(self):
        cases = json.loads(TODD_WAY_EVALS.read_text())["evals"]
        digests = run_evals.subject_digests(
            "output-style", cases, None, REPO_ROOT / "plugins" / "explaining")
        self.assertIn("todd-way", digests)
        self.assertNotIn("error", digests["todd-way"])
        self.assertGreater(digests["todd-way"]["chars"], 0)


class ToddWayEvalsFixture(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(TODD_WAY_EVALS.read_text())
        self.cases = {c["name"]: c for c in self.data["evals"]}

    def test_declares_the_output_style_kind_and_one_style_per_case(self):
        self.assertEqual(self.data["kind"], "output-style")
        for case in self.data["evals"]:
            self.assertEqual(case["style"], "todd-way")

    def test_reuses_the_skills_memory_fixture_instead_of_duplicating_it(self):
        rel = self.data["memory_fixture"]
        self.assertTrue((TODD_WAY_EVALS.parent / rel).is_file())
        self.assertEqual(
            (TODD_WAY_EVALS.parent / rel).resolve(),
            (REPO_ROOT / "plugins/explaining/skills/explaining/evals"
                          "/memory-fixture/CLAUDE.md").resolve())

    def test_covers_both_registers_and_the_seam(self):
        """A combined style can regress in two directions — losing concision on
        operational replies, or losing depth on explanatory ones. One case pins
        each end, and one pins them holding at the same time."""
        self.assertIn("operational-register-stays-terse", self.cases)
        self.assertIn("explanatory-register-terms-introduced-and-claims-grounded", self.cases)
        self.assertIn("seam-explanatory-depth-without-losing-concision", self.cases)

    def test_operational_case_is_machine_checked_for_absent_artifacts(self):
        """The expensive failure mode of an always-on explanatory style is that it
        fires on operational questions too. That is checkable without a grader:
        no .html, no review log, no explanation.md should exist afterwards."""
        case = self.cases["operational-register-stays-terse"]
        command = case["checks"][0]["command"]
        for artifact in ("*.html", "*.review.jsonl", "explanation.md"):
            self.assertIn(artifact, command)
        self.assertNotIn("artifacts", case)

    def test_prompts_never_leak_the_behavior_under_test(self):
        for case in self.data["evals"]:
            prompt = case["prompt"].lower()
            for word in ("diagram", "mermaid", "html", "illustrate", "illustration",
                          "draw", "render", "concise", "blind reader", "brief",
                          "terse", "short", "define", "definition", "jargon",
                          "glossary", "acronym", "plain words", "best-effort"):
                self.assertNotIn(word, prompt,
                                  f"case {case['id']} prompt leaks {word!r}")

    def test_covers_the_jargon_edge_cases(self):
        """Where a model answers with bare jargon and the first fixture could not
        tell: the first sentence under 'lead with the result', a 'what does this
        mean' question that is explanatory in disguise, a PR description, a doc
        comment reaching for a label, a non-English reply borrowing English terms,
        an acronym-heavy domain, and the over-correction guard for a senior
        audience."""
        for name in ("first-sentence-leads-with-the-answer-without-bare-jargon",
                      "error-message-question-is-explanatory-in-disguise",
                      "pr-description-introduces-its-own-terms",
                      "doc-comment-names-the-behaviour-not-a-label",
                      "non-english-reply-introduces-borrowed-english-terms",
                      "acronym-heavy-domain-expands-every-acronym",
                      "senior-audience-no-padding-but-new-terms-still-introduced"):
            self.assertIn(name, self.cases)

    def test_every_explanatory_case_is_machine_checked_on_the_reply(self):
        """The LLM grader passed a baseline reply with ten bare terms as
        'contextualized in place' (2026-09-08). The floor is therefore a machine
        check on the REPLY text, not on files the executor chose to leave behind,
        for every case whose job is understanding."""
        operational = {"operational-register-stays-terse",
                       "doc-comment-names-the-behaviour-not-a-label",
                       "multi-actor-flow-illustrated"}
        for case in self.data["evals"]:
            if case["name"] in operational:
                continue
            commands = [c["command"] for c in case.get("checks", [])]
            self.assertTrue(any("check-term-discipline.ts" in c and "{reply}" in c
                                for c in commands),
                            f"case {case['id']} has no reply-level term check")

    def test_non_english_case_passes_its_own_definitional_cues(self):
        case = self.cases["non-english-reply-introduces-borrowed-english-terms"]
        command = case["checks"][0]["command"]
        self.assertIn("--cues", command)
        self.assertIn("là", command)

    def test_label_case_is_machine_checked_for_the_label_and_intact_code(self):
        case = self.cases["doc-comment-names-the-behaviour-not-a-label"]
        command = case["checks"][0]["command"]
        self.assertIn("best[- ]?effort", command)
        self.assertIn("^// persistSnapshot", command)
        self.assertIn("snapshot persist failed", command)

    def test_fixture_sources_exist(self):
        for case in self.data["evals"]:
            for entry in case.get("files", []):
                if "source" in entry:
                    self.assertTrue((REPO_ROOT / entry["source"]).is_file(),
                                    f"case {case['id']} fixture missing: {entry['source']}")

    def test_the_fixture_names_its_oracle(self):
        """Brief-contracts: a parser/heuristic check with no named oracle burns
        rounds. The fixture states what 'introduced' means and which direction of
        error is by design, once, for every case to inherit."""
        self.assertIn("oracle", self.data)
        self.assertIn("NOT an introduction", self.data["oracle"])

    def test_every_planned_check_argv_points_at_a_real_script(self):
        _, plugin_dir, _ = run_evals.derive_kind_and_dirs(
            TODD_WAY_EVALS, self.data.get("kind"))
        for case in self.data["evals"]:
            for planned in run_evals.plan_checks(case, plugin_dir, Path("/tmp/scratch")):
                target = planned["argv"][1]
                if target == "-c":  # `sh -c '...'` check, nothing on disk to point at
                    continue
                self.assertTrue(Path(target).is_file(),
                                 f"check points at a missing script: {planned['argv']}")

    def test_the_skills_own_fixture_is_left_alone(self):
        """The style is an addition, not a replacement: the skill keeps its own
        eval fixture, so a regression in either can still be attributed."""
        skill_data = json.loads(EXPLAINING_EVALS.read_text())
        self.assertEqual([c["id"] for c in skill_data["evals"]], [1, 2, 3, 4])
        self.assertNotEqual(skill_data.get("kind"), "output-style")


class ToddWayStyle(unittest.TestCase):
    def setUp(self):
        self.fields, self.body = run_evals.parse_frontmatter(TODD_WAY_STYLE)

    def test_frontmatter_matches_the_output_styles_contract(self):
        self.assertEqual(self.fields["name"], "Todd way")
        self.assertTrue(self.fields["description"])
        # The reader is still doing software engineering; only the response shape
        # changes. Omitting this would strip Claude Code's built-in engineering
        # instructions, which is a far bigger change than the one intended.
        self.assertEqual(self.fields["keep-coding-instructions"], "true")

    def test_carries_every_concise_rule(self):
        for rule in ("Lead with the result", "Cut narration, keep substance",
                      "Short by default", "State things plainly",
                      "Give full detail on request",
                      "Never trade correctness for brevity"):
            self.assertIn(rule, self.body, f"missing Concise rule: {rule!r}")

    def test_carries_every_explaining_rule(self):
        for marker in ("### B1 — Illustrate a flow instead of narrating it",
                        "### B2 — Term discipline: define before use",
                        "### B3 — Grounding: anchor every abstract claim",
                        "### B4 — Name the behaviour, not a concept",
                        "### B5 — Blind-reader review before delivery"):
            self.assertIn(marker, self.body, f"missing explaining rule: {marker!r}")

    def test_names_the_seam_that_resolves_the_two_conflicting_defaults(self):
        """Concise says cut; explaining says define and ground. Combining them
        without stating which wins where leaves the model to guess per turn, and
        the guess is what the seam case in the fixture measures."""
        self.assertIn("Only \"short by default\" yields", self.body)

    def test_closes_the_two_jargon_gaps_the_first_run_exposed(self):
        """Measured 2026-09-08 on the seam case: with the style on, the reply's
        first sentence dropped 'write-ahead log, buffer pool, vacuum' bare (Part A's
        'lead with the result' pulled jargon into sentence one), and the reply that
        summarised explanation.md re-dropped nine of eleven listed terms the file
        had defined. B2 now says both explicitly."""
        self.assertIn("The first sentence is not exempt", self.body)
        self.assertIn("Every channel the reader sees", self.body)

    def test_inlined_blind_reader_brief_is_identical_to_the_shipped_template(self):
        """The style inlines the brief as its no-plugin fallback, and
        check-review-log.ts asserts a rendered brief reproduces the shipped
        template verbatim. A drifted copy would fail that checker at review time,
        far from the edit that caused it — so the drift is caught here instead."""
        template = _brief_region(BRIEF_TEMPLATE.read_text())
        self.assertIn(template, self.body,
                       "the brief inlined in todd-way.md has drifted from "
                       "references/blind-reader-brief.md")

    def test_script_discovery_never_relies_on_a_shell_glob(self):
        """A non-matching glob aborts the whole command under zsh, so a glob in
        the discovery line breaks the discovery it exists to do — measured, not
        theorized: the glob form returned an empty path on this machine."""
        discovery = [ln for ln in self.body.splitlines() if "EXPLAINING=" in ln
                      or "explaining/skills/explaining" in ln]
        self.assertTrue(discovery, "no tooling-discovery block found")
        self.assertTrue(any("find " in ln for ln in discovery))
        self.assertFalse(any("cache/*" in ln for ln in discovery))


if __name__ == "__main__":
    unittest.main()
