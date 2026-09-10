"""Tests for install.sh — the symlink installer.

Stdlib `unittest` only (host python3 is 3.9.6), no network, no API calls:

    python3 -m unittest discover -s tests -t .

These run the REAL installer against a REAL empty target directory, because that is
the only shape that catches this class of defect. Two obligations, both earned:

  1. **Empty target.** `install.sh` assembles a tree in `$CLAUDE_DIR`; a test that
     pre-builds the directories it expects proves nothing about the `mkdir -p` the
     installer owes. Every case here starts from `mkdtemp()` and nothing else.
  2. **Both spellings of the path a person types.** The installer resolves its own
     location and is invoked with `$CLAUDE_DIR`; a suite that only ever passes
     absolute paths proves the installer works for absolute paths. One case types
     the relative form, from a different working directory.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "install.sh"


def run_installer(claude_dir, *args, cwd=None, timeout=60):
    """Impure edge: run install.sh with CLAUDE_DIR pointed at a throwaway target.

    timeout= is mandatory here for the same reason it is in tool code: a hung
    installer would stall the suite with no failing assertion to point at.
    """
    env = {**os.environ, "CLAUDE_DIR": str(claude_dir)}
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True, text=True, env=env,
        cwd=str(cwd) if cwd else None, timeout=timeout,
    )


class InstallsIntoAnEmptyTarget(unittest.TestCase):
    """The layout install.sh claims to produce, built from nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.target = Path(self._tmp.name) / "claude"  # deliberately absent
        self.addCleanup(self._tmp.cleanup)
        self.proc = run_installer(self.target, "explaining")

    def test_exits_clean_with_no_warnings(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertNotIn("WARN:", self.proc.stderr)
        self.assertIn("0 warning(s)", self.proc.stdout)

    def test_links_the_output_style_by_file_name(self):
        link = self.target / "output-styles" / "todd-way.md"
        self.assertTrue(link.is_symlink(), f"not a symlink: {link}")
        self.assertEqual(link.resolve(),
                         (REPO_ROOT / "plugins/explaining/output-styles/todd-way.md").resolve())

    def test_links_the_tools_directory_under_the_plugin_name(self):
        """The style is a system-prompt fragment with no relative path back to the
        repo, so its scripts must be reachable at a path the style names literally:
        `~/.claude/tools/explaining`."""
        link = self.target / "tools" / "explaining"
        self.assertTrue(link.is_symlink(), f"not a symlink: {link}")
        self.assertEqual(link.resolve(), (REPO_ROOT / "plugins/explaining/tools").resolve())

    def test_the_scripts_and_brief_the_style_names_are_reachable_through_the_link(self):
        """Every path the style hard-codes after `$EXPLAINING`, resolved through the
        installed link. A move that breaks one of these breaks B1 or B5 at runtime,
        where the only symptom is a fallback and a line of prose."""
        base = self.target / "tools" / "explaining"
        for rel in ("scripts/render-illustration.ts",
                    "scripts/validate-mermaid.ts",
                    "scripts/check-review-log.ts",
                    "scripts/check-term-discipline.ts",
                    "references/blind-reader-brief.md"):
            with self.subTest(rel=rel):
                self.assertTrue((base / rel).is_file(), f"unreachable through the link: {rel}")

    def test_links_the_design_system_beside_the_style(self):
        """The style sends every HTML page through the Reading design system at a
        literal path, `~/.claude/output-styles/design-system/`; install.sh is the only
        thing that creates it."""
        link = self.target / "output-styles" / "design-system"
        self.assertTrue(link.is_symlink(), f"not a symlink: {link}")
        self.assertEqual(link.resolve(),
                         (REPO_ROOT / "plugins/explaining/output-styles/design-system").resolve())

    def test_every_design_system_path_the_style_names_is_reachable_through_the_link(self):
        style = (REPO_ROOT / "plugins/explaining/output-styles/todd-way.md").read_text()
        named = sorted(set(re.findall(r"~/\.claude/output-styles/design-system/([\w.-]+)", style)))
        self.assertIn("specimen.html", named)
        for rel in named:
            with self.subTest(rel=rel):
                self.assertTrue((self.target / "output-styles" / "design-system" / rel).is_file(),
                                f"the style names design-system/{rel}, which is not installed")

    def test_the_design_system_carries_no_markdown(self):
        """Claude Code reads .md files under output-styles/ as styles; one inside the
        design system could surface as a second, bogus output style."""
        design = REPO_ROOT / "plugins/explaining/output-styles/design-system"
        self.assertEqual(sorted(p.name for p in design.rglob("*.md")), [])

    def test_the_fallback_template_wears_the_design_systems_colours(self):
        """The style's no-tooling fallback restates Reading's colour tokens inline. A
        retune of reading.css that misses the fallback would leave two looks."""
        style = (REPO_ROOT / "plugins/explaining/output-styles/todd-way.md").read_text()
        css = (REPO_ROOT / "plugins/explaining/output-styles/design-system/reading.css").read_text()
        fallback = style[style.index("**Fallback.**"):style.index("**Mermaid safe syntax.**")]
        for name, value in re.findall(r"--([a-z-]+):(#[0-9a-f]{6})", fallback):
            with self.subTest(token=name, value=value):
                self.assertIn(f"--{name}: {value};", css)

    def test_installs_no_skill_because_the_skill_is_archived(self):
        """The explaining rules now ship only as the output style. A `skills/`
        directory reappearing here means the archived skill was resurrected without
        anyone deciding to."""
        self.assertFalse((self.target / "skills" / "explaining").exists())

    def test_running_twice_relinks_nothing_and_backs_up_nothing(self):
        again = run_installer(self.target, "explaining")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already linked", again.stdout)
        self.assertIn("0 linked", again.stdout)
        self.assertEqual(list(self.target.rglob("*.bak.*")), [])


class InstallsWithARelativeTarget(unittest.TestCase):
    def test_relative_claude_dir_from_another_cwd(self):
        """The shape a person actually types: cd somewhere, name the directory. An
        absolute-only suite passes over a relative-path defect; this one does not."""
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_installer("claude", "explaining", cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            link = Path(tmp) / "claude" / "tools" / "explaining"
            self.assertTrue(link.is_symlink(), proc.stdout + proc.stderr)
            self.assertEqual(link.resolve(), (REPO_ROOT / "plugins/explaining/tools").resolve())


class BacksUpAConflictingTarget(unittest.TestCase):
    def test_a_foreign_file_is_moved_aside_not_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "claude"
            (target / "output-styles").mkdir(parents=True)
            victim = target / "output-styles" / "todd-way.md"
            victim.write_text("hand-written, must survive\n")

            proc = run_installer(target, "explaining")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            backups = list((target / "output-styles").glob("todd-way.md.bak.*"))
            self.assertEqual(len(backups), 1, f"no backup made: {list(target.rglob('*'))}")
            self.assertEqual(backups[0].read_text(), "hand-written, must survive\n")
            self.assertTrue(victim.is_symlink())


class ListsComponents(unittest.TestCase):
    def test_list_reports_the_tools_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_installer(Path(tmp) / "claude", "--list")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            line = next(l for l in proc.stdout.splitlines() if l.strip().startswith("- explaining"))
            self.assertIn("tools", line)
            self.assertIn("output-styles: 1", line)
            self.assertNotIn("skills:", line)


class EveryPluginInstallsCleanly(unittest.TestCase):
    def test_no_plugin_warns_about_an_unsupported_component(self):
        """The whitelist at the bottom of install_plugin is the repo's directory
        contract. A new top-level directory in any plugin must be added there
        deliberately, not discovered as a warning during someone's install."""
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_installer(Path(tmp) / "claude", timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            unsupported = [l for l in proc.stderr.splitlines() if "unsupported component" in l]
            self.assertEqual(unsupported, [])


class ArchivedSkillIsOutsideThePluginTree(unittest.TestCase):
    """`archive/` is not under `plugins/`, which is what keeps the archived skill out
    of the installer's reach AND out of the eval harness's fixture discovery."""

    def test_the_archived_skill_exists_and_is_not_a_plugin_component(self):
        archived = REPO_ROOT / "archive" / "skills" / "explaining" / "SKILL.md"
        self.assertTrue(archived.is_file(), "the archived skill went missing")
        self.assertFalse((REPO_ROOT / "plugins" / "explaining" / "skills").exists())

    def test_the_archived_skill_says_it_is_archived_and_names_its_replacement(self):
        text = (REPO_ROOT / "archive/skills/explaining/SKILL.md").read_text()
        head = text.split("---", 2)[-1][:1200]
        self.assertIn("ARCHIVED", head)
        self.assertIn("Todd way", head)


if __name__ == "__main__":
    unittest.main()
