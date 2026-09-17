# -*- coding: utf-8 -*-
"""The guardrails. Features of the tool, on by default, enforced in code.

Reddit spam-filters automated activity hard, and a filtered account does not
come back by fixing code. So none of these is a policy the caller is trusted to
remember, and none has a switch that loosens it - except --allow-links, which
exists for other users of a public tool and is off by default.

  rules-not-fetched     a write refuses unless the subreddit's rules were read
                        from the live page IN THIS RUN (same process)
  pace-submission-gap   no two submissions of any kind within 10 minutes
  pace-post-per-day     no more than one post per subreddit in any 24 hours
  near-duplicate        nothing that repeats text or phrasing sent in the last 30 days
  link                  no URL, markdown link, email address or bare domain
  bare-link             even with --allow-links, no text that is little more than a link
  vote-request          no request for upvotes
  account               writes go out as ONE pinned account, never another

Everything is decided from the sent log, which is append-only. The attempt row
is written BEFORE the submit press, inside the same lock as the checks, so two
runs cannot both pass a gap, and a press that errored still counts: the
conservative direction, because a failed request may still have reached Reddit.
"""
import hashlib
import json
import os
import re
import time
import uuid

from .state import Fail, FileLock, Refused, home, read_json, write_json_atomic

WRITE_KINDS = ("comment", "reply", "post")

SUBMISSION_GAP = 10 * 60
POST_WINDOW = 24 * 60 * 60
POSTS_PER_SUB_IN_WINDOW = 1
REPEAT_WINDOW = 30 * 24 * 60 * 60

SHINGLE = 5                 # words per shingle for the similarity measures
JACCARD_LIMIT = 0.5         # refuse at or above: half the phrasing is shared
CONTAINMENT_LIMIT = 0.6     # refuse at or above: the shorter text is mostly inside the longer
SHARED_RUN = 10             # refuse any identical run of this many consecutive words

BARE_LINK_MIN_WORDS = 15    # with --allow-links, a link needs at least this much text around it


# ---------------------------------------------------------------- links

# A link is the fastest route into Reddit's spam filter, so by default a draft
# holding one is refused outright. Three shapes are always links, code or not.
_SCHEME_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,15}://\S+")
_WWW = re.compile(r"(?i)(?<![\w.-])www\.[a-z0-9-]+\.\S+")
_MD_LINK = re.compile(r"\[[^\]\n]*\]\([^)\s]+[^)]*\)|<[a-z][a-z0-9+.-]*:[^>\s]+>|^\s*\[[^\]\n]+\]:\s*\S+", re.M)
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b")
# A bare domain is found by SHAPE, not by a list of top-level domains: a list
# loses to the next TLD it has never heard of, and that loss would let a link
# through. The shape is word.word with a final label of two or more letters.
_DOTTED = re.compile(r"(?i)(?<![\w@/.-])((?:[a-z0-9-]+\.)+([a-z]{2,24}))(?![\w-])")
# What the shape would otherwise catch in ordinary technical writing: file
# names. Every entry here is an extension that is NOT a top-level domain, so
# exempting it can never let a real domain through.
NON_TLD_EXTENSIONS = frozenset("""
js jsx mjs cjs ts tsx json jsonc yaml yml toml lock txt csv tsv ini cfg conf env log exe dll bat
cmd ps1 psm1 cpp hpp cxx java kt kts swift rb php html htm css scss sass less vue svelte sql
sqlite db xml svg png jpg jpeg gif webp bmp ico pdf doc docx xls xlsx ppt pptx mp4 mp3 wav gz
tgz bz2 xz whl pyc ipynb gradle rst tex jar class obj lib out bin dat pkl pth onnx parquet proto
graphql gql tfvars hcl nix mdx
""".split())
# Inside a code span, dotted names are overwhelmingly code (os.path.join), so
# only final labels that are common web domains are treated as links there.
WEB_TLDS = frozenset("""
com net org io dev ai app co me gg xyz tech info biz cloud site online page ly to tv us uk ca
de eu fm link blog news store shop club pro codes tools run website space live studio systems
software solutions digital network world today email inc llc ltd sh so gl cc
""".split())
_CODE = (re.compile(r"```.*?```", re.S), re.compile(r"`[^`\n]+`"), re.compile(r"^(?: {4}|\t).*$", re.M))


def find_links(text):
    """Every link-shaped thing in `text`, as the exact substrings found."""
    found = []
    for rx in (_SCHEME_URL, _WWW, _MD_LINK, _EMAIL):
        found += [m.group(0) for m in rx.finditer(text)]
    code_spans = [(m.start(), m.end()) for rx in _CODE for m in rx.finditer(text)]

    def in_code(pos):
        return any(a <= pos < b for a, b in code_spans)

    for m in _DOTTED.finditer(text):
        token, tld = m.group(1), m.group(2).lower()
        if any(token in f for f in found):
            continue
        if in_code(m.start()):
            if tld in WEB_TLDS:
                found.append(token)
        elif tld not in NON_TLD_EXTENSIONS:
            found.append(token)
    return found


def _strip_links(text):
    for rx in (_MD_LINK, _SCHEME_URL, _WWW, _EMAIL):
        text = rx.sub(" ", text)
    return text


_VOTE_REQUEST = re.compile(
    r"(?i)\b(?:please|pls|plz|kindly|go|drop|give|leave|smash|hit|appreciate)\b[^.!?\n]{0,40}\b(?:up ?votes?|upvot\w*|karma)\b"
    r"|(?:^|[.!?\n]\s*)(?:up ?vote|upvote)\b"
    r"|\bupvote (?:this|me|us|if|so)\b")


def lint(kind, text, title=None, allow_links=False):
    """Refusals that depend only on the text. Returns a list of Refused."""
    out = []
    whole = (title or "") + "\n" + (text or "")
    if kind not in WRITE_KINDS:
        out.append(Refused("kind", "unknown write kind %r; this tool writes %s only"
                           % (kind, ", ".join(WRITE_KINDS))))
    if not (text or "").strip():
        out.append(Refused("empty", "the text is empty"))
    if kind == "post" and not (title or "").strip():
        out.append(Refused("empty", "a post needs a title"))
    links = find_links(whole)
    if links and not allow_links:
        out.append(Refused("link", "the text contains %d link(s): %s. Links are refused by default: "
                           "say the name and let people search. (--allow-links exists for other "
                           "users of this tool; the refusal is the default for a reason.)"
                           % (len(links), ", ".join(repr(x) for x in links[:5]))))
    if links and allow_links:
        words = re.findall(r"[A-Za-z0-9']+", _strip_links(whole))
        if len(words) < BARE_LINK_MIN_WORDS:
            out.append(Refused("bare-link", "the text is little more than a link (%d words besides "
                               "it; at least %d are required). A bare link nobody asked for is "
                               "refused even with --allow-links." % (len(words), BARE_LINK_MIN_WORDS)))
    m = _VOTE_REQUEST.search(whole)
    if m:
        out.append(Refused("vote-request", "the text asks for votes (%r). Asking for upvotes is "
                           "vote manipulation." % m.group(0).strip()))
    return out


# ---------------------------------------------------------------- repetition

def words(text):
    text = (text or "").lower()
    text = re.sub(r"[*_~>#`\[\]()|]", " ", text)
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)


def shingles(ws, n):
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def similarity(a, b):
    """How much of text `a` repeats text `b`: jaccard and containment over
    5-word shingles, and the first identical run of SHARED_RUN words."""
    wa, wb = words(a), words(b)
    sa, sb = shingles(wa, SHINGLE), shingles(wb, SHINGLE)
    inter = sa & sb
    jac = len(inter) / float(len(sa | sb)) if (sa or sb) else 0.0
    con = len(inter) / float(min(len(sa), len(sb))) if (sa and sb) else 0.0
    runs = shingles(wa, SHARED_RUN) & shingles(wb, SHARED_RUN)
    run = None
    if runs:
        for i in range(len(wa) - SHARED_RUN + 1):
            if tuple(wa[i:i + SHARED_RUN]) in runs:
                run = " ".join(wa[i:i + SHARED_RUN])
                break
    return {"identical": bool(wa) and wa == wb, "jaccard": jac, "containment": con, "shared_run": run}


def check_repetition(text, title, attempts, now):
    whole = ((title or "") + "\n" + (text or "")).strip()
    for row in attempts:
        if now - row["ts"] > REPEAT_WINDOW:
            continue
        prior = ((row.get("title") or "") + "\n" + (row.get("text") or "")).strip()
        s = similarity(whole, prior)
        why = None
        if s["identical"]:
            why = "it is identical to"
        elif s["shared_run"]:
            why = "it repeats the phrase %r from" % s["shared_run"]
        elif s["jaccard"] >= JACCARD_LIMIT:
            why = "%.0f%% of its phrasing matches" % (100 * s["jaccard"])
        elif s["containment"] >= CONTAINMENT_LIMIT:
            why = "%.0f%% of the shorter text is inside" % (100 * s["containment"])
        if why:
            return Refused("near-duplicate", "%s what was sent to r/%s at %s (%s). Reddit search finds "
                           "repeated phrasing; rewrite it." % (why, row.get("subreddit"), row.get("iso"),
                                                               row.get("target")))
    return None


# ---------------------------------------------------------------- pace

def _clock(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def check_pace(kind, subreddit, attempts, now):
    sub = (subreddit or "").lower()
    for row in attempts:
        if now - row["ts"] < SUBMISSION_GAP:
            return Refused("pace-submission-gap", "a %s was sent at %s; no two submissions within %d "
                           "minutes. Next allowed after %s."
                           % (row["kind"], row["iso"], SUBMISSION_GAP // 60, _clock(row["ts"] + SUBMISSION_GAP)))
    if kind == "post":
        recent = [r for r in attempts if r["kind"] == "post" and r["subreddit"].lower() == sub
                  and now - r["ts"] < POST_WINDOW]
        if len(recent) >= POSTS_PER_SUB_IN_WINDOW:
            first = min(r["ts"] for r in recent)
            return Refused("pace-post-per-day", "r/%s already had a post from this tool at %s; at most %d "
                           "per subreddit in 24 hours. Next allowed after %s."
                           % (subreddit, _clock(first), POSTS_PER_SUB_IN_WINDOW, _clock(first + POST_WINDOW)))
    return None


# ---------------------------------------------------------------- rules gate

def _rules_path(root, subreddit):
    return os.path.join(root, "rules", subreddit.lower() + ".json")


def record_rules(subreddit, rules, source_url, run_id, root=None):
    if not rules:
        raise Fail("refusing to record an empty rule list for r/%s: an empty read is a broken "
                   "instrument, not a subreddit without rules" % subreddit)
    entry = {"subreddit": subreddit, "rules": rules, "source_url": source_url, "run_id": run_id,
             "fetched_at": time.time(), "iso": _clock(time.time())}
    write_json_atomic(_rules_path(root or home(), subreddit), entry)
    return entry


def require_rules(subreddit, run_id, root=None):
    entry = read_json(_rules_path(root or home(), subreddit))
    if not entry:
        return None, Refused("rules-not-fetched", "the rules of r/%s have never been read. A write "
                             "reads them from the live page first." % subreddit)
    if entry.get("run_id") != run_id:
        return None, Refused("rules-not-fetched", "the rules of r/%s on file were read by another run "
                             "(%s), not this one. Rules change; they are read again for every write."
                             % (subreddit, entry.get("iso")))
    if not entry.get("rules"):
        return None, Refused("rules-not-fetched", "the rules on file for r/%s are empty" % subreddit)
    return entry, None


# ---------------------------------------------------------------- account

def pinned_account(root=None):
    return read_json(os.path.join(root or home(), "account.json"))


def pin_account(account_id, root=None):
    path = os.path.join(root or home(), "account.json")
    current = read_json(path)
    if current and current.get("account_id") != account_id:
        raise Refused("account", "this machine is pinned to account %s. The tool writes as one account "
                      "only and will not re-pin. If that account is truly retired, delete %s by hand."
                      % (current.get("account_id"), path))
    if current:
        return current
    entry = {"account_id": account_id, "pinned_at": _clock(time.time())}
    write_json_atomic(path, entry)
    return entry


def check_account(signed_in_id, root=None):
    pin = pinned_account(root)
    if not signed_in_id:
        return Refused("account", "could not read which account the browser is signed in as")
    if not pin:
        return Refused("account", "no account is pinned. Before the first real submission, the owner "
                       "pins the one account this tool may write as: cc-ai-reddit account --pin")
    if pin["account_id"] != signed_in_id:
        return Refused("account", "the browser is signed in as %s but this machine is pinned to %s. "
                       "The tool never writes as a second account." % (signed_in_id, pin["account_id"]))
    return None


# ---------------------------------------------------------------- the sent log

class Ledger(object):
    """sent.jsonl: one JSON object per line, appended, never rewritten.

    row "attempt"  written before the submit press: ts, iso, kind, subreddit,
                   target, title, text, sha256, account
    row "result"   written after verification: attempt id, status
                   (verified / unverified / failed), permalink, detail
    """

    def __init__(self, root=None):
        self.root = root or home()
        self.path = os.path.join(self.root, "sent.jsonl")

    def rows(self):
        out = []
        try:
            f = open(self.path, encoding="utf-8")
        except FileNotFoundError:
            return out
        with f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    raise Fail("%s line %d does not parse. The guardrails are decided from this file, "
                               "so nothing is sent until it is repaired by hand." % (self.path, n))
        return out

    def attempts(self):
        return [r for r in self.rows() if r.get("row") == "attempt"]

    def check(self, kind, subreddit, text, title=None, allow_links=False, now=None):
        """Every refusal that would stop this write right now. Empty means clear."""
        now = time.time() if now is None else now
        attempts = self.attempts()
        out = lint(kind, text, title, allow_links)
        for r in (check_pace(kind, subreddit, attempts, now), check_repetition(text, title, attempts, now)):
            if r:
                out.append(r)
        return out

    def _lock(self):
        return FileLock(self.path + ".lock", wait=60, what="the sent log")

    def reserve(self, kind, subreddit, target, text, title=None, account=None, allow_links=False, now=None):
        """Check again and append the attempt row, as ONE locked step. Raises
        Refused without writing anything. Returns the attempt id."""
        with self._lock():
            now = time.time() if now is None else now
            refusals = self.check(kind, subreddit, text, title, allow_links, now)
            if refusals:
                raise refusals[0]
            row = {"row": "attempt", "id": uuid.uuid4().hex, "ts": now, "iso": _clock(now), "kind": kind,
                   "subreddit": subreddit, "target": target, "title": title, "text": text,
                   "sha256": hashlib.sha256(((title or "") + "\n" + text).encode("utf-8")).hexdigest(),
                   "account": account}
            self._append(row)
            return row["id"]

    def record_result(self, attempt_id, status, permalink=None, detail=None):
        now = time.time()
        with self._lock():
            self._append({"row": "result", "id": attempt_id, "ts": now, "iso": _clock(now),
                          "status": status, "permalink": permalink, "detail": detail})

    def _append(self, row):
        os.makedirs(self.root, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
