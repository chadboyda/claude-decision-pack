#!/usr/bin/env python3
"""Screenshot a page at phone and desktop widths with ONE headless Chromium, then close it.

usage:
  python3 shoot.py URL --out DIR [--name before] [--widths 390 1440] [--full] [--wait 800]
                   [--box "css selector" ...] [--frames 4] [--reduced-motion] [--dpr 2]
                   [--storage-state state.json] [--before-shot "click:#menu" "wait:300" ...]

URL may be http(s)://..., file://..., or a local .html path.
Under 768px the page is loaded as a phone (iPhone UA, is_mobile, has_touch); wider widths use a
desktop Chrome UA. Device scale is 1 by default, so image px == CSS px. --dpr 2 gives sharper,
readable shots; then pass `annotate.py mark --scale 2` (boxes stay in CSS px).
--storage-state loads a signed-in session. --before-shot drives the page into a state (hover, open
menu, focused field, typed input) before every shot, box and frame; see _page.ACTIONS_HELP.

Writes, per width W:
  DIR/<name>-W.png          first viewport (always). Trust this one for fixed/sticky elements.
                            Covers page y 0..viewport height only (plus scrollY, printed when an
                            action scrolled the page). Mark anything lower on the -full shot.
  DIR/<name>-W-full.png     full page (with --full). Fixed elements can land in odd places here;
                            never report an overlay that shows only in the full-page shot.
  DIR/<name>-W.boxes.json   with --box: [{"selector","index","x","y","w","h"}] in page coordinates
                            (valid for both the viewport and the full-page shot).
  DIR/<name>-W-frame-K.png  with --frames N: N viewport shots at evenly spaced scroll offsets,
                            taken by wheel-scrolling so scroll-linked motion runs. Combine with
                            annotate.py strip. Offsets are in DIR/<name>-W-frames.json.
--reduced-motion loads the page with prefers-reduced-motion: reduce (run it twice and compare strips).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _page import add_args, goto, new_context, one_browser, run_actions, to_url  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="before")
    ap.add_argument("--widths", nargs="+", type=int, default=[390, 1440])
    ap.add_argument("--full", action="store_true", help="also save a full-page screenshot")
    ap.add_argument("--wait", type=int, default=800, help="ms to wait after load")
    ap.add_argument("--box", nargs="*", default=[], help="CSS selectors to measure")
    ap.add_argument("--frames", type=int, default=0, help="N scroll frames for motion review")
    ap.add_argument("--reduced-motion", action="store_true")
    ap.add_argument("--dpr", type=float, default=1, help="device pixel ratio (pass the same to mark --scale)")
    add_args(ap)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright is not installed: pip install playwright && playwright install chromium")

    with one_browser(), sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for w in a.widths:
                ctx = new_context(browser, w, a.reduced_motion, a.storage_state, a.dpr)
                page = ctx.new_page()
                goto(page, to_url(a.url), a.wait)
                run_actions(page, a.before_shot)
                base = out / f"{a.name}-{w}"
                page.screenshot(path=f"{base}.png")
                sy = page.evaluate("Math.round(window.scrollY)")
                Path(f"{base}.meta.json").write_text(json.dumps(
                    {"scrollY": sy, "viewport": page.viewport_size, "dpr": a.dpr, "actions": a.before_shot}))
                print(f"{base}.png" + (f"  (viewport shot at scrollY={sy}: pass `mark --offset 0 {sy}`)" if sy else ""))
                if a.full:
                    page.screenshot(path=f"{base}-full.png", full_page=True)
                    print(f"{base}-full.png")
                if a.box:
                    boxes = []
                    for sel in a.box:
                        for i, el in enumerate(page.query_selector_all(sel)):
                            b = el.bounding_box()
                            if b:
                                boxes.append({"selector": sel, "index": i, "x": round(b["x"]),
                                              "y": round(b["y"]), "w": round(b["width"]),
                                              "h": round(b["height"])})
                    Path(f"{base}.boxes.json").write_text(json.dumps(boxes, indent=1))
                    print(f"{base}.boxes.json")
                if a.frames > 1:
                    vh = page.viewport_size["height"]
                    total = page.evaluate("document.documentElement.scrollHeight")
                    span = max(0, total - vh)
                    offsets = []
                    for k in range(a.frames):
                        target = round(span * k / (a.frames - 1))
                        cur = page.evaluate("window.scrollY")
                        while abs(target - cur) > 4:  # wheel in steps so scroll-linked effects fire
                            page.mouse.wheel(0, max(-400, min(400, target - cur)))
                            page.wait_for_timeout(60)
                            nxt = page.evaluate("window.scrollY")
                            if nxt == cur:
                                break
                            cur = nxt
                        page.wait_for_timeout(700)
                        offsets.append(page.evaluate("window.scrollY"))
                        page.screenshot(path=f"{base}-frame-{k}.png")
                        print(f"{base}-frame-{k}.png")
                    Path(f"{base}-frames.json").write_text(json.dumps({"scrollY": offsets, "scrollHeight": total}))
                ctx.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
