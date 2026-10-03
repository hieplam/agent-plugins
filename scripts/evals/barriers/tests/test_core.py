"""Unit tests for scripts/evals/barriers/core.py — the pure core of the barrier eval.

Stdlib `unittest` only, no `claude -p` calls:

    python3 -m unittest discover -s scripts/evals/barriers/tests -t .
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

BARRIERS_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("barrier_core", BARRIERS_DIR / "core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def reask_case(**overrides):
    case = {
        "id": "vi-ymir-qmd", "tier": "reask", "in_sample": False, "category": "term", "language": "vi",
        "source": {"kind": "history", "session": "s-1", "date": "2026-06-17", "project": "ymir"},
        "workspace": None, "files": [{"path": "PR-10.md", "content": "Adds qmd search."}],
        "message": "giải thích cho tao pr này", "note": "The PR is saved as PR-10.md here.",
        "follow_up": "qmd là gì?", "flaw": "it used the tool name qmd without saying what qmd is",
        "terms": ["qmd"], "must_cover": ["what the PR changes"],
    }
    case.update(overrides)
    return case


def replay_case(**overrides):
    case = {
        "id": "en-tribe-done", "tier": "replay", "in_sample": True, "category": "status", "language": "en",
        "source": {"kind": "transcript", "session": "s-2", "date": "2026-09-30", "project": "tribe"},
        "conversation": [{"role": "user", "text": "build the card"}, {"role": "assistant", "text": "Planning now."}],
        "message": "<task-notification>T9 passed</task-notification>",
        "work": [{"role": "tool_call", "text": "Bash: gh pr view 206"}, {"role": "tool_result", "text": "MERGED"}],
        "original_reply": "T9 passed its Done commands. That was the last task.",
        "follow_up": "all done?", "flaw": "it never said whether the card was done by the owner's definition",
    }
    case.update(overrides)
    return case


class CaseValidation(unittest.TestCase):
    def test_sound_cases_pass(self):
        self.assertEqual(core.validate_case(reask_case()), [])
        self.assertEqual(core.validate_case(replay_case()), [])

    def test_missing_fields_are_named(self):
        case = reask_case()
        del case["follow_up"]
        self.assertEqual(core.validate_case(case), ["missing field(s): follow_up"])

    def test_in_sample_false_is_not_missing(self):
        self.assertEqual(core.validate_case(reask_case(in_sample=False)), [])

    def test_enums_are_enforced(self):
        errors = core.validate_case(reask_case(tier="rebuilt", category="vibes", language="fr"))
        self.assertEqual(len(errors), 3, errors)

    def test_replay_needs_the_original_reply(self):
        self.assertIn("a replay case needs original_reply: the reply the follow-up reacted to",
                      core.validate_case(replay_case(original_reply="")))

    def test_reask_cannot_carry_a_conversation(self):
        errors = core.validate_case(reask_case(conversation=[{"role": "user", "text": "x"}]))
        self.assertIn("a reask case has no conversation or work: only the message survives", errors)

    def test_file_paths_must_stay_inside_the_folder(self):
        for path in ("../etc/passwd", "/abs/path.md", "a/../../b", "C:\\x"):
            with self.subTest(path=path):
                errors = core.validate_case(reask_case(files=[{"path": path, "content": "x"}]))
                self.assertTrue(any("must be relative" in e for e in errors), errors)

    def test_the_flaw_must_not_reach_the_session(self):
        case = reask_case(note="Hint: it used the tool name qmd without saying what qmd is")
        self.assertIn("the flaw text appears in what the session sees: the answer key leaks",
                      core.validate_case(case))

    def test_a_long_follow_up_must_not_reach_the_session(self):
        follow_up = "explain more with surrounding context please, I am lost"
        case = reask_case(follow_up=follow_up, files=[{"path": "a.md", "content": f"notes: {follow_up}"}])
        self.assertIn("the follow-up appears in what the session sees: the answer key leaks", core.validate_case(case))

    def test_a_short_follow_up_may_repeat_in_the_conversation(self):
        """'all done?' asked earlier in the same session is history, not a leak."""
        case = replay_case(conversation=[{"role": "user", "text": "all done?"}])
        self.assertEqual(core.validate_case(case), [])

    def test_workspace_needs_a_hex_commit(self):
        errors = core.validate_case(reask_case(workspace={"repo": "ai-dict", "commit": "main"}))
        self.assertIn("workspace must be null or {repo, commit} with a hex commit", errors)


class SessionPrompt(unittest.TestCase):
    def test_reask_is_the_owners_message_plus_the_note(self):
        self.assertEqual(core.build_session_prompt(reask_case()),
                         "giải thích cho tao pr này\n\nThe PR is saved as PR-10.md here.")

    def test_replay_shows_conversation_message_and_work_but_not_the_answer_key(self):
        prompt = core.build_session_prompt(replay_case())
        self.assertIn("[user]\nbuild the card", prompt)
        self.assertIn("<newest_message>\n<task-notification>T9 passed</task-notification>\n</newest_message>", prompt)
        self.assertIn("[tool call]\nBash: gh pr view 206", prompt)
        for secret in ("all done?", "owner's definition", "That was the last task"):
            self.assertNotIn(secret, prompt)


class SessionCommand(unittest.TestCase):
    def test_the_session_cannot_reach_outside_its_folder(self):
        argv = core.build_session_command("claude-opus-5-5", "medium", 5)
        joined = " ".join(argv)
        self.assertIn("--tools Read,Glob,Grep,Write,Edit,Agent", joined)
        self.assertIn("--permission-prompts none", joined)
        self.assertIn("--setting-sources project", joined)
        self.assertIn("--no-session-persistence", joined)
        self.assertNotIn("bypassPermissions", joined)
        self.assertNotIn("dangerously", joined)
        self.assertNotIn("Bash", argv[argv.index("--tools") + 1])

    def test_model_and_effort_are_pinned(self):
        argv = core.build_session_command("claude-opus-5-5", "medium", 5)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")

    def test_the_prompt_is_not_an_argument(self):
        """A message starting with '-' must never be parsed as a flag: the prompt goes on stdin."""
        argv = core.build_session_command("claude-opus-5-5", "medium", 5)
        self.assertEqual(argv[-2:], ["--max-budget-usd", "5"])

    def test_the_judge_has_no_tools_and_no_customization(self):
        argv = core.build_judge_command("claude-sonnet-5-5", "medium")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--safe-mode", argv)
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), core.JUDGE_SCHEMA)


def stream(*events):
    return [json.dumps(e) for e in events]


INIT = {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "output_style": "Todd way", "tools": ["Read"]}


class StreamParsing(unittest.TestCase):
    def test_reply_is_the_main_threads_text_without_subagent_text(self):
        parsed = core.parse_stream(stream(
            INIT,
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Not done: T10 left."}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Agent", "input": {}}]}},
            {"type": "assistant", "parent_tool_use_id": "t1",
             "message": {"content": [{"type": "text", "text": "READER: PASS"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Waiting on: the runner."}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": "Waiting on: the runner.",
             "total_cost_usd": 0.4, "duration_ms": 9000},
        ))
        self.assertEqual(parsed["reply"], "Not done: T10 left.\n\nWaiting on: the runner.")
        self.assertEqual(parsed["tool_uses"], {"Agent": 1})
        self.assertEqual(core.classify_session(parsed, "Todd way", "claude-opus-5-5"), ("ok", ""))

    def test_garbage_lines_are_skipped(self):
        parsed = core.parse_stream(["not json", "", json.dumps(INIT)])
        self.assertEqual(parsed["init"]["output_style"], "Todd way")

    def test_a_rate_limited_session_is_not_an_error(self):
        parsed = core.parse_stream(stream(INIT, {"type": "result", "is_error": True, "subtype": "success",
                                                 "result": "You've hit your session limit · resets 2am"}))
        self.assertEqual(core.classify_session(parsed, "Todd way", "claude-opus-5-5")[0], "rate_limited")

    def test_a_session_under_the_wrong_style_is_a_breach(self):
        parsed = core.parse_stream(stream(
            dict(INIT, output_style="default"),
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"type": "result", "is_error": False, "result": "hi"}))
        status, reason = core.classify_session(parsed, "Todd way", "claude-opus-5-5")
        self.assertEqual(status, "isolation_breach")
        self.assertIn("'default'", reason)

    def test_no_result_event_is_an_error(self):
        self.assertEqual(core.classify_session(core.parse_stream(stream(INIT)), "Todd way", "x")[0], "error")


class Judge(unittest.TestCase):
    def test_the_prompt_carries_the_answer_key_and_the_reply(self):
        prompt = core.build_judge_prompt(reask_case(), "PR này thêm qmd, một công cụ tìm kiếm…")
        self.assertIn("<follow_up>\nqmd là gì?\n</follow_up>", prompt)
        self.assertIn("it used the tool name qmd without saying what qmd is", prompt)
        self.assertIn("- what the PR changes", prompt)
        self.assertIn("PR này thêm qmd", prompt)
        self.assertIn("File the assistant could read, `PR-10.md`", prompt)

    def test_the_prompt_never_names_an_arm(self):
        prompt = core.build_judge_prompt(reask_case(), "reply")
        for arm_word in ("Todd way", "output style", "Part C", "arm"):
            self.assertNotIn(arm_word, prompt)

    def test_replay_context_includes_the_work_done(self):
        prompt = core.build_judge_prompt(replay_case(), "Done. PR #206 is merged.")
        self.assertIn("[tool result]\nMERGED", prompt)

    def test_structured_output_is_read(self):
        payload = {"structured_output": {"follow_up_needed": False, "evidence": "says Done",
                                         "answers_message": True, "wrong_claims": []}}
        verdict = core.parse_verdict(payload)
        self.assertTrue(core.is_win(verdict))

    def test_json_inside_result_text_is_read(self):
        payload = {"result": 'Here: {"follow_up_needed": true, "evidence": "x", "answers_message": true, "wrong_claims": []}'}
        self.assertFalse(core.is_win(core.parse_verdict(payload)))

    def test_a_malformed_verdict_is_refused(self):
        for payload in ({"result": "no json"}, {"structured_output": {"follow_up_needed": "no"}}, ["x"]):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    core.parse_verdict(payload)

    def test_a_wrong_claim_or_an_unanswered_message_loses(self):
        base = {"follow_up_needed": False, "evidence": "", "answers_message": True, "wrong_claims": []}
        self.assertTrue(core.is_win(base))
        self.assertFalse(core.is_win(dict(base, wrong_claims=["PR #9 is merged"])))
        self.assertFalse(core.is_win(dict(base, answers_message=False)))
        self.assertFalse(core.is_win(dict(base, follow_up_needed=True)))

    def test_the_stub_prompt_asks_for_exactly_the_flaw(self):
        prompt = core.build_stub_prompt(reask_case())
        self.assertIn("exactly this flaw: it used the tool name qmd", prompt)
        self.assertNotIn("qmd là gì?", prompt)


class Metrics(unittest.TestCase):
    def test_language_detection(self):
        self.assertEqual(core.detect_language("PR này thêm công cụ tìm kiếm cho wiki của dự án."), "vi")
        self.assertEqual(core.detect_language("This PR adds a search tool, called qmd, to the wiki."), "en")

    def test_bare_labels_counts_only_labels_without_a_gloss(self):
        self.assertEqual(core.bare_labels("N1, N2 and R2-1 are as I said. N3 (run install.sh) is new."), 3)

    def test_status_line(self):
        case = replay_case()
        self.assertTrue(core.reply_metrics("**Not done:** T10 is left.", case)["status_line"])
        self.assertTrue(core.reply_metrics("Xong. PR #2 đã merge.", case)["status_line"])
        self.assertFalse(core.reply_metrics("T9 passed its Done commands.", case)["status_line"])

    def test_long_sentences(self):
        long = " ".join(["word"] * 30) + "."
        metrics = core.reply_metrics(f"Short one here. {long}", replay_case())
        self.assertEqual(metrics["long_sentence_share"], 0.5)


class Statistics(unittest.TestCase):
    def test_wilson_interval(self):
        low, high = core.wilson(8, 10)
        self.assertAlmostEqual(low, 0.490, places=2)
        self.assertAlmostEqual(high, 0.943, places=2)
        self.assertEqual(core.wilson(0, 0), (0.0, 0.0))

    def test_mcnemar(self):
        self.assertEqual(core.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(core.mcnemar_exact(0, 6), 0.03125)
        self.assertAlmostEqual(core.mcnemar_exact(3, 3), 1.0)

    def test_cell_win_is_a_strict_majority(self):
        self.assertTrue(core.cell_win([True, True, False]))
        self.assertFalse(core.cell_win([True, False]))
        self.assertTrue(core.cell_win([True, None]))
        self.assertIsNone(core.cell_win([None, None]))

    def test_paired_counts(self):
        a = {"c1": True, "c2": True, "c3": False, "c4": None}
        b = {"c1": True, "c2": False, "c3": True, "c4": True}
        self.assertEqual(core.paired(a, b), {"cases": 3, "both": 1, "only_a": 1, "only_b": 1, "neither": 0,
                                             "p_two_sided": 1.0})


class Ratchet(unittest.TestCase):
    def test_a_significant_loss_is_a_regression(self):
        baseline = {f"c{i}": True for i in range(8)}
        candidate = dict(baseline, **{f"c{i}": False for i in range(6)})
        result = core.ratchet(baseline, candidate)
        self.assertEqual(result["status"], "REGRESSION")
        self.assertEqual((result["before"], result["after"]), (8, 2))

    def test_noise_holds(self):
        baseline = {"c1": True, "c2": False, "c3": True}
        candidate = {"c1": False, "c2": True, "c3": True}
        self.assertEqual(core.ratchet(baseline, candidate)["status"], "HOLD")

    def test_a_significant_gain_is_improved(self):
        baseline = {f"c{i}": False for i in range(6)}
        candidate = {f"c{i}": True for i in range(6)}
        self.assertEqual(core.ratchet(baseline, candidate)["status"], "IMPROVED")

    def test_new_cases_are_listed_not_scored(self):
        result = core.ratchet({"c1": True}, {"c1": True, "new": False})
        self.assertEqual(result["only_in_candidate"], ["new"])
        self.assertEqual(result["cases"], 1)


class Mining(unittest.TestCase):
    def test_signals_in_both_languages(self):
        self.assertEqual(core.signals("qmd là gì?"), ["what-is"])
        self.assertIn("dont-understand", core.signals("1, keep it 2, explain with more context, i dont understand"))
        self.assertIn("vietnamese", core.signals("giải thích lại bằng tiếng việt"))
        self.assertEqual(core.signals("done?"), ["status"])
        self.assertEqual(core.signals("add a delete button to the side panel"), [])

    def test_history_candidates_pair_each_signal_with_the_message_before_it(self):
        entries = [
            {"sessionId": "s", "timestamp": 1, "display": "giải thích cho tao pr này"},
            {"sessionId": "s", "timestamp": 2, "display": "/model"},
            {"sessionId": "s", "timestamp": 3, "display": "qmd là gì?"},
            {"sessionId": "t", "timestamp": 4, "display": "what is this?"},
        ]
        found = core.history_candidates(entries, {})
        self.assertEqual([(c["message"], c["follow_up"]) for c in found], [("giải thích cho tao pr này", "qmd là gì?")])

    def test_pasted_text_is_expanded_inline_or_from_the_cache(self):
        pasted = {"1": {"content": "inline text"}, "2": {"contentHash": "abc"}, "3": {"contentHash": "gone"}}
        text = core.expand_pasted("[Pasted text #1 +3 lines] / [Pasted text #2 +9 lines] / [Pasted text #3]",
                                  pasted, {"abc": "cached text"})
        self.assertEqual(text, "inline text / cached text / [Pasted text #3]")


class Transcripts(unittest.TestCase):
    ENTRIES = [
        {"type": "user", "uuid": "u1", "message": {"content": "archive the skill"}},
        {"type": "assistant", "uuid": "a1", "message": {"content": [
            {"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Archiving now."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "git mv a b"}}]}},
        {"type": "user", "uuid": "r1", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "assistant", "uuid": "a2", "message": {"content": [{"type": "text", "text": "Done: PR #196 merged."}]}},
        {"type": "user", "uuid": "x", "isSidechain": True, "message": {"content": "subagent noise"}},
        {"type": "user", "uuid": "m", "isMeta": True, "message": {"content": "meta"}},
        {"type": "user", "uuid": "c", "message": {"content": "<local-command-stdout>ok</local-command-stdout>"}},
        {"type": "user", "uuid": "u2", "message": {"content": "<system-reminder>x</system-reminder>done?"}},
    ]

    def test_main_thread_items(self):
        items = core.transcript_items(self.ENTRIES)
        self.assertEqual([(i["role"], i["text"]) for i in items], [
            ("user", "archive the skill"), ("assistant", "Archiving now."), ("tool_call", "Bash: git mv a b"),
            ("tool_result", "ok"), ("assistant", "Done: PR #196 merged."), ("user", "done?")])

    def test_replay_parts_split_at_the_follow_up(self):
        parts = core.replay_parts(core.transcript_items(self.ENTRIES), 5)
        self.assertEqual(parts["message"], "archive the skill")
        self.assertEqual(parts["original_reply"], "Archiving now.\n\nDone: PR #196 merged.")
        self.assertEqual([w["role"] for w in parts["work"]], ["tool_call", "tool_result"])
        self.assertEqual(parts["conversation"], [])
        self.assertEqual(parts["follow_up"], "done?")

    def test_replay_parts_refuse_a_follow_up_that_is_not_a_user_item(self):
        with self.assertRaises(ValueError):
            core.replay_parts(core.transcript_items(self.ENTRIES), 2)


class Scrubbing(unittest.TestCase):
    def test_known_secret_shapes_and_named_literals_are_removed(self):
        text = "key sk-ant-abcdefghijklmnop and ghp_" + "a" * 30 + " login lamhiep16/Tossai2026 password: hunter22x"
        clean = core.scrub(text, literals=("Tossai2026",))
        self.assertNotIn("sk-ant-abcdefghijklmnop", clean)
        self.assertNotIn("ghp_", clean)
        self.assertNotIn("Tossai2026", clean)
        self.assertIn("password: [REDACTED]", clean)

    def test_scrub_case_walks_every_field(self):
        case = reask_case(files=[{"path": "a.md", "content": "token=abcdef123456"}])
        self.assertEqual(core.scrub_case(case)["files"][0]["content"], "token=[REDACTED]")


if __name__ == "__main__":
    unittest.main()


class Report(unittest.TestCase):
    def setUp(self):
        self.cases = [reask_case(id="out-a"), reask_case(id="out-b"), replay_case(id="in-c")]
        self.sessions = [
            {"case": c, "arm": arm, "run": 1, "status": "ok", "cost_usd": 0.5, "duration_ms": 10000,
             "metrics": {"words": 100, "bare_labels": 0, "language_match": True, "status_line": False},
             "tool_uses": {}}
            for c in ("out-a", "out-b", "in-c") for arm in ("plain", "styled")]

    def verdict(self, case, arm, needed, win):
        return {"case": case, "arm": arm, "run": 1, "judge_model": "j",
                "verdict": {"follow_up_needed": needed, "evidence": "", "answers_message": True, "wrong_claims": []},
                "win": win}

    def test_a_case_whose_known_bad_reply_passes_is_not_scored(self):
        verdicts = [self.verdict("out-a", "stub", True, False), self.verdict("out-b", "stub", False, True),
                    self.verdict("in-c", "original", True, False)]
        verdicts += [self.verdict(c, arm, False, True) for c in ("out-a", "out-b", "in-c") for arm in ("plain", "styled")]
        report = core.summarize_run(self.cases, self.sessions, verdicts, ["plain", "styled"], [("styled", "plain")], 1)
        self.assertEqual(report["scored_cases"], ["in-c", "out-a"])
        self.assertEqual(report["excluded"], {"out-b": "the judge passed the known-bad reply"})
        calibration = next(g for g in report["gates"] if g["gate"] == "judge calibration")
        self.assertFalse(calibration["pass"])
        self.assertIn("2 of 3", calibration["detail"])

    def test_wins_split_in_and_out_of_sample(self):
        verdicts = [self.verdict(c, cal, True, False) for c, cal in (("out-a", "stub"), ("out-b", "stub"), ("in-c", "original"))]
        verdicts += [self.verdict(c, "styled", False, True) for c in ("out-a", "out-b", "in-c")]
        verdicts += [self.verdict(c, "plain", True, False) for c in ("out-a", "out-b", "in-c")]
        report = core.summarize_run(self.cases, self.sessions, verdicts, ["plain", "styled"], [("styled", "plain")], 1)
        self.assertEqual(report["summary"]["styled"]["out_of_sample"]["wins"], 2)
        self.assertEqual(report["summary"]["plain"]["all"]["wins"], 0)
        self.assertEqual(report["pairs"][0]["out_of_sample"]["only_a"], 2)
        self.assertTrue(all(g["pass"] for g in report["gates"]))
        markdown = core.render_report_md(report)
        self.assertIn("| styled | 3/3 = 100%", markdown)
        self.assertIn("All gates pass", markdown)

    def test_an_ungraded_calibration_excludes_the_case(self):
        verdicts = [self.verdict(c, "plain", False, True) for c in ("out-a", "out-b", "in-c")]
        report = core.summarize_run(self.cases, self.sessions, verdicts, ["plain"], [], 1)
        self.assertEqual(report["scored_cases"], [])
        self.assertFalse(next(g for g in report["gates"] if g["gate"] == "judge calibration")["pass"])
