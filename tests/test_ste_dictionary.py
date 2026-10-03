"""Tests for Part C of the Todd way style — the Simplified Technical English rules and the
dictionary that grows from real misreadings.

The dictionary is edited by a model in a later session, not by a person at a desk. These
tests are the contract that model's edit must keep: the two tables keep their columns, a
word sits in one table at most once, and the protocol still names the file the installed
style resolves to. Stdlib `unittest` only:

    python3 -m unittest discover -s tests -t .
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STYLE = REPO_ROOT / "plugins/explaining/output-styles/todd-way.md"

USE_HEADER = ["Word", "The one meaning", "Not for (say instead)", "Evidence"]
AVOID_HEADER = ["Word", "Write instead", "Evidence"]


def section(text, heading):
    """The body under a Markdown heading, up to the next heading of the same or higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading + "\n") + len(heading) + 1
    stop = re.compile(r"^#{1,%d} " % level, re.M).search(text, start)
    return text[start:stop.start() if stop else len(text)]


def table_rows(body):
    """Each Markdown table line in `body` as a list of trimmed cells, header and divider included."""
    rows = []
    for line in body.splitlines():
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


class DictionaryKeepsItsShape(unittest.TestCase):
    def setUp(self):
        self.style = STYLE.read_text(encoding="utf-8")
        self.use = table_rows(section(self.style, "#### Use"))
        self.avoid = table_rows(section(self.style, "#### Avoid"))

    def test_both_tables_keep_their_header(self):
        self.assertEqual(self.use[0], USE_HEADER)
        self.assertEqual(self.avoid[0], AVOID_HEADER)

    def test_every_row_has_as_many_cells_as_its_header(self):
        """A pipe inside a cell, or a dropped column, shifts every cell after it."""
        for name, rows, header in (("Use", self.use, USE_HEADER), ("Avoid", self.avoid, AVOID_HEADER)):
            for row in rows[2:]:
                with self.subTest(table=name, word=row[0]):
                    self.assertEqual(len(row), len(header), row)

    def test_every_row_carries_dated_evidence(self):
        """The protocol admits only a barrier that happened: a date and the words that caused it."""
        for row in self.use[2:] + self.avoid[2:]:
            with self.subTest(word=row[0]):
                self.assertRegex(row[-1], r"^\d{4}-\d{2}-\d{2}: \S")

    def test_a_word_appears_once_across_both_tables(self):
        words = [row[0].strip("`").lower() for row in self.use[2:] + self.avoid[2:]]
        self.assertEqual(len(words), len(set(words)), words)

    def test_the_seed_entry_is_done(self):
        """The one entry carried over from the 2026-10-03 transcript analysis."""
        self.assertIn("done", [row[0].strip("`") for row in self.use[2:]])


class ProtocolNamesTheFileToEdit(unittest.TestCase):
    def test_protocol_resolves_the_installed_link_to_its_source(self):
        """install.sh links the style; editing the link's target is editing the repo file.
        The protocol must say to resolve the link, or a model writes a copy that the next
        install overwrites."""
        style = STYLE.read_text(encoding="utf-8")
        self.assertIn("realpath ~/.claude/output-styles/todd-way.md", style)

    def test_part_c_applies_to_both_registers(self):
        style = STYLE.read_text(encoding="utf-8")
        self.assertIn("## Part C — Plain technical English (both registers)", style)


if __name__ == "__main__":
    unittest.main()
