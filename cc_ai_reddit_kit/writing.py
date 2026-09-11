# -*- coding: utf-8 -*-
"""comment, reply and post: staged by default, sent only with --submit.

Every write stages the way a person would - open the composer, type, read the
text back character for character, screenshot it - and then, by default,
empties the composer, proves it empty and closes it. Nothing is pressed.

THE ONE PRESS. `press_submit` is called from exactly one place, in `run`,
inside `if submit`, after the guardrails have passed a second time under the
sent log's lock and the attempt row has been written. tests/test_writing.py
drives `run` against a recording surface and proves that without --submit the
press never happens and the sent log is never touched.

After a real press the result is VERIFIED, not assumed: the new comment or post
is found by the signed-in author and the exact text, then read again on its own
permalink after a reload, and checked for removal. A press that cannot be
verified is logged as unverified and reported as a failure.
"""
import json
import os
import re
import time

from . import guardrails as G, rules as R, selectors as SEL, threads as T
from .state import Fail, RUN_ID, log, sub_dir

# Measured 2026-09-10: the composers are rich text and post markdown literally.
_MARKDOWN = re.compile(r"\*\*|__|~~|`|^#{1,6}\s|^>\s", re.M)


def prepare(text):
    """(text as it will be typed, its paragraphs). Every non-blank line becomes
    one paragraph; that is how the rich text editor holds it and reads it back."""
    paras = [line.strip() for line in (text or "").replace("\r\n", "\n").split("\n") if line.strip()]
    marks = sorted({m.strip() for m in _MARKDOWN.findall("\n".join(paras))})
    if marks:
        raise Fail("the composer is rich text and posts markdown literally, so %s would appear exactly as "
                   "typed. Write plain text." % ", ".join(repr(m) for m in marks))
    return "\n\n".join(paras), paras


def _norm(s):
    s = (s or "").replace("\u00a0", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in s.split("\n")).strip()


def default_shot(kind):
    return os.path.join(sub_dir("shots"), "%s-%s.png" % (kind, time.strftime("%Y%m%d-%H%M%S")))


# ---------------------------------------------------------------- the flow

def precheck(kind, subreddit, raw_text, title, allow_links, ledger):
    """Everything that can be refused from the text and the log alone, before
    any browser is touched. Returns (text, paragraphs, title)."""
    text, paras = prepare(raw_text)
    title = title.strip() if title is not None else None
    refusals = ledger.check(kind, subreddit, text, title, allow_links)
    if refusals:
        raise refusals[0]
    return text, paras, title


def run(kind, surface, raw_text, title, submit, allow_links, shot, ledger, out=print):
    text, paras, title = precheck(kind, surface.sub, raw_text, title, allow_links, ledger)

    target = surface.open()
    sub = target["subreddit"]
    G.record_rules(sub, surface.rules(), target["permalink"], RUN_ID, root=ledger.root)
    entry, refusal = G.require_rules(sub, RUN_ID, root=ledger.root)
    if refusal:
        raise refusal
    out(R.format_rules(entry))

    me = surface.account()
    if not me:
        raise Fail("the browser is not signed in to Reddit. Writing needs a signed-in profile, and this "
                   "tool never signs in.")

    # Staged from the moment the composer is asked for: a failure halfway
    # through opening it (a flair chosen, a box opened) is still discarded.
    staged = True
    try:
        surface.open_composer()
        got = surface.type(text, paras, title)
        wrong = []
        if _norm(got.get("text")) != _norm(text):
            wrong.append(("text", text, got.get("text")))
        if title is not None and _norm(got.get("title")) != _norm(title):
            wrong.append(("title", title, got.get("title")))
        if wrong:
            path = os.path.join(sub_dir("shots"), "mismatch-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
            with open(path, "w", encoding="utf-8") as f:
                json.dump([{"field": w[0], "want": w[1], "got": w[2]} for w in wrong], f, indent=1)
            raise Fail("the composer does not hold what was typed (%s); details in %s"
                       % (", ".join(w[0] for w in wrong), path))
        shot_path = surface.screenshot(shot or default_shot(kind))
        out("WOULD SEND %s to r/%s as u/%s (%s):" % (kind, sub, me["name"], me["id"]))
        if title is not None:
            out("  title | %s" % title)
        for line in text.split("\n"):
            out("  %s" % ("| " + line if line else "|"))

        if not submit:
            # One discard, and its FAIL is this run's result: a discard that
            # could not prove the box empty is not tried again in `finally`.
            staged = False
            _discard(surface)
            pin = G.pinned_account(ledger.root)
            out("RESULT staged kind=%s sub=%s target=%s account=%s pinned=%s chars=%d shot=%s"
                % (kind, sub, target["permalink"], me["id"],
                   "yes" if pin and pin.get("account_id") == me["id"] else "no", len(text), shot_path))
            return "staged"

        refusal = G.check_account(me["id"], root=ledger.root)
        if refusal:
            raise refusal
        attempt = ledger.reserve(kind, sub, target["permalink"], text, title, me["id"], allow_links)
        surface.press_submit()
        staged = False
        link, detail = surface.verify(text, title, me)
        ledger.record_result(attempt, "verified" if link else "unverified", link, detail)
        if not link:
            raise Fail("submit was pressed but the result could not be verified (%s). It is logged as "
                       "unverified. Look at %s by hand before anything else." % (detail, target["permalink"]))
        out("RESULT sent kind=%s sub=%s permalink=%s verified=yes" % (kind, sub, link))
        return "sent"
    finally:
        if staged:
            try:
                _discard(surface)
            except Exception as exc:
                log("COULD NOT DISCARD the staged text (%s). Check the browser tab." % exc)


def _discard(surface):
    surface.clear()
    surface.cancel()


# ---------------------------------------------------------------- comment and reply

TRIGGER_RECT_JS = ("(() => { const t = document.querySelector('%s'); if (!t) return null; "
                   "const r = t.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2, "
                   "h: r.height}; })()" % SEL.COMPOSER_TRIGGER)
EDITORS_JS = ("[...document.querySelectorAll('shreddit-composer [contenteditable=\"true\"]')]"
              ".filter(e => e.getBoundingClientRect().height > 0).map(e => { const r = e.getBoundingClientRect(); "
              "return {x: r.x + Math.min(40, r.width / 2), y: r.y + r.height / 2, text: e.innerText}; })")


class CommentSurface(object):

    # Set when this run asks for the composer. Only this says a box was never
    # opened; an empty or failed read of the page does not.
    opened = False

    def __init__(self, b, url, reply):
        self.b = b
        self.reply = reply
        self.kind = "reply" if reply else "comment"
        self.sub, self.post_id, self.comment_id = T.parse_permalink(url)
        if reply and not self.comment_id:
            raise Fail("reply takes a COMMENT permalink (.../comments/<post>/comment/<id>/); for the thread "
                       "itself use comment")
        if not reply and self.comment_id:
            raise Fail("comment takes a THREAD permalink; to answer a comment use reply")
        self.url = T.canonical(url)

    def open(self):
        landed = self.b.goto(self.url)
        data = self.b.wait_for(SEL.THREAD_JS, "the post on %s" % self.url)
        post = data["post"]
        if post["id"] != self.post_id:
            raise Fail("asked for post %s and the page holds %s" % (self.post_id, post["id"]))
        if post["locked"] or post["archived"]:
            raise Fail("the thread is %s; nothing can be added to it" % ("locked" if post["locked"] else "archived"))
        self.sub = post["subreddit"] or self.sub
        target = {"subreddit": self.sub, "title": post["title"], "permalink": landed.split("?")[0]}
        if self.reply:
            hit = [c for c in data["comments"] if c["id"] == self.comment_id]
            if not hit:
                raise Fail("comment %s is not on its permalink page" % self.comment_id)
            if SEL.REMOVED_TEXT.search(hit[0]["text"] or "") or hit[0]["author"] in (None, "[deleted]"):
                raise Fail("comment %s is deleted or removed; there is nobody to reply to" % self.comment_id)
        return target

    def rules(self):
        return R.read_sidebar(self.b, self.sub)

    def account(self):
        return self.b.me()

    def _editors(self):
        """Every visible comment editor. Anything but a list is a read that did
        not happen, and an editor that could not be read may still hold text."""
        found = self.b.js(EDITORS_JS)
        if not isinstance(found, list):
            raise Fail("could not read the comment editor on %s (the page answered %r). It may still hold "
                       "text; look at the tab before any other write to this thread." % (self.url, found))
        return found

    def _editor(self):
        found = self._editors()
        if len(found) != 1:
            raise Fail("expected exactly one open comment editor, found %d" % len(found))
        return found[0]

    def _saved(self, remove=False):
        """{matched, chars}: the unsent comment Reddit keeps in this browser for
        this box (see SAVED_DRAFTS_JS), removed first when `remove`."""
        parent = "t1_" + self.comment_id if self.reply else None
        got = self.b.js(SEL.SAVED_DRAFTS_JS % (json.dumps("t3_" + self.post_id), json.dumps(parent),
                                               json.dumps(remove)))
        if not isinstance(got, dict) or "matched" not in got:
            raise Fail("could not read the unsent comments Reddit keeps in this browser (%s), so the comment box "
                       "on %s is not proven empty" % (got.get("error") if isinstance(got, dict) else repr(got),
                                                     self.url))
        return got

    def open_composer(self):
        self.opened = True              # from here on a click may have opened a box that holds text
        with self.b.focused():
            if self.reply:
                rng_js = ("(() => { const c = document.querySelector('shreddit-comment[thingid=\"t1_%s\"]'); "
                          "if (!c) return null; const r = c.getBoundingClientRect(); "
                          "const k = c.querySelector('shreddit-comment'); "
                          "return {y: r.y + 10, h: r.height, top: r.y, bottom: k ? k.getBoundingClientRect().y "
                          ": r.y + r.height}; })()" % self.comment_id)
                self.b.scroll_to(rng_js, "the comment being replied to")
                rng = self.b.js(rng_js)
                buttons = [x for x in self.b.ax(SEL.REPLY_BUTTON) if rng["top"] < x["y"] < rng["bottom"]]
                if len(buttons) != 1:
                    raise Fail("expected one Reply control on comment %s, found %d" % (self.comment_id, len(buttons)))
                self.b.click(buttons[0]["x"], buttons[0]["y"])
            else:
                r = self.b.scroll_to(TRIGGER_RECT_JS, "the comment box")
                self.b.click(r["x"], r["y"])
            self.b.wait_for(EDITORS_JS + ".length", "the opened comment editor", timeout=10)
        time.sleep(1.0)                 # the box fills in any saved text just after it appears

    def type(self, text, paras, title):
        with self.b.focused():
            ed = self._editor()
            self.b.click(ed["x"], ed["y"])
            time.sleep(0.4)
            held = _norm(self._editor()["text"])
            if held:
                # Reddit put back an unsent comment (SAVED_DRAFTS_JS). Typing
                # now would put the draft inside it.
                log("the comment box opened holding %d characters of an earlier unsent comment; emptying it "
                    "before typing" % len(held))
                self.b.select_all_and_delete()
                time.sleep(0.6)
                if _norm(self._editor()["text"]):
                    raise Fail("the comment box opened holding earlier text and it could not be emptied, so "
                               "nothing was typed. Look at the tab.")
            for i, p in enumerate(paras):
                if i:
                    self.b.key("Enter")
                self.b.insert_text(p)
            time.sleep(1.0)
            return {"text": self._editor()["text"]}

    def screenshot(self, path):
        return self.b.screenshot(path)

    def _buttons(self):
        found = self.b.js(SEL.VISIBLE_COMPOSER_BUTTONS_JS)
        return ([x for x in found if x["slot"] == "submit-button"], [x for x in found if x["slot"] == "cancel-button"])

    def clear(self):
        """Empty the open editor and prove it empty. A closed box has nothing in
        its editor; what Reddit saved of it is removed, and proven gone, by
        cancel()."""
        if not self.opened:
            return
        with self.b.focused():
            found = self._editors()
            if not found:
                return
            if len(found) != 1:
                raise Fail("expected exactly one open comment editor, found %d" % len(found))
            self.b.click(found[0]["x"], found[0]["y"])
            self.b.select_all_and_delete()
            time.sleep(0.6)
            if _norm(self._editor()["text"]):
                raise Fail("the comment editor still holds text after clearing it")

    def cancel(self):
        """Close the box, remove the copy Reddit keeps of the unsent text, and
        prove it gone the way the next run would meet it: reload the page,
        reopen the box, read it empty, close it."""
        if not self.opened:
            return
        self._close()
        time.sleep(1.0)                 # a save the page still had pending lands before the copy is removed
        removed = self._saved(remove=True)
        if removed["matched"]:
            log("removed the unsent comment Reddit kept for this box (%d characters saved)" % removed["chars"])
        if self._saved()["matched"]:
            raise Fail("the unsent comment Reddit keeps for the box on %s is still in the browser after "
                       "removing it. Empty that box by hand before any other write to it." % self.url)
        self.b.goto(self.url)
        self.b.wait_for(SEL.THREAD_JS, "the post on %s" % self.url)
        self.open_composer()
        held = _norm(self._editor()["text"])
        if held:
            raise Fail("the comment box on %s reopened holding %d characters after the discard, so Reddit "
                       "still has the unsent text. Empty that box by hand before any other write to it."
                       % (self.url, len(held)))
        self._close()
        if self._saved()["matched"]:
            raise Fail("reopening the empty comment box on %s saved an unsent comment for it again" % self.url)
        log("reopened the comment box on a reloaded page: it is empty")

    def _close(self):
        if not self._editors():
            return
        with self.b.focused():
            _, cancel = self._buttons()
            if len(cancel) != 1:
                raise Fail("expected one Cancel control on the open composer, found %d" % len(cancel))
            self.b.click(cancel[0]["x"], cancel[0]["y"])
            deadline = time.time() + 6
            while self._editors():
                if time.time() > deadline:
                    raise Fail("the composer did not close after Cancel")
                time.sleep(0.5)

    def press_submit(self):
        submit, _ = self._buttons()
        if len(submit) != 1 or submit[0]["disabled"]:
            raise Fail("expected one enabled submit control on the composer, found %r" % submit)
        with self.b.focused():
            self.b.click(submit[0]["x"], submit[0]["y"])
        log("%s pressed" % submit[0]["name"])

    def verify(self, text, title, me):
        want = _norm(text)
        found = None
        deadline = time.time() + 45
        while not found and time.time() < deadline:
            time.sleep(2)
            for c in (self.b.js(SEL.THREAD_JS) or {}).get("comments", []):
                if c["author"] == me["name"] and _norm(c["text"]) == want:
                    found = c
                    break
        if not found:
            return None, "no comment by u/%s with this text appeared within 45 seconds" % me["name"]
        link = "https://www.reddit.com" + found["permalink"]
        self.b.goto(T.canonical(link))
        again = [c for c in (self.b.js(SEL.THREAD_JS) or {}).get("comments", []) if c["id"] == found["id"]]
        if not again:
            return None, "the comment is not on its own permalink after a reload"
        if again[0]["author"] != me["name"] or SEL.REMOVED_TEXT.search(again[0]["text"] or ""):
            return None, "the comment reads as removed after a reload"
        return link, "present on its permalink after a reload, by the signed-in account, text identical"


# ---------------------------------------------------------------- post

# The title is a textarea inside post-composer-title's shadow root. Measured
# 2026-09-10: a click at the centre of its accessibility box did not always
# focus it (a clear left the title in place), so it is clicked by the textarea's
# own rectangle, near its left edge.
TITLE_JS = r"""(() => { let t = null; const walk = r => { const x = r.querySelector('textarea[name="title"]');
  if (x && !t) t = x; r.querySelectorAll('*').forEach(e => { if (e.shadowRoot && !t) walk(e.shadowRoot); }); };
  walk(document); if (!t) return null; const q = t.getBoundingClientRect();
  return {x: q.x + Math.min(60, q.width / 2), y: q.y + q.height / 2, value: t.value}; })()"""
BODY_TEXT_JS = r"""(() => { const e = [...document.querySelectorAll('[contenteditable="true"][name="body"]')]
  .find(x => x.getBoundingClientRect().height > 0); return e ? e.innerText : null; })()"""
POST_BUTTON_STATE_JS = r"""(() => { const out = []; const walk = r => { r.querySelectorAll('button').forEach(b => {
  if (b.innerText.trim() === 'Post') { const q = b.getBoundingClientRect(); if (q.height > 0) out.push({disabled: b.disabled,
  x: q.x + q.width / 2, y: q.y + q.height / 2}); } }); r.querySelectorAll('*').forEach(e => { if (e.shadowRoot) walk(e.shadowRoot); }); };
  walk(document); return out; })()"""


class PostSurface(object):

    kind = "post"

    def __init__(self, b, sub, flair):
        if not re.match(r"^[A-Za-z0-9_]{2,21}$", sub or ""):
            raise Fail("not a subreddit name: %r" % sub)
        self.b = b
        self.sub = sub
        self.flair = flair

    def open(self):
        self.b.goto(SEL.URL_SUBREDDIT % self.sub)
        self.b.wait_for("!!document.querySelector('shreddit-subreddit-header')", "the header of r/%s" % self.sub, 15)
        return {"subreddit": self.sub, "permalink": SEL.URL_SUBREDDIT % self.sub}

    def rules(self):
        return R.read_sidebar(self.b, self.sub)

    def account(self):
        return self.b.me()

    def _one(self, name, roles=("button",), what="control"):
        found = self.b.ax(name, roles)
        if len(found) != 1:
            raise Fail("expected one %s on the post form, found %d" % (what, len(found)))
        return found[0]

    def open_composer(self):
        self.b.goto(SEL.URL_SUBMIT % self.sub)
        deadline = time.time() + 20
        while not self.b.ax(SEL.TITLE_BOX, ("textbox",)):
            if time.time() > deadline:
                raise Fail("the post form did not appear on %s" % self.b.url())
            time.sleep(1)
        with self.b.focused():
            self._choose_flair()

    def _choose_flair(self):
        button = self._one(SEL.FLAIR_BUTTON, what="flair control")
        required = button["name"].endswith("*")
        if not self.flair and not required:
            return
        self.b.click(button["x"], button["y"])
        time.sleep(2)
        options = self.b.ax(re.compile(".+"), ("radio",))
        pick = [o for o in options if o["name"] == self.flair]
        if len(pick) != 1:
            names = [o["name"] for o in options]
            cancel = self.b.ax(SEL.CANCEL_BUTTON)
            if cancel:
                self.b.click(cancel[-1]["x"], cancel[-1]["y"])
            if self.flair:
                raise Fail("r/%s offers no flair named %r. It offers: %s" % (self.sub, self.flair, ", ".join(names)))
            raise Fail("r/%s requires a flair. Pass --flair with one of: %s" % (self.sub, ", ".join(names)))
        self.b.click(pick[0]["x"], pick[0]["y"])
        time.sleep(0.5)
        add = self._one(re.compile(r"^Add$"), what="Add control in the flair dialog")
        self.b.click(add["x"], add["y"])
        time.sleep(1.5)
        if self.b.ax(re.compile(".+"), ("radio",)):
            raise Fail("the flair dialog did not close after Add")
        # MEASURED 2026-09-10: a chosen flair replaces "Add flair and tags" with
        # a button named exactly after the flair and an "edit flair" button. The
        # flair's name is in the modal's shadow root, not in the page's text.
        chip = self.b.ax(re.compile("^%s$" % re.escape(self.flair)))
        if len(chip) != 1 or not self.b.ax(re.compile(r"^edit flair$", re.I)):
            raise Fail("the flair %r does not show on the form after Add" % self.flair)

    def _title(self):
        t = self.b.js(TITLE_JS)
        if not t:
            raise Fail("the post form has no title field")
        return t

    def type(self, text, paras, title):
        with self.b.focused():
            t = self._title()
            self.b.click(t["x"], t["y"])
            time.sleep(0.3)
            self.b.insert_text(title)
            box = self._one(SEL.BODY_BOX, ("textbox",), "body box")
            self.b.click(box["x"], box["y"])
            time.sleep(0.3)
            for i, p in enumerate(paras):
                if i:
                    self.b.key("Enter")
                self.b.insert_text(p)
            time.sleep(1.0)
        return {"title": self._title()["value"], "text": self.b.js(BODY_TEXT_JS)}

    def screenshot(self, path):
        return self.b.screenshot(path)

    def clear(self):
        if "/submit" not in self.b.url():
            return                      # the form was never opened
        if self.b.ax(re.compile(".+"), ("radio",)):
            cancel = self.b.ax(SEL.CANCEL_BUTTON)
            if cancel:
                with self.b.focused():
                    self.b.click(cancel[-1]["x"], cancel[-1]["y"])
                time.sleep(1)
        with self.b.focused():
            box = self._one(SEL.BODY_BOX, ("textbox",), "body box")
            self.b.click(box["x"], box["y"])
            time.sleep(0.3)
            self.b.select_all_and_delete()
            time.sleep(0.4)
            t = self._title()
            self.b.click(t["x"], t["y"])
            time.sleep(0.3)
            self.b.select_all_and_delete()
            time.sleep(0.4)
        if _norm(self._title()["value"]) or _norm(self.b.js(BODY_TEXT_JS)):
            raise Fail("the post form still holds text after clearing it")

    def cancel(self):
        if "/submit" not in self.b.url():
            return
        self.b.leave(SEL.URL_SUBREDDIT % self.sub)

    def press_submit(self):
        state = self.b.js(POST_BUTTON_STATE_JS)
        if len(state) != 1 or state[0]["disabled"]:
            raise Fail("expected one enabled Post control, found %r" % state)
        with self.b.focused():
            self.b.click(state[0]["x"], state[0]["y"])
        log("Post pressed")

    def verify(self, text, title, me):
        deadline = time.time() + 60
        link = None
        while time.time() < deadline:
            time.sleep(2)
            url = self.b.url()
            if "/comments/" in url:
                link = T.canonical(url)
                break
        if not link:
            return None, "the page did not move to a new post within 60 seconds"
        link = self.b.goto(link).split("?")[0]
        data = self.b.js(SEL.THREAD_JS)
        if not data or data["post"]["author"] != me["name"] or _norm(data["post"]["title"]) != _norm(title):
            return None, "the post at %s is not ours or does not carry this title" % link
        if SEL.REMOVED_TEXT.search(self.b.js("(document.querySelector('shreddit-post') || {}).innerText || ''")):
            return None, "the post reads as removed after a reload"
        return link, "present after a reload, by the signed-in account, title identical"
