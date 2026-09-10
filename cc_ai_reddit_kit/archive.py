# -*- coding: utf-8 -*-
"""Reading Reddit through the Arctic Shift archive.

Arctic Shift (https://github.com/ArthurHeitmann/arctic_shift) is a free public
archive of Reddit run by one person. No key, no approval, no JS challenge. It is
the default for listings, history and bulk reads because it keeps load off
Reddit and off the browser. Be a good citizen of it: every response is cached
on disk, requests are spaced, and the User-Agent names this tool.

MEASURED 2026-09-10, and each of these shapes the code below:
  * Posts are ingested about 15 to 30 seconds after they are created
    (retrieved_on - created_utc). Close enough to live for a daily scan.
  * BUT score and num_comments are FROZEN at ingest. A thread with ten live
    comments and score 2 read back as num_comments 0, score 1. A filter on
    either field would be a filter on nothing. So `scan` COUNTS comments from
    the archived comments instead, and reports score as score_at_ingest.
  * Comments are ingested continuously too: the archived tree of that same
    thread held all ten comments.
  * Subreddit rules in the archive are stale (the copy for one subreddit was
    retrieved in January 2025 and no longer matched the live sidebar). Rules
    are therefore read from the live page, never from here.
  * "Timeout. Maybe slow down a bit" comes back as HTTP 422, for a query that
    succeeded a minute later unchanged. It is load, not a bad query: wait and
    ask again, a bounded number of times, then fail saying so.
  * Comment search by author refuses very active accounts with 422.
  * Every response carries X-RateLimit-Reset (seconds). A 429 means wait that.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .state import Fail, log, read_json, sub_dir, write_json_atomic

BASE = "https://arctic-shift.photon-reddit.com"
STATUS_PAGE = "https://status.arctic-shift.photon-reddit.com"
USER_AGENT = "cc-ai-reddit/%s (+https://github.com/thefrederiksen/cc-ai-reddit)" % __version__

MIN_GAP = 1.0                   # seconds between requests from this process
RETRY_WAITS = (5, 15, 40)       # after a 422 timeout or a network error
MAX_RATE_WAIT = 120             # never sit longer than this on one 429
PAGE = 100

_last_request = [0.0]


def _cache_path(url):
    return os.path.join(sub_dir("cache", "archive"), hashlib.sha1(url.encode()).hexdigest() + ".json")


def _url(path, params):
    clean = {k: v for k, v in params.items() if v is not None}
    return BASE + path + "?" + urllib.parse.urlencode(clean)


def get(path, params, ttl, cache=True):
    """GET one archive endpoint, served from the disk cache when younger than
    `ttl` seconds. Returns the parsed body. Raises Fail with the reason and
    what to do about it; never returns an empty stand-in. Bulk downloads pass
    cache=False: they keep their own files and would only double the disk."""
    url = _url(path, params)
    cached = read_json(_cache_path(url)) if cache else None
    if cached and ttl and time.time() - cached["fetched_at"] < ttl:
        return cached["body"]
    last = None
    for attempt in range(len(RETRY_WAITS) + 1):
        wait = MIN_GAP - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read().decode("utf-8"))
            if body.get("error"):
                last = "archive answered 200 with error %r" % body["error"]
            else:
                if cache:
                    write_json_atomic(_cache_path(url), {"url": url, "fetched_at": time.time(), "body": body})
                return body
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")[:200]
            if exc.code == 429:
                reset = min(int(exc.headers.get("X-RateLimit-Reset") or 30), MAX_RATE_WAIT)
                log("archive rate limit: waiting %ds as it asks" % reset)
                time.sleep(reset)
                last = "HTTP 429"
                continue
            if exc.code == 422 and "timeout" in text.lower():
                last = "HTTP 422 %s" % text
            else:
                raise Fail("Arctic Shift refused %s: HTTP %d %s" % (url, exc.code, text))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = "network: %s" % exc
        if attempt < len(RETRY_WAITS):
            log("archive: %s; asking again in %ds" % (last, RETRY_WAITS[attempt]))
            time.sleep(RETRY_WAITS[attempt])
    raise Fail("Arctic Shift did not answer %s after %d attempts (last: %s). It is a free "
               "service with no uptime promise; check %s and try later, or use --live."
               % (url, len(RETRY_WAITS) + 1, last, STATUS_PAGE))


def _page_desc(path, params, after, limit, ttl, max_pages):
    """Page a search endpoint newest-first, from now back to `after`. The next
    page starts one second above the oldest row seen, so rows sharing that
    second are not skipped; ids already held are dropped. Returns rows."""
    rows, seen, before = [], set(), None
    for _ in range(max_pages):
        q = dict(params, sort="desc", limit=PAGE, after=int(after), before=before)
        batch = get(path, q, ttl).get("data") or []
        fresh = [r for r in batch if r["id"] not in seen]
        seen.update(r["id"] for r in fresh)
        rows.extend(fresh)
        if limit and len(rows) >= limit:
            return rows[:limit]
        if len(batch) < PAGE or not fresh:
            return rows
        before = int(batch[-1]["created_utc"]) + 1
    raise Fail("more than %d pages (%d rows) matched %s since %s; narrow the window "
               "(--max-age-hours or --days) rather than read a partial count"
               % (max_pages, len(rows), params, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(after))))


def subreddit_posts(sub, after, limit, ttl=300, fields=None):
    return _page_desc("/api/posts/search", {"subreddit": sub, "fields": fields}, after, limit, ttl, max_pages=20)


def newest_post(sub, ttl=300):
    """The newest archived post of a subreddit at all, or None. Tells a quiet
    window apart from a subreddit the archive does not know."""
    data = get("/api/posts/search", {"subreddit": sub, "limit": 1, "sort": "desc",
                                     "fields": "id,created_utc,subreddit"}, ttl).get("data") or []
    return data[0] if data else None


def comment_counts(sub, after, ttl=300):
    """How many archived comments each post has, for comments created since
    `after`. Pass the creation time of the oldest post being counted: no comment
    on a post can be older than the post."""
    rows = _page_desc("/api/comments/search", {"subreddit": sub, "fields": "id,link_id,created_utc"},
                      after, None, ttl, max_pages=30)
    counts = {}
    for r in rows:
        link = (r.get("link_id") or "").split("_")[-1]
        counts[link] = counts.get(link, 0) + 1
    return counts


def post(post_id, ttl=120):
    data = get("/api/posts/ids", {"ids": post_id}, ttl).get("data") or []
    if not data:
        raise Fail("the archive has no post with id %s (it may be too new by a few seconds, or "
                   "not exist)" % post_id)
    return data[0]


def comment_tree(post_id, ttl=120):
    return get("/api/comments/tree", {"link_id": "t3_" + post_id, "limit": 9999}, ttl).get("data") or []


def user_comments(author, after, limit, ttl=300):
    return _page_desc("/api/comments/search", {"author": author,
                                               "fields": "id,author,subreddit,link_id,parent_id,created_utc,score,body"},
                      after, limit, ttl, max_pages=10)


def user_posts(author, after, limit, ttl=300):
    # Whole rows: `permalink` is not a selectable field and is needed with its slug.
    return _page_desc("/api/posts/search", {"author": author}, after, limit, ttl, max_pages=10)


def flatten_tree(nodes):
    """The archive's Reddit-shaped tree as a flat list of comment dicts, each
    with `depth`. Collapsed 'more' stubs are returned as kind 'more' so a caller
    can tell a complete tree from a truncated one."""
    out = []

    def walk(items, depth):
        for n in items:
            d = dict(n.get("data") or {})
            d["kind"] = n.get("kind")
            d["depth"] = depth
            replies = d.pop("replies", None)
            out.append(d)
            if isinstance(replies, dict):
                walk((replies.get("data") or {}).get("children") or [], depth + 1)
            elif isinstance(replies, list):
                walk(replies, depth + 1)

    walk(nodes, 0)
    return out
