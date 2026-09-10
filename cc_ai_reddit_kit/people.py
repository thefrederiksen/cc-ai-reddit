# -*- coding: utf-8 -*-
"""me: a user's recent posts and comments, and who is still waiting on them.

Read from the archive. For every thread the user was active in, the archived
comment tree is read once, and each reply to one of the user's posts or
comments counts as ANSWERED only when the user has a comment directly beneath
it. Replies by AutoModerator and by deleted accounts are not waiting on anyone
and are not listed.

The archive ingests comments within seconds, so this is close to live; a reply
deleted after ingest can still appear, and the output says the source.
"""
import re
import time

from . import archive
from .listings import iso
from .state import Fail

NOT_WAITING = {"AutoModerator", "[deleted]"}
MAX_THREADS = 40


def username(raw):
    """A username as typed - `name`, `u/name`, `/u/name` - reduced to the name."""
    name = (raw or "").strip().strip("/")
    for prefix in ("u/", "user/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
    if not re.match(r"^[A-Za-z0-9_-]{3,20}$", name):
        raise Fail("not a Reddit username: %r" % raw)
    return name


def me(username_arg, days, limit):
    name = username(username_arg)
    now = time.time()
    after = now - days * 86400
    comments = archive.user_comments(name, after, limit)
    posts = archive.user_posts(name, after, limit)
    threads = sorted({c["link_id"].split("_")[-1] for c in comments} | {p["id"] for p in posts})
    if len(threads) > MAX_THREADS:
        raise Fail("u/%s was active in %d threads in %d days; reading that many comment trees is not polite "
                   "to the archive. Narrow --days." % (name, len(threads), days))
    trees = {}
    for t in threads:
        trees[t] = [c for c in archive.flatten_tree(archive.comment_tree(t)) if c.get("kind") == "t1"]
    items = []
    for p in posts:
        items.append(_item("post", p["id"], "t3_" + p["id"], p["id"], p.get("subreddit"), p.get("created_utc"),
                           p.get("score"), p.get("title"), trees.get(p["id"], []), name, now,
                           permalink="https://www.reddit.com" + p["permalink"]))
    for c in comments:
        link = c["link_id"].split("_")[-1]
        items.append(_item("comment", c["id"], "t1_" + c["id"], link, c.get("subreddit"), c.get("created_utc"),
                           c.get("score"), c.get("body"), trees.get(link, []), name, now))
    items.sort(key=lambda i: i["created"], reverse=True)
    return name, items, len(threads)


def _item(kind, thing_id, fullname, link, subreddit, created, score, text, tree, user, now, permalink=None):
    replies = [c for c in tree if c.get("parent_id") == fullname and c.get("author") != user
               and c.get("author") not in NOT_WAITING]
    waiting = []
    for r in replies:
        answered = any(c.get("parent_id") == "t1_" + r["id"] and c.get("author") == user for c in tree)
        if not answered:
            waiting.append({"author": r.get("author"), "created": iso(r["created_utc"]),
                            "age_hours": round((now - r["created_utc"]) / 3600, 1),
                            "text": (r.get("body") or "")[:300],
                            "permalink": "https://www.reddit.com/r/%s/comments/%s/comment/%s/" % (subreddit, link, r["id"])})
    if kind == "comment":
        # The archive's own comment permalink is the old /<slug>/<id>/ form,
        # which renders nothing on the current site (measured 2026-09-10).
        permalink = "https://www.reddit.com/r/%s/comments/%s/comment/%s/" % (subreddit, link, thing_id)
    return {"source": "archive", "kind": kind, "id": thing_id, "subreddit": subreddit, "created": iso(created),
            "score_archived": score, "text": (text or "")[:300], "permalink": permalink,
            "replies": len(replies), "unanswered": waiting}
