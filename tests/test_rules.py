# -*- coding: utf-8 -*-
"""Rules are read from the community sidebar's own source, and every way that
read can come back empty is a FAIL that names it, never an empty rule list.

No browser, no network: the page answers the fetch with what Reddit was
measured to answer.
"""
import json
import os
import sys
import unittest
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cc_ai_reddit_kit import rules as R, selectors as SEL
from cc_ai_reddit_kit.state import Fail

RULES = [{"n": 1, "title": "Be civil", "text": "No insults."}]
PARTIAL_URL = "https://www.reddit.com/svc/shreddit/feeds/subreddit-right-rail?name=SubA"


class Page(object):
    """A www.reddit.com page that answers only the sidebar fetch for one subreddit."""

    def __init__(self, answer, sub="SubA"):
        self.answer = answer
        self.sub = sub
        self.asked = []

    def url(self):
        return "https://www.reddit.com/r/SubA/comments/abc123/a_thread/"

    def js(self, expr):
        self.asked.append(expr)
        if expr == SEL.RULES_JS % json.dumps(SEL.RULES_PARTIAL % quote(self.sub)):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        raise AssertionError("the page model does not answer: %s" % expr[:120])


def answer(**kw):
    a = {"status": 200, "url": PARTIAL_URL, "subreddit": "SubA", "sidebar": True, "details": len(RULES),
         "rules": RULES}
    a.update(kw)
    return a


class ReadSidebar(unittest.TestCase):

    def test_the_rules_come_from_the_sidebar_partial_of_the_named_subreddit(self):
        page = Page(answer())
        self.assertEqual(R.read_sidebar(page, "SubA"), RULES)
        self.assertEqual(len(page.asked), 1)
        self.assertIn(json.dumps("/svc/shreddit/feeds/subreddit-right-rail?name=SubA"), page.asked[0])

    def test_the_subreddit_name_is_matched_without_case(self):
        self.assertEqual(R.read_sidebar(Page(answer(subreddit="suba")), "SubA"), RULES)

    def test_a_sidebar_of_another_subreddit_fails(self):
        with self.assertRaisesRegex(Fail, "names r/SubB"):
            R.read_sidebar(Page(answer(subreddit="SubB")), "SubA")

    def test_a_sidebar_that_names_no_subreddit_fails(self):
        with self.assertRaisesRegex(Fail, "names r/None"):
            R.read_sidebar(Page(answer(subreddit=None)), "SubA")

    def test_a_sidebar_without_rules_fails_and_says_so(self):
        with self.assertRaisesRegex(Fail, "has no rules section"):
            R.read_sidebar(Page(answer(rules=[], details=0)), "SubA")

    def test_a_rule_that_does_not_parse_fails_rather_than_shortening_the_list(self):
        with self.assertRaisesRegex(Fail, "holds 2 rule entries but only 1"):
            R.read_sidebar(Page(answer(details=2)), "SubA")

    def test_a_rejected_fetch_is_not_a_rule_list(self):
        with self.assertRaisesRegex(RuntimeError, "fetch rejected"):
            R.read_sidebar(Page(RuntimeError("fetch rejected")), "SubA")

    def test_the_name_is_quoted_into_the_partial_url(self):
        page = Page(answer(subreddit="a&b"), sub="a&b")
        R.read_sidebar(page, "a&b")
        self.assertIn("name=a%26b", page.asked[0])

    def test_an_answer_that_is_not_the_sidebar_fails(self):
        with self.assertRaisesRegex(Fail, "without its Community information section"):
            R.read_sidebar(Page(answer(sidebar=False, rules=[])), "SubA")

    def test_an_http_error_fails(self):
        with self.assertRaisesRegex(Fail, "answered HTTP 500"):
            R.read_sidebar(Page(answer(status=500)), "SubA")

    def test_the_js_challenge_fails(self):
        with self.assertRaisesRegex(Fail, "JS challenge"):
            R.read_sidebar(Page(answer(url=PARTIAL_URL + "&js_challenge=1")), "SubA")

    def test_a_page_that_does_not_answer_fails(self):
        with self.assertRaisesRegex(Fail, "could not be fetched"):
            R.read_sidebar(Page(None), "SubA")


if __name__ == "__main__":
    unittest.main()
