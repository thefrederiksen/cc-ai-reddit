# -*- coding: utf-8 -*-
"""rules: a subreddit's rules, read from Reddit itself in the run that needs them.

Never from the archive (its copy of one subreddit's rules was twenty months old
and wrong on 2026-09-10) and never from /about/rules/ (see selectors.py). The
community sidebar is fetched from its own source rather than read off the page,
because on a post page it loads only when scrolled into view (selectors.py,
2026-10-03). A read tells apart the ways it can come back empty: an answer
that is not the sidebar, and a sidebar that has no rules section. Neither is
ever reported as "this subreddit has no rules".
"""
import json
from urllib.parse import quote

from . import guardrails as G, selectors as SEL
from .state import Fail, RUN_ID


def read_sidebar(b, subreddit):
    got = b.js(SEL.RULES_JS % json.dumps(SEL.RULES_PARTIAL % quote(subreddit)))
    if not isinstance(got, dict) or "status" not in got:
        raise Fail("the community sidebar of r/%s could not be fetched from %s (the page answered %r), so "
                   "its rules could not be read" % (subreddit, b.url(), got))
    if "js_challenge" in (got.get("url") or ""):
        raise Fail("the community sidebar of r/%s is behind Reddit's JS challenge (%s), so its rules could "
                   "not be read" % (subreddit, got.get("url")))
    if got["status"] != 200:
        raise Fail("the community sidebar of r/%s answered HTTP %s at %s, so its rules could not be read"
                   % (subreddit, got["status"], got.get("url")))
    if not got.get("sidebar"):
        raise Fail("the community sidebar of r/%s came back without its Community information section (%s), "
                   "so its rules could not be read. Reddit may have changed the sidebar; see RULES_JS in "
                   "selectors.py." % (subreddit, got.get("url")))
    if (got.get("subreddit") or "").lower() != subreddit.lower():
        raise Fail("the community sidebar fetched for r/%s names r/%s, so the rules of r/%s could not be read"
                   % (subreddit, got.get("subreddit"), subreddit))
    if not got.get("rules"):
        raise Fail("the community sidebar of r/%s has no rules section. A write refuses where it cannot "
                   "read rules; look at the subreddit by hand." % subreddit)
    if len(got["rules"]) != got.get("details"):
        raise Fail("the community sidebar of r/%s holds %s rule entries but only %d read as a numbered rule, "
                   "so its rules could not be read whole. Reddit may have changed the sidebar; see RULES_JS "
                   "in selectors.py." % (subreddit, got.get("details"), len(got["rules"])))
    return got["rules"]


def fetch(b, subreddit):
    b.goto(SEL.URL_SUBREDDIT % subreddit)
    return G.record_rules(subreddit, read_sidebar(b, subreddit), b.url(), RUN_ID)


def format_rules(entry):
    lines = ["RULES r/%s, read from the live page at %s:" % (entry["subreddit"], entry["iso"])]
    for r in entry["rules"]:
        lines.append("  %d. %s%s" % (r["n"], r["title"], (": " + r["text"]) if r.get("text") else ""))
    return "\n".join(lines)
