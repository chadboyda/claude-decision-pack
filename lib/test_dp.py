"""Unit tests for dp.py (SPEC.md section 11, tests 1-9).

python3 -m pytest -q lib/test_page.py lib/test_dp.py   (from the plugin root)
"""
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dp  # noqa: E402
import schema  # noqa: E402

FIX = HERE / "fixtures"
# Optional: point DP_TW_PARSER_DIR at a Todo Watch daemon folder (holding detail.py) to run its real parser.
TW_DETAIL = Path(os.environ["DP_TW_PARSER_DIR"]) if os.environ.get("DP_TW_PARSER_DIR") else None
ENV_KEYS = ("DECISION_PACK_HOME", "XDG_CONFIG_HOME", "DP_TIMEZONE", "DP_LEDGER", "DP_MEMORY_DIR",
            "DP_TRANSCRIPTS_DIR", "DP_NOW", "DP_TW_BIN", "DP_STOP_HOOK")


def isolate(tmp):
    """Point every setting at tmp, with an empty config dir and a fixed zone. Returns the old values."""
    old = {k: os.environ.get(k) for k in ENV_KEYS}
    os.environ["DECISION_PACK_HOME"] = str(tmp)
    os.environ["XDG_CONFIG_HOME"] = str(Path(tmp) / "config")
    os.environ["DP_TIMEZONE"] = "America/New_York"
    os.environ["DP_LEDGER"] = str(Path(tmp) / "ledger.md.txt")
    os.environ["DP_MEMORY_DIR"] = str(Path(tmp) / "memory")
    os.environ["DP_TRANSCRIPTS_DIR"] = str(Path(tmp) / "transcripts")
    os.environ["DP_TW_BIN"] = "/bin/false"
    os.environ.pop("DP_NOW", None)
    os.environ.pop("DP_STOP_HOOK", None)
    return old


def restore(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def sample():
    raw = (FIX / "sample-pack.json").read_text().replace("{FIX}", str(FIX))
    return json.loads(raw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp-test-")
        self.env = isolate(self.tmp)
        self.pack = sample()
        dp.save_pack(self.pack)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        restore(self.env)

    def lint_errors(self, pack):
        return dp.lint(pack)[0]


class TestSchemaAndLint(Base):
    """Test 1: the sample passes; each lint rule has one failing fixture."""

    def test_sample_passes(self):
        self.assertEqual(schema.validate(self.pack), [])
        errs, warns = dp.lint(self.pack)
        self.assertEqual(errs, [], errs)

    def mutate(self, fn, expect):
        p = copy.deepcopy(self.pack)
        fn(p)
        errs = self.lint_errors(p)
        self.assertTrue(any(expect in e for e in errs), f"expected {expect!r} in {errs}")

    def d(self, p, i=0):
        return p["decisions"][i]

    def test_title_too_long(self):
        self.mutate(lambda p: self.d(p).update(title="x" * 58 + " ok?"), "max 60")

    def test_title_not_question(self):
        self.mutate(lambda p: self.d(p).update(title="Start with a set date"), "ending in '?'")

    def test_title_has_id(self):
        self.mutate(lambda p: self.d(p).update(title="Merge #123 first?"), "PR, ticket or file ID")

    def test_title_has_ticket(self):
        self.mutate(lambda p: self.d(p).update(title="Ship ABC-123 today?"), "PR, ticket or file ID")

    def test_two_recommended(self):
        self.mutate(lambda p: self.d(p)["options"][1].update(recommended=True), "exactly one option")

    def test_no_recommended(self):
        self.mutate(lambda p: self.d(p)["options"][0].update(recommended=False), "exactly one option")

    def test_one_option(self):
        self.mutate(lambda p: self.d(p).update(options=self.d(p)["options"][:1]), "2 or 3 options")

    def test_four_options(self):
        def f(p):
            o = self.d(p, 1)["options"]
            o.append(dict(o[2], key="C", button="Later"))
        self.mutate(f, "2 or 3 options")

    def test_button_long(self):
        self.mutate(lambda p: self.d(p)["options"][0].update(button="Set a date for every card"), "max 18")

    def test_no_history_check(self):
        self.mutate(lambda p: self.d(p).pop("history_check"), "history_check")

    def test_exact_log_hit(self):
        dp.log_path().write_text(json.dumps({"id": "OLD-1", "project": "other", "question": "Turn on the 4 switches?",
                                             "choice_key": "A", "choice_label": "Turn on", "answered_at_local": "Mon Nov 9 2:00 PM EST"}) + "\n")
        self.mutate(lambda p: None, "already answered")

    def test_no_shot_no_reason(self):
        def f(p):
            d = self.d(p, 2)
            d["shots"] = []
            d["no_visual_reason"] = None
        self.mutate(f, "no picture")

    def test_reason_on_ui(self):
        self.mutate(lambda p: self.d(p).update(no_visual_reason="none needed"), "needs a picture")

    def test_ui_without_390(self):
        def f(p):
            for o in self.d(p)["options"]:
                o["shots"] = [s for s in o["shots"] if s["width"] == 1440]
        self.mutate(f, "no 390")

    def test_gated_without_pair(self):
        self.mutate(lambda p: self.d(p).update(gate="pricing", default_key=None, default_at=None, default_at_local=None),
                    "before/after pair")

    def test_unviewed(self):
        self.mutate(lambda p: self.d(p)["options"][0]["shots"][0].update(viewed=False), "not viewed")

    def test_missing_file(self):
        self.mutate(lambda p: self.d(p)["options"][0]["shots"][0].update(path="/nope/x.png"), "missing file")

    def test_big_png(self):
        big = Path(self.tmp) / "big.png"
        big.write_bytes(b"\x89PNG" + os.urandom(700 * 1024))
        self.mutate(lambda p: self.d(p)["options"][0]["shots"][0].update(path=str(big)), "over 600 KB")

    def test_localhost(self):
        self.mutate(lambda p: self.d(p).update(details_md="http://localhost:3000/x"), "localhost")

    def test_banned_note_word(self):
        self.mutate(lambda p: self.d(p)["options"][0]["shots"][0]["callouts"][0].update(note="Fixed in PR 12"), "internal word")

    def test_note_too_long(self):
        self.mutate(lambda p: self.d(p)["options"][0]["shots"][0]["callouts"][0].update(note="word " * 20), "over 90")

    def test_default_on_one_way(self):
        self.mutate(lambda p: self.d(p, 2).update(default_key="A", default_at_local="Wed Nov 18 6:00 PM EST"), "not allowed")

    def test_default_without_time(self):
        self.mutate(lambda p: self.d(p).update(default_at_local=None), "no default_at_local")

    def test_update_text(self):
        self.mutate(lambda p: self.d(p).update(bluf="Update: I recommend A now."), "update/was-now")

    def test_two_asks_warns(self):
        p = copy.deepcopy(self.pack)
        self.d(p).update(title="Merge it and turn on the switch?")
        errs, warns = dp.lint(p)
        self.assertTrue(any("two asks" in w for w in warns))
        self.assertFalse(any("two asks" in e for e in errs))


class TestChatMapping(Base):
    """Test 2."""

    def test_ok_picks_two_way_only(self):
        r = dp.map_chat(self.pack, "ok")
        self.assertEqual({a["id"]: a["choice_key"] for a in r["answers"]}, {"Q-1": "A", "Q-2": "A"})
        self.assertEqual(r["skipped_one_way"], ["Q-3"])

    def test_numbered(self):
        r = dp.map_chat(self.pack, "1. yes 2. B 3. no")
        self.assertEqual({a["id"]: a["choice_key"] for a in r["answers"]}, {"Q-1": "A", "Q-2": "B", "Q-3": "B"})
        self.assertEqual(r["unclear"], [])

    def test_maybe_is_unclear_for_one_card(self):
        r = dp.map_chat(self.pack, "2. maybe")
        self.assertEqual(r["answers"], [])
        self.assertEqual([u["id"] for u in r["unclear"]], ["Q-2"])

    def test_yes_does_not_spread(self):
        r = dp.map_chat(self.pack, "1. yes 2. maybe")
        self.assertEqual([a["id"] for a in r["answers"]], ["Q-1"])
        self.assertEqual([u["id"] for u in r["unclear"]], ["Q-2"])

    def test_no_with_three_options_is_unclear(self):
        r = dp.map_chat(self.pack, "2. no")
        self.assertEqual([u["id"] for u in r["unclear"]], ["Q-2"])

    def test_ids_work(self):
        r = dp.map_chat(self.pack, "Q-2: C")
        self.assertEqual([(a["id"], a["choice_key"]) for a in r["answers"]], [("Q-2", "C")])


class TestRecord(Base):
    def test_conflict_newest_wins(self):
        """Test 3: page A then chat B logs B with changed_from A."""
        dp.record("sample", "Q-1", "A", source="page", words="", answered_at="2026-11-16T20:00:00Z", use_tw=False)
        res = dp.record("sample", "Q-1", "B", source="chat", words="2. B", answered_at="2026-11-16T21:00:00Z", use_tw=False)
        self.assertEqual(res["row"]["changed_from"], "A")
        rows = dp.read_log()
        self.assertEqual([r["choice_key"] for r in rows], ["A", "B"])
        d = dp.find(dp.load_pack("sample"), "Q-1")
        self.assertEqual(d["outcome"]["choice_key"], "B")
        self.assertEqual(d["status"], "decided")
        self.assertTrue(d["mirrors"]["log"])
        self.assertFalse(d["mirrors"]["page"])
        self.assertTrue(Path(res["page_update"]).exists())

    def test_agent_source_rejected(self):
        """Test 4."""
        r = subprocess.run([sys.executable, str(HERE / "dp.py"), "record", "sample", "Q-1", "--choice", "A",
                            "--source", "agent", "--no-tw"], capture_output=True, text=True, env=dict(os.environ))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refused", r.stderr)
        self.assertEqual(dp.read_log(), [])

    def test_standing_rule_goes_to_ledger(self):
        dp.record("sample", "Q-2", "A", source="chat", words="only Pro", standing="Tiers: Pro only",
                  answered_at="2026-11-16T21:00:00Z", use_tw=False)
        text = Path(os.environ["DP_LEDGER"]).read_text()
        self.assertIn('- Tiers: Pro only — "only Pro" (Nov. 16, 2026)', text)

    def test_material_and_stale(self):
        dp.record("sample", "Q-2", None, kind="material", source="page", use_tw=False)
        self.assertEqual(dp.find(dp.load_pack("sample"), "Q-2")["status"], "needs_material")
        pack = dp.load_pack("sample")
        dp.find(pack, "Q-1")["version"] = 2
        dp.save_pack(pack)
        res = dp.record("sample", "Q-1", "A", source="page", decision_version=1, use_tw=False)
        self.assertTrue(res["stale"])
        self.assertEqual(dp.find(dp.load_pack("sample"), "Q-1")["status"], "open")

    def test_tw_close_called(self):
        log = Path(self.tmp) / "tw.log"
        fake = Path(self.tmp) / "tw"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n")
        fake.chmod(0o755)
        os.environ["DP_TW_BIN"] = str(fake)
        pack = dp.load_pack("sample")
        dp.find(pack, "Q-1")["todo_watch"] = {"task": "#9", "ledger": "L77"}
        dp.save_pack(pack)
        dp.record("sample", "Q-1", "A", source="page", words="go with A")
        self.assertIn("task done L77 --reason already_done --evidence go with A", log.read_text())
        self.assertTrue(dp.find(dp.load_pack("sample"), "Q-1")["mirrors"]["todo_watch"])


class TestTime(Base):
    def test_local(self):
        """Test 5: times show in the configured zone with that zone's own abbreviation."""
        self.assertEqual(dp.fmt_local("2026-11-18T23:00:00Z"), "Wed Nov 18 6:00 PM EST")
        self.assertEqual(dp.fmt_local("2026-07-01T14:05:00Z"), "Wed Jul 1 10:05 AM EDT")
        os.environ["DP_TIMEZONE"] = "Europe/Berlin"
        self.assertEqual(dp.fmt_local("2026-11-18T23:00:00Z"), "Thu Nov 19 12:00 AM CET")
        self.assertEqual(dp.date_local("2026-11-18T23:00:00Z"), "Nov 19, 2026")

    def test_meta_carries_zone(self):
        self.assertEqual(dp.public_meta(self.pack)["timezone"], "America/New_York")

    def test_default_zone_is_system_local(self):
        os.environ.pop("DP_TIMEZONE")
        os.environ["TZ"] = "Asia/Tokyo"
        try:
            self.assertEqual(dp.fmt_local("2026-11-18T23:00:00Z"), "Thu Nov 19 8:00 AM JST")
        finally:
            os.environ.pop("TZ")

    def test_bad_zone_exits(self):
        os.environ["DP_TIMEZONE"] = "Mars/Olympus"
        with self.assertRaises(SystemExit):
            dp.local_zone()


class TestConfig(unittest.TestCase):
    """Settings resolve env first, then the config file, then built-in defaults."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dp-cfg-"))
        self.old = {k: os.environ.pop(k, None) for k in ENV_KEYS + ("TZ",)}
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        self.home = self.tmp / "home"
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        restore(self.old)
        os.environ["HOME"] = self.old_home

    def write_config(self, data):
        p = self.tmp / "xdg" / "decision-pack" / "config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        return p

    def test_defaults(self):
        self.assertEqual(dp.config_path(), self.tmp / "xdg" / "decision-pack" / "config.json")
        self.assertEqual(dp.root(), self.home / ".decision-pack")
        self.assertIsNone(dp.ledger_path())
        self.assertIsNone(dp.memory_dir())
        self.assertIsNone(dp.transcripts_dir())
        self.assertIsNone(dp.tw_bin())

    def test_xdg_unset_uses_home_config(self):
        os.environ.pop("XDG_CONFIG_HOME")
        self.assertEqual(dp.config_path(), self.home / ".config" / "decision-pack" / "config.json")

    def test_config_file(self):
        self.write_config({"home": "~/packs", "timezone": "Europe/Berlin", "ledger_path": "~/notes/ledger.md",
                           "memory_dir": str(self.tmp / "mem"), "transcripts_dir": str(self.tmp / "tr"),
                           "todo_watch_bin": "/opt/tw"})
        self.assertEqual(dp.root(), self.home / "packs")
        self.assertEqual(dp.ledger_path(), self.home / "notes" / "ledger.md")
        self.assertEqual(dp.memory_dir(), self.tmp / "mem")
        self.assertEqual(dp.transcripts_dir(), self.tmp / "tr")
        self.assertEqual(dp.tw_bin(), "/opt/tw")
        self.assertEqual(dp.zone_name(), "Europe/Berlin")

    def test_env_beats_config(self):
        self.write_config({"home": str(self.tmp / "from-config"), "timezone": "Europe/Berlin"})
        os.environ["DECISION_PACK_HOME"] = str(self.tmp / "from-env")
        os.environ["DP_TIMEZONE"] = "Asia/Tokyo"
        self.assertEqual(dp.root(), self.tmp / "from-env")
        self.assertEqual(dp.zone_name(), "Asia/Tokyo")

    def test_bad_config_exits(self):
        p = self.tmp / "xdg" / "decision-pack" / "config.json"
        p.parent.mkdir(parents=True)
        p.write_text("{not json")
        with self.assertRaises(SystemExit):
            dp.root()

    def test_optional_integrations_noop(self):
        """No ledger, memory, transcripts or Todo Watch configured: check and record still work."""
        os.environ["DECISION_PACK_HOME"] = str(self.tmp / "packs")
        os.environ["DP_TIMEZONE"] = "America/New_York"
        res = dp.check("reminder default date")
        self.assertEqual(res["searched"], [str(self.tmp / "packs" / "decisions.jsonl")])
        self.assertEqual(res["hits"], [])
        pack = sample()
        dp.find(pack, "Q-1")["todo_watch"] = {"task": "#9", "ledger": "L77"}
        dp.save_pack(pack)
        dp.record("sample", "Q-1", "A", source="page", words="go", standing="A rule")
        d = dp.find(dp.load_pack("sample"), "Q-1")
        self.assertTrue(d["mirrors"]["todo_watch"])
        self.assertEqual(d["status"], "decided")

    def test_legacy_ct_keys_migrate(self):
        os.environ["DECISION_PACK_HOME"] = str(self.tmp / "packs")
        pack = sample()
        d = dp.find(pack, "Q-1")
        d["default_at_ct"] = d.pop("default_at_local")
        d["history_check"]["quote_date_ct"] = d["history_check"].pop("quote_date_local")
        dp.save_pack(pack)
        dp.log_path().write_text(json.dumps({"id": "Q-9", "answered_at_ct": "Mon Nov 9 2:00 PM EST"}) + "\n")
        d2 = dp.find(dp.load_pack("sample"), "Q-1")
        self.assertEqual(d2["default_at_local"], "Wed Nov 18 6:00 PM EST")
        self.assertNotIn("default_at_ct", d2)
        self.assertIn("quote_date_local", d2["history_check"])
        self.assertEqual(dp.read_log()[0]["answered_at_local"], "Mon Nov 9 2:00 PM EST")


class TestDue(Base):
    def test_defaults(self):
        """Test 6: a two-way default past due applies; one-way or freeze never does."""
        pack = dp.load_pack("sample")
        d3 = dp.find(pack, "Q-3")
        d3.update(default_key="A", default_at="2026-11-18T23:00:00Z", default_at_local="Wed Nov 18 6:00 PM EST")
        d2 = dp.find(pack, "Q-2")
        d2.update(gate="freeze", default_key="A", default_at="2026-11-18T23:00:00Z", default_at_local="Wed Nov 18 6:00 PM EST")
        dp.save_pack(pack)
        os.environ["DP_NOW"] = "2026-11-19T00:00:00Z"
        res = dp.due(dp.load_pack("sample"), use_tw=False, responses=[])  # page read, no answers
        self.assertEqual(res["applied"], ["Q-1"])
        after = dp.load_pack("sample")
        self.assertEqual(dp.find(after, "Q-1")["status"], "defaulted")
        self.assertEqual(dp.find(after, "Q-1")["outcome"]["by"], "default")
        self.assertEqual(dp.find(after, "Q-2")["status"], "open")
        self.assertEqual(dp.find(after, "Q-3")["status"], "open")
        self.assertEqual(dp.read_log()[-1]["source"], "default")

    def test_not_due_yet(self):
        os.environ["DP_NOW"] = "2026-11-18T22:00:00Z"
        self.assertEqual(dp.due(dp.load_pack("sample"), use_tw=False, responses=[])["applied"], [])


class TestSeed(Base):
    def test_split(self):
        """Test 7: 120 writes split into 3 batches, each <= 50 writes and under 1 MiB."""
        writes = [{"op": "set", "collection": "decisions", "doc_id": f"X-{i}", "data": {"pad": "y" * 6000}} for i in range(120)]
        batches = dp.split_batches(writes)
        self.assertEqual(len(batches), 3)
        for b in batches:
            self.assertLessEqual(len(b), 50)
            self.assertLess(len(json.dumps({"writes": b}).encode()), 1024 * 1024)

    def test_split_by_bytes(self):
        writes = [{"op": "set", "collection": "d", "doc_id": f"X-{i}", "data": {"pad": "y" * 200_000}} for i in range(12)]
        for b in dp.split_batches(writes):
            self.assertLess(len(json.dumps({"writes": b}).encode()), 1024 * 1024)

    def test_seed_files(self):
        files = dp.seed(self.pack, versions={"meta/pack": 2, "decisions/Q-1": 4})
        self.assertEqual(len(files), 1)
        writes = json.loads(Path(files[0]).read_text())["writes"]
        by = {w["doc_id"]: w for w in writes}
        self.assertEqual(by["Q-1"]["op"], "update")
        self.assertEqual(by["Q-1"]["if_version"], 4)
        self.assertEqual(by["Q-2"]["op"], "set")
        doc = json.loads(Path(by["Q-1"]["file_path"]).read_text())
        self.assertEqual(doc["options"][0]["shots"][0]["src"], "shots/ui-a-1440.png")
        self.assertNotIn(str(FIX), json.dumps(doc))


class TestTodoWatch(Base):
    def test_description(self):
        """Test 8: the real Todo Watch parser reads the options and the pick."""
        text = dp.tw_description(self.pack, dp.find(self.pack, "Q-2"))
        self.assertIn("A) Pro only (recommended)", text)
        if not (TW_DETAIL and (TW_DETAIL / "detail.py").exists()):
            self.skipTest("DP_TW_PARSER_DIR not set")
        sys.path.insert(0, str(TW_DETAIL))
        try:
            import detail as DT
        except Exception as e:  # pragma: no cover
            self.skipTest(f"Todo Watch parser not importable: {e}")
        out = DT.decision_field({"lane": "need", "kind": "task", "subject": "Q-2", "description": text})
        self.assertEqual(len(out["opts"]), 3)
        self.assertEqual(out["rec_i"], 0)
        self.assertEqual(out["q"], "New tiers on Starter and Pro?")


class TestCheck(unittest.TestCase):
    def test_memory_and_ledger(self):
        """Test 9: finds a rule in the memory folder and the ledger, with the user's words and a date."""
        tmp = Path(tempfile.mkdtemp(prefix="dp-test-"))
        old = isolate(tmp)
        try:
            mem = tmp / "memory"
            mem.mkdir()
            (mem / "beta-users-see-new-ui.md").write_text(
                "---\nmodified: 2026-11-02T15:00:00Z\n---\nNever exclude beta users from experiments: \"beta users see "
                "the treatment too\".\n")
            (mem / "unrelated.md").write_text("Prefer short titles.\n")
            Path(os.environ["DP_LEDGER"]).write_text('- Beta users stay in experiments — "never exclude beta users" (Nov. 4, 2026)\n')
            res = dp.check("exclude beta users from experiments", transcripts=False)
        finally:
            restore(old)
            shutil.rmtree(tmp, ignore_errors=True)
        mem_hits = [h for h in res["hits"] if h["source"] == "memory"]
        self.assertEqual(len(mem_hits), 1, res["hits"])
        self.assertIn("beta users see the treatment", mem_hits[0]["words"])
        self.assertEqual(mem_hits[0]["date_local"], "Nov 2, 2026")
        led = [h for h in res["hits"] if h["source"] == "ledger"]
        self.assertEqual(led[0]["words"], "never exclude beta users")
        self.assertEqual(led[0]["date_local"], "Nov 4, 2026")

    def test_transcripts_user_turns_only(self):
        tmp = Path(tempfile.mkdtemp(prefix="dp-test-"))
        old = isolate(tmp)
        try:
            os.environ["DP_TRANSCRIPTS_DIR"] = str(tmp)
            now = dp.iso(dp.now_utc())
            rows = [
                {"type": "user", "message": {"role": "user", "content": "never put a default date on page fix cards"}, "timestamp": now, "sessionId": "s1"},
                {"type": "assistant", "message": {"role": "assistant", "content": "default date page fix cards"}, "timestamp": now},
                {"type": "user", "isMeta": True, "message": {"role": "user", "content": "default date page fix cards meta"}, "timestamp": now},
            ]
            (tmp / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            res = dp.check("page fix default date")
            t = [h for h in res["hits"] if h["source"] == "transcript"]
            self.assertEqual(len(t), 1)
            self.assertIn("never put a default date", t[0]["words"])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(tmp, ignore_errors=True)


class TestSyncPlan(Base):
    def test_new_responses_once(self):
        """The core of runtime test 16, offline: a response maps once; a second sync does nothing."""
        resp = Path(self.tmp) / "responses.json"
        resp.write_text(json.dumps([{"id": "Q-1", "version": 3, "data": {
            "decision_id": "Q-1", "decision_version": 1, "kind": "pick", "choice_key": "B", "choice_label": "No date",
            "note": "no date for now", "regions": [{"shot_role": "option-A", "width": 1440, "callout_n": 3,
                                                    "box_px": [80, 480, 1280, 200], "anchor": "Q-1:option-A-1440#c3"}],
            "asset_ids": [], "source": "page", "answered_at": "2026-11-16T21:12:04Z"}}]))
        out = io.StringIO()
        with redirect_stdout(out):
            dp.main(["sync-plan", "sample", "--responses", str(resp), "--apply", "--no-tw"])
        got = json.loads(out.getvalue())
        self.assertEqual([r["id"] for r in got["recorded"]], ["Q-1"])
        row = dp.read_log()[-1]
        self.assertEqual(row["regions"][0]["callout_n"], 3)
        self.assertEqual(row["words"], "no date for now")
        out2 = io.StringIO()
        with redirect_stdout(out2):
            dp.main(["sync-plan", "sample", "--responses", str(resp), "--apply", "--no-tw"])
        self.assertEqual(json.loads(out2.getvalue())["recorded"], [])
        self.assertEqual(len(dp.read_log()), 1)


class TestReviewFixes(Base):
    """Regression tests: due vs sync, seed meta pin, page answers to the ledger, hooks, quote box."""

    def resp_file(self, did, key, at, kind="pick"):
        f = Path(self.tmp) / f"resp-{did}-{key}.json"
        f.write_text(json.dumps([{"id": did, "data": {"decision_id": did, "decision_version": 1, "kind": kind,
                                                     "choice_key": key, "choice_label": key, "note": "", "regions": [],
                                                     "asset_ids": [], "source": "page", "answered_at": at}}]))
        return f

    def test_page_answer_before_default_wins(self):
        """The user picks B at 3:00 PM EST, the default (A) was due 6:00 PM EST, due runs at 7:30 PM EST."""
        resp = self.resp_file("Q-1", "B", "2026-11-18T20:00:00Z")
        os.environ["DP_NOW"] = "2026-11-19T00:30:00Z"
        # due on its own (no page read) never applies a default
        r = dp.due(dp.load_pack("sample"), use_tw=False)
        self.assertEqual(r["applied"], [])
        self.assertEqual(r["past_due"], ["Q-1"])
        self.assertIn("Not applied", r["lines"][0])
        self.assertEqual(dp.find(dp.load_pack("sample"), "Q-1")["status"], "open")
        # due with the page read records the answer first; no default
        r = dp.due(dp.load_pack("sample"), use_tw=False, responses=dp.normalize_responses(str(resp)))
        self.assertEqual(r["synced"], ["Q-1"])
        self.assertEqual(r["applied"], [])
        d = dp.find(dp.load_pack("sample"), "Q-1")
        self.assertEqual((d["status"], d["outcome"]["choice_key"], d["outcome"]["by"]), ("decided", "B", "user"))

    def test_default_then_sync_his_earlier_answer_wins(self):
        """Even if a default was applied first (old flow), the earlier page answer is still new and wins."""
        os.environ["DP_NOW"] = "2026-11-19T00:30:00Z"
        dp.due(dp.load_pack("sample"), use_tw=False, responses=[])
        d = dp.find(dp.load_pack("sample"), "Q-1")
        self.assertEqual((d["status"], d["outcome"]["by"]), ("defaulted", "default"))
        self.assertIsNone(d.get("synced_answer_at"))
        resp = self.resp_file("Q-1", "B", "2026-11-18T20:00:00Z")
        new = dp.sync_plan(dp.load_pack("sample"), dp.normalize_responses(str(resp)))
        self.assertEqual([r["choice_key"] for r in new], ["B"])
        out = io.StringIO()
        with redirect_stdout(out):
            dp.main(["sync-plan", "sample", "--responses", str(resp), "--apply", "--no-tw"])
        d = dp.find(dp.load_pack("sample"), "Q-1")
        self.assertEqual((d["status"], d["outcome"]["choice_key"], d["outcome"]["by"]), ("decided", "B", "user"))
        self.assertEqual(dp.read_log()[-1]["changed_from"], "A")
        self.assertEqual(dp.sync_plan(dp.load_pack("sample"), dp.normalize_responses(str(resp))), [])

    def test_cli_due_without_responses_does_not_apply(self):
        os.environ["DP_NOW"] = "2026-11-19T00:30:00Z"
        out = io.StringIO()
        with redirect_stdout(out):
            dp.main(["due", "sample", "--no-tw"])
        self.assertIn("Run sync first", out.getvalue())
        self.assertEqual(dp.read_log(), [])

    def test_seed_versions_need_meta_pin(self):
        pack = dp.load_pack("sample")
        with self.assertRaises(dp.SeedError):
            dp.seed(pack, {"decisions/Q-1": 3})
        files = dp.seed(pack, {"decisions/Q-1": 3}, no_meta=True)
        writes = [w for f in files for w in json.loads(Path(f).read_text())["writes"]]
        self.assertNotIn("meta", {w["collection"] for w in writes})
        files = dp.seed(pack, {"meta/pack": 2, "decisions/Q-1": 3})
        writes = {f"{w['collection']}/{w['doc_id']}": w for f in files for w in json.loads(Path(f).read_text())["writes"]}
        self.assertEqual((writes["meta/pack"]["op"], writes["meta/pack"]["if_version"]), ("update", 2))
        self.assertEqual(writes["decisions/Q-1"]["if_version"], 3)
        # no versions at all: a first seed, every doc is a set
        files = dp.seed(pack)
        self.assertTrue(all(w["op"] == "set" for f in files for w in json.loads(Path(f).read_text())["writes"]))

    def test_page_answer_on_standing_card_goes_to_ledger(self):
        pack = dp.load_pack("sample")
        dp.find(pack, "Q-1")["standing_rule"] = "Reminders from customer answers"
        dp.save_pack(pack)
        resp = self.resp_file("Q-1", "A", "2026-11-16T21:00:00Z")
        with redirect_stdout(io.StringIO()):
            dp.main(["sync-plan", "sample", "--responses", str(resp), "--apply", "--no-tw"])
        lines = [ln for ln in Path(os.environ["DP_LEDGER"]).read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("- Reminders from customer answers: ", lines[0])
        self.assertIn("(Nov. 16, 2026)", lines[0])
        self.assertTrue(dp.read_log()[-1]["standing"])
        # a note-only answer on a standing card does not write a ledger line
        resp2 = self.resp_file("Q-1", None, "2026-11-16T22:00:00Z", kind="something_else")
        with redirect_stdout(io.StringIO()):
            dp.main(["sync-plan", "sample", "--responses", str(resp2), "--apply", "--no-tw"])
        self.assertEqual(len([ln for ln in Path(os.environ["DP_LEDGER"]).read_text().splitlines() if ln.strip()]), 1)

    def test_quote_needs_choice_key_and_new_fact(self):
        p = copy.deepcopy(self.pack)
        d = dp.find(p, "Q-2")
        d["history_check"].pop("quote_choice_key")
        self.assertTrue(any("quote_choice_key" in e for e in self.lint_errors(p)))
        d["history_check"]["quote_choice_key"] = "B"
        self.assertTrue(any("new_fact" in e for e in self.lint_errors(p)))
        d["history_check"]["new_fact"] = "In tests about half the fixes were wrong."
        self.assertEqual(self.lint_errors(p), [])
        self.assertEqual(dp.public_decision(d)["history_check"]["quote_choice_key"], "B")

    def test_stop_hook(self):
        self.assertTrue(dp.asks_in_chat("Built it.\n\nWant me to deploy it now?"))
        self.assertTrue(dp.asks_in_chat("Done. Should I merge the PR"))
        self.assertFalse(dp.asks_in_chat("Done. 2 decisions for you: https://claude.ai/artifact/abc ?"))
        self.assertFalse(dp.asks_in_chat("Deployed and verified in prod."))
        res = dp.hook_stop({"last_assistant_message": "Which one do you prefer, A or B?"})
        self.assertEqual(res["decision"], "block")
        self.assertIsNone(dp.hook_stop({"last_assistant_message": "Which one?", "stop_hook_active": True}))
        tp = Path(self.tmp) / "t.jsonl"
        tp.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Ok to ship?"}]}}) + "\n")
        self.assertEqual(dp.hook_stop({"transcript_path": str(tp)})["decision"], "block")
        r = subprocess.run([sys.executable, str(HERE / "dp.py"), "hook", "stop"], input=json.dumps({"last_assistant_message": "All done."}),
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_stop_hook_off_switch(self):
        ask = {"last_assistant_message": "Which one do you prefer, A or B?"}
        os.environ["DP_STOP_HOOK"] = "off"
        try:
            self.assertIsNone(dp.hook_stop(ask))
        finally:
            os.environ.pop("DP_STOP_HOOK")
        cfg = dp.config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"stop_hook": "off"}))
        try:
            self.assertIsNone(dp.hook_stop(ask))
        finally:
            cfg.unlink()
        self.assertEqual(dp.hook_stop(ask)["decision"], "block")

    def test_session_start_hook(self):
        self.assertEqual(dp.hook_session_start(), "")
        pack = dp.load_pack("sample")
        pack["artifact_url"] = "https://claude.ai/artifact/xyz"
        dp.save_pack(pack)
        os.environ["DP_NOW"] = "2026-11-19T00:30:00Z"
        out = dp.hook_session_start()
        self.assertIn("decision-pack sync-plan sample", out)
        self.assertIn("past due: Q-1", out)
        self.assertEqual(dp.read_log(), [])  # never applies anything

    def test_sync_plan_steps_arm_watch(self):
        out = io.StringIO()
        with redirect_stdout(out):
            dp.main(["sync-plan", "sample"])
        self.assertIn("0. ArtifactComments watch", out.getvalue())
        self.assertIn("never run due on its own", out.getvalue())


class TestImages(Base):
    def test_mark_crop_shrink(self):
        src = Path(self.tmp) / "in.png"
        shutil.copy(FIX / "ui-a-1440.png", src)
        cj = Path(self.tmp) / "c.json"
        cj.write_text(json.dumps([{"n": 3, "box": [80, 120, 600, 300], "note": "The card"}]))
        out = Path(self.tmp) / "out.png"
        dp.main(["mark", str(src), str(cj), str(out)])
        self.assertTrue(out.exists())
        meta = json.loads((Path(self.tmp) / "out.callouts.json").read_text())
        self.assertEqual(meta["px"], [1440, 900])
        dp.main(["crop", str(out), str(cj)])
        self.assertTrue((Path(self.tmp) / "crops" / "out-c3.png").exists())
        size, px, path = dp.shrink(str(out))
        self.assertLess(size, dp.MAX_SHOT)
        self.assertEqual(path, str(out))  # flat UI stays a 256-color PNG

    def gradient(self, path, w=720, h=1200):
        """A dark two-axis gradient, the hero art a 256-color palette bands."""
        from PIL import Image, ImageChops
        g = Image.linear_gradient("L")
        x, y = g.rotate(90).resize((w, h)), g.resize((w, h))
        Image.merge("RGB", (x, y, ImageChops.multiply(x, y))).save(path)
        return path

    def test_shrink_photo_to_jpeg(self):
        src = self.gradient(Path(self.tmp) / "hero.png")
        size, px, path = dp.shrink(str(src), max_kb=100)
        self.assertTrue(path.endswith("hero.jpg"))
        self.assertFalse(src.exists())
        self.assertLessEqual(size, 100 * 1024)
        from PIL import Image
        with Image.open(path) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertTrue(im.info.get("progressive"))
            self.assertEqual(list(im.size), px)

    def test_shrink_scales_to_fit_and_updates_pack(self):
        src = self.gradient(Path(self.tmp) / "big.png")
        pack = sample()
        pack["project"] = "imgs"
        shot = dp.all_shots(pack["decisions"][0])[0]
        shot.update(path=str(src), px=[720, 1200], callouts=[{"n": 1, "box": [100, 200, 300, 400], "note": "x"}])
        dp.save_pack(pack)
        with redirect_stdout(io.StringIO()) as out:
            dp.main(["shrink", str(src), "--max-kb", "15"])
        self.assertIn("pack imgs: shots updated", out.getvalue())
        s2 = dp.all_shots(dp.load_pack("imgs")["decisions"][0])[0]
        self.assertTrue(s2["path"].endswith("big.jpg"))
        self.assertLessEqual(os.path.getsize(s2["path"]), 15 * 1024)
        self.assertLess(s2["px"][0], 720)
        k = s2["px"][0] / 720
        self.assertEqual(s2["callouts"][0]["box"], [round(v * k) for v in (100, 200, 300, 400)])

    def test_shrink_keeps_an_existing_jpeg(self):
        src = self.gradient(Path(self.tmp) / "hero.png")
        other = Path(self.tmp) / "hero.jpg"
        other.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            dp.shrink(str(src))
        self.assertEqual(other.read_bytes(), b"keep me")
        self.assertTrue(src.exists())

if __name__ == "__main__":
    unittest.main()
