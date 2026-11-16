"""Page tests for assets/pack.html (SPEC.md section 11, tests 10-13). Headless Chromium, local files,
no claude.ai runtime: a fake `window.claude` stands in for db/user/comments where a test needs writes.

python3 -m pytest -q lib/test_page.py lib/test_dp.py   (from the plugin root)
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dp  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

FIX = HERE / "fixtures"

FAKE_RUNTIME = r"""
<script>
(function(){
  var data = window.DP_SEED; delete window.DP_LOCAL_PACK;
  var store = {}; var subs = [];
  store["meta/pack"] = data.meta;
  data.decisions.forEach(function(d){ store["decisions/" + d.id] = d; });
  function snapColl(c){ var docs = Object.keys(store).filter(function(k){ return k.split("/")[0] === c; }).map(function(k){
      var v = store[k]; return {id:k.split("/")[1], exists:true, data:function(){ return v; }, metadata:{fromCache:false}}; });
    return {docs:docs, size:docs.length, empty:!docs.length, metadata:{fromCache:false, hasPendingWrites:false}, docChanges:function(){ return []; }}; }
  function notify(){ subs.forEach(function(s){ setTimeout(function(){ s(); }, 0); }); }
  var db = {
    doc:function(p){ return {path:p, set:function(v){ window.DP_WRITES.push([p, JSON.parse(JSON.stringify(v))]); store[p] = v; notify(); return Promise.resolve(); },
      onSnapshot:function(cb){ var f = function(){ var v = store[p]; cb({id:p.split("/")[1], exists:!!v, data:function(){ return v; }, metadata:{fromCache:false}}); }; subs.push(f); f(); return function(){}; }}; },
    collection:function(c){ return {onSnapshot:function(cb){ var f = function(){ cb(snapColl(c)); }; subs.push(f); setTimeout(f, 0); return function(){}; }}; }
  };
  var user = {id:function(){ return Promise.resolve("u_test"); }, isOwner:function(){ return Promise.resolve(true); }};
  window.DP_WRITES = []; window.DP_SENT = []; window.DP_SEND_CHECKS = 0;
  var seq = window.DP_CAN_SEND;
  var comments = seq ? {
    canSendToClaude:function(){ window.DP_SEND_CHECKS++; return Promise.resolve(seq.length > 1 ? seq.shift() : seq[0]); },
    sendToClaude:function(o){ window.DP_SENT.push(o); return Promise.resolve(); },
    anchorFor:function(){ return Promise.resolve("anchor-1"); }} : null;
  window.confirm = function(){ throw new Error("window.confirm must not be used"); };
  window.claude = {use:function(n){ return Promise.resolve(n === "db" ? db : n === "user" ? user : n === "comments" ? comments : null); }};
})();
</script>
"""

NEVER = """<script>window.DP_TIMEOUT_MS = 1500; window.claude = {use: function(){ return new Promise(function(){}); }};</script>"""


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dp-page-")
        cls.old_env = {k: os.environ.get(k) for k in ("DECISION_PACK_HOME", "XDG_CONFIG_HOME", "DP_TIMEZONE")}
        os.environ.update(DECISION_PACK_HOME=cls.tmp, XDG_CONFIG_HOME=str(Path(cls.tmp) / "config"),
                          DP_TIMEZONE="America/New_York")
        pack = json.loads((FIX / "sample-pack.json").read_text().replace("{FIX}", str(FIX)))
        dp.save_pack(pack)
        cls.local = dp.local_html(pack)
        html = cls.local.read_text()
        start = html.index("<script>window.DP_LOCAL_PACK=")
        end = html.index("</script>", start) + len("</script>")
        seed = html[start:end].replace("window.DP_LOCAL_PACK=", "window.DP_LOCAL_PACK=window.DP_SEED=")
        cls.live = Path(cls.tmp) / "live.html"
        cls.live.write_text(html[:start] + seed + FAKE_RUNTIME + html[end:])
        cls.live_send = Path(cls.tmp) / "live_send.html"
        cls.live_send.write_text(html[:start] + seed + '<script>window.DP_CAN_SEND=["no_session","available"];</script>'
                                 + FAKE_RUNTIME + html[end:])
        cls.never = Path(cls.tmp) / "never.html"
        cls.never.write_text(dp.TEMPLATE.read_text().replace("<!--DP_LOCAL-->", NEVER))
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for k, v in cls.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def page(self, path, width, scheme="light", hash_=""):
        ctx = self.browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 844},
                                       color_scheme=scheme, device_scale_factor=1)
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(Path(path).as_uri() + hash_)
        pg.wait_for_selector("article.card", timeout=5000)
        pg.wait_for_timeout(300)
        pg._errors = errors
        return ctx, pg

    def no_hscroll(self, pg):
        return pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

    def test_10_layout_banner_cards_both_themes(self):
        for w in (390, 1440):
            for scheme in ("light", "dark"):
                ctx, pg = self.page(self.local, w, scheme)
                try:
                    self.assertTrue(self.no_hscroll(pg), f"horizontal scroll at {w} {scheme}")
                    self.assertIn("View only here", pg.inner_text("#banners"))
                    self.assertEqual(pg.locator("article.card.open").count(), 3)
                    bg = pg.evaluate("getComputedStyle(document.body).backgroundColor")
                    self.assertEqual(bg, "rgb(17, 21, 19)" if scheme == "dark" else "rgb(245, 246, 244)")
                    self.assertEqual(pg._errors, [])
                finally:
                    ctx.close()

    def test_10_live_mode_no_banner(self):
        ctx, pg = self.page(self.live, 390)
        try:
            self.assertEqual(pg.inner_text("#banners").strip(), "")
            self.assertTrue(pg.locator("#takeall").is_visible())
            self.assertTrue(self.no_hscroll(pg))
        finally:
            ctx.close()

    def test_10_timeout_message(self):
        ctx = self.browser.new_context(viewport={"width": 390, "height": 844})
        pg = ctx.new_page()
        try:
            pg.goto(self.never.as_uri())
            pg.wait_for_selector(".banner.err", timeout=5000)
            self.assertIn("Could not load decisions", pg.inner_text("#banners"))
        finally:
            ctx.close()
        self.assertIn("window.DP_TIMEOUT_MS || 10000", dp.TEMPLATE.read_text())

    def test_11_hotspots_align(self):
        for w in (390, 1440):
            ctx, pg = self.page(self.live, w)
            try:
                frame = pg.locator("#d-Q-1 .frame").first
                width = int(frame.get_attribute("data-width"))
                img = frame.locator("img").bounding_box()
                pack = json.loads((FIX / "sample-pack.json").read_text().replace("{FIX}", str(FIX)))
                shot = [s for s in pack["decisions"][0]["options"][0]["shots"] if s["width"] == width][0]
                k = img["width"] / shot["px"][0]
                for c in shot["callouts"]:
                    hb = frame.locator(f".hot[aria-label^='Callout {c['n']}:']").bounding_box()
                    want = [img["x"] + c["box"][0] * k, img["y"] + c["box"][1] * k, c["box"][2] * k, c["box"][3] * k]
                    got = [hb["x"], hb["y"], hb["width"], hb["height"]]
                    for a, b in zip(want, got):
                        self.assertLessEqual(abs(a - b), 4, f"{w}: callout {c['n']} want {want} got {got}")
                    tb = pg.evaluate("e => { const r = e.getBoundingClientRect(); return r.width }",
                                     frame.locator(f".hot[aria-label^='Callout {c['n']}:']").element_handle())
                    self.assertGreater(tb, 0)
            finally:
                ctx.close()

    def test_11_tap_callout_adds_chip(self):
        ctx, pg = self.page(self.live, 1440)
        try:
            pg.locator("#d-Q-1 .frame .hot[aria-label^='Callout 3:']").click()
            pg.wait_for_selector("#d-Q-1 .rchip", timeout=3000)
            self.assertIn("Callout 3", pg.inner_text("#d-Q-1 .regions"))
            writes = pg.evaluate("window.DP_WRITES")
            self.assertEqual(writes[-1][0], "responses/Q-1")
            self.assertEqual(writes[-1][1]["regions"][0]["callout_n"], 3)
            self.assertEqual(writes[-1][1]["regions"][0]["anchor"], "Q-1:option-A-1440#c3")
            pg.locator("#d-Q-1 button.opt").nth(1).click()
            pg.wait_for_timeout(200)
            last = pg.evaluate("window.DP_WRITES")[-1][1]
            self.assertEqual(last["choice_key"], "B")
            self.assertEqual(last["regions"][0]["callout_n"], 3)
            self.assertTrue(pg.locator("#sendbar").is_visible())
            self.assertIn("2 answers saved" if len({w[0] for w in pg.evaluate("window.DP_WRITES")}) > 1 else "1 answer saved",
                          pg.inner_text("#sendbar"))
        finally:
            ctx.close()

    def test_11_point_box(self):
        ctx, pg = self.page(self.live, 1440)
        try:
            pg.locator("#d-Q-1 .ptbtn").click()
            fr = pg.locator("#d-Q-1 .frame").first.bounding_box()
            pg.mouse.move(fr["x"] + fr["width"] * 0.55, fr["y"] + fr["height"] * 0.08)
            pg.mouse.down()
            pg.mouse.move(fr["x"] + fr["width"] * 0.7, fr["y"] + fr["height"] * 0.12, steps=4)
            pg.mouse.up()
            pg.wait_for_selector("#d-Q-1 .rchip", timeout=3000)
            g = pg.evaluate("window.DP_WRITES")[-1][1]["regions"][0]
            self.assertIsNone(g["callout_n"])
            self.assertGreater(g["box_px"][2], 0)
            self.assertIn("Box on", pg.inner_text("#d-Q-1 .regions"))
        finally:
            ctx.close()

    def test_12_lightbox(self):
        ctx, pg = self.page(self.local, 390)
        try:
            pg.locator("#d-Q-1 .frame img").first.click()
            pg.wait_for_selector(".lb", timeout=3000)
            # No target=_blank popup (the artifact iframe may block popups): "Actual size" works in place.
            self.assertEqual(pg.locator(".lb a").count(), 0)
            pg.locator(".lb button", has_text="Actual size").click()
            self.assertIn("z1", pg.get_attribute(".lb", "class"))
            # Chromium reports "pan-x pan-y pinch-zoom" as its shorthand "manipulation"
            self.assertIn(pg.evaluate("getComputedStyle(document.querySelector('.lb .scroll')).touchAction"),
                          ("manipulation", "pan-x pan-y pinch-zoom", "pinch-zoom pan-x pan-y"))
            self.assertIn("touch-action:pinch-zoom pan-x pan-y", dp.TEMPLATE.read_text())
            pg.keyboard.press("Escape")
            self.assertEqual(pg.locator(".lb").count(), 0)
        finally:
            ctx.close()

    def test_send_button_rechecks(self):
        """canSendToClaude says no_session at load and available later: the Send button must appear."""
        ctx, pg = self.page(self.live_send, 390)
        try:
            self.assertFalse(pg.locator("#sendbtn").is_visible())
            pg.locator("#d-Q-1 button.opt").nth(1).click()
            pg.wait_for_selector("#sendbtn:not([hidden])", timeout=3000)
            self.assertGreaterEqual(pg.evaluate("window.DP_SEND_CHECKS"), 2)
            pg.locator("#sendbtn").click()
            pg.wait_for_timeout(200)
            sent = pg.evaluate("window.DP_SENT")
            self.assertEqual(len(sent), 1)
            self.assertIn("Q-1 B", sent[0]["text"])
            # times use the pack's configured zone (meta.timezone) and its own abbreviation
            self.assertRegex(pg.evaluate("window.DP_WRITES[window.DP_WRITES.length - 1][1].answered_at_local"),
                             r"^\w{3} \w{3} \d+ \d+:\d\d [AP]M (EST|EDT)$")
        finally:
            ctx.close()

    def test_takeall_sheet(self):
        """Take all my picks uses an in-page sheet (window.confirm throws in this fake runtime)."""
        ctx, pg = self.page(self.live, 390)
        try:
            pg.locator("#takeall").click()
            pg.wait_for_selector("#takeall-sheet", timeout=2000)
            text = pg.inner_text("#takeall-sheet")
            self.assertIn("Q-1 → A", text)
            self.assertIn("Q-2 → A", text)
            self.assertNotIn("Q-3", text)
            small = pg.evaluate("""[...document.querySelectorAll('#takeall-sheet button')].map(b => b.getBoundingClientRect().height)
              .filter(h => h < 44).length""")
            self.assertEqual(small, 0)
            pg.locator("#takeall-sheet button", has_text="Cancel").click()
            self.assertEqual(pg.locator("#takeall-sheet").count(), 0)
            self.assertEqual(pg.evaluate("window.DP_WRITES"), [])
            pg.locator("#takeall").click()
            pg.locator("#takeall-sheet button", has_text="Take 2 picks").click()
            pg.wait_for_timeout(300)
            writes = pg.evaluate("window.DP_WRITES")
            self.assertEqual(sorted(w[0] for w in writes), ["responses/Q-1", "responses/Q-2"])
            self.assertTrue(all(w[1]["choice_key"] == "A" and w[1]["kind"] == "ok_recommended" for w in writes))
            self.assertEqual(pg._errors, [])
        finally:
            ctx.close()

    def test_earlier_answer_button_saves_option(self):
        """The 'You said yes to ...' box names the option and its button saves that choice_key."""
        ctx, pg = self.page(self.live, 390)
        try:
            box = pg.inner_text("#d-Q-2 .maybe")
            self.assertIn("You said yes to A (pro only) on Nov 6", box)
            pg.locator("#d-Q-2 .maybe button", has_text="Keep my Nov 6 answer: A").click()
            pg.wait_for_timeout(200)
            w = pg.evaluate("window.DP_WRITES")[-1]
            self.assertEqual(w[0], "responses/Q-2")
            self.assertEqual(w[1]["choice_key"], "A")
            self.assertEqual(w[1]["kind"], "yes_that_stands")
        finally:
            ctx.close()

    def test_13_deep_link(self):
        ctx, pg = self.page(self.local, 390, hash_="#d-Q-3")
        try:
            pg.wait_for_timeout(800)
            self.assertIn("hl", pg.get_attribute("#d-Q-3", "class"))
            top = pg.evaluate("document.getElementById('d-Q-3').getBoundingClientRect().top")
            self.assertLess(top, 400)
        finally:
            ctx.close()

    def test_a11y_targets(self):
        ctx, pg = self.page(self.live, 390)
        try:
            small = pg.evaluate("""[...document.querySelectorAll('button.opt, .alts button, .takeall, .rowbtns button')]
              .filter(b => b.offsetParent).map(b => b.getBoundingClientRect()).filter(r => r.height < 44).length""")
            self.assertEqual(small, 0)
        finally:
            ctx.close()


if __name__ == "__main__":
    unittest.main()
