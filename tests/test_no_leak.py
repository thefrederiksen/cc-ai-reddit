# -*- coding: utf-8 -*-
"""This repository is public. These tests read the ARTEFACTS - every file that
would be committed - and fail on identifiers, by shape.

They do not test tools/redact_evidence.py; they test what is on disk, so a
file that skipped the redactor fails here too. Every detector is first pointed
at a fabricated value and watched matching it: a detector that cannot match
its own target would pass forever by finding nothing.

BRIEF.md is not scanned. It is the owner's build mandate, it names private
context on purpose, and it is his to decide whether it is committed.
"""
import glob
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from redact_evidence import redact  # noqa: E402

NOT_SCANNED = {"BRIEF.md"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "build"}
TEXT_EXT = (".py", ".md", ".txt", ".toml", ".in", ".cmd", ".json", ".gitignore")

ACCOUNT_ID = re.compile(r"\bt2_(?!<account>)[a-z0-9]{4,}\b")
FABRICATED_IDS = {"t2_fake", "t2_test"}
U_NAME = re.compile(r"(?<![A-Za-z0-9_/])u/(?!<user>)([A-Za-z0-9_-]{2,20})")
JSON_AUTHOR = re.compile(r'"(?:author|name)":\s*"(?!<user>")([^"]*)"')
HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<)[A-Za-z]", re.I)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
FABRICATED_EMAIL = {"someone@example.com"}
PRODUCT = re.compile("dev" + "throttle", re.I)
ATTRIBUTION = re.compile("co-" + "authored-by|gener" + "ated with|" + "anthro" + "pic", re.I)


def files():
    out = []
    for dirpath, dirnames, names in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), ROOT)
            if rel in NOT_SCANNED or not (n.endswith(TEXT_EXT) or n in ("LICENSE",)):
                continue
            out.append(rel)
    return sorted(out)


def read(rel):
    with open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


EVIDENCE = sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs", "evidence", "*.txt")))
AUTHORED = [f for f in files() if not f.startswith(os.path.join("docs", "evidence"))]


# Fabricated values for the positive controls, ASSEMBLED so that this file does
# not itself contain the shapes it forbids - a control written out literally
# would be flagged by the very scan it exists to prove.
FAKE_ID = "t2_" + "abcd1234"
FAKE_HOME_BACK = "C:" + "\\" + "Users" + "\\" + "somebody" + "\\AppData"
FAKE_HOME_FWD = "c:" + "/" + "Users" + "/" + "somebody/x"
FAKE_EMAIL = "a.person" + "@" + "example.org"


class TheInstrumentsWork(unittest.TestCase):

    def test_there_is_something_to_scan(self):
        self.assertGreaterEqual(len(AUTHORED), 15, AUTHORED)
        self.assertGreaterEqual(len(EVIDENCE), 8, "the dated evidence files are missing: %r" % EVIDENCE)

    def test_every_detector_matches_a_fabricated_target(self):
        self.assertTrue(ACCOUNT_ID.search("id %s here" % FAKE_ID))
        self.assertFalse(ACCOUNT_ID.search("id t2_<account> here"))
        self.assertTrue(U_NAME.search("by u/Some_Name-9"))
        self.assertFalse(U_NAME.search("by u/<user>"))
        self.assertFalse(U_NAME.search("https://example.com/menu/item"))
        self.assertTrue(JSON_AUTHOR.search('{"author": "Some_Name"}'))
        self.assertFalse(JSON_AUTHOR.search('{"author": "<user>"}'))
        self.assertTrue(HOME.search(FAKE_HOME_BACK))
        self.assertTrue(HOME.search(FAKE_HOME_FWD))
        self.assertFalse(HOME.search(r"<home>\AppData"))
        self.assertTrue(EMAIL.search("write to " + FAKE_EMAIL))
        self.assertTrue(PRODUCT.search("Dev" + "Throttle"))
        self.assertTrue(ATTRIBUTION.search("Gener" + "ated with a tool"))

    def test_the_redactor_removes_every_shape_the_checks_look_for(self):
        raw = ('{"author": "Some_Name", "text": "thanks Some_Name"}\n'
               "u/Other-Person replied  /user/Third_One/  " + FAKE_ID + "\n"
               "shot=" + FAKE_HOME_BACK + "\\x.png  mail " + FAKE_EMAIL + "\n"
               "RESULT me user=Fourth_1 unanswered=2\n")
        out = redact(raw)
        for rx in (ACCOUNT_ID, U_NAME, JSON_AUTHOR, HOME, EMAIL):
            self.assertIsNone(rx.search(out), (rx.pattern, out))
        for name in ("Some_Name", "Other-Person", "Third_One", "Fourth_1"):
            self.assertNotIn(name, out)


class NothingIdentifyingIsInTheRepository(unittest.TestCase):

    def scan(self, paths, rx, allowed=()):
        bad = []
        for rel in paths:
            for n, line in enumerate(read(rel).decode("utf-8", "replace").splitlines(), 1):
                for m in rx.finditer(line):
                    if m.group(0) not in allowed:
                        bad.append("%s:%d %s" % (rel, n, m.group(0)[:60]))
        return bad

    def test_every_file_is_ascii(self):
        bad = []
        for rel in AUTHORED + EVIDENCE:
            try:
                read(rel).decode("ascii")
            except UnicodeDecodeError as exc:
                bad.append("%s at byte %d" % (rel, exc.start))
        self.assertEqual(bad, [])

    def test_no_reddit_account_id(self):
        self.assertEqual(self.scan(AUTHORED + EVIDENCE, ACCOUNT_ID, FABRICATED_IDS), [])

    def test_no_username_in_the_evidence(self):
        self.assertEqual(self.scan(EVIDENCE, U_NAME), [])
        self.assertEqual(self.scan(EVIDENCE, JSON_AUTHOR), [])

    def test_no_home_directory_path(self):
        self.assertEqual(self.scan(AUTHORED + EVIDENCE, HOME), [])

    def test_no_email_address(self):
        self.assertEqual(self.scan(AUTHORED + EVIDENCE, EMAIL, FABRICATED_EMAIL), [])

    def test_no_marketing_target_named(self):
        self.assertEqual(self.scan(AUTHORED + EVIDENCE, PRODUCT), [])

    def test_no_attribution_in_authored_files(self):
        # Evidence quotes other people's public comments and may name anyone.
        self.assertEqual(self.scan(AUTHORED, ATTRIBUTION), [])


if __name__ == "__main__":
    unittest.main()
