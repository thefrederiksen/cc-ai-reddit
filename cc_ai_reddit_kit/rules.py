# -*- coding: utf-8 -*-
"""rules: a subreddit's rules, read from the live page.

Never from the archive (its copy of one subreddit's rules was twenty months old
and wrong on 2026-09-10) and never from /about/rules/ (see selectors.py). The
community sidebar is lazy-loaded, so a read waits for it and then tells apart
the two ways it can come back empty: a sidebar that has no rules section, and a
sidebar that never loaded. Neither is ever reported as "this subreddit has no
rules".
"""
import time

from . import guardrails as G, selectors as SEL
from .state import Fail, RUN_ID


def read_sidebar(b, subreddit, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = b.js(SEL.RULES_JS)
        if found:
            return found
        time.sleep(1)
    if b.js("!!document.querySelector('aside[aria-label=\"Community information\"]')"):
        raise Fail("the community sidebar of r/%s has no rules section. A write refuses where it cannot "
                   "read rules; look at the subreddit by hand." % subreddit)
    raise Fail("the community sidebar never loaded on %s, so the rules of r/%s could not be read"
               % (b.url(), subreddit))


def fetch(b, subreddit):
    b.goto(SEL.URL_SUBREDDIT % subreddit)
    return G.record_rules(subreddit, read_sidebar(b, subreddit), b.url(), RUN_ID)


def format_rules(entry):
    lines = ["RULES r/%s, read from the live page at %s:" % (entry["subreddit"], entry["iso"])]
    for r in entry["rules"]:
        lines.append("  %d. %s%s" % (r["n"], r["title"], (": " + r["text"]) if r.get("text") else ""))
    return "\n".join(lines)
