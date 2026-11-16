"""Shared page setup for shoot.py (upstream also shares it with a probe.py that is not vendored here): real User-Agents, phone emulation under 768px,
a load fallback for pages that never go network-idle, a signed-in session (--storage-state) and
pre-shot actions (--before-shot) so hover, open-menu, focus and typed states can be captured."""
import contextlib
import fcntl
import os
import sys
import tempfile
from pathlib import Path

DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.5 Mobile/15E148 Safari/604.1")
# Viewport height per width. 375 is the SHORT phone (iPhone SE, 375x667): overlays, the thumb zone and the first-viewport
# hierarchy are judged there as well as on the tall 390x844 phone, because a bar that clears the hero CTA at 844 covers it at 667.
HEIGHTS = {375: 667, 390: 844, 1280: 800, 1440: 900}
SHORT_PHONE = 375

ACTIONS_HELP = ("actions run in order after load: hover:SEL click:SEL focus:SEL tab:N "
                "type:SEL=text press:Key wait:MS scroll:SEL top (scroll back to 0,0)")


@contextlib.contextmanager
def one_browser():
    """Hold a machine-wide lock while a headless browser runs, so parallel agents never run two at once.
    Override the lock file with FINESSE_BROWSER_LOCK."""
    path = os.environ.get("FINESSE_BROWSER_LOCK") or os.path.join(tempfile.gettempdir(), "finesse-browser.lock")
    with open(path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"waiting for the browser lock ({path}): another finesse browser is running", file=sys.stderr, flush=True)
            fcntl.flock(f, fcntl.LOCK_EX)
        yield


def to_url(u):
    return u if "://" in u else Path(u).resolve().as_uri()


def add_args(ap):
    ap.add_argument("--storage-state", help="Playwright storage-state JSON (cookies + localStorage) "
                    "for signed-in pages; save one with `playwright codegen --save-storage=state.json URL`")
    ap.add_argument("--before-shot", nargs="*", default=[], metavar="ACTION", help=ACTIONS_HELP)


def new_context(browser, width, reduced_motion=False, storage_state=None, dpr=1):
    mobile = width < 768
    return browser.new_context(
        viewport={"width": width, "height": HEIGHTS.get(width, 844 if mobile else 900)},
        device_scale_factor=dpr,  # 1 by default: image px == CSS px, so boxes map 1:1 onto screenshots
        is_mobile=mobile, has_touch=mobile,
        user_agent=MOBILE_UA if mobile else DESKTOP_UA,
        reduced_motion="reduce" if reduced_motion else "no-preference",
        storage_state=storage_state,
    )


def goto(page, url, wait_ms=800):
    """networkidle first; analytics-heavy pages may never idle, so fall back to load plus a wait."""
    try:
        page.goto(url, wait_until="networkidle", timeout=25000)
    except Exception:
        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(2500)
    page.wait_for_timeout(wait_ms)


def run_actions(page, actions):
    """Drive the page into a state before the shot or probe. Fails loudly on a bad selector."""
    for act in actions:
        kind, _, arg = act.partition(":")
        if kind == "hover":
            page.hover(arg, timeout=8000)
        elif kind == "click":
            page.click(arg, timeout=8000)
        elif kind == "focus":
            page.focus(arg, timeout=8000)
        elif kind == "tab":
            for _ in range(int(arg or 1)):
                page.keyboard.press("Tab")
        elif kind == "type":
            sel, _, text = arg.partition("=")
            page.fill(sel, "", timeout=8000)
            page.type(sel, text, delay=30)
        elif kind == "press":
            page.keyboard.press(arg)
        elif kind == "wait":
            page.wait_for_timeout(int(arg))
        elif kind == "scroll":
            page.locator(arg).first.scroll_into_view_if_needed(timeout=8000)
        elif kind == "top":
            page.evaluate("window.scrollTo(0, 0)")
        else:
            raise SystemExit(f"unknown --before-shot action {act!r}; {ACTIONS_HELP}")
        page.wait_for_timeout(250)
