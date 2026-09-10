# -*- coding: utf-8 -*-
"""Every handle on Reddit's page the tool relies on, in one place, each with the
date it was measured on the live site. When Reddit changes a control the
failing command names the handle, and the fix is one line here.

THE SHAPE OF THE SITE (measured 2026-09-10, www.reddit.com, desktop)
  * Web components. The app is `shreddit-app`; posts, comments and the composer
    are custom elements (`shreddit-post`, `shreddit-comment`,
    `shreddit-composer`). Much of their chrome sits in shadow roots, so a
    light-DOM querySelectorAll returns 0 for controls a person can see. The
    DATA, however, sits on the custom elements' own attributes, and that is
    the stable handle: permalink, post-title, score, comment-count, author,
    thingid, parentid, depth.
  * Controls are found by ACCESSIBLE NAME through the accessibility tree and
    pressed with coordinate clicks, which pass through shadow roots.
  * The first navigation of a session can land on a `js_challenge=1` URL. A
    real browser passes it by itself; the tool waits for the challenge URL to
    clear and for `shreddit-app` to exist before reading anything.
  * Anonymous HTTP to reddit.com (about.json and friends) answers 403, and
    old.reddit.com redirects. Reads that need Reddit itself go through the
    browser; everything else goes through the archive (archive.py).
"""
import re

# -- the app ------------------------------------------------------ 2026-09-10
APP = "shreddit-app"
# "true" when a person is signed in. The viewer's own account id (t2_...) is on
# every `[user-id]` element: shreddit-post, comment-composer-host.
SIGNED_IN_JS = "(() => { const a = document.querySelector('shreddit-app'); return !!a && a.getAttribute('user-logged-in') === 'true'; })()"
VIEWER_ID_JS = "(() => { const e = document.querySelector('[user-id]'); return e ? e.getAttribute('user-id') : null; })()"
# The signed-in account's name and id, asked of Reddit from inside the page.
ME_JS = ("fetch('/api/me.json', {credentials: 'include'}).then(r => r.ok ? r.json() : null)"
         ".then(j => j && j.data && j.data.name ? {name: j.data.name, id: 't2_' + j.data.id} : null)")
BLOCKED = re.compile(r"blocked by network security|whoa there, pardner|you've been blocked|too many requests", re.I)

URL_LISTING = "https://www.reddit.com/r/%s/%s/"
URL_SUBREDDIT = "https://www.reddit.com/r/%s/"
URL_SUBMIT = "https://www.reddit.com/r/%s/submit/?type=TEXT"
# DO NOT USE /r/<sub>/about/rules/. For a signed-in session it redirected to
# /mod/<sub>/rules/, the moderator rule editor (measured 2026-09-10). Rules are
# read from the community sidebar instead, which every thread page carries.
PERMALINK = re.compile(r"^https://(?:www\.|old\.|new\.)?reddit\.com((?:/r/([A-Za-z0-9_]+))?/comments/([a-z0-9]+)(?:/[^/?#]*)?/?"
                       r"(?:(?:comment/)?([a-z0-9]+)/?)?)(?:[?#].*)?$")
# WHERE THE TOOL NAVIGATES, measured 2026-09-10 on one thread and one comment:
#   renders   /comments/<post>/                      redirects to /r/<Sub>/comments/<post>/<slug>/
#   renders   /comments/<post>/comment/<comment>/    redirects to /r/<Sub>/comments/<post>/comment/<comment>/
#   renders   /r/<Sub>/comments/<post>/<slug>/       only with the subreddit's exact case
#   NOTHING   /r/<Sub>/comments/<post>/              no slug: shreddit-app loads, no shreddit-post ever does
#   NOTHING   /r/<sub lowercased>/comments/...       wrong case, same symptom
#   NOTHING   /r/<Sub>/comments/<post>/<slug>/<comment>/   the archive's comment permalink form
# So every permalink is reduced to the first two forms before it is opened.
URL_THREAD = "https://www.reddit.com/comments/%s/"
URL_COMMENT = "https://www.reddit.com/comments/%s/comment/%s/"

# -- listings ------------------------------------------------------ 2026-09-10
# One shreddit-post per card. Measured: 28 rendered on /new/ at load, more on
# scroll. The count at load varies, so a listing is scrolled until it holds
# enough or stops growing.
LISTING_JS = r"""[...document.querySelectorAll('shreddit-post')].map(p => ({
  id: (p.getAttribute('id') || '').replace(/^t3_/, ''),
  title: p.getAttribute('post-title'),
  author: p.getAttribute('author'),
  subreddit: p.getAttribute('subreddit-name'),
  created: p.getAttribute('created-timestamp'),
  score: p.getAttribute('score'),
  comments: p.getAttribute('comment-count'),
  permalink: p.getAttribute('permalink'),
  flair: ((p.querySelector('shreddit-post-flair') || {}).textContent || '').trim() || null,
  post_type: p.getAttribute('post-type'),
  stickied: p.hasAttribute('stickied')
}))"""

# -- a thread ------------------------------------------------------ 2026-09-10
# shreddit-post carries: id (t3_...), post-title, author, subreddit-name,
# created-timestamp, score, comment-count, permalink, item-state (UNMODERATED
# on a live post), user-id (the VIEWER, not the author; the author is author-id).
# Each shreddit-comment carries: thingid (t1_...), parentid (absent on a top
# level comment), depth, author, created, score, permalink. Its text is the
# element in slot="comment".
THREAD_JS = r"""(() => {
  const p = document.querySelector('shreddit-post');
  if (!p) return null;
  const body = p.querySelector('[slot="text-body"]');
  const txt = e => (e ? e.innerText : '').replace(/\u00a0/g, ' ').trim();
  return {
    post: {
      id: (p.getAttribute('id') || '').replace(/^t3_/, ''),
      title: p.getAttribute('post-title'),
      author: p.getAttribute('author'),
      subreddit: p.getAttribute('subreddit-name'),
      created: p.getAttribute('created-timestamp'),
      score: p.getAttribute('score'),
      comments: p.getAttribute('comment-count'),
      permalink: p.getAttribute('permalink'),
      state: p.getAttribute('item-state'),
      locked: p.hasAttribute('locked'),
      archived: p.hasAttribute('archived'),
      text: txt(body)
    },
    comments: [...document.querySelectorAll('shreddit-comment')].map(c => ({
      id: (c.getAttribute('thingid') || '').replace(/^t1_/, ''),
      parent: c.getAttribute('parentid'),
      depth: Number(c.getAttribute('depth') || 0),
      author: c.getAttribute('author'),
      created: c.getAttribute('created'),
      score: c.getAttribute('score'),
      permalink: c.getAttribute('permalink'),
      text: txt(c.querySelector('[slot="comment"]'))
    }))
  };
})()"""
REMOVED_TEXT = re.compile(r"removed by (reddit|the moderators|moderators)|sorry, this post (was|has been) removed|"
                          r"\[\s*removed\s*(by reddit)?\s*\]|\[deleted\]", re.I)

# -- rules --------------------------------------------------------- 2026-09-10
# The community sidebar, lazy-loaded into faceplate-partial
# #subreddit-right-rail__partial. Each rule is a <details> inside
# aside[aria-label="Community information"]; the <summary> reads "<n>\n<title>"
# and the description is the rest of the element's text. Read textContent, not
# innerText: a collapsed <details> hides its description from innerText.
RULES_JS = r"""[...document.querySelectorAll('aside[aria-label="Community information"] details')].map(d => {
  const s = d.querySelector('summary');
  const head = (s ? s.textContent : '').replace(/\s+/g, ' ').trim();
  const m = head.match(/^(\d+)\s+(.*)$/);
  return m ? {n: Number(m[1]), title: m[2], text: d.textContent.replace(s.textContent, '').replace(/\s+/g, ' ').trim()} : null;
}).filter(Boolean)"""

# -- the comment composer ------------------------------------------ 2026-09-10
# Collapsed, it is a faceplate-textarea-input inside comment-composer-host,
# placeholder "Join the conversation". It is NOT in the accessibility tree and
# has a zero box until it has been scrolled into view, so it is clicked by its
# own rectangle once visible. Clicked, it opens shreddit-composer in rich text
# mode: a Lexical editor, div[contenteditable][data-lexical-editor], plus two
# buttons - slot="cancel-button" (type reset, name "Cancel") and
# slot="submit-button" (type submit, name "Comment"). Measured: insertText
# reads back exactly; Enter starts a new paragraph and paragraphs read back
# joined by a blank line; Ctrl+A then Backspace empties it; Cancel closes it.
# Markdown typed into rich text mode is NOT rendered: "**x**" reads back, and
# would be posted, with its asterisks.
COMPOSER_TRIGGER = "comment-composer-host faceplate-textarea-input"
REPLY_BUTTON = re.compile(r"^Reply$")
COMMENT_BUTTON = re.compile(r"^Comment$")
CANCEL_BUTTON = re.compile(r"^Cancel$")
VISIBLE_EDITOR_JS = r"""(() => {
  const e = [...document.querySelectorAll('shreddit-composer [contenteditable="true"]')].find(x => x.getBoundingClientRect().height > 0);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return {x: Math.round(r.x + Math.min(40, r.width / 2)), y: Math.round(r.y + r.height / 2), text: e.innerText};
})()"""
VISIBLE_COMPOSER_BUTTONS_JS = r"""[...document.querySelectorAll('shreddit-composer button, comment-composer-host button, shreddit-comment button')]
  .filter(b => b.getBoundingClientRect().height > 0 && /^(submit|cancel)-button$/.test(b.getAttribute('slot') || ''))
  .map(b => { const r = b.getBoundingClientRect(); return {slot: b.getAttribute('slot'), name: b.innerText.trim(), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), disabled: b.disabled}; })"""

# -- the post composer --------------------------------------------- 2026-09-10
# /r/<sub>/submit/?type=TEXT, the r-post-composer-form. Accessible names:
# textbox "Title", textbox "Post body text field", button "Add flair and tags"
# (with " *" when the subreddit requires a flair), button "Post", button
# "Save Draft", button "Drafts". "Save Draft" is never pressed.
TITLE_BOX = re.compile(r"^Title$")
BODY_BOX = re.compile(r"^Post body text field$")
FLAIR_BUTTON = re.compile(r"^Add flair and tags( \*)?$")
POST_BUTTON = re.compile(r"^Post$")
