---
name: reddit-browser
description: Read Reddit and stage or send comments, replies and text posts through a signed-in browser with cc-ai-reddit - scan a subreddit, read a thread, read rules, find unanswered replies, read the account's inbox and notifications without marking them read, score a draft post. The tool enforces subreddit rules, human pace, duplicate, link and account guardrails and dry-runs every write unless --submit. Documents the traps (JS challenge, shreddit-* elements, lazy loading, rich text composer, pages that mark messages read). Triggers on "reddit", "scan a subreddit", "read a reddit thread", "subreddit rules", "comment on reddit", "reply on reddit", "post to reddit", "unanswered reddit replies", "reddit inbox", "reddit notifications", "reddit messages", "did anyone reply on reddit", "score a reddit post".
---

# Reddit through cc-ai-reddit

Use the tool. Do not improvise browser-harness calls at Reddit, do not curl
reddit.com, and do not reach for the Reddit API. Each of those was tried and
closed, and the tool encodes what the site does to automation.

```
cc-ai-reddit <command> ...                       installed
py -3.11 <repo>/cc_ai_reddit.py <command> ...    straight from a clone
```

## Read the result line, always

```
RESULT ...                 exit 0
FAIL <reason and fix>      exit 1   nothing was sent
REFUSED rule=<name> ...    exit 3   a guardrail said no; nothing was sent
```

A FAIL is a finding, not an obstacle. Do not go and do the same thing by hand.
A REFUSED is the tool doing its job. **Never work around a refusal**: not by
rewording only to dodge the repetition check, not by waiting out the pace gap
in a loop, not by passing `--allow-links`, not by switching profiles.

## Reading: three layers

| Need | Command | Source |
|---|---|---|
| Newest posts, comment counts | `scan <sub> --new --limit N --max-age-hours N [--json]` | archive |
| Reddit's hot ranking, live scores | `scan <sub> --live [--hot]` | browser |
| One thread right now | `thread <permalink> [--json]` | browser |
| One thread, every comment | `thread <permalink> --archive` | archive |
| A subreddit's current rules | `rules <sub>` | browser |
| A user's history and who is waiting on them | `me <username> [--days N]` | archive |
| Everything waiting for the signed-in account | `inbox [--limit N] [--json]` | browser, marks nothing read |
| A screenshot | `shot [url] [--out file.png]` | browser |

The archive is Arctic Shift, a free public service. It is cached and spaced by
the tool; do not add your own loops over it. Its freshness, measured
2026-09-10: posts and comments arrive within about 30 seconds, but a post's
`score` and `num_comments` are recorded at ingest and read near zero for
hours. That is why archive `scan` counts comments itself and calls the score
`score_archived`. When a current score matters, use `--live`.

**An archive scan is a list of candidates, not what readers see.** The same
hour, the archive listed 8 posts in 7 hours where the live /new page showed 4
in 24: the rest had been removed by moderators or held by AutoModerator after
ingest. Rows carry `removed` when the archive has learned of it, but it does
not always know. Confirm a thread with `thread <permalink>` (live) before
drafting anything for it.

`thread` (live) reports `comments_held` against `comments_stated`. If they
differ, the page did not render everything; say so rather than treating the
held comments as the whole thread.

## The inbox: read it with the tool, never by opening Reddit's pages

`inbox` lists comment replies, post replies, username mentions, private
messages and notifications for the signed-in account, each with `unread` and
`unread_in`, plus one `chat` row when the chat button shows unread. It is
account-bound: signed out or not the pinned account is a FAIL. `RESULT inbox
... marks_read=no` is the whole point: it reads with `mark=false` and without
running Reddit's page scripts, so what was unread stays unread for the owner.

`me` (archive) and `inbox` (live) answer different questions: `me` is who is
waiting on the account's recent comments; `inbox` is what Reddit delivered,
including mentions, messages and notices from Reddit. Use `inbox` to see
what arrived, then `thread` on a row's permalink to read the conversation.

It does not list chat. If the `chat` row is there, tell the owner to read chat
by hand; do not open it.

## Writing: stage, read, then decide

```
cc-ai-reddit comment <thread permalink> --file draft.txt
cc-ai-reddit reply <comment permalink> --file draft.txt
cc-ai-reddit post <sub> --title "..." --file body.txt [--flair "Exact Flair"]
```

Without `--submit` each of these reads the rules live and prints them, opens
the real composer, types the draft, reads it back character for character,
screenshots it, prints exactly what WOULD be sent, then empties and closes the
composer. Look at the rules it printed and the screenshot before anything else.

`--submit` is a deliberate act. It re-runs every guardrail under a lock, logs
the attempt, presses once, and then verifies the result on a reload. `RESULT
sent ... verified=yes` with a permalink is the only proof a submission
happened. Anything else means look at the target by hand before doing anything.

Drafts are plain text. One paragraph per line. Markdown is refused because the
composer is rich text and would post the asterisks.

## The guardrails the tool enforces

| rule= | Refuses |
|---|---|
| `rules-not-fetched` | a write whose subreddit rules were not read live in this run |
| `pace-submission-gap` | a second submission of any kind within 10 minutes |
| `pace-same-subreddit` | a second comment or reply in one subreddit within 60 minutes |
| `pace-post-per-day` | a second post in one subreddit within 24 hours |
| `near-duplicate` | text or phrasing repeating anything sent in 30 days (any identical 10-word run counts) |
| `link` | any URL, markdown link, email address or bare domain |
| `bare-link` | with `--allow-links`, a text that is little more than a link |
| `vote-request` | asking for upvotes |
| `account` | no pinned account, or a browser signed in as a different one |

There are no commands for voting, messaging, following or changing accounts.
If asked for one, say it does not exist.

**The account is the owner's decision.** `cc-ai-reddit account` shows who the
browser is signed in as and what is pinned. Never run `account --pin`, and
never sign a browser in, unless the owner has named that account for this
tool in the conversation.

## The traps, so nobody rediscovers them

All measured on www.reddit.com, 2026-09-10.

- **JS challenge.** The first navigation can land on a `js_challenge=1` URL.
  A real browser passes it on its own; the tool waits for the URL to clear and
  for `shreddit-app` before reading. Anonymous HTTP gets 403; `old.reddit.com`
  redirects.
- **Web components.** Posts, comments and the composer are `shreddit-post`,
  `shreddit-comment`, `shreddit-composer`, with their chrome in shadow roots,
  so a light-DOM `querySelectorAll` returns 0 for things on screen. The DATA is
  on the elements' own attributes (`post-title`, `permalink`, `score`,
  `comment-count`, `author`, `thingid`, `parentid`, `depth`). Controls are
  found by accessible name and pressed with coordinate clicks.
- **`user-id` is the viewer, not the author.** On `shreddit-post` the author
  is `author` / `author-id`.
- **Most permalink forms load an empty page.** `/r/<Sub>/comments/<id>/`
  without its slug, any URL with the subreddit in the wrong case, and the old
  `/<slug>/<comment id>/` comment form all load `shreddit-app` and never a post,
  which looks exactly like a slow page. `/comments/<id>/` and
  `/comments/<id>/comment/<comment id>/` redirect to the right one. The tool
  navigates only by those two; pass it any form.
- **Lazy loading.** Listings and comment trees grow as you scroll. The number
  rendered at load varies (a /new listing showed 28 one day; an earlier read
  showed 3). Scroll until the count stops growing, then report what you hold.
- **Hidden tabs.** The tool works in a background tab. Wheel scrolling there
  needs focus emulation around the scroll; screenshots and clicks do not.
- **The collapsed comment box is not in the accessibility tree** and has a
  zero-size box until it is scrolled into view.
- **Rich text composer.** A Lexical editor: text inserts and reads back
  exactly, Enter starts a paragraph, markdown is NOT rendered.
- **Reddit keeps unsent comments and puts them back.** Every edit in a comment
  box is saved in the browser (`localStorage`, `comment-draft-items-<viewer
  user-id>`, one entry per thread box or per Reply box) and restored when that
  box opens again, after a reload and after the editor was emptied and closed.
  Emptying the editor does not remove it. The tool empties a box that opens
  holding text before it types a character, removes the saved entry on every
  discard, and proves it by reloading the page and reopening the box empty.
  A composer mismatch showing the draft inside other text is this.
- **Ctrl+A as a plain key press does not select a textarea.** The post title
  kept its text through three attempts; Chrome needs the SelectAll editing
  command on the key down.
- **Leaving a filled post form raises a beforeunload dialog** that freezes the
  page, so every later call times out. The tool empties the form and proves it
  empty before leaving, and only then answers Leave.
- **`/r/<sub>/about/rules/` opens the moderator rule editor** for a signed-in
  session. Rules are read from the community sidebar (`<details>` in
  `aside[aria-label="Community information"]`); read `textContent`, since a
  collapsed rule hides its description from `innerText`.
- **Opening the message inbox marks it read.** Measured 2026-09-11: opening
  `/message/inbox/` in a tab dropped the account's unread count from 337 to
  300. `/message/inbox.json?mark=false`, and the page's HTML fetched without
  running its scripts, change nothing. Never navigate to `/message/...`.
- **Opening `/notifications` clears the bell badge.** The page's
  `mark-all-notifications-seen` element tells Reddit everything was seen.
  Items stay unread until clicked; the unread marker is `selected` on each
  row's `rpl-inbox-row` (`is-viewed` and `viewed_at` only mean scrolled into
  view). The tool fetches the list's partial,
  `/svc/shreddit/notifications-inbox-content/20/route`, instead.
- **Opening chat opens a conversation.** In a focused tab `/chat/` went to the
  newest conversation by itself; in a hidden tab its room list never loaded.
  The chat button's count is server-rendered in
  `/svc/shreddit/header-action-item-chat` (`initial-count`). Chat requests are
  a "Requests" entry inside chat, which the tool cannot read without opening it.
- **The message inbox and the notifications keep separate read states.** The
  same reply read in one place was still unread in the other. `unread_in`
  says which.
- **Some subreddits require a flair.** The flair control's name ends in ` *`;
  `post` then needs `--flair` with an exact option name, and lists them if not.

## score

```
cc-ai-reddit score draft.md --sub <sub> [--flair F] [--when "YYYY-MM-DD HH:MM"]
```

Posts only; the draft needs a stated title (frontmatter `title:` or a `# `
heading). Report the BAND (what similar validation posts actually scored) and
the printed error. When the output says the point estimate is not better than
a constant, do not quote the point estimate to anyone as a prediction. Models
are per subreddit and built with `tools/train_model.py`.
