# -*- coding: utf-8 -*-
"""Turn captured command output into evidence fit for a public repository.

  py -3.11 tools/redact_evidence.py <raw.txt> <docs/evidence/YYYY-MM-DD-name.txt> [--name USERNAME ...]

This repository is public. Captured output carries three kinds of identifier
that must not reach it, and each is removed by SHAPE rather than by a list of
known values - a list of names would itself be the leak:

  * Reddit usernames: every u/<name> and /user/<name>, every JSON "author" and
    "name" value, `user=<name>` on a RESULT line, and then every other place
    in the file where one of the names collected that way appears. --name adds
    names that appear only in bare form (the argument of `me`).
  * Reddit account ids: every t2_<id>.
  * Local paths under a user's home directory, and email addresses.

What is kept: subreddit names, post titles, comment text, permalinks and
counts. Those are public content and they are what the evidence proves.
tests/test_no_leak.py checks the committed evidence against the same shapes,
independently of this script, so a file that skipped it cannot pass.
"""
import json
import re
import sys

USER = "<user>"
U_NAME = re.compile(r"(?<![A-Za-z0-9_/])u/([A-Za-z0-9_-]{2,20})")
USER_PATH = re.compile(r"/user/([A-Za-z0-9_-]{2,20})")
ACCOUNT_ID = re.compile(r"\bt2_[a-z0-9]+\b")
JSON_NAME = re.compile(r'"(author|name)":\s*"([^"<][^"]*)"')
RESULT_USER = re.compile(r"\buser=([A-Za-z0-9_-]{2,20})")
HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", re.I)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")


def collect_names(text, extra):
    names = set(extra)
    names.update(m.group(1) for m in U_NAME.finditer(text))
    names.update(m.group(1) for m in USER_PATH.finditer(text))
    names.update(m.group(2) for m in JSON_NAME.finditer(text))
    names.update(m.group(1) for m in RESULT_USER.finditer(text))
    names.discard(USER)
    return {n for n in names if n and n not in ("[deleted]",)}


def redact(text, extra=()):
    names = collect_names(text, extra)
    text = ACCOUNT_ID.sub("t2_<account>", text)
    text = HOME.sub("<home>", text)
    text = EMAIL.sub("<email>", text)
    text = U_NAME.sub("u/" + USER, text)
    text = USER_PATH.sub("/user/" + USER, text)
    text = JSON_NAME.sub(lambda m: '"%s": "%s"' % (m.group(1), USER), text)
    text = RESULT_USER.sub("user=" + USER, text)
    # Longest first, so a name that contains another is replaced whole.
    for n in sorted(names, key=len, reverse=True):
        text = re.sub(r"(?<![A-Za-z0-9_-])%s(?![A-Za-z0-9_-])" % re.escape(n), USER, text)
    return text


def main():
    args = sys.argv[1:]
    extra = []
    while "--name" in args:
        i = args.index("--name")
        extra.append(args[i + 1])
        del args[i:i + 2]
    if len(args) != 2:
        print(__doc__)
        sys.exit(2)
    with open(args[0], encoding="utf-8", errors="replace") as f:
        raw = f.read()
    out = redact(raw, extra)
    out.encode("ascii")
    with open(args[1], "w", encoding="ascii", newline="\n") as f:
        f.write(out)
    print("RESULT redacted in=%s out=%s names=%d" % (args[0], args[1], len(collect_names(raw, extra))))


if __name__ == "__main__":
    main()
