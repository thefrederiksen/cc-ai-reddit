# -*- coding: utf-8 -*-
"""scan: the newest (or hottest) posts of a subreddit.

Default source is the Arctic Shift archive: no browser, no load on Reddit.
--live reads the listing page in the browser instead. The two report different
things on purpose and name them differently:

  archive  comments        counted from the archived comments (the archive's
                           own num_comments is frozen at ingest and reads 0)
           score_archived  whatever score the archive holds; for a post a few
                           hours old that is the score seconds after posting
  live     comments, score what the page shows right now

           removed         the archive's removed_by_category, e.g. "moderator" or
                           "automod_filtered", when it has learned of a removal

MEASURED 2026-09-10: the archive listed 8 posts in 7 hours where the live /new
page showed 4 in 24. The difference was posts removed by moderators or held by
AutoModerator after they were ingested; nobody reading the subreddit sees them.
An archive scan is a list of candidates. Confirm one with `thread` before
acting on it.

The archive has no hot ranking, since its scores are not live, so --hot needs
--live; asking the archive for it fails rather than quietly sorting by new.
"""
import datetime
import re
import time

from . import archive, selectors as SEL
from .state import Fail


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def parse_ts(s):
    """A Reddit page timestamp (2026-09-10T13:38:33.138000+0000) as epoch seconds."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?(?:Z|[+-]00:?00)$", s or "")
    if not m:
        raise Fail("unexpected timestamp %r on the page; the markup changed (see selectors.py)" % s)
    return datetime.datetime(*map(int, m.groups()), tzinfo=datetime.timezone.utc).timestamp()


def scan_archive(sub, sort, limit, max_age_hours):
    if sort != "new":
        raise Fail("the archive has no hot ranking: its scores are not live. Use --live --hot.")
    now = time.time()
    # Whole rows, not a field list: `permalink` is not a selectable field, and
    # the permalink is needed with its slug (see threads.canonical).
    posts = archive.subreddit_posts(sub, now - max_age_hours * 3600, limit)
    note = None
    if not posts:
        newest = archive.newest_post(sub)
        if not newest:
            raise Fail("the archive holds no posts at all for r/%s. Check the subreddit name." % sub)
        note = ("no posts in the last %g hours; the newest archived post in r/%s is %.1f hours old"
                % (max_age_hours, newest.get("subreddit") or sub, (now - newest["created_utc"]) / 3600))
        return [], note
    counts = archive.comment_counts(sub, min(p["created_utc"] for p in posts))
    rows = []
    for p in posts:
        name = p.get("subreddit") or sub
        rows.append({"source": "archive", "subreddit": name, "id": p["id"], "title": p.get("title"),
                     "author": p.get("author"), "flair": p.get("link_flair_text"), "created": iso(p["created_utc"]),
                     "age_hours": round((now - p["created_utc"]) / 3600, 1), "comments": counts.get(p["id"], 0),
                     "score_archived": p.get("score"), "removed": p.get("removed_by_category"),
                     "nsfw": bool(p.get("over_18")), "permalink": "https://www.reddit.com" + p["permalink"]})
    return rows, note


def scan_live(b, sub, sort, limit, max_age_hours):
    url = SEL.URL_LISTING % (sub, sort)
    b.goto(url)
    b.wait_for("document.querySelectorAll('shreddit-post').length", "posts on %s" % url)
    now = time.time()
    cutoff = now - max_age_hours * 3600
    seen, stalls = {}, 0
    while True:
        before = len(seen)
        for r in b.js(SEL.LISTING_JS):
            if r["id"] and r["id"] not in seen:
                seen[r["id"]] = r
        fresh = [r for r in seen.values() if parse_ts(r["created"]) >= cutoff]
        past_window = sort == "new" and any(parse_ts(r["created"]) < cutoff for r in seen.values())
        if len(fresh) >= limit or past_window:
            break
        stalls = stalls + 1 if len(seen) == before else 0
        if stalls >= 3:
            break
        b.scroll_down()
    rows = []
    for r in seen.values():
        ts = parse_ts(r["created"])
        if ts < cutoff:
            continue
        rows.append({"source": "live", "subreddit": r["subreddit"] or sub, "id": r["id"], "title": r["title"],
                     "author": r["author"], "flair": r["flair"], "created": iso(ts),
                     "age_hours": round((now - ts) / 3600, 1), "comments": int(r["comments"] or 0),
                     "score": int(r["score"] or 0), "permalink": "https://www.reddit.com" + r["permalink"]})
    note = "%d posts rendered after scrolling; %d inside the last %g hours" % (len(seen), len(rows), max_age_hours)
    return rows[:limit], note
