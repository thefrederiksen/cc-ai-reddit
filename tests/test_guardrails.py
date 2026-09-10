# -*- coding: utf-8 -*-
"""The guardrails refuse. Each test here asserts a PRESENCE - a Refused with
a named rule - and each has a control beside it that passes, so a check that
can never fire cannot pass this file by finding nothing.

No browser, no network. State goes to a temporary directory.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cc_ai_reddit_kit import guardrails as G
from cc_ai_reddit_kit.state import Fail, Refused

NOW = 1789000000.0


def rules():
    return [{"n": 1, "title": "Be civil", "text": ""}]


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class StateCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cc-ai-reddit-test-")
        self.ledger = G.Ledger(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def send(self, kind, sub, text, ago, title=None):
        """Put a past attempt on the log, as if it was sent `ago` seconds before NOW."""
        return self.ledger.reserve(kind, sub, "https://www.reddit.com/r/%s/comments/abc/x/" % sub,
                                   text, title=title, account="t2_test", now=NOW - ago)

    def rule_of(self, refusals):
        return [r.rule for r in refusals]


# ------------------------------------------------------------------ rules first

class RulesNotFetchedRefuses(StateCase):

    def test_rules_never_read_refuses(self):
        entry, refusal = G.require_rules("SomeSub", "run-1", root=self.root)
        self.assertIsNone(entry)
        self.assertEqual(refusal.rule, "rules-not-fetched")

    def test_rules_read_by_another_run_refuses(self):
        G.record_rules("SomeSub", rules(), "https://www.reddit.com/r/SomeSub/", "run-OLD", root=self.root)
        entry, refusal = G.require_rules("SomeSub", "run-NOW", root=self.root)
        self.assertIsNone(entry)
        self.assertEqual(refusal.rule, "rules-not-fetched")

    def test_rules_read_in_this_run_pass_and_carry_the_rules(self):
        G.record_rules("SomeSub", rules(), "https://www.reddit.com/r/SomeSub/", "run-NOW", root=self.root)
        entry, refusal = G.require_rules("somesub", "run-NOW", root=self.root)
        self.assertIsNone(refusal)
        self.assertEqual(entry["rules"][0]["title"], "Be civil")

    def test_an_empty_rule_list_is_never_recorded(self):
        with self.assertRaises(Fail):
            G.record_rules("SomeSub", [], "https://www.reddit.com/r/SomeSub/", "run-NOW", root=self.root)


# ------------------------------------------------------------------ pace

class PaceLimitRefuses(StateCase):

    def test_a_second_submission_within_ten_minutes_refuses(self):
        self.send("comment", "SubA", "an earlier comment about build caches and flaky tests", ago=5 * 60)
        got = self.ledger.check("comment", "SubB", "a different comment about deploy scripts", now=NOW)
        self.assertIn("pace-submission-gap", self.rule_of(got))

    def test_after_ten_minutes_another_subreddit_is_clear(self):
        self.send("comment", "SubA", "an earlier comment about build caches and flaky tests", ago=11 * 60)
        got = self.ledger.check("comment", "SubB", "a different comment about deploy scripts", now=NOW)
        self.assertEqual(got, [])

    def test_two_comments_in_one_subreddit_within_an_hour_refuse(self):
        self.send("comment", "SubA", "an earlier comment about build caches and flaky tests", ago=30 * 60)
        got = self.ledger.check("reply", "suba", "a reply about something else entirely", now=NOW)
        self.assertIn("pace-same-subreddit", self.rule_of(got))

    def test_the_same_subreddit_is_clear_after_an_hour(self):
        self.send("comment", "SubA", "an earlier comment about build caches and flaky tests", ago=61 * 60)
        self.assertEqual(self.ledger.check("comment", "SubA", "a new comment on another topic", now=NOW), [])

    def test_a_second_post_in_a_subreddit_within_a_day_refuses(self):
        self.send("post", "SubA", "body of the first post", ago=23 * 3600, title="First post")
        got = self.ledger.check("post", "SubA", "body of a second post", title="Second", now=NOW)
        self.assertIn("pace-post-per-day", self.rule_of(got))

    def test_a_post_is_clear_after_a_day(self):
        self.send("post", "SubA", "body of the first post", ago=25 * 3600, title="First post")
        self.assertEqual(self.ledger.check("post", "SubA", "body of a second post", title="Second", now=NOW), [])

    def test_a_row_stamped_in_the_future_still_refuses(self):
        # Clock skew must fail closed, never open a gap.
        self.send("comment", "SubA", "an earlier comment about build caches", ago=-300)
        got = self.ledger.check("comment", "SubB", "a different comment", now=NOW)
        self.assertIn("pace-submission-gap", self.rule_of(got))

    def test_the_gap_holds_across_processes_because_it_is_on_disk(self):
        self.send("comment", "SubA", "an earlier comment about build caches and flaky tests", ago=60)
        fresh = G.Ledger(self.root)                  # a new invocation reads the same file
        self.assertIn("pace-submission-gap", self.rule_of(fresh.check("comment", "SubB", "other", now=NOW)))


# ------------------------------------------------------------------ repetition

LONG = ("We ran eight agents at once on one repository and the thing that broke first was not "
        "the model, it was the shared build cache, which two of them kept invalidating for each other.")


class NearDuplicateRefuses(StateCase):

    def test_identical_text_refuses(self):
        self.send("comment", "SubA", LONG, ago=2 * 86400)
        self.assertIn("near-duplicate", self.rule_of(self.ledger.check("comment", "SubB", LONG, now=NOW)))

    def test_identical_after_case_and_punctuation_changes_refuses(self):
        self.send("comment", "SubA", LONG, ago=2 * 86400)
        again = LONG.upper().replace(",", "").replace(".", "!")
        self.assertIn("near-duplicate", self.rule_of(self.ledger.check("comment", "SubB", again, now=NOW)))

    def test_a_short_identical_comment_refuses(self):
        self.send("comment", "SubA", "Thanks, that fixed it for me.", ago=3600 * 5)
        got = self.ledger.check("comment", "SubB", "thanks that fixed it for me", now=NOW)
        self.assertIn("near-duplicate", self.rule_of(got))

    def test_a_reused_ten_word_phrase_refuses(self):
        self.send("comment", "SubA", LONG, ago=2 * 86400)
        new = ("Different opening entirely. In my case the thing that broke first was not the model, "
               "it was logging, so I would look there before anything else.")
        got = self.ledger.check("comment", "SubB", new, now=NOW)
        self.assertIn("near-duplicate", self.rule_of(got))
        self.assertIn("the thing that broke first was not the model", str(got[0]))

    def test_a_lightly_edited_copy_refuses(self):
        self.send("comment", "SubA", LONG, ago=2 * 86400)
        edited = LONG.replace("eight", "six").replace("repository", "repo").replace("two of them", "a pair")
        self.assertIn("near-duplicate", self.rule_of(self.ledger.check("comment", "SubB", edited, now=NOW)))

    def test_genuinely_different_text_is_clear(self):
        self.send("comment", "SubA", LONG, ago=2 * 86400)
        other = ("Pin the toolchain version in CI and the flaky failures mostly go away; ours came from "
                 "a minor compiler bump that nobody noticed for a week.")
        self.assertEqual(self.ledger.check("comment", "SubB", other, now=NOW), [])

    def test_text_older_than_thirty_days_no_longer_blocks(self):
        self.send("comment", "SubA", LONG, ago=31 * 86400)
        self.assertEqual(self.ledger.check("comment", "SubB", LONG, now=NOW), [])

    def test_a_post_title_counts_toward_repetition(self):
        self.send("post", "SubA", "short body one", ago=3 * 86400,
                  title="What broke first when we ran eight coding agents on one repository")
        got = self.ledger.check("post", "SubB", "short body two", now=NOW,
                                title="What broke first when we ran eight coding agents on one repository")
        self.assertIn("near-duplicate", self.rule_of(got))


# ------------------------------------------------------------------ links

class ALinkInTheTextRefuses(unittest.TestCase):

    def links(self, text, allow=False):
        return [r.rule for r in G.lint("comment", text, allow_links=allow)]

    def test_every_link_shape_refuses_by_default(self):
        for text in ("see https://example.com/page for details",
                     "see http://example.com",
                     "it is at www.example.com if you want it",
                     "I wrote it up [here](https://example.com/post) last week",
                     "the docs <https://example.com/docs> cover it",
                     "reference style works too\n\n[1]: https://example.com",
                     "mail me at someone@example.com",
                     "just search example.com",
                     "I use toolname.dev for this",
                     "Claude.ai handles it",
                     "try some-tool.io/pricing",
                     "the repo lives at `example.com/code`",
                     "ftp://files.example.org/pub"):
            self.assertIn("link", self.links(text), text)

    def test_bare_domains_are_found_by_shape_not_by_a_list(self):
        # A top-level domain nobody listed must still be caught.
        self.assertIn("link", self.links("have a look at somebrandnew.zzzzq"))

    def test_ordinary_technical_writing_is_not_a_link(self):
        for text in ("I moved the config into package.json and it worked",
                     "Next.js and Vue.js both handle this",
                     "we pinned v1.2 and then 3.11.6",
                     "e.g. the second case, i.e. the slow one",
                     "call `os.path.join` rather than string concatenation",
                     "edit `setup.py` and rerun",
                     "it lives in `README.md` near the top",
                     "the file is data.parquet"):
            self.assertNotIn("link", self.links(text), text)

    def test_a_file_name_that_is_also_a_domain_needs_code_formatting(self):
        # .py and .md are real top-level domains, so outside a code span they refuse.
        self.assertIn("link", self.links("edit setup.py and rerun"))
        self.assertNotIn("link", self.links("edit `setup.py` and rerun"))

    def test_a_web_domain_in_a_code_span_still_refuses(self):
        self.assertIn("link", self.links("run it against `mytool.com`"))

    def test_allow_links_lets_a_link_through_with_enough_text_around_it(self):
        text = ("The benchmark numbers are in the appendix of the write up, and the part that matters "
                "for you is the second table: https://example.com/report")
        self.assertIn("link", self.links(text))
        self.assertEqual(self.links(text, allow=True), [])

    def test_a_bare_link_refuses_even_with_allow_links(self):
        self.assertIn("bare-link", self.links("https://example.com/my-thing", allow=True))
        self.assertIn("bare-link", self.links("check this out: [my tool](https://example.com)", allow=True))

    def test_the_refusal_names_what_it_found(self):
        got = G.lint("comment", "look at example.com and https://x.example.org/a")
        self.assertIn("'example.com'", str(got[0]))


class AVoteRequestRefuses(unittest.TestCase):

    def test_asking_for_votes_refuses(self):
        for text in ("Please upvote so more people see this", "Upvote if you agree!",
                     "drop an upvote if it helped", "would appreciate some karma here"):
            self.assertIn("vote-request", [r.rule for r in G.lint("comment", text)], text)

    def test_talking_about_votes_is_not_a_request(self):
        self.assertEqual(G.lint("comment", "the score on that thread went negative fast"), [])


# ------------------------------------------------------------------ account

class OneAccountOnly(StateCase):

    def test_no_pinned_account_refuses(self):
        self.assertEqual(G.check_account("t2_aaa", root=self.root).rule, "account")

    def test_a_different_signed_in_account_refuses(self):
        G.pin_account("t2_aaa", root=self.root)
        self.assertEqual(G.check_account("t2_bbb", root=self.root).rule, "account")

    def test_the_pinned_account_is_clear(self):
        G.pin_account("t2_aaa", root=self.root)
        self.assertIsNone(G.check_account("t2_aaa", root=self.root))

    def test_the_pin_cannot_be_moved_to_a_second_account(self):
        G.pin_account("t2_aaa", root=self.root)
        with self.assertRaises(Refused):
            G.pin_account("t2_bbb", root=self.root)


# ------------------------------------------------------------------ the log

class TheSentLogIsAppendOnly(StateCase):

    def test_a_refused_reservation_writes_nothing(self):
        self.send("comment", "SubA", LONG, ago=60)
        before = read_bytes(self.ledger.path)
        with self.assertRaises(Refused):
            self.ledger.reserve("comment", "SubB", "t", "another text entirely", now=NOW)
        self.assertEqual(read_bytes(self.ledger.path), before)

    def test_new_rows_never_change_the_bytes_already_written(self):
        self.send("comment", "SubA", LONG, ago=3 * 86400)
        first = read_bytes(self.ledger.path)
        aid = self.send("comment", "SubB", "a completely unrelated second comment", ago=86400)
        self.ledger.record_result(aid, "verified", permalink="https://www.reddit.com/r/SubB/comments/abc/x/def/")
        after = read_bytes(self.ledger.path)
        self.assertTrue(after.startswith(first))
        rows = [json.loads(line) for line in after.decode("utf-8").splitlines()]
        self.assertEqual([r["row"] for r in rows], ["attempt", "attempt", "result"])
        self.assertEqual(rows[1]["text"], "a completely unrelated second comment")
        for field in ("ts", "iso", "subreddit", "target", "text", "sha256"):
            self.assertIn(field, rows[0])

    def test_a_corrupt_log_stops_everything(self):
        with open(self.ledger.path, "w", encoding="utf-8") as f:
            f.write("{not json\n")
        with self.assertRaises(Fail):
            self.ledger.check("comment", "SubA", "text", now=NOW)

    def test_concurrent_runs_cannot_both_pass_the_gap(self):
        """The check and the append are one locked step: eight runs racing for
        eight different subreddits at the same instant produce exactly one send."""
        won, refused, errors = [], [], []

        def one(i):
            try:
                self.ledger.reserve("comment", "Sub%d" % i, "t", "distinct text number %d about topic %d" % (i, i * 7),
                                    now=NOW)
                won.append(i)
            except Refused:
                refused.append(i)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(won), 1, "%d runs passed a ten-minute gap at the same instant" % len(won))
        self.assertEqual(len(self.ledger.attempts()), 1)


if __name__ == "__main__":
    unittest.main()
