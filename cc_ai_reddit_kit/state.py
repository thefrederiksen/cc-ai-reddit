# -*- coding: utf-8 -*-
"""Where the tool keeps its state, and the primitives every writer of it uses.

State lives in CC_AI_REDDIT_HOME when that is set, otherwise in
%LOCALAPPDATA%\\cc-ai-reddit on Windows and ~/.cc-ai-reddit elsewhere. Nothing
in it belongs in a repository: it holds the sent log, which carries the exact
text of everything this machine has submitted, and the pinned account id.

Output contract shared by every command:
    RESULT <verb> key=value ...     exit 0
    FAIL <reason and the fix>       exit 1   something broke; nothing was sent
    REFUSED <which guardrail, why>  exit 3   a guardrail said no; nothing was sent
Progress lines go to stderr, so stdout stays parseable under --json.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import tempfile
import time
import uuid

EXIT_FAIL = 1
EXIT_REFUSED = 3

# One id per process. The rules gate uses it: rules count as "fetched in this
# run" only when the cached copy carries this exact id.
RUN_ID = uuid.uuid4().hex


def home():
    base = os.environ.get("CC_AI_REDDIT_HOME")
    if not base:
        if os.environ.get("LOCALAPPDATA"):
            base = os.path.join(os.environ["LOCALAPPDATA"], "cc-ai-reddit")
        else:
            base = os.path.join(os.path.expanduser("~"), ".cc-ai-reddit")
    os.makedirs(base, exist_ok=True)
    return base


def sub_dir(*parts):
    path = os.path.join(home(), *parts)
    os.makedirs(path, exist_ok=True)
    return path


_TYPOGRAPHY = {u"\u2018": "'", u"\u2019": "'", u"\u201c": '"', u"\u201d": '"', u"\u2013": "-", u"\u2014": "-",
               u"\u2026": "...", u"\u00a0": " ", u"\u2022": "*", u"\u00b7": "-"}


def ascii_text(s):
    """Text for a terminal or a log: ASCII only. Common typography becomes its
    plain equivalent; anything else becomes a visible \\u escape, never a crash."""
    s = "".join(_TYPOGRAPHY.get(ch, ch) for ch in str(s))
    return s.encode("ascii", "backslashreplace").decode("ascii")


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), ascii_text(msg)), file=sys.stderr, flush=True)


class Fail(Exception):
    """Something broke. The message says what, and how to fix it."""


class Refused(Exception):
    """A guardrail said no. `rule` names the guardrail."""

    def __init__(self, rule, message):
        Exception.__init__(self, message)
        self.rule = rule


def die(msg):
    print("FAIL " + ascii_text(msg), flush=True)
    sys.exit(EXIT_FAIL)


def refuse(exc):
    print("REFUSED rule=%s %s" % (exc.rule, ascii_text(exc)), flush=True)
    sys.exit(EXIT_REFUSED)


def read_json(path):
    """The parsed file, or None when it does not exist. A file that exists and
    does not parse is a Fail, never an empty default."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise Fail("%s exists but is not valid JSON (%s). Inspect it by hand; it was not "
                   "overwritten." % (path, exc))


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=True)
        os.replace(tmp, path)
    except BaseException:
        _remove(tmp)
        raise


def _remove(path, tries=60, gap=0.05):
    """Remove a file this process owns. Windows refuses a delete while any
    reader holds the file open, so retry briefly and report if it still fails."""
    for i in range(tries):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            if i == tries - 1:
                log("could not remove %s (%s)" % (path, str(exc).splitlines()[0][:80]))
                return False
            time.sleep(gap)


def _pid_alive(pid):
    if not hasattr(ctypes, "windll"):
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    code = wt.DWORD()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259          # STILL_ACTIVE


class FileLock(object):
    """Cross-process mutual exclusion around one file.

    Taken over from cc-linkedin, where each rule below was measured failing
    first. The lock record is written to a temporary file and LINKED into
    place, so a lock file that exists is always complete: bytes that were read
    and do not parse are a broken record and are taken over. A lock file that
    could not be OPENED says nothing about its content (on Windows it is
    usually a sharing violation), so it is treated as held and waited for. A
    record naming a dead process is taken over. Nothing is reclaimed on a timer.
    """

    poll = 0.05

    def __init__(self, path, wait=60, what="the state file"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.wait = wait
        self.what = what

    def _sentinel(self):
        fd, tmp = tempfile.mkstemp(prefix=".lock-", dir=os.path.dirname(self.path))
        try:
            os.write(fd, json.dumps({"pid": os.getpid(), "argv": sys.argv[1:4],
                                     "since": time.strftime("%Y-%m-%d %H:%M:%S")}).encode())
        finally:
            os.close(fd)
        return tmp

    def _holder(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return "gone", None
        except OSError:
            return "unreadable", None
        try:
            rec = json.loads(raw)
        except ValueError:
            return "broken", None
        if not isinstance(rec, dict) or not rec.get("pid"):
            return "broken", None
        return "held", rec

    def __enter__(self):
        deadline = time.time() + self.wait
        told = False
        tmp = self._sentinel()
        try:
            while True:
                try:
                    os.link(tmp, self.path)
                    return self
                except FileExistsError:
                    pass
                except OSError:
                    if time.time() > deadline:
                        raise Fail("gave up taking the lock on %s after %ds (%s)"
                                   % (self.what, self.wait, self.path))
                    time.sleep(self.poll)
                    continue
                verdict, rec = self._holder()
                if verdict == "gone":
                    continue
                if verdict == "broken":
                    log("lock on %s is a broken record; taking it over" % self.what)
                    _remove(self.path)
                    continue
                if verdict == "held" and not _pid_alive(rec["pid"]):
                    log("lock on %s held by dead pid %s; taking it over" % (self.what, rec["pid"]))
                    _remove(self.path)
                    continue
                if verdict == "held" and not told:
                    log("waiting for %s: pid %s holds it since %s" % (self.what, rec["pid"], rec.get("since")))
                    told = True
                if time.time() > deadline:
                    raise Fail("gave up waiting for %s after %ds; %s still holds %s. If that "
                               "process is gone, delete the file." % (self.what, self.wait,
                                                                     (rec or {}).get("pid", "someone"),
                                                                     self.path))
                time.sleep(self.poll)
        finally:
            _remove(tmp)

    def __exit__(self, *exc):
        _remove(self.path)
