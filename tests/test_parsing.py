# -*- coding: utf-8 -*-
"""The inputs a person types: permalinks in every form Reddit hands out, and
usernames with or without their prefix. No browser, no network."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cc_ai_reddit_kit import people, threads
from cc_ai_reddit_kit.state import Fail


class PermalinksReduceToTheFormsThatRender(unittest.TestCase):

    def test_thread_forms(self):
        for url in ("https://www.reddit.com/r/SomeSub/comments/abc123/a_slug/",
                    "https://www.reddit.com/r/somesub/comments/abc123/",
                    "https://old.reddit.com/r/SomeSub/comments/abc123/a_slug/?sort=new",
                    "https://www.reddit.com/comments/abc123/",
                    "/r/SomeSub/comments/abc123/a_slug/"):
            self.assertEqual(threads.canonical(url), "https://www.reddit.com/comments/abc123/", url)
            self.assertIsNone(threads.parse_permalink(url)[2], url)

    def test_comment_forms(self):
        for url in ("https://www.reddit.com/r/SomeSub/comments/abc123/comment/def456/",
                    "https://www.reddit.com/r/SomeSub/comments/abc123/a_slug/def456/",
                    "https://www.reddit.com/comments/abc123/comment/def456/"):
            self.assertEqual(threads.canonical(url), "https://www.reddit.com/comments/abc123/comment/def456/", url)

    def test_the_subreddit_is_read_when_the_url_names_it(self):
        self.assertEqual(threads.parse_permalink("https://www.reddit.com/r/SomeSub/comments/abc123/x/")[0], "SomeSub")
        self.assertIsNone(threads.parse_permalink("https://www.reddit.com/comments/abc123/")[0])

    def test_anything_else_fails(self):
        for url in ("https://www.reddit.com/r/SomeSub/", "https://example.com/r/x/comments/abc/", "abc123", ""):
            with self.assertRaises(Fail):
                threads.parse_permalink(url)


class Usernames(unittest.TestCase):

    def test_prefixes_are_removed_and_the_name_is_kept_whole(self):
        for raw in ("user123", "u/user123", "/u/user123/", "U/user123", "user/user123"):
            self.assertEqual(people.username(raw), "user123", raw)
        # The name must never lose its own leading letters: "u" is part of it.
        self.assertEqual(people.username("uu_name"), "uu_name")

    def test_a_non_name_fails(self):
        for raw in ("", "u/", "has space", "x"):
            with self.assertRaises(Fail):
                people.username(raw)


if __name__ == "__main__":
    unittest.main()
