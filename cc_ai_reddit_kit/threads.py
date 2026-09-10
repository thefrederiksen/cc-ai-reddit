# -*- coding: utf-8 -*-
"""thread: one post and its comments.

Default source is the live page, because the point of reading one thread is
its state right now. --archive reads the archived comment tree instead, which
holds every comment without scrolling but carries archived scores.

Reddit lazy-loads comments. A live read scrolls until the rendered count stops
growing and then REPORTS how many it holds against the count the post states.
It never presents a partial tree as the whole one.
"""
import time

from . import archive, selectors as SEL
from .listings import iso
from .state import Fail


def parse_permalink(url):
    """(subreddit or None, post id, comment id or None) from a thread or
    comment permalink. The subreddit is as written in the URL, which may not be
    its real case; the page is the authority on that."""
    u = (url or "").strip()
    if u.startswith("/r/") or u.startswith("/comments/"):
        u = "https://www.reddit.com" + u
    m = SEL.PERMALINK.match(u)
    if not m:
        raise Fail("not a Reddit permalink: %r. Expected https://www.reddit.com/r/<sub>/comments/<id>/... "
                   "(a thread) or .../comments/<id>/comment/<id>/ (a comment)" % url)
    return m.group(2), m.group(3), m.group(4)


def canonical(url):
    """The form of a permalink the site renders, whatever form was given. See
    URL_THREAD in selectors.py for the forms that load nothing."""
    sub, post_id, comment_id = parse_permalink(url)
    if comment_id:
        return SEL.URL_COMMENT % (post_id, comment_id)
    return SEL.URL_THREAD % post_id


def _full(link):
    return ("https://www.reddit.com" + link) if link and link.startswith("/") else link


def read_live(b, url):
    sub, post_id, comment_id = parse_permalink(url)
    b.goto(canonical(url))
    data = b.wait_for(SEL.THREAD_JS, "the post on %s" % canonical(url))
    if data["post"]["id"] != post_id:
        raise Fail("asked for post %s and the page holds post %s" % (post_id, data["post"]["id"]))
    total = int(data["post"]["comments"] or 0)
    stalls = 0
    while comment_id is None and len(data["comments"]) < total and stalls < 3:
        held = len(data["comments"])
        b.scroll_down(2)
        data = b.js(SEL.THREAD_JS)
        stalls = stalls + 1 if len(data["comments"]) == held else 0
    post = data["post"]
    post["permalink"] = _full(post["permalink"])
    for c in data["comments"]:
        c["permalink"] = _full(c["permalink"])
    if comment_id and not any(c["id"] == comment_id for c in data["comments"]):
        raise Fail("comment %s is not on its own permalink page; it may be deleted" % comment_id)
    return {"source": "live", "post": post, "comments": data["comments"],
            "comments_held": len(data["comments"]), "comments_stated": total, "signed_in": b.signed_in()}


def read_archive(url):
    sub, post_id, comment_id = parse_permalink(url)
    p = archive.post(post_id)
    nodes = archive.flatten_tree(archive.comment_tree(post_id))
    comments = [{"id": c.get("id"), "parent": c.get("parent_id"), "depth": c["depth"], "author": c.get("author"),
                 "created": iso(c["created_utc"]) if c.get("created_utc") else None,
                 "score_archived": c.get("score"), "text": c.get("body"),
                 "permalink": "https://www.reddit.com/r/%s/comments/%s/comment/%s/" % (p.get("subreddit"), post_id, c.get("id"))}
                for c in nodes if c.get("kind") == "t1"]
    collapsed = sum(len(c.get("children") or []) for c in nodes if c.get("kind") == "more")
    post = {"id": p["id"], "title": p.get("title"), "author": p.get("author"), "subreddit": p.get("subreddit"),
            "created": iso(p["created_utc"]), "score_archived": p.get("score"), "flair": p.get("link_flair_text"),
            "removed": p.get("removed_by_category"), "text": p.get("selftext"),
            "permalink": "https://www.reddit.com" + p["permalink"]}
    return {"source": "archive", "post": post, "comments": comments, "comments_held": len(comments),
            "comments_collapsed_in_archive": collapsed, "fetched": iso(time.time())}
