# -*- coding: utf-8 -*-
"""--submit absent means nothing is sent.

`writing.run` is driven against a surface that records every call instead of
touching a browser. Each test asserts what WAS called as well as what was not,
so a flow that silently skipped everything could not pass by pressing nothing.

No browser, no network. State goes to a temporary directory.
"""
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cc_ai_reddit_kit import guardrails as G, writing as W
from cc_ai_reddit_kit.state import Fail, Refused

RULES = [{"n": 1, "title": "Stay on topic", "text": "Posts must be about the subject of this community."}]
DRAFT = ("The flaky part for us was never the model.\n\n"
         "It was two runs writing to the same build cache at once, so pin one cache per checkout.")


class Recorder(object):

    def __init__(self, sub="SubA", rules=RULES, readback=None, account=None, on_type=None):
        self.sub = sub
        self.calls = []
        self._rules = rules
        self._readback = readback
        self._account = account if account is not None else {"name": "someone", "id": "t2_fake"}
        self._on_type = on_type

    def open(self):
        self.calls.append("open")
        return {"subreddit": self.sub, "title": "a thread",
                "permalink": "https://www.reddit.com/r/%s/comments/abc123/" % self.sub}

    def rules(self):
        self.calls.append("rules")
        return self._rules

    def account(self):
        self.calls.append("account")
        return self._account

    def open_composer(self):
        self.calls.append("open_composer")

    def type(self, text, paras, title):
        self.calls.append("type")
        if self._on_type:
            self._on_type()
        return {"text": text if self._readback is None else self._readback, "title": title}

    def screenshot(self, path):
        self.calls.append("screenshot")
        return path

    def clear(self):
        self.calls.append("clear")

    def cancel(self):
        self.calls.append("cancel")

    def press_submit(self):
        self.calls.append("press_submit")

    def verify(self, text, title, me):
        self.calls.append("verify")
        return "https://www.reddit.com/r/%s/comments/abc123/comment/def456/" % self.sub, "recorded"


class FlowCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cc-ai-reddit-write-")
        self.ledger = G.Ledger(self.root)
        self.out = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_flow(self, surface, text=DRAFT, submit=False, kind="comment", title=None):
        return W.run(kind, surface, text, title, submit, False, os.path.join(self.root, "shot.png"),
                     self.ledger, out=self.out.append)


class SubmitAbsentSendsNothing(FlowCase):

    def test_without_submit_the_draft_is_staged_then_discarded_and_nothing_is_pressed(self):
        s = Recorder()
        self.assertEqual(self.run_flow(s), "staged")
        self.assertEqual(s.calls, ["open", "rules", "account", "open_composer", "type", "screenshot",
                                   "clear", "cancel"])
        self.assertNotIn("press_submit", s.calls)
        self.assertEqual(self.ledger.rows(), [], "a dry run wrote to the sent log")
        self.assertTrue(any(line.startswith("RESULT staged") for line in self.out))

    def test_without_submit_a_pinned_account_still_sends_nothing(self):
        G.pin_account("t2_fake", root=self.root)
        s = Recorder()
        self.assertEqual(self.run_flow(s), "staged")
        self.assertNotIn("press_submit", s.calls)
        self.assertEqual(self.ledger.rows(), [])

    def test_a_dry_run_prints_what_it_would_send_and_the_rules(self):
        self.run_flow(Recorder())
        text = "\n".join(self.out)
        self.assertIn("| The flaky part for us was never the model.", text)
        self.assertIn("Stay on topic: Posts must be about the subject of this community.", text)

    def test_the_press_is_called_from_exactly_one_place(self):
        """Structural, and a presence: exactly one call site exists, and it is
        in run after the dry-run return."""
        sources = {}
        for folder in ("cc_ai_reddit_kit", "."):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py"):
                    with open(os.path.join(ROOT, folder, name), encoding="utf-8") as f:
                        sources[os.path.join(folder, name)] = f.read()
        self.assertIn(os.path.join("cc_ai_reddit_kit", "writing.py"), sources)
        calls = [(n, m.start()) for n, src in sources.items() for m in re.finditer(r"\.press_submit\(\)", src)]
        self.assertEqual(len(calls), 1, calls)
        src = sources[os.path.join("cc_ai_reddit_kit", "writing.py")]
        self.assertLess(src.index("if not submit:"), calls[0][1])
        self.assertLess(src.index("ledger.reserve("), calls[0][1])


class WithSubmit(FlowCase):

    def test_with_submit_and_the_pinned_account_it_presses_once_and_logs_both_rows(self):
        G.pin_account("t2_fake", root=self.root)
        s = Recorder()
        self.assertEqual(self.run_flow(s, submit=True), "sent")
        self.assertEqual(s.calls.count("press_submit"), 1)
        self.assertEqual([r["row"] for r in self.ledger.rows()], ["attempt", "result"])
        self.assertEqual(self.ledger.rows()[1]["status"], "verified")
        self.assertEqual(self.ledger.rows()[0]["text"], DRAFT)

    def test_with_submit_and_no_pinned_account_it_refuses_and_discards(self):
        s = Recorder()
        with self.assertRaises(Refused) as ctx:
            self.run_flow(s, submit=True)
        self.assertEqual(ctx.exception.rule, "account")
        self.assertNotIn("press_submit", s.calls)
        self.assertEqual(s.calls[-2:], ["clear", "cancel"])
        self.assertEqual(self.ledger.rows(), [])

    def test_a_run_that_loses_the_race_under_the_lock_discards_and_presses_nothing(self):
        G.pin_account("t2_fake", root=self.root)

        def another_run_sends_meanwhile():
            self.ledger.reserve("comment", "SubZ", "t", "some other text entirely, sent by a parallel run",
                                now=time.time() - 30)

        s = Recorder(on_type=another_run_sends_meanwhile)
        with self.assertRaises(Refused) as ctx:
            self.run_flow(s, submit=True)
        self.assertEqual(ctx.exception.rule, "pace-submission-gap")
        self.assertNotIn("press_submit", s.calls)
        self.assertEqual(s.calls[-2:], ["clear", "cancel"])
        self.assertEqual(len(self.ledger.attempts()), 1)


class RefusedBeforeTheBrowser(FlowCase):

    def test_a_link_in_the_text_refuses_before_anything_is_opened(self):
        s = Recorder()
        with self.assertRaises(Refused) as ctx:
            self.run_flow(s, text="I wrote about it at example.com last week.", submit=True)
        self.assertEqual(ctx.exception.rule, "link")
        self.assertEqual(s.calls, [])

    def test_markdown_refuses_before_anything_is_opened(self):
        s = Recorder()
        with self.assertRaises(Fail):
            self.run_flow(s, text="this is **important**")
        self.assertEqual(s.calls, [])

    def test_the_pace_refuses_before_anything_is_opened(self):
        self.ledger.reserve("comment", "SubB", "t", "an earlier comment on another subreddit", now=time.time() - 120)
        s = Recorder()
        with self.assertRaises(Refused):
            self.run_flow(s)
        self.assertEqual(s.calls, [])


class NothingIsTypedWithoutRules(FlowCase):

    def test_unreadable_rules_stop_the_run_before_the_composer_opens(self):
        s = Recorder(rules=[])
        with self.assertRaises(Fail):
            self.run_flow(s, submit=True)
        self.assertNotIn("open_composer", s.calls)
        self.assertNotIn("press_submit", s.calls)


class WhatWasTypedIsWhatWouldBeSent(FlowCase):

    def test_a_readback_that_differs_fails_discards_and_presses_nothing(self):
        G.pin_account("t2_fake", root=self.root)
        s = Recorder(readback="The flaky part for us was never the model.")
        with self.assertRaises(Fail):
            self.run_flow(s, submit=True)
        self.assertNotIn("press_submit", s.calls)
        self.assertEqual(s.calls[-2:], ["clear", "cancel"])
        self.assertEqual(self.ledger.rows(), [])

    def test_a_signed_out_browser_fails_before_the_composer_opens(self):
        s = Recorder(account={})
        with self.assertRaises(Fail):
            self.run_flow(s)
        self.assertNotIn("open_composer", s.calls)


if __name__ == "__main__":
    unittest.main()
