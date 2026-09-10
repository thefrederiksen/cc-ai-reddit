# -*- coding: utf-8 -*-
"""The one way every command touches the browser.

Resolve the browser from the profile registry and require it to be UP (this
tool never launches or signs in a browser), attach browser-harness to it, hold
a per-browser lock so two runs never drive one browser at once, open ONE
background tab, and close that tab at the end.

The browser is named one of two ways, and exactly one:
  --profile NAME    looked up with `bh-profiles.ps1 env NAME` and required to be
                    UP by `bh-profiles.ps1 status NAME`. The script is found at
                    CC_AI_REDDIT_BH_PROFILES, or in its standard place under
                    %LOCALAPPDATA%\\cc-director\\connections. Default profile:
                    CC_AI_REDDIT_PROFILE, else "cencon".
  --cdp-url URL     a Chromium you started yourself with --remote-debugging-port.

MEASURED 2026-09-10
  * A background tab created over CDP can be read, clicked, typed into and
    screenshotted without being brought to the front. Wheel scrolling in a
    hidden tab needs focus emulation, switched on only around the scroll.
  * Leaving a post form that holds text raises a beforeunload dialog that
    freezes the page's JavaScript; every evaluate then times out. The tool
    empties a form and proves it empty before it leaves, and names any dialog
    it meets rather than timing out on it.
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from contextlib import contextmanager

from . import selectors as SEL
from .state import Fail, FileLock, home, log, read_json, write_json_atomic

NAV_GAP = 4.0       # seconds between page loads, across invocations


def _profiles_script():
    path = os.environ.get("CC_AI_REDDIT_BH_PROFILES") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "cc-director", "connections", "bh-profiles.ps1")
    if not os.path.isfile(path):
        raise Fail("no browser profile registry script at %s. Point CC_AI_REDDIT_BH_PROFILES at your "
                   "bh-profiles.ps1, or pass --cdp-url for a browser started with remote debugging." % path)
    return path


def _registry(*args, timeout=90):
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _profiles_script()]
                       + list(args), capture_output=True, text=True, timeout=timeout)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def resolve(profile=None, cdp_url=None):
    """(daemon name, CDP URL) for the browser to drive. Fails with the fix."""
    if cdp_url:
        m = re.match(r"^http://(127\.0\.0\.1|localhost):(\d{2,5})/?$", cdp_url)
        if not m:
            raise Fail("--cdp-url must look like http://127.0.0.1:9222")
        try:
            with urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=5) as r:
                json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            raise Fail("nothing answers CDP at %s (%s). Start the browser with --remote-debugging-port."
                       % (cdp_url, exc))
        return "cc-ai-reddit-%s" % m.group(2), cdp_url.rstrip("/")
    name = profile or os.environ.get("CC_AI_REDDIT_PROFILE") or "cencon"
    code, out = _registry("env", name)
    env = dict(re.findall(r"export (BU_NAME|BU_CDP_URL)=(\S+)", out))
    if code != 0 or "BU_CDP_URL" not in env:
        raise Fail("profile %r is not in the browser registry: %s" % (name, out[:300]))
    code, out = _registry("status", name)
    if code != 0:
        raise Fail("browser profile %r is not usable (%s). Start it with: bh-profiles.ps1 up %s. This tool "
                   "never launches a browser." % (name, out[:200], name))
    return env["BU_NAME"], env["BU_CDP_URL"]


class Browser(object):

    def __init__(self, profile=None, cdp_url=None):
        self.name, self.cdp_url = resolve(profile, cdp_url)
        self.lock = FileLock(os.path.join(home(), "browser-%s.lock" % self.name), wait=900, what="the browser")
        self.h = None
        self.tab = None

    def __enter__(self):
        os.environ["BU_NAME"] = self.name
        os.environ["BU_CDP_URL"] = self.cdp_url
        os.environ.setdefault("BH_TAB_MARKER", "0")
        self.lock.__enter__()
        try:
            # Imported only now: browser-harness reads BU_NAME when it is imported.
            from browser_harness import admin
            try:
                admin.ensure_daemon()
            except RuntimeError as exc:
                raise Fail("browser-harness could not attach to %s: %s" % (self.cdp_url, exc))
            from browser_harness import helpers
            self.h = helpers
            self.tab = helpers.new_tab("about:blank")
            return self
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        try:
            if self.h is not None and self.tab:
                if self.pending_dialog():
                    log("a dialog is open on the working tab; it is closed with the tab")
                self.h.close_tab(self.tab)
        except Exception as e:
            log("could not close the working tab: %s" % e)
        finally:
            self.lock.__exit__(None, None, None)

    # -- page state -------------------------------------------------------

    def js(self, expr):
        return self.h.js(expr)

    def url(self):
        return self.h.page_info()["url"]

    def text(self, n=3000):
        return self.js("document.body ? document.body.innerText.slice(0, %d) : ''" % n) or ""

    def pending_dialog(self):
        return self.h.page_info().get("dialog")

    def signed_in(self):
        return bool(self.js(SEL.SIGNED_IN_JS))

    def me(self):
        """{name, id} of the signed-in account, or None when signed out."""
        return self.js(SEL.ME_JS)

    def _space(self):
        path = os.path.join(home(), "navigation.json")
        last = (read_json(path) or {}).get("last", 0)
        wait = last + NAV_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        write_json_atomic(path, {"last": time.time()})

    def goto(self, url, timeout=45, settle=2.0):
        """Load a Reddit page and wait until it is really there: past any JS
        challenge, shreddit-app present, document complete. Returns the URL."""
        self._space()
        self.h.goto_url(url)
        deadline = time.time() + timeout
        cur = url
        while True:
            time.sleep(1)
            try:
                info = self.h.page_info()
                if info.get("dialog"):
                    raise Fail("a %s dialog is open on the page (%r); refusing to guess an answer to it"
                               % (info["dialog"].get("type"), info["dialog"].get("message")))
                cur = info["url"]
                ready = self.js("document.readyState === 'complete' && !!document.querySelector('shreddit-app')")
            except (RuntimeError, TimeoutError, KeyError):
                # MEASURED 2026-09-10: while a document is being replaced (and on
                # one first load, for several seconds) evaluate times out. That
                # is the page loading, and the deadline below still bounds it.
                ready = False
            if ready and "js_challenge" not in cur:
                break
            if time.time() > deadline:
                raise Fail("%s did not finish loading in %ds (now at %s): %r"
                           % (url, timeout, cur, self.text(200)))
        time.sleep(settle)
        m = SEL.BLOCKED.search(self.text(2000))
        if m:
            raise Fail("Reddit is refusing this browser on %s (%r). Stop for today." % (url, m.group(0)))
        return cur

    def wait_for(self, expr, what, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.js(expr)
            if value:
                return value
            time.sleep(0.5)
        raise Fail("%s did not appear within %ds on %s" % (what, timeout, self.url()))

    # -- finding and pressing ---------------------------------------------

    def ax(self, name, roles=("button",)):
        """Visible controls whose accessible name matches `name`, with the
        viewport coordinates of their centres."""
        out = []
        height = self.js("innerHeight")
        for n in self.h.cdp("Accessibility.getFullAXTree")["nodes"]:
            if n.get("ignored"):
                continue
            role = (n.get("role") or {}).get("value")
            label = (n.get("name") or {}).get("value") or ""
            if role not in roles or not name.search(label):
                continue
            try:
                q = self.h.cdp("DOM.getBoxModel", backendNodeId=n["backendDOMNodeId"])["model"]["content"]
            except Exception:
                continue            # not rendered: no box
            x, y = sum(q[0::2]) / 4, sum(q[1::2]) / 4
            if 0 < y < height:
                out.append({"role": role, "name": label, "x": x, "y": y})
        return out

    def click(self, x, y):
        self.h.click_at_xy(x, y)

    def insert_text(self, text):
        self.h.cdp("Input.insertText", text=text)

    def key(self, key, modifiers=0):
        self.h.press_key(key, modifiers=modifiers)

    def select_all_and_delete(self):
        """Select everything in the focused field and delete it.

        MEASURED 2026-09-10: Ctrl+A sent as an ordinary key press emptied the
        rich text editor but left the post title (a textarea in a shadow root)
        untouched. Chrome needs the SelectAll editing command on the raw key
        down, which is how browser-harness's own fill_input does it."""
        mac = "Macintosh" in (self.h.cdp("Browser.getVersion").get("userAgent") or "")
        sa = {"key": "a", "code": "KeyA", "modifiers": 4 if mac else 2,
              "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65, "commands": ["SelectAll"]}
        self.h.cdp("Input.dispatchKeyEvent", type="rawKeyDown", **sa)
        self.h.cdp("Input.dispatchKeyEvent", type="keyUp", **{k: v for k, v in sa.items() if k != "commands"})
        self.h.press_key("Backspace")

    _focus_depth = 0

    @contextmanager
    def focused(self):
        """Focus emulation for a hidden tab, re-entrant: only the outermost
        block switches it on and off."""
        if self._focus_depth == 0:
            self.h.cdp("Emulation.setFocusEmulationEnabled", enabled=True)
        self._focus_depth += 1
        try:
            yield
        finally:
            self._focus_depth -= 1
            if self._focus_depth == 0:
                self.h.cdp("Emulation.setFocusEmulationEnabled", enabled=False)

    def capture_existing_reddit_tab(self, path):
        """Screenshot the one Reddit tab already open in this browser, without
        bringing it to the front. Several or none: say so, never pick one."""
        tabs = [t for t in self.h.list_tabs(include_chrome=False)
                if t["targetId"] != self.tab and "reddit.com" in (t.get("url") or "")]
        if len(tabs) != 1:
            raise Fail("found %d Reddit tabs in this browser; name the page to capture: shot <url>" % len(tabs))
        self.h.switch_tab(tabs[0]["targetId"])
        try:
            return self.screenshot(path), tabs[0]["url"]
        finally:
            self.h.switch_tab(self.tab)

    def scroll_to(self, rect_expr, what, top=150, margin=220, steps=14):
        """Wheel-scroll until the element whose rectangle `rect_expr` returns
        ({y, h}) sits inside the viewport. Returns that rectangle."""
        with self.focused():
            for _ in range(steps):
                r = self.js(rect_expr)
                if r is None:
                    raise Fail("%s is not on the page at %s" % (what, self.url()))
                vh = self.js("innerHeight")
                if r["h"] > 0 and top < r["y"] < vh - margin:
                    return r
                self.h.scroll(640, 400, dy=350 if r["y"] >= vh - margin else -350)
                time.sleep(1.0)
        raise Fail("could not scroll %s into view at %s" % (what, self.url()))

    def scroll_down(self, times=1):
        with self.focused():
            for _ in range(times):
                self.h.scroll(640, 400, dy=900)
                time.sleep(1.2)

    def screenshot(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        try:
            return self.h.capture_screenshot(path, max_dim=1800)
        except RuntimeError as exc:
            raise Fail("the browser did not produce a screenshot of %s (%s)" % (self.url(), exc))

    def leave(self, url):
        """Navigate away from a form this run has already proven empty. A
        beforeunload prompt here can only be guarding nothing, so it is
        answered Leave. Called ONLY after that proof.

        MEASURED 2026-09-10: while the prompt is up, Page.navigate does not
        return, so the navigate call times out. That timeout is the prompt
        announcing itself, and the prompt is then read and answered."""
        try:
            self.h.goto_url(url)
        except (TimeoutError, RuntimeError):
            pass
        time.sleep(2.5)
        dialog = self.pending_dialog()
        if dialog:
            if dialog.get("type") != "beforeunload":
                raise Fail("a %s dialog appeared while leaving the form (%r); not answering it blind"
                           % (dialog.get("type"), dialog.get("message")))
            log("answering the page's leave prompt with Leave: the form was proven empty first")
            self.h.cdp("Page.handleJavaScriptDialog", accept=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            time.sleep(1)
            try:
                if self.url().rstrip("/") == url.rstrip("/"):
                    return
            except (TimeoutError, RuntimeError):
                continue
        raise Fail("could not leave the form for %s; the tab is at %s" % (url, self.url()))
