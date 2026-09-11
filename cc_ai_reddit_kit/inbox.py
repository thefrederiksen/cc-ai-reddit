# -*- coding: utf-8 -*-
"""inbox: everything on Reddit waiting for the signed-in account, read without
marking any of it read.

Three sources, each fetched from inside a www.reddit.com page so that none of
Reddit's page scripts runs (what opening the pages instead does is measured in
selectors.py, beside the handles):

  inbox          the message inbox, /message/inbox.json?mark=false: comment
                 replies, post replies, username mentions, private messages,
                 each with Reddit's unread flag
  notifications  the list the bell opens, as server-rendered HTML: replies,
                 messages from Reddit, anything else it notifies about
  chat           the chat button's unread count. Chat is not opened: opening it
                 opens a conversation, so chat is counted, not listed. Whether
                 Reddit counts a pending chat request on that button was not
                 observed (the account measured had none).

A reply that is both in the inbox and in the notifications is one row, with
the places it is unread in `unread_in`.

Nothing is reported that was not rendered. A source that answers anything but
its own rendered list - a sign-in page, a challenge, a changed page - is a
FAIL naming the source, and zero rows are reported only when every source
rendered. The account is checked before and after the read: signed out, or
signed in as anyone but the pinned account, is a FAIL.
"""
import json
import re
from html.parser import HTMLParser

from . import guardrails as G, selectors as SEL, threads as T
from .listings import iso, parse_ts
from .state import Fail

KINDS = ("comment_reply", "post_reply", "username_mention", "private_message")
MAX_PAGE = 100


# ---------------------------------------------------------------- the account

def check_account(me, pin):
    """An inbox belongs to one account. Fail unless the browser is signed in as
    the pinned one."""
    if not me or not me.get("id"):
        raise Fail("the browser is not signed in to Reddit. An inbox belongs to one account; sign the "
                   "browser in by hand (this tool never signs in).")
    if not pin:
        raise Fail("no account is pinned, so there is no telling whose inbox this is. The owner pins the "
                   "one account this machine works as: cc-ai-reddit account --pin")
    if pin.get("account_id") != me["id"]:
        raise Fail("the browser is signed in as %s but this machine is pinned to %s. The inbox is read only "
                   "as the pinned account." % (me["id"], pin.get("account_id")))


# ---------------------------------------------------------------- html

_VOID = frozenset("area base br col embed hr img input link meta source track wbr".split())


class Node(object):

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children = []


class _Tree(HTMLParser):
    """The document as a tree. Tag and attribute names come out lowercased."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = self.cur = Node("#document", {}, None)

    def _node(self, tag, attrs):
        node = Node(tag, {k: ("" if v is None else v) for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._node(tag, attrs)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(html):
    tree = _Tree()
    tree.feed(html or "")
    tree.close()
    return tree.root


def walk(node):
    for c in node.children:
        if isinstance(c, Node):
            yield c
            yield from walk(c)


def first(node, pred):
    return next((n for n in walk(node) if pred(n)), None)


def text_of(node, skip=lambda n: False):
    parts = []

    def rec(n):
        for c in n.children:
            if isinstance(c, str):
                parts.append(c)
            elif c.tag not in ("script", "style", "svg") and not skip(c):
                rec(c)

    rec(node)
    return " ".join("".join(parts).split())


# ---------------------------------------------------------------- the three sources

def _permalink(href):
    if not href:
        return None
    full = href if href.startswith("http") else "https://www.reddit.com" + href
    return T.canonical(full) if SEL.PERMALINK.match(full) else full


def inbox_rows(listing):
    """Rows from one page of /message/inbox.json."""
    children = (listing.get("data") or {}).get("children") if isinstance(listing, dict) else None
    if listing.get("kind") != "Listing" or not isinstance(children, list):
        raise Fail("the message inbox did not answer a listing (kind %r); the page changed (see "
                   "INBOX_JSON in selectors.py)" % listing.get("kind"))
    rows = []
    for c in children:
        d = c.get("data") or {}
        if c.get("kind") == "t4":
            kind, label = "private_message", d.get("subject") or "private message"
            permalink = "https://www.reddit.com/message/messages/%s/" % d.get("id")
        elif c.get("kind") == "t1":
            kind = d.get("type") if d.get("type") in KINDS else "other"
            label = d.get("subject") or d.get("type")
            permalink = _permalink(d.get("context"))
        else:
            kind, label, permalink = "other", c.get("kind"), None
        rows.append({"kind": kind, "label": label, "id": d.get("name"), "author": d.get("author"),
                     "subreddit": d.get("subreddit"),
                     "created": iso(d["created_utc"]) if d.get("created_utc") else None,
                     "title": d.get("link_title"), "text": d.get("body"), "permalink": permalink,
                     "unread": bool(d.get("new")), "source": "inbox"})
    return rows


def _testid(value):
    return lambda n: n.attrs.get("data-testid") == value


def _notification(n):
    row = first(n, lambda x: x.tag == "rpl-inbox-row")
    title = first(n, _testid("title"))
    if row is None or title is None:
        raise Fail("a %s on the notifications list has no %s; the page changed (see NOTIFICATIONS_PARTIAL "
                   "in selectors.py)" % (n.tag, "rpl-inbox-row" if row is None else "title"))
    body = first(n, _testid("body"))
    when = first(n, lambda x: x.tag == "faceplate-timeago")
    link = first(n, lambda x: x.tag == "a" and x.attrs.get("href"))
    href = link.attrs["href"] if link else row.attrs.get("inaccessiblehref")
    title_text = text_of(title)
    out = {"title": title_text, "text": text_of(body, skip=_testid("sent-at")) if body else "",
           "created": iso(parse_ts(when.attrs.get("ts"))) if when else None, "permalink": _permalink(href),
           "unread": "selected" in row.attrs, "source": "notifications"}
    sub = re.search(r"/r/([A-Za-z0-9_]+)/", href or "") or re.search(r"\br/([A-Za-z0-9_]+)", title_text)
    out["subreddit"] = sub.group(1) if sub else None
    if n.tag == "notification-announcement":
        menu = first(n, lambda x: x.tag == "announcement-overflow-menu")
        out.update(kind="other", label="announcement", id=n.attrs.get("announcement-id"),
                   author=menu.attrs.get("author-name") if menu else None)
    else:
        mtype = n.attrs.get("message-type") or ""
        menu = first(n, lambda x: x.tag == "notification-context-menu")
        author = re.search(r"\bu/([A-Za-z0-9_-]{3,20})", title_text)
        out.update(kind=mtype.lower() if mtype.lower() in KINDS else "other", label=mtype,
                   id=(menu.attrs.get("comment-id") if menu else None) or n.attrs.get("notification-id"),
                   author=author.group(1) if author else None)
    return out


def notification_rows(html):
    """Rows from the notifications list partial. Fails when the list did not render."""
    root = parse_html(html)
    box = first(root, lambda n: n.attrs.get("data-id") == SEL.NOTIFICATIONS_CONTAINER)
    if box is None:
        raise Fail("the notifications list did not render: no %s in what Reddit answered (%r). A sign-in "
                   "page or a challenge looks like this." % (SEL.NOTIFICATIONS_CONTAINER, text_of(root)[:120]))
    return [_notification(n) for n in walk(box) if n.tag in ("notification-item", "notification-announcement")]


def chat_unread(html):
    """The chat button's unread count. Fails when the button did not render one."""
    badge = first(parse_html(html), lambda n: n.tag == "dynamic-badge" and n.attrs.get("id") == SEL.CHAT_BADGE_ID)
    count = badge.attrs.get("initial-count", "") if badge else ""
    if not re.match(r"^\d+$", count):
        raise Fail("the chat button did not render its unread count (no %s with a number); the page changed "
                   "or is not signed in" % SEL.CHAT_BADGE_ID)
    return int(count)


def merge(inbox, notifications, chat_count):
    """One list, newest first: the chat count leads when there is one."""
    rows, by_id = [], {}
    for r in inbox + notifications:
        twin = by_id.get(r["id"]) if r["id"] else None
        if twin is not None:
            twin["sources"].append(r["source"])
            if r["unread"]:
                twin["unread_in"].append(r["source"])
            continue
        row = {k: v for k, v in r.items() if k not in ("unread", "source")}
        row.update(sources=[r["source"]], unread_in=[r["source"]] if r["unread"] else [])
        rows.append(row)
        if r["id"]:
            by_id[r["id"]] = row
    for row in rows:
        row["unread"] = bool(row["unread_in"])
    rows.sort(key=lambda r: r["created"] or "", reverse=True)
    if chat_count:
        rows.insert(0, {"kind": "chat", "label": "chat button count", "id": None, "author": None,
                        "subreddit": None, "created": None, "title": None,
                        "text": "Reddit's chat button shows %d unread. Not listed: this tool does not open chat, "
                                "because opening it opens a conversation and marks it read. Read chat by hand."
                                % chat_count,
                        "permalink": SEL.URL_CHAT, "sources": ["chat"], "unread_in": ["chat"], "unread": True})
    return rows


# ---------------------------------------------------------------- the read

def _fetch(b, path, what):
    got = b.js(SEL.FETCH_TEXT_JS % json.dumps(path))
    if not isinstance(got, dict) or "status" not in got:
        raise Fail("%s could not be fetched from the page (the page answered %r)" % (what, got))
    if got["status"] != 200:
        raise Fail("%s answered HTTP %s at %s" % (what, got["status"], got.get("url")))
    if "js_challenge" in (got.get("url") or ""):
        raise Fail("%s is behind Reddit's JS challenge (%s); nothing was read" % (what, got.get("url")))
    return got.get("text") or ""


def read_inbox(b, limit):
    rows, after = [], None
    while len(rows) < limit:
        path = SEL.INBOX_JSON % (min(MAX_PAGE, limit - len(rows)), "&after=" + after if after else "")
        text = _fetch(b, path, "the message inbox")
        try:
            listing = json.loads(text)
        except ValueError:
            raise Fail("the message inbox did not answer JSON (it began %r): a sign-in page or a challenge, "
                       "not an inbox" % text[:80])
        if not isinstance(listing, dict):
            raise Fail("the message inbox answered %r, not a listing" % text[:80])
        rows += inbox_rows(listing)
        after = listing["data"].get("after")
        if not after:
            break
    return rows[:limit]


def read(b, limit, root=None):
    """Everything waiting for the pinned account. Marks nothing read."""
    if limit < 1:
        raise Fail("--limit must be at least 1")
    b.goto(SEL.URL_HOME)
    me = b.me()
    check_account(me, G.pinned_account(root))
    counted = b.js(SEL.INBOX_COUNT_JS)
    if not isinstance(counted, int):
        raise Fail("Reddit did not answer the account's unread count (it answered %r)" % counted)
    inbox = read_inbox(b, limit)
    notifications = notification_rows(_fetch(b, SEL.NOTIFICATIONS_PARTIAL, "the notifications list"))
    chat = chat_unread(_fetch(b, SEL.CHAT_BUTTON_PARTIAL, "the chat button"))
    after = b.me()
    if not after or after.get("id") != me["id"]:
        raise Fail("the signed-in account changed while the inbox was read; nothing read is reported")
    rows = merge(inbox, notifications, chat)
    return {"account": me, "rows": rows[:limit], "held": len(rows), "inbox": len(inbox),
            "notifications": len(notifications), "chat_unread": chat, "reddit_unread_count": counted}
