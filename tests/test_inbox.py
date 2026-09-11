# -*- coding: utf-8 -*-
"""inbox: what is waiting for the account, read without marking anything read.

`inbox.read` is driven against a model of the site that answers only the
handles the command uses, each shaped the way Reddit was measured to answer
it. Anything else it is asked is an assertion, so a test cannot pass on a read
the model never served. Every failure test also asserts what WAS done before
the failure, so a read that silently skipped a source could not pass.

No browser, no network. State goes to a temporary directory.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cc_ai_reddit_kit import guardrails as G, inbox as I, selectors as SEL
from cc_ai_reddit_kit.state import Fail

ME = {"name": "someone", "id": "t2_fake"}
OTHER = {"name": "someone_else", "id": "t2_test"}


def thing(kind, **data):
    return {"kind": kind, "data": data}


def listing(children, after=None):
    return json.dumps({"kind": "Listing", "data": {"after": after, "children": children}})


INBOX_EVERY_KIND = [
    thing("t1", name="t1_def456", type="comment_reply", subject="comment reply", new=True, author="replier_one",
          subreddit="SubA", created_utc=1789086730, link_title="A thread", body="A reply to your comment.",
          context="/r/SubA/comments/abc123/a_thread/def456/?context=3"),
    thing("t1", name="t1_ghi789", type="post_reply", subject="post reply", new=False, author="replier_two",
          subreddit="SubB", created_utc=1789000000, link_title="Your post", body="A reply to your post.",
          context="/r/SubB/comments/xyz987/your_post/ghi789/?context=3"),
    thing("t1", name="t1_jkl012", type="username_mention", subject="username mention", new=True,
          author="mentioner", subreddit="SubC", created_utc=1788900000, link_title="Elsewhere",
          body="Mentioning you here.", context="/r/SubC/comments/mno345/elsewhere/jkl012/?context=3"),
    thing("t4", name="t4_pqr678", id="pqr678", subject="a question", new=True, author="writer",
          subreddit=None, created_utc=1788800000, body="A private message."),
]

NOTIFICATIONS = """<faceplate-loader name="NotificationsInbox_x" loading="eager"><script>SML.load([]);</script>
</faceplate-loader><div class="flex flex-col " data-id="notification-container-element">
<mark-all-notifications-seen initial-messages-count="0" last-sent-at="2026-09-11T00:32:17.253000+0000"></mark-all-notifications-seen>
<div class="flex flex-col overflow-visible"><faceplate-tracker source="inbox" action="view" noun="inbox">
<div class="grid"><div class="flex"><div></div><mark-all-messages-read><button rpl class="button">
<span class="flex"><span>Mark all as read</span></span></button></mark-all-messages-read></div></div>
%s
</faceplate-tracker></div></div>"""

REPLY_NOTIFICATION = """<notification-item notification-id="n-1" message-type="COMMENT_REPLY" has-link="true" is-viewed="true">
<rpl-inbox-row hoverable selected role="none" inaccessibleHref="https://www.reddit.com/r/SubA/comments/abc123/a_thread/def456/?context=1" class="nd:hidden">
<div slot="leading"><span rpl avatar><img src="https://example.com/a.png" alt="avatar for notification" class="h-full"></span></div>
<div class="flex"><div class="flex-1"><a rpl class="a" href="https://www.reddit.com/r/SubA/comments/abc123/a_thread/def456/?context=1">
<div data-testid="title"><span class="text-secondary"><span class="line-clamp-2">The post author replied to your comment in r/SubA</span></span></div>
<div class="col-start-2" data-testid="body"><span class="line-clamp-2"> A reply to your comment &amp; more. </span>
<span class="text-neutral" data-testid="sent-at"><faceplate-timeago ts="2026-09-11T00:32:17.253Z" format="narrow">
<time datetime="2026-09-11T00:32:17.253Z" title="Friday">2h ago</time></faceplate-timeago></span></div></a></div></div>
<div slot="trailing"><svg rpl fill="currentColor" viewBox="0 0 20 20"><path d="m18 14z"></path></svg></div>
<notification-context-menu slot="hover-actions" message-type="COMMENT_REPLY" notification-id="n-1" comment-id="t1_def456"></notification-context-menu>
<rpl-inbox-row-touch-only><notification-context-menu slot="hover-actions" message-type="COMMENT_REPLY" notification-id="n-1" comment-id="t1_def456"></notification-context-menu></rpl-inbox-row-touch-only>
</rpl-inbox-row></notification-item>"""

OTHER_NOTIFICATION = """<notification-item notification-id="n-2" message-type="SOME_LATER_TYPE" has-link="true" is-viewed="false">
<rpl-inbox-row hoverable role="none" inaccessibleHref="https://www.reddit.com/r/SubD/comments/stu901/a_post/" class="nd:hidden">
<div class="flex"><a rpl href="https://www.reddit.com/r/SubD/comments/stu901/a_post/">
<div data-testid="title"><span><span class="line-clamp-2">Your post in r/SubD is getting attention</span></span></div>
<div data-testid="body"><span class="line-clamp-2">A post title</span><span data-testid="sent-at">
<faceplate-timeago ts="2026-09-09T08:00:00.000Z"><time>2d ago</time></faceplate-timeago></span></div></a></div>
<notification-context-menu slot="hover-actions" message-type="SOME_LATER_TYPE" notification-id="n-2"></notification-context-menu>
</rpl-inbox-row></notification-item>"""

ANNOUNCEMENT = """<notification-announcement announcement-id="ann_abc123" notification-telemetry-data="{&quot;title&quot;:&quot;News&quot;}">
<rpl-inbox-row hoverable selected role="none" inaccessibleHref="/notifications/a/ann_abc123" class="nd:hidden">
<div class="flex"><a rpl href="/notifications/a/ann_abc123"><div data-testid="title"><span><span class="line-clamp-2">A message from the site</span></span></div>
<div class="col-start-2" data-testid="body">Something the site wants you to know.</div>
<span data-testid="sent-at"><faceplate-timeago ts="2026-06-24T12:47:30.480Z"><time>3mo ago</time></faceplate-timeago></span></a></div>
<announcement-overflow-menu slot="hover-actions" announcement-id="ann_abc123" subject="News" author-name="site_team" author-id="t2_test"></announcement-overflow-menu>
</rpl-inbox-row></notification-announcement>"""


def chat_button(count):
    return ('<faceplate-tracker noun="chat"><reddit-chat-header-button><a href="/chat">'
            '<dynamic-badge id="header-action-item-chat-button-badge" initial-count="%s" set-count-event="" '
            'appearance="ALERT"></dynamic-badge></a></reddit-chat-header-button></faceplate-tracker>' % count)


SIGN_IN_PAGE = "<html><body><shreddit-app><h1>Log In</h1></shreddit-app></body></html>"


class Site(object):
    """www.reddit.com as the inbox command sees it."""

    def __init__(self, me=ME, me_after="same", count=3, inbox=(listing(INBOX_EVERY_KIND),),
                 notifications=NOTIFICATIONS % (REPLY_NOTIFICATION + OTHER_NOTIFICATION + ANNOUNCEMENT),
                 chat=chat_button(2), status=None, final_url=None):
        self._me, self._me_after = me, me if me_after == "same" else me_after
        self.count = count
        self.inbox_pages = list(inbox)
        self.notifications, self.chat = notifications, chat
        self.status = status or {}
        self.final_url = final_url or {}
        self.gotos, self.fetched, self.me_calls = [], [], 0

    def goto(self, url):
        self.gotos.append(url)
        return url

    def me(self):
        self.me_calls += 1
        return self._me if self.me_calls == 1 else self._me_after

    def js(self, expr):
        if expr == SEL.INBOX_COUNT_JS:
            return self.count
        m = re.match(r'^fetch\(("(?:[^"\\]|\\.)*"), ', expr)
        if not m or expr != SEL.FETCH_TEXT_JS % m.group(1):
            raise AssertionError("the site model does not answer: %s" % expr[:120])
        path = json.loads(m.group(1))
        self.fetched.append(path)
        if path.startswith("/message/inbox.json?"):
            text = self.inbox_pages.pop(0)
            key = "inbox"
        elif path == SEL.NOTIFICATIONS_PARTIAL:
            text, key = self.notifications, "notifications"
        elif path == SEL.CHAT_BUTTON_PARTIAL:
            text, key = self.chat, "chat"
        else:
            raise AssertionError("the inbox fetched a path it has no business with: %s" % path)
        return {"status": self.status.get(key, 200), "url": self.final_url.get(key, "https://www.reddit.com" + path),
                "text": text}


class Case(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cc-ai-reddit-inbox-")
        G.pin_account("t2_fake", root=self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def read(self, site, limit=25):
        return I.read(site, limit, root=self.root)


class ARenderedListWithEveryKind(Case):

    def setUp(self):
        Case.setUp(self)
        self.site = Site()
        self.got = self.read(self.site)
        self.rows = self.got["rows"]
        self.by_id = {r["id"]: r for r in self.rows}

    def test_every_kind_is_a_row(self):
        self.assertEqual(sorted(r["kind"] for r in self.rows),
                         ["chat", "comment_reply", "other", "other", "post_reply", "private_message",
                          "username_mention"])
        self.assertEqual((self.got["inbox"], self.got["notifications"], self.got["chat_unread"],
                          self.got["reddit_unread_count"]), (4, 3, 2, 3))

    def test_a_reply_in_the_inbox_and_the_notifications_is_one_row_unread_in_both(self):
        r = self.by_id["t1_def456"]
        self.assertEqual([x["id"] for x in self.rows].count("t1_def456"), 1)
        self.assertEqual(r["sources"], ["inbox", "notifications"])
        self.assertEqual(r["unread_in"], ["inbox", "notifications"])
        self.assertEqual((r["author"], r["subreddit"], r["created"]), ("replier_one", "SubA", "2026-09-11T00:32:10Z"))
        self.assertEqual(r["permalink"], "https://www.reddit.com/comments/abc123/comment/def456/")

    def test_each_row_carries_author_subreddit_time_text_permalink_and_unread(self):
        post = self.by_id["t1_ghi789"]
        self.assertEqual((post["kind"], post["unread"], post["unread_in"]), ("post_reply", False, []))
        self.assertEqual(post["text"], "A reply to your post.")
        mention = self.by_id["t1_jkl012"]
        self.assertEqual((mention["kind"], mention["author"], mention["unread"]), ("username_mention", "mentioner", True))
        pm = self.by_id["t4_pqr678"]
        self.assertEqual((pm["kind"], pm["label"], pm["subreddit"]), ("private_message", "a question", None))
        self.assertEqual(pm["permalink"], "https://www.reddit.com/message/messages/pqr678/")

    def test_other_notifications_keep_reddits_own_label(self):
        later = self.by_id["n-2"]
        self.assertEqual((later["kind"], later["label"], later["subreddit"]), ("other", "SOME_LATER_TYPE", "SubD"))
        self.assertEqual((later["unread"], later["created"]), (False, "2026-09-09T08:00:00Z"))
        self.assertEqual(later["text"], "A post title", "the time label was read as part of the text")
        ann = self.by_id["ann_abc123"]
        self.assertEqual((ann["kind"], ann["label"], ann["author"], ann["unread"]),
                         ("other", "announcement", "site_team", True))
        self.assertEqual((ann["title"], ann["text"]), ("A message from the site", "Something the site wants you to know."))
        self.assertEqual(ann["permalink"], "https://www.reddit.com/notifications/a/ann_abc123")

    def test_chat_leads_as_a_count_and_the_rest_is_newest_first(self):
        self.assertEqual(self.rows[0]["kind"], "chat")
        self.assertIn("shows 2 unread", self.rows[0]["text"])
        created = [r["created"] for r in self.rows[1:]]
        self.assertEqual(created, sorted(created, reverse=True))

    def test_only_the_home_page_is_opened_and_the_inbox_is_read_with_mark_false(self):
        self.assertEqual(self.site.gotos, [SEL.URL_HOME])
        self.assertEqual(len(self.site.fetched), 3, self.site.fetched)
        inbox_reads = [p for p in self.site.fetched if p.startswith("/message/inbox.json?")]
        self.assertEqual(len(inbox_reads), 1)
        self.assertTrue(all("mark=false" in p for p in inbox_reads), inbox_reads)

    def test_the_limit_holds_across_sources(self):
        rows = self.read(Site(), limit=3)["rows"]
        self.assertEqual([r["kind"] for r in rows], ["chat", "comment_reply", "post_reply"])

    def test_a_limit_past_one_page_follows_the_listing(self):
        first = [thing("t1", name="t1_a%03d" % i, type="post_reply", subject="post reply", new=True, author="x",
                       subreddit="SubA", created_utc=1789000000 - i, body="b",
                       context="/r/SubA/comments/abc123/t/a%03d/" % i) for i in range(100)]
        site = Site(inbox=(listing(first, after="t1_a099"), listing(INBOX_EVERY_KIND)))
        got = self.read(site, limit=150)
        reads = [p for p in site.fetched if p.startswith("/message/inbox.json?")]
        self.assertEqual(reads, [SEL.INBOX_JSON % (100, ""), SEL.INBOX_JSON % (50, "&after=t1_a099")])
        self.assertEqual(got["inbox"], 104)


class ARenderedEmptyList(Case):

    def test_zero_rows_when_every_source_rendered_empty(self):
        site = Site(count=0, inbox=(listing([]),), notifications=NOTIFICATIONS % "", chat=chat_button(0))
        got = self.read(site)
        self.assertEqual(got["rows"], [])
        self.assertEqual((got["inbox"], got["notifications"], got["chat_unread"]), (0, 0, 0))
        self.assertEqual(len(site.fetched), 3, "an empty result was reported without reading every source")


class AnUnrenderedPage(Case):

    def assertFailsAfter(self, site, fetched, words):
        with self.assertRaises(Fail) as ctx:
            self.read(site)
        self.assertIn(words, str(ctx.exception))
        self.assertEqual(len(site.fetched), fetched, site.fetched)

    def test_a_sign_in_page_instead_of_the_inbox_fails(self):
        self.assertFailsAfter(Site(inbox=(SIGN_IN_PAGE,)), 1, "did not answer JSON")

    def test_an_inbox_answer_that_is_not_a_listing_fails(self):
        self.assertFailsAfter(Site(inbox=(json.dumps({"kind": "t2", "data": {}}),)), 1, "did not answer a listing")

    def test_a_refused_inbox_fails(self):
        self.assertFailsAfter(Site(status={"inbox": 403}), 1, "HTTP 403")

    def test_a_challenge_fails(self):
        site = Site(final_url={"notifications": "https://www.reddit.com/?js_challenge=1"})
        self.assertFailsAfter(site, 2, "JS challenge")

    def test_a_notifications_list_that_did_not_render_fails_rather_than_reading_as_empty(self):
        self.assertFailsAfter(Site(notifications=SIGN_IN_PAGE), 2, "did not render")

    def test_a_notification_without_its_unread_marker_fails(self):
        broken = NOTIFICATIONS % REPLY_NOTIFICATION.replace("rpl-inbox-row", "some-other-row")
        self.assertFailsAfter(Site(notifications=broken), 2, "has no rpl-inbox-row")

    def test_a_chat_button_without_its_count_fails(self):
        self.assertFailsAfter(Site(chat="<faceplate-tracker></faceplate-tracker>"), 3, "unread count")
        self.assertFailsAfter(Site(chat=chat_button("")), 3, "unread count")


class TheWrongAccount(Case):

    def assertFailsBeforeReading(self, site, words):
        with self.assertRaises(Fail) as ctx:
            self.read(site)
        self.assertIn(words, str(ctx.exception))
        self.assertEqual(site.gotos, [SEL.URL_HOME], "the account was not checked on a page")
        self.assertEqual(site.fetched, [], "the inbox was read before the account was checked")

    def test_signed_in_as_another_account_fails_before_anything_is_read(self):
        self.assertFailsBeforeReading(Site(me=OTHER), "pinned to t2_fake")

    def test_signed_out_fails_before_anything_is_read(self):
        self.assertFailsBeforeReading(Site(me=None), "not signed in")

    def test_no_pinned_account_fails_before_anything_is_read(self):
        os.remove(os.path.join(self.root, "account.json"))
        self.assertFailsBeforeReading(Site(), "no account is pinned")

    def test_an_account_that_changes_during_the_read_fails(self):
        site = Site(me_after=OTHER)
        with self.assertRaises(Fail) as ctx:
            self.read(site)
        self.assertIn("changed while the inbox was read", str(ctx.exception))
        self.assertEqual(len(site.fetched), 3)


if __name__ == "__main__":
    unittest.main()
