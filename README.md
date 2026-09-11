# cc-ai-reddit

A command line tool that reads Reddit, and comments, replies and posts on it,
through a browser you are already signed in to. It is built for an agent to
drive, and it is built to be a good Reddit citizen rather than a spam cannon:
the rules that keep an account in good standing are enforced inside the tool,
on by default, and it refuses rather than bends.

- **Reading** comes from the [Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift)
  public archive by default (no browser, no load on Reddit), and from your
  browser when you need a thread's live state.
- **Writing** happens in your browser, the way a person does it: open the
  composer, type, read the text back. Every write is a **dry run unless you
  pass `--submit`**.
- **Scoring** a draft post against a per-subreddit model tells you, before
  anything is sent, how posts like it have actually done - with the model's
  error printed beside the number.

There is no Reddit API key, no app registration and no stored password. The
tool drives a Chromium that is already running with remote debugging, through
[browser-harness](https://github.com/browser-use/browser-harness).

## The guardrails

These are features of the tool, enforced in code and covered by tests
(`tests/test_guardrails.py`, `tests/test_writing.py`). None of them has a flag
that switches it off, except the link guard, which exists as an explicit
opt-out for other uses of a public tool.

| Guardrail | What the tool does |
|---|---|
| Rules first | A write reads the subreddit's rules from the live page in the same run, prints them, and refuses if it cannot read them. |
| Human pace | No two submissions of any kind within 10 minutes. No two comments or replies in one subreddit within 60 minutes. At most one post per subreddit in any 24 hours. Kept on disk, so it holds across runs. |
| Repetition | Refuses anything that repeats text or phrasing sent in the last 30 days: identical text, a lightly edited copy, or any identical run of ten words. Reddit search finds repeated phrasing. |
| No links | Refuses any URL, markdown link, email address or bare domain (`example.com`) in the text. `--allow-links` lifts this; even then, a text that is little more than a link is refused. |
| No vote requests | Refuses text that asks for upvotes. |
| One account | Before the first real submission, the owner pins the one account the machine may write as (`account --pin`). A browser signed in as any other account is refused, and the pin cannot be moved by the tool. |
| Dry run by default | Without `--submit` the draft is staged, read back, screenshotted, printed, and then removed, including the copy Reddit keeps of an unsent comment: the discard is proven by reloading the page and reopening the comment box empty. A box that opens holding earlier text is emptied before anything is typed. Nothing is pressed and nothing is logged as sent. |
| A log of everything sent | An append-only local log with the time, subreddit, target, permalink and exact text of every submission. Rows are never rewritten. The pace and repetition guards are decided from it. |
| Verified or it did not happen | After a real submission the tool reloads the page, finds the new comment or post by the signed-in author and the exact text, and checks it was not removed. An unverified send is logged and reported as a failure. |

## What it does not do

- It does not vote, and has no command that could.
- It does not send direct messages or chat.
- It does not create, switch or sign in to accounts, and it never types a password.
- It does not launch a browser. The browser must already be running and signed in.
- It does not post links, images, video or polls. Text posts only.
- It does not schedule, repost or crosspost.
- It does not moderate. It deliberately avoids `/about/rules/`, which sends a
  signed-in session to the moderator rule editor.
- It does not scrape at volume. Archive reads are cached and spaced; browser
  page loads are spaced across runs.
- It has no switch that bypasses pace, repetition, rules or the account pin.

## Install

Python 3.11 or newer.

```
pip install .                 the tool
pip install .[score]          plus numpy, pandas, lightgbm and xgboost for `score`
```

`cc_ai_reddit.py` at the repository root also runs directly with no install
(`py -3.11 cc_ai_reddit.py ...`), which is what `cc-ai-reddit.cmd` does.

You need a Chromium-based browser started with `--remote-debugging-port` and a
dedicated `--user-data-dir`, signed in to Reddit for anything that writes.
Point the tool at it with `--cdp-url http://127.0.0.1:<port>`, or register it
as a named profile in a `bh-profiles.ps1` registry and use `--profile <name>`
(the script is found through `CC_AI_REDDIT_BH_PROFILES`).

## Usage

```
cc-ai-reddit scan <sub> [--new|--hot] [--limit N] [--max-age-hours N] [--live] [--json]
cc-ai-reddit thread <permalink> [--archive] [--json]
cc-ai-reddit rules <sub> [--json]
cc-ai-reddit me <username> [--days N] [--limit N] [--json]
cc-ai-reddit shot [url] [--out file.png]
cc-ai-reddit score <draft.md> --sub <sub> [--title T] [--flair F] [--author-posts N] [--when "YYYY-MM-DD HH:MM"]

cc-ai-reddit comment <thread permalink> --file draft.txt [--submit] [--allow-links] [--shot file.png]
cc-ai-reddit reply <comment permalink> --file draft.txt [--submit] [--allow-links] [--shot file.png]
cc-ai-reddit post <sub> --title "..." --file body.txt [--flair F] [--submit] [--allow-links] [--shot file.png]
cc-ai-reddit account [--pin]
```

- `scan` lists a subreddit's newest posts from the archive, with comment counts
  counted from archived comments. `--live` reads the listing page instead, and
  `--hot` (Reddit's ranking) needs `--live`.
- `thread` reads one post and its comments from the live page, scrolling until
  the count stops growing, and reports how many comments it holds against the
  number the post states. `--archive` reads the archived tree.
- `rules` prints a subreddit's rules as the live page shows them.
- `me` lists a user's recent posts and comments from the archive, and every
  reply to them that the user has not answered.
- `shot` screenshots a page, or the one Reddit tab already open.
- `comment`, `reply`, `post` stage by default. Drafts are plain text: the
  composer is rich text and would post markdown characters literally, so
  markdown is refused rather than sent looking broken.
- `account` shows which account the browser is signed in as and which account
  is pinned. `--pin` is the owner's deliberate act before the first real send.

## Output

```
RESULT <verb> key=value ...        exit 0
FAIL <what broke, and the fix>     exit 1   nothing was sent
REFUSED rule=<guardrail> <why>     exit 3   nothing was sent
```

`--json` prints one JSON object per line before the RESULT line. Progress goes
to stderr. All output is ASCII.

An empty read is never reported as a quiet result it cannot prove: a listing
that renders nothing, a rules sidebar that never loads, or an archive that
does not know the subreddit is a FAIL with the reason.

## Where reading comes from

1. **Arctic Shift** for listings, history and anything bulk. It is a free,
   public Reddit archive run by one person, at
   <https://arctic-shift.photon-reddit.com> (source and API documentation:
   <https://github.com/ArthurHeitmann/arctic_shift>). This tool relies on it,
   so it tries to cost it as little as possible: every response is cached on
   disk, requests are spaced at least a second apart, rate-limit headers are
   obeyed, and the User-Agent names this project. If you need a large history,
   use Arctic Shift's monthly dumps rather than its API.
2. **The browser** for what the archive cannot give: a thread's live state,
   current scores, and a subreddit's current rules.
3. **The browser** for every write.

Measured on 2026-09-10, and why the tool is shaped this way:

- The archive ingests posts and comments within about 30 seconds.
- It records a post's score and comment count at ingest, so for a post a few
  hours old both read near zero. `scan` therefore counts comments itself and
  labels the archive's score `score_archived`.
- It keeps posts that Reddit later removed or held for moderation: in one hour
  it listed 8 posts in 7 hours where the live page showed 4 in 24. Rows carry
  `removed` when the archive knows; confirm a thread live before acting on it.
- Its copy of subreddit rules can be very old; rules are always read live.
- Reddit answers anonymous HTTP with 403, and its registered API requires
  manual approval, which is why the live layer is a browser.

## score

`score` predicts a draft post's score from what is known at posting time:
title and body length and word counts, punctuation in the title, the hour and
weekday, the flair, and the author's post count. It does not read meaning.

**Read its error before its number.** The output leads with a band: of the
validation posts the model rated the same way, what did they actually score
(10th, 50th and 90th percentile). Then it prints the point estimate with the
model's validation error and the error of always guessing the median. For the
one model measured so far, the point estimate is no better than that constant,
and the tool says so in the output; the band is where the signal is.

Models are per subreddit and are not shipped here. Build one from the archive:

```
py -3.11 tools/train_model.py download <sub> --since 2024-01 --data <dir>
py -3.11 tools/train_model.py build <sub> --data <dir> --holdout-from 2026-01-01
```

`build` trains on posts before the holdout date, measures on posts after it,
and writes every number `score` prints into the model's manifest. `import`
brings in an existing model of the same shape. A model directory is data only;
the tool never loads a pickle.

## State

In `CC_AI_REDDIT_HOME`, or `%LOCALAPPDATA%\cc-ai-reddit` on Windows and
`~/.cc-ai-reddit` elsewhere:

```
sent.jsonl        the append-only log of every submission
account.json      the pinned account id
rules/<sub>.json  the last rules read, with the run that read them
models/<sub>/     score models
cache/archive/    cached archive responses
shots/            staging screenshots
```

## Tests

```
py -3.11 -m unittest discover -s tests
```

No browser and no network. They cover every guardrail refusing (rules not
read, each pace limit, near duplicates, links, vote requests, the account
pin), that a run without `--submit` presses nothing and logs nothing, and
that the repository itself carries no account ids, usernames, home paths or
email addresses. Dated live evidence is in `docs/evidence/`; the screenshots
beside it stay on the machine that took them.

## Skill

`.claude/skills/reddit-browser/SKILL.md` tells an agent how to use the tool
and what the site does to automation that is not expecting it.

## License

MIT. See [LICENSE](LICENSE).
