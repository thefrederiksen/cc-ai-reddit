# -*- coding: utf-8 -*-
"""--submit absent means nothing is sent.

`writing.run` is driven against a surface that records every call instead of
touching a browser. Each test asserts what WAS called as well as what was not,
so a flow that silently skipped everything could not pass by pressing nothing.

The comment box itself is driven through the real CommentSurface against a
model of the page that behaves the way Reddit was measured to behave: every
edit is saved in the browser, the saved text comes back when the box opens,
and emptying the editor does not remove the saved copy.

No browser, no network. State goes to a temporary directory.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cc_ai_reddit_kit import guardrails as G, selectors as SEL, writing as W
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


# ---------------------------------------------------------------- the comment box

class Page(object):
    """One thread page with one comment box, standing in for the browser.

    Behaves as measured on Reddit: every edit is saved in the browser under the
    thread; opening the box puts the saved text back; select-all and delete
    empties the editor and leaves the saved copy alone; a reload keeps it.
    Only the handles CommentSurface uses are answered - anything else is an
    assertion, so a test cannot pass on a read the model never served."""

    URL = "https://www.reddit.com/r/SubA/comments/abc123/a_thread/"
    KEY = ("t3_abc123", None)
    TRIGGER = (100, 300)
    EDITOR = (120, 340)
    CANCEL = (400, 420)
    SUBMIT = (480, 420)

    def __init__(self, saved=None, stuck=False, restores_on_load=False, unreadable_after_shot=False):
        self.saved = dict(saved or {})
        self.stuck = stuck                          # select-all and delete leaves the text in place
        self.restores_on_load = restores_on_load    # something outside this page writes the text back on load
        self.unreadable_after_shot = unreadable_after_shot
        self.unreadable = False
        self.open = False
        self.editor = ""
        self.opened_with = []
        self.inserts = []
        self.loads = 0
        self.editor_reads = 0
        self.last_typed = ""

    def goto(self, url):
        self.loads += 1
        self.open, self.editor = False, ""
        if self.restores_on_load and self.last_typed:
            self.saved[self.KEY] = self.last_typed
        return self.URL

    def url(self):
        return self.URL

    def me(self):
        return {"name": "someone", "id": "t2_fake"}

    @contextmanager
    def focused(self):
        yield

    def wait_for(self, expr, what, timeout=20):
        value = self.js(expr)
        if not value:
            raise Fail("%s did not appear" % what)
        return value

    def scroll_to(self, expr, what):
        return self.js(expr)

    def js(self, expr):
        if expr == SEL.THREAD_JS:
            return {"post": {"id": "abc123", "subreddit": "SubA", "title": "a thread", "locked": False,
                             "archived": False}, "comments": []}
        if expr == SEL.RULES_JS:
            return RULES
        if expr == W.TRIGGER_RECT_JS:
            return {"x": self.TRIGGER[0], "y": self.TRIGGER[1], "h": 40}
        if expr in (W.EDITORS_JS, W.EDITORS_JS + ".length"):
            self.editor_reads += 1
            if self.unreadable:
                return None
            found = [{"x": self.EDITOR[0], "y": self.EDITOR[1], "text": self.editor}] if self.open else []
            return found if expr == W.EDITORS_JS else len(found)
        if expr == SEL.VISIBLE_COMPOSER_BUTTONS_JS:
            if not self.open:
                return []
            return [{"slot": "cancel-button", "name": "Cancel", "x": self.CANCEL[0], "y": self.CANCEL[1],
                     "disabled": False},
                    {"slot": "submit-button", "name": "Comment", "x": self.SUBMIT[0], "y": self.SUBMIT[1],
                     "disabled": False}]
        for remove in (False, True):
            if expr == SEL.SAVED_DRAFTS_JS % (json.dumps(self.KEY[0]), json.dumps(self.KEY[1]), json.dumps(remove)):
                text = self.saved.pop(self.KEY, None) if remove else self.saved.get(self.KEY)
                return {"matched": 1 if text is not None else 0, "chars": len(text or "")}
        raise AssertionError("the page model does not answer: %s" % expr[:120])

    def click(self, x, y):
        if (x, y) == self.TRIGGER and not self.open:
            self.open = True
            self.editor = self.saved.get(self.KEY, "")
            self.opened_with.append(self.editor)
        elif (x, y) == self.CANCEL and self.open:
            self.open, self.editor = False, ""
        elif (x, y) == self.EDITOR and self.open:
            pass
        else:
            raise AssertionError("a click on nothing at %r" % ((x, y),))

    def _edited(self):
        self.saved[self.KEY] = self.last_typed = self.editor

    def insert_text(self, text):
        if not self.open:
            raise AssertionError("typed with no box open")
        self.inserts.append((self.editor, text))
        self.editor += text
        self._edited()

    def key(self, key, modifiers=0):
        if key != "Enter" or not self.open:
            raise AssertionError("unexpected key %r" % key)
        self.editor += "\n\n"
        self._edited()

    def select_all_and_delete(self):
        if not self.stuck:
            self.editor = ""

    def screenshot(self, path):
        if self.unreadable_after_shot:
            self.unreadable = True
        return path

    def ax(self, *a, **k):
        raise AssertionError("a thread comment never looks up controls by name")


class TheCommentBox(FlowCase):

    def setUp(self):
        FlowCase.setUp(self)
        self.logs = []
        for patcher in (mock.patch("time.sleep"), mock.patch.object(W, "log", side_effect=self.logs.append)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def comment(self, page):
        return self.run_flow(W.CommentSurface(page, Page.URL, reply=False))

    def staged(self):
        return [line for line in self.out if line.startswith("RESULT staged")]

    # -- a box that opens pre-filled

    def test_a_box_that_opens_holding_earlier_text_is_emptied_before_a_character_is_typed(self):
        page = Page(saved={Page.KEY: "An earlier comment that was never sent."})
        self.assertEqual(self.comment(page), "staged")
        self.assertEqual(page.opened_with[0], "An earlier comment that was never sent.", "the case did not happen")
        self.assertTrue(page.inserts, "nothing was typed")
        self.assertEqual(page.inserts[0][0], "", "the draft was typed into the earlier text")
        self.assertEqual(len(self.staged()), 1)

    def test_a_box_that_opens_pre_filled_and_cannot_be_emptied_fails_with_nothing_typed(self):
        page = Page(saved={Page.KEY: "An earlier comment that was never sent."}, stuck=True)
        with self.assertRaises(Fail) as ctx:
            self.comment(page)
        self.assertIn("nothing was typed", str(ctx.exception))
        self.assertEqual(page.opened_with, ["An earlier comment that was never sent."])
        self.assertEqual(page.inserts, [])
        self.assertEqual(self.staged(), [])

    # -- a discard whose saved text would come back

    def test_emptying_the_editor_alone_leaves_text_the_box_puts_back(self):
        """The control for the test below: in this model, as on Reddit, select-all
        and delete and Cancel leave the saved copy, and the box refills."""
        page = Page()
        s = W.CommentSurface(page, Page.URL, reply=False)
        s.open_composer()
        text, paras = W.prepare(DRAFT)
        s.type(text, paras, None)
        page.click(*Page.EDITOR)
        page.select_all_and_delete()
        page.click(*Page.CANCEL)
        page.goto(Page.URL)
        page.click(*Page.TRIGGER)
        self.assertEqual(page.editor, text)

    def test_a_discard_removes_the_saved_copy_and_the_next_run_opens_an_empty_box(self):
        page = Page()
        self.assertEqual(self.comment(page), "staged")
        self.assertNotIn(Page.KEY, page.saved)
        self.assertEqual(self.comment(page), "staged")
        # run 1 opens the box, reopens it on a reload to prove it; run 2 the same
        self.assertEqual(page.opened_with, ["", "", "", ""])
        self.assertEqual(page.loads, 4)
        self.assertEqual([into for into, _ in page.inserts if into == ""], [""] * 2, page.inserts)
        self.assertEqual(len(self.staged()), 2)
        self.assertNotIn(Page.KEY, page.saved)

    def test_a_box_that_reopens_holding_text_after_the_discard_fails_instead_of_staging(self):
        page = Page(restores_on_load=True)
        with self.assertRaises(Fail) as ctx:
            self.comment(page)
        self.assertIn("reopened holding", str(ctx.exception))
        self.assertEqual(len(page.opened_with), 2, "the box was not reopened to prove it empty")
        self.assertEqual(self.staged(), [])

    # -- an editor that cannot be read

    def test_an_editor_that_cannot_be_read_is_not_an_empty_one(self):
        page = Page()
        s = W.CommentSurface(page, Page.URL, reply=False)
        s.open_composer()
        text, paras = W.prepare(DRAFT)
        s.type(text, paras, None)
        page.unreadable = True
        with self.assertRaises(Fail) as ctx:
            s.clear()
        self.assertIn("could not read the comment editor", str(ctx.exception))
        with self.assertRaises(Fail):
            s.cancel()
        self.assertTrue(page.open, "the box was treated as closed")
        self.assertEqual(page.saved.get(Page.KEY), text, "the saved copy was treated as gone")
        self.assertEqual(page.loads, 0)

    def test_a_dry_run_whose_editor_turns_unreadable_fails_instead_of_staging(self):
        page = Page(unreadable_after_shot=True)
        with self.assertRaises(Fail):
            self.comment(page)
        self.assertEqual(self.staged(), [])
        self.assertEqual(page.saved.get(Page.KEY), DRAFT)

    def test_a_run_that_never_asked_for_the_box_has_nothing_to_discard(self):
        page = Page()
        s = W.CommentSurface(page, Page.URL, reply=False)
        s.clear()
        s.cancel()
        self.assertEqual((page.editor_reads, page.loads), (0, 0))


if __name__ == "__main__":
    unittest.main()
