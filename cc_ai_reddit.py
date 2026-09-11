# -*- coding: utf-8 -*-
r"""cc-ai-reddit: read Reddit, and post and comment on it, through your own signed-in browser.

READ
  scan <sub> [--new|--hot] [--limit N] [--max-age-hours N] [--live] [--json]
  thread <permalink> [--archive] [--json]
  rules <sub> [--json]
  me <username> [--days N] [--limit N] [--json]
  inbox [--limit N] [--json]
  shot [url] [--out PNG]
  score <draft.md> --sub <sub> [--title T] [--flair F] [--author-posts N] [--when "YYYY-MM-DD HH:MM"]

WRITE (a dry run unless --submit is given)
  comment <thread permalink> --file <draft> [--submit] [--allow-links] [--shot PNG]
  reply <comment permalink> --file <draft> [--submit] [--allow-links] [--shot PNG]
  post <sub> --title <t> --file <body> [--flair F] [--submit] [--allow-links] [--shot PNG]
  account [--pin]

Browser commands take --profile NAME (default: CC_AI_REDDIT_PROFILE, else cencon)
or --cdp-url URL. The browser must already be running and signed in; this tool
never launches one and never signs in.

Output: RESULT lines (exit 0), FAIL <reason> (exit 1), REFUSED rule=<guardrail> (exit 3).
"""
import argparse
import json
import os
import sys
import time

from cc_ai_reddit_kit import __version__
from cc_ai_reddit_kit.state import Fail, Refused, ascii_text, die, log, refuse, sub_dir


def _browser(a):
    from cc_ai_reddit_kit.browser import Browser
    return Browser(profile=a.profile, cdp_url=a.cdp_url)


def say(s=""):
    print(ascii_text(s), flush=True)


def as_json(obj):
    print(json.dumps(obj, ensure_ascii=True), flush=True)


def _draft(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise Fail("draft not found: %s" % path)


# ---------------------------------------------------------------- reads

def cmd_scan(a):
    from cc_ai_reddit_kit import listings as L
    sort = "hot" if a.hot else "new"
    if a.live:
        with _browser(a) as b:
            rows, note = L.scan_live(b, a.sub, sort, a.limit, a.max_age_hours)
    else:
        rows, note = L.scan_archive(a.sub, sort, a.limit, a.max_age_hours)
    if note:
        log(note)
    for r in rows:
        if a.json:
            as_json(r)
        else:
            score = r["score"] if "score" in r else "%s*" % r["score_archived"]
            say("%5.1fh  %3d comments  score %-5s  %s" % (r["age_hours"], r["comments"], score, r["title"]))
            say("        %s" % r["permalink"])
    if rows and not a.live and not a.json:
        say("  * score as archived: seconds after posting for new posts. Use --live for current scores.")
    say("RESULT scan sub=%s source=%s sort=%s n=%d max_age_hours=%g"
        % (a.sub, "live" if a.live else "archive", sort, len(rows), a.max_age_hours))


def cmd_thread(a):
    from cc_ai_reddit_kit import threads as T
    if a.archive:
        data = T.read_archive(a.permalink)
    else:
        with _browser(a) as b:
            data = T.read_live(b, a.permalink)
    if a.json:
        as_json(data)
    else:
        p = data["post"]
        say("r/%s  %s" % (p["subreddit"], p["title"]))
        say("  u/%s  %s  %s" % (p["author"], p["created"], p["permalink"]))
        if p.get("text"):
            say("  " + p["text"][:600].replace("\n", "\n  "))
        for c in data["comments"]:
            say("%s- u/%s: %s" % ("  " * (c["depth"] + 1), c["author"], (c["text"] or "").replace("\n", " ")[:200]))
    stated = data.get("comments_stated")
    say("RESULT thread id=%s source=%s comments_held=%d%s" % (data["post"]["id"], data["source"], data["comments_held"],
                                                            " comments_stated=%d" % stated if stated is not None else ""))


def cmd_rules(a):
    from cc_ai_reddit_kit import rules as R
    with _browser(a) as b:
        entry = R.fetch(b, a.sub)
    if a.json:
        as_json(entry)
    else:
        say(R.format_rules(entry))
    say("RESULT rules sub=%s n=%d source=%s" % (a.sub, len(entry["rules"]), entry["source_url"]))


def cmd_me(a):
    from cc_ai_reddit_kit import people as P
    name, items, threads = P.me(a.username, a.days, a.limit)
    waiting = sum(len(i["unanswered"]) for i in items)
    for i in items:
        if a.json:
            as_json(i)
            continue
        say("%-7s r/%-20s %s  replies %d  unanswered %d  %s" % (i["kind"], i["subreddit"], i["created"], i["replies"],
                                                               len(i["unanswered"]), i["permalink"]))
        for w in i["unanswered"]:
            say("          waiting %.1fh: u/%s: %s" % (w["age_hours"], w["author"], w["text"].replace("\n", " ")[:160]))
            say("          %s" % w["permalink"])
    if not items:
        log("no archived posts or comments by u/%s in the last %g days - check the name if that is unexpected"
            % (name, a.days))
    say("RESULT me user=%s days=%g posts=%d comments=%d threads=%d unanswered=%d source=archive"
        % (name, a.days, sum(1 for i in items if i["kind"] == "post"),
           sum(1 for i in items if i["kind"] == "comment"), threads, waiting))


def cmd_inbox(a):
    from cc_ai_reddit_kit import inbox as I
    with _browser(a) as b:
        got = I.read(b, a.limit)
    for r in got["rows"]:
        if a.json:
            as_json(r)
            continue
        kind = r["kind"] if r["kind"] != "other" else "other/%s" % r["label"]
        say("%-6s %-22s %-24s %-20s %s" % ("UNREAD" if r["unread"] else "read", kind,
                                           "r/" + r["subreddit"] if r["subreddit"] else "-", r["created"] or "-",
                                           "u/" + r["author"] if r["author"] else "-"))
        for line in (r["title"], r["text"]):
            if line:
                say("       " + line.replace("\n", " ")[:300])
        if r["permalink"]:
            say("       " + r["permalink"])
    if not got["rows"]:
        log("the message inbox and the notifications list both rendered and both are empty; the chat button "
            "shows 0 unread")
    log("read with mark=false and without running Reddit's page scripts: nothing was marked read")
    say("RESULT inbox account=%s shown=%d unread_shown=%d held=%d inbox=%d notifications=%d chat_unread=%d "
        "reddit_unread_count=%d marks_read=no"
        % (got["account"]["id"], len(got["rows"]), sum(1 for r in got["rows"] if r["unread"]), got["held"],
           got["inbox"], got["notifications"], got["chat_unread"], got["reddit_unread_count"]))


def cmd_shot(a):
    out = a.out or os.path.join(sub_dir("shots"), "shot-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
    with _browser(a) as b:
        if a.url:
            url = b.goto(a.url)
            path = b.screenshot(out)
        else:
            path, url = b.capture_existing_reddit_tab(out)
    say("RESULT shot path=%s url=%s" % (path, url))


def cmd_score(a):
    from cc_ai_reddit_kit import score as S
    S.run(a)


# ---------------------------------------------------------------- writes

def cmd_write(a):
    from cc_ai_reddit_kit import guardrails as G, threads as T, writing as W
    ledger = G.Ledger()
    text = _draft(a.file)
    if a.cmd == "post":
        W.precheck("post", a.sub, text, a.title, a.allow_links, ledger)
    else:
        sub, _, _ = T.parse_permalink(a.permalink)
        W.precheck(a.cmd, sub, text, None, a.allow_links, ledger)
    with _browser(a) as b:
        if a.cmd == "post":
            surface = W.PostSurface(b, a.sub, a.flair)
            W.run("post", surface, text, a.title, a.submit, a.allow_links, a.shot, ledger, out=say)
        else:
            surface = W.CommentSurface(b, a.permalink, reply=(a.cmd == "reply"))
            W.run(a.cmd, surface, text, None, a.submit, a.allow_links, a.shot, ledger, out=say)


def cmd_account(a):
    from cc_ai_reddit_kit import guardrails as G
    with _browser(a) as b:
        b.goto("https://www.reddit.com/")
        me = b.me()
    if a.pin:
        if not me:
            raise Fail("cannot pin: the browser is not signed in to Reddit")
        G.pin_account(me["id"])
    pin = G.pinned_account()
    if me:
        say("signed in as u/%s (%s)" % (me["name"], me["id"]))
    else:
        say("not signed in")
    say("pinned account: %s" % (pin["account_id"] + " since " + pin["pinned_at"] if pin else "none"))
    say("RESULT account signed_in=%s id=%s pinned=%s match=%s"
        % ("yes" if me else "no", me["id"] if me else "-", pin["account_id"] if pin else "none",
           "yes" if (me and pin and pin["account_id"] == me["id"]) else "no"))


# ---------------------------------------------------------------- parser

def main():
    ap = argparse.ArgumentParser(prog="cc-ai-reddit", description=__doc__.split("\n")[0],
                                 epilog="version %s" % __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def browser_args(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--profile", help="browser profile name in the bh-profiles registry (default cencon)")
        g.add_argument("--cdp-url", help="a browser you started with --remote-debugging-port, e.g. http://127.0.0.1:9222")

    sp = sub.add_parser("scan", help="newest or hottest posts of a subreddit")
    sp.add_argument("sub")
    order = sp.add_mutually_exclusive_group()
    order.add_argument("--new", action="store_true", help="newest first (default)")
    order.add_argument("--hot", action="store_true", help="Reddit's hot ranking (needs --live)")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--max-age-hours", type=float, default=24)
    sp.add_argument("--live", action="store_true", help="read the listing page in the browser, not the archive")
    sp.add_argument("--json", action="store_true")
    browser_args(sp)
    sp.set_defaults(fn=cmd_scan)

    sp = sub.add_parser("thread", help="one post and its comments")
    sp.add_argument("permalink")
    sp.add_argument("--archive", action="store_true", help="read the archived comment tree, not the live page")
    sp.add_argument("--json", action="store_true")
    browser_args(sp)
    sp.set_defaults(fn=cmd_thread)

    sp = sub.add_parser("rules", help="a subreddit's rules, from the live page")
    sp.add_argument("sub")
    sp.add_argument("--json", action="store_true")
    browser_args(sp)
    sp.set_defaults(fn=cmd_rules)

    sp = sub.add_parser("me", help="a user's recent posts and comments, and replies still waiting on them")
    sp.add_argument("username")
    sp.add_argument("--days", type=float, default=30)
    sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_me)

    sp = sub.add_parser("inbox", help="replies, mentions, messages and notifications waiting for the signed-in "
                                      "account. Marks nothing read.")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--json", action="store_true")
    browser_args(sp)
    sp.set_defaults(fn=cmd_inbox)

    sp = sub.add_parser("shot", help="screenshot a Reddit page (or the one Reddit tab already open)")
    sp.add_argument("url", nargs="?")
    sp.add_argument("--out")
    browser_args(sp)
    sp.set_defaults(fn=cmd_shot)

    sp = sub.add_parser("score", help="predict how a draft POST will score, with the model's error")
    sp.add_argument("file")
    sp.add_argument("--sub", required=True)
    sp.add_argument("--title")
    sp.add_argument("--flair")
    sp.add_argument("--author-posts", type=int, default=0)
    sp.add_argument("--when", help="posting time 'YYYY-MM-DD HH:MM' in UTC (default: now)")
    sp.add_argument("--model-dir")
    sp.set_defaults(fn=cmd_score)

    for name, help_ in (("comment", "comment on a thread"), ("reply", "reply to one comment")):
        sp = sub.add_parser(name, help=help_ + " (dry run unless --submit)")
        sp.add_argument("permalink")
        sp.add_argument("--file", required=True, help="the draft, UTF-8 plain text")
        sp.add_argument("--submit", action="store_true", help="actually send it. Default stages and discards.")
        sp.add_argument("--allow-links", action="store_true",
                        help="permit links in the text. Off by default: links are refused.")
        sp.add_argument("--shot", help="where to write the staging screenshot")
        browser_args(sp)
        sp.set_defaults(fn=cmd_write)

    sp = sub.add_parser("post", help="a text post (dry run unless --submit)")
    sp.add_argument("sub")
    sp.add_argument("--title", required=True)
    sp.add_argument("--file", required=True, help="the body, UTF-8 plain text")
    sp.add_argument("--flair")
    sp.add_argument("--submit", action="store_true", help="actually post it. Default stages and discards.")
    sp.add_argument("--allow-links", action="store_true",
                    help="permit links in the text. Off by default: links are refused.")
    sp.add_argument("--shot")
    browser_args(sp)
    sp.set_defaults(fn=cmd_write)

    sp = sub.add_parser("account", help="which account the browser is signed in as, and the pinned one")
    sp.add_argument("--pin", action="store_true",
                    help="pin the signed-in account as the ONE account this machine may write as")
    browser_args(sp)
    sp.set_defaults(fn=cmd_account)

    a = ap.parse_args()
    for name in ("profile", "cdp_url"):
        if not hasattr(a, name):
            setattr(a, name, None)
    try:
        a.fn(a)
    except Refused as exc:
        refuse(exc)
    except Fail as exc:
        die(str(exc))
    except (RuntimeError, TimeoutError) as exc:
        # browser-harness reports a browser that stopped answering this way.
        # Nothing is retried: the run stops and says what did not answer.
        die("the browser did not answer (%s: %s). Nothing was sent. Check the profile with "
            "bh-profiles.ps1 status, then run the command again." % (type(exc).__name__, str(exc)[:300]))


if __name__ == "__main__":
    main()
