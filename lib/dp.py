#!/usr/bin/env python3
"""decision-pack: build, lint, seed and read back decision packs.

One pack per project at <home>/<project>/pack.json (home: $DECISION_PACK_HOME, the config file, or
~/.decision-pack; see config() below). The page (assets/pack.html) reads the
pack from the artifact's db; this script writes the files the session hands to the Artifact,
ArtifactData and ArtifactComments tools, and keeps the answer log (<home>/decisions.jsonl).

Run `decision-pack <command> --help` for each command. SPEC.md is the source of truth.
"""
import argparse
import datetime as dt
import glob
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import schema  # noqa: E402

PLUGIN = HERE.parent
TEMPLATE = PLUGIN / "assets" / "pack.html"
VENDOR = HERE / "vendor"
TEMPLATE_VERSION = "0.1.0"
USER_SOURCES = ("page", "todo-watch", "chat", "comment", "default")
UI_KINDS = ("real_ui", "mock", "pair")
UI_GATES = ("checkout", "pricing", "new_visual", "sign_in")
MAX_SHOT = 600 * 1024
BATCH_WRITES = 50
BATCH_BYTES = 1024 * 1024 - 16 * 1024  # leave room for the envelope
OK_WORDS = {"ok", "okay", "k", "yes", "yep", "yeah", "sure", "nice", "looks good", "lgtm", "ship it",
            "go", "go ahead", "do it", "sounds good", "all good", "approved", "take all my picks"}
NO_WORDS = {"no", "nope", "nah", "don't", "dont"}
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "from", "with", "is", "it", "be", "at",
        "this", "that", "we", "i", "you", "my", "our", "do", "does", "should", "would", "can", "now", "later"}


# ---------- paths and io ----------

# ---------- config ----------
#
# Every setting resolves in this order: environment variable, config file, built-in default.
# Config file: ${XDG_CONFIG_HOME:-~/.config}/decision-pack/config.json, for example
#   {"home": "~/decision-packs", "timezone": "Europe/Berlin",
#    "ledger_path": "~/notes/decisions-ledger.md", "memory_dir": "~/notes/memory",
#    "transcripts_dir": "~/.claude/projects/<project-folder>", "todo_watch_bin": "/usr/local/bin/tw"}
# Optional integrations (ledger, memory, transcripts, Todo Watch) are off when unset.

def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "decision-pack" / "config.json"


def config() -> dict:
    try:
        data = json.loads(config_path().read_text())
    except FileNotFoundError:
        return {}
    except ValueError as e:
        die(f"bad JSON in {config_path()}: {e}")
    return data if isinstance(data, dict) else {}


def setting(env, key, default=None):
    v = os.environ.get(env) or config().get(key) or default
    return v if v not in ("", None) else None


def opt_path(env, key):
    v = setting(env, key)
    return Path(os.path.expandvars(str(v))).expanduser() if v else None


# ---------- paths and io ----------

def root() -> Path:
    return opt_path("DECISION_PACK_HOME", "home") or Path.home() / ".decision-pack"


def pack_dir(project: str) -> Path:
    return root() / project


def pack_path(project: str) -> Path:
    return pack_dir(project) / "pack.json"


def log_path() -> Path:
    return root() / "decisions.jsonl"


def registry_path() -> Path:
    return root() / "registry.json"


def memory_dir():
    """Folder of markdown notes `check` searches; None when unset."""
    return opt_path("DP_MEMORY_DIR", "memory_dir")


def ledger_path():
    """Standing-rules file `check` searches and answers append to; None when unset."""
    return opt_path("DP_LEDGER", "ledger_path")


def transcripts_dir():
    """Claude Code transcript folder (*.jsonl) `check` searches; None when unset."""
    return opt_path("DP_TRANSCRIPTS_DIR", "transcripts_dir")


def migrate(obj):
    """Rename legacy `*_ct` time-label keys to `*_local` in place (older packs and log rows)."""
    if isinstance(obj, dict):
        for k in [k for k in obj if k.endswith("_ct")]:
            v = obj.pop(k)
            obj.setdefault(k[:-3] + "_local", v)
        for v in obj.values():
            migrate(v)
    elif isinstance(obj, list):
        for v in obj:
            migrate(v)
    return obj


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except FileNotFoundError:
        return default


def write_json(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(p)


def load_pack(project):
    pack = migrate(read_json(pack_path(project)))
    if pack is None:
        die(f"no pack at {pack_path(project)} (run: decision-pack init {project} --title '...')")
    return pack


def save_pack(pack):
    write_json(pack_path(pack["project"]), pack)


def read_log():
    rows = []
    try:
        for line in log_path().read_text().splitlines():
            if line.strip():
                try:
                    rows.append(migrate(json.loads(line)))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return rows


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


# ---------- time ----------

def local_zone():
    """The configured IANA zone (config `timezone` or $DP_TIMEZONE), else the system's local zone."""
    from zoneinfo import ZoneInfo
    name = setting("DP_TIMEZONE", "timezone")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            die(f"unknown timezone {name!r} (use an IANA name such as America/New_York)")
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        try:
            link = os.path.realpath("/etc/localtime")
            name = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else ""
        except OSError:
            name = ""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.datetime.now().astimezone().tzinfo


def zone_name():
    z = local_zone()
    return getattr(z, "key", None)


def now_utc():
    v = os.environ.get("DP_NOW")
    return parse_iso(v) if v else dt.datetime.now(dt.timezone.utc)


def parse_iso(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def iso(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local(value):
    d = parse_iso(value) if isinstance(value, str) else value
    return d.astimezone(local_zone())


def clock(d):
    """'5:00 PM CST' for an aware local datetime."""
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'} {d.tzname()}"


def fmt_local(value):
    """'2026-11-18T23:00:00Z' -> 'Wed Nov 18 5:00 PM CST' in the configured zone."""
    d = _local(value)
    return f"{d:%a %b} {d.day} {clock(d)}"


def date_local(value):
    d = _local(value)
    return f"{d:%b} {d.day}, {d.year}"


# ---------- pack helpers ----------

def decisions(pack):
    return sorted(pack.get("decisions") or [], key=lambda d: (d.get("order", 0), d.get("id", "")))


def find(pack, did):
    for d in pack.get("decisions") or []:
        if d.get("id") == did:
            return d
    die(f"no decision {did} in pack {pack.get('project')}")


def option(d, key):
    for o in d.get("options") or []:
        if o.get("key") == key:
            return o
    return None


def recommended(d):
    rec = [o for o in d.get("options") or [] if o.get("recommended")]
    return rec[0] if len(rec) == 1 else None


def all_shots(d):
    out = list(d.get("shots") or [])
    for o in d.get("options") or []:
        out += o.get("shots") or []
    return out


def pack_shots(pack):
    out = []
    for d in pack.get("decisions") or []:
        out += [(d.get("id"), s) for s in all_shots(d)]
    for f in pack.get("fyi") or []:
        if f.get("shot"):
            out.append(("fyi", f["shot"]))
    return out


def defaultable(d):
    return d.get("door") == "two_way" and (d.get("gate") or "none") == "none"


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split()


# ---------- check (history search) ----------

def stems(topic):
    out = []
    for w in norm(topic):
        if w in STOP or len(w) < 3:
            continue
        out.append(w[:5] if len(w) > 5 else w)
    return list(dict.fromkeys(out))


def line_score(text, st):
    low = text.lower()
    hit = sum(1 for s in st if re.search(r"\b" + re.escape(s), low))
    return hit


def needed(st):
    return len(st) if len(st) <= 3 else math.ceil(0.75 * len(st))


DATE_RES = [
    (re.compile(r"\((\d{4}-\d{2}-\d{2})\)"), "%Y-%m-%d"),
    (re.compile(r"\(([A-Z][a-z]{2})\.? (\d{1,2}), (\d{4})\)"), None),
]


def ledger_date(line):
    m = DATE_RES[0][0].search(line)
    if m:
        return m.group(1)
    m = DATE_RES[1][0].search(line)
    if m:
        return f"{m.group(1)} {m.group(2)}, {m.group(3)}"
    return None


def snippet(text, st, width=240):
    low = text.lower()
    pos = min([low.find(s) for s in st if low.find(s) >= 0] or [0])
    start = max(0, pos - 60)
    s = " ".join(text[start:start + width].split())
    return ("..." if start else "") + s + ("..." if start + width < len(text) else "")


def quotes_in(text):
    return [q for q in re.findall(r"[\"“]([^\"”]{8,240})[\"”]", text)]


def search_log(st):
    hits = []
    for r in read_log():
        text = " ".join(str(r.get(k) or "") for k in ("question", "choice_label", "words"))
        if line_score(text, st) >= needed(st):
            hits.append({"source": "decisions.jsonl", "path": str(log_path()), "date_local": r.get("answered_at_local"),
                         "words": r.get("words") or r.get("choice_label"), "text": r.get("question"),
                         "id": r.get("id"), "project": r.get("project"), "choice_key": r.get("choice_key")})
    return hits


def search_ledger(st):
    hits = []
    if not ledger_path():
        return hits
    try:
        lines = ledger_path().read_text().splitlines()
    except FileNotFoundError:
        return hits
    for ln in lines:
        if ln.startswith("- ") and line_score(ln, st) >= needed(st):
            q = quotes_in(ln)
            hits.append({"source": "ledger", "path": str(ledger_path()), "date_local": ledger_date(ln),
                         "words": " / ".join(q) if q else None, "text": snippet(ln[2:], st)})
    return hits


def search_memory(st):
    hits = []
    md = memory_dir()
    if not md:
        return hits
    lp = ledger_path()
    skip = {"MEMORY.md"} | ({lp.name} if lp else set())
    for p in sorted(md.glob("*.md")):
        if p.name in skip:
            continue
        try:
            text = p.read_text()
        except OSError:
            continue
        m = re.search(r"modified:\s*(\S+)", text)
        when = None
        if m:
            try:
                when = date_local(m.group(1))
            except ValueError:
                when = None
        for ln in text.splitlines():
            if line_score(ln, st) >= needed(st):
                q = quotes_in(text)
                hits.append({"source": "memory", "path": str(p), "date_local": when,
                             "words": " / ".join(q[:2]) if q else None, "text": snippet(ln, st)})
                break
    return hits


def user_text(obj):
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain") or obj.get("isCompactSummary"):
        return None
    msg = obj.get("message") or {}
    if msg.get("role") != "user":
        return None
    c = msg.get("content")
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return None
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    if not isinstance(c, str):
        return None
    c = c.strip()
    if not c or c.startswith("<") or c.startswith("This session is being continued") or c.startswith("["):
        return None
    if "<teammate-message" in c or "<task-notification" in c or "sent a message:" in c[:200]:
        return None
    return c


USER_LINE = re.compile(r'"type":\s*"user"')


def search_transcripts(st, days):
    hits = []
    tdir = transcripts_dir()
    if not tdir:
        return hits
    since = now_utc() - dt.timedelta(days=days)
    files = [p for p in tdir.glob("*.jsonl") if dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc) >= since]
    if not files or not st:
        return hits
    rare = max(st, key=len)
    lines = []
    rg = shutil.which("rg")
    if rg:
        r = subprocess.run([rg, "-i", "-N", "--no-filename", "-F", rare] + [str(f) for f in files],
                           capture_output=True, text=True, errors="replace")
        lines = [ln for ln in r.stdout.splitlines() if USER_LINE.search(ln)]
    else:
        for f in files:
            with open(f, errors="replace") as fh:
                lines += [ln for ln in fh if rare in ln.lower() and USER_LINE.search(ln)]
    seen = set()
    for ln in lines:
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        text = user_text(obj)
        if not text or line_score(text, st) < needed(st):
            continue
        ts = parse_iso(obj.get("timestamp")) if obj.get("timestamp") else None
        if ts and ts < since:
            continue
        key = text[:120]
        if key in seen:
            continue
        seen.add(key)
        hits.append({"source": "transcript", "path": str(tdir / ((obj.get("sessionId") or "?") + ".jsonl")),
                     "date_local": fmt_local(ts) if ts else None, "words": snippet(text, st), "text": None, "_ts": ts})
    hits.sort(key=lambda h: h.get("_ts") or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    for h in hits:
        h.pop("_ts", None)
    return hits[:15]


def check(topic, days=21, transcripts=True):
    st = stems(topic)
    hits = search_log(st) + search_ledger(st) + search_memory(st)
    if transcripts:
        hits += search_transcripts(st, days)
    searched = [str(log_path())]
    if ledger_path():
        searched.append(str(ledger_path()))
    if memory_dir():
        searched.append(str(memory_dir()) + "/*.md")
    if transcripts and transcripts_dir():
        searched.append(f"{transcripts_dir()}/*.jsonl (user turns, {days} days)")
    return {"topic": topic, "stems": st, "searched": searched, "hits": hits}


def exact_log_hit(d, project):
    want = norm(d.get("title"))
    for r in read_log():
        if r.get("project") == project and r.get("id") == d.get("id"):
            continue
        if norm(r.get("question")) == want:
            return r
    return None


# ---------- lint ----------

ID_IN_TITLE = re.compile(r"(#\d+|\bPR\b|\b[A-Z]{2,}-\d+\b|\b\w+\.(py|tsx?|jsx?|json|png|html|md|css|yml|yaml)\b)")
BANNED_NOTE = re.compile(r"(\bPR\b|#\d+|\bv\d+\b|\b[A-Z]{2,}-\d+\b|\b\w+\.(py|tsx?|jsx?|json|png|html|md|css|yml|yaml)\b|"
                         r"\b[\w-]+/[\w./-]+\b)")
UPDATE_TEXT = re.compile(r"(?i)(\bupdate:|\bwas/now\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.? \d{1,2} update\b)")
TWO_ASKS = re.compile(r"(?i)\b(and|then)\b\s+(turn|add|ship|merge|create|rerun|run|fix|start|make|switch|send|delete|remove)\b")
TEXT_FIELDS = ("title", "bluf", "cost_of_wait", "impact", "unknowns")


def lint(pack):
    errors, warns = list(schema.validate(pack)), []
    project = pack.get("project", "")
    raw = json.dumps(pack)
    if re.search(r"(localhost|127\.0\.0\.1)", raw):
        errors.append("pack.json holds a localhost URL")
    for d in pack.get("decisions") or []:
        if not isinstance(d, dict):
            continue
        did = d.get("id", "?")
        t = d.get("title") or ""
        if t and not t.rstrip().endswith("?"):
            errors.append(f"{did}: title must be a question ending in '?'")
        if ID_IN_TITLE.search(t):
            errors.append(f"{did}: title holds a PR, ticket or file ID ({ID_IN_TITLE.search(t).group(0)}); move it to details_md")
        if TWO_ASKS.search(t):
            warns.append(f"{did}: title may bundle two asks ('{TWO_ASKS.search(t).group(0)}'); split into two cards")
        opts = d.get("options") or []
        if not 2 <= len(opts) <= 3:
            errors.append(f"{did}: needs 2 or 3 options, has {len(opts)}")
        nrec = sum(1 for o in opts if o.get("recommended"))
        if nrec != 1:
            errors.append(f"{did}: exactly one option must be recommended (has {nrec})")
        hc = d.get("history_check")
        if not hc:
            errors.append(f"{did}: no history_check (run: decision-pack check \"<topic words>\")")
        elif hc.get("result") == "doubtful" and hc.get("quote"):
            qk = hc.get("quote_choice_key")
            rec_o = recommended(d)
            if not qk or not option(d, qk):
                errors.append(f"{did}: history_check has a quote but no quote_choice_key (the option their earlier words "
                              "picked). The page button must save that option, never a bare 'Yes, that stands'")
            elif rec_o and qk != rec_o.get("key") and not (hc.get("new_fact") or "").strip():
                errors.append(f"{did}: their earlier words picked {qk} but I recommend {rec_o.get('key')}; set "
                              "history_check.new_fact (what changed since, one short line)")
        elif d.get("status") == "open" and hc.get("result") == "none":
            hit = exact_log_hit(d, project)
            if hit:
                errors.append(f"{did}: already answered in decisions.jsonl ({hit.get('id')} {hit.get('choice_label')!r}, "
                              f"{hit.get('answered_at_local')}); act on it, or set history_check.result to doubtful")
        shots = all_shots(d)
        nvr = d.get("no_visual_reason")
        changes_ui = bool(d.get("changes_ui")) or (d.get("gate") in UI_GATES)
        if not shots and not nvr:
            errors.append(f"{did}: no picture and no no_visual_reason")
        if nvr and changes_ui:
            errors.append(f"{did}: changes UI, so it needs a picture, not no_visual_reason")
        ui = [s for s in shots if s.get("kind") in UI_KINDS]
        roles = {}
        for s in ui:
            roles.setdefault(s.get("role"), set()).add(s.get("width"))
        for role, widths in roles.items():
            if 1440 in widths and 390 not in widths:
                errors.append(f"{did}: UI shot '{role}' has 1440 but no 390")
            if 390 in widths and 1440 not in widths:
                errors.append(f"{did}: UI shot '{role}' has 390 but no 1440 (1440 first)")
        if d.get("gate") in UI_GATES:
            have = {(s.get("role"), s.get("width")) for s in shots} | \
                   {("pair", s.get("width")) for s in shots if s.get("kind") == "pair"}
            ok = all(("pair", w) in have or (("before", w) in have and ("after", w) in have) for w in (1440, 390))
            if not ok:
                errors.append(f"{did}: gated ({d.get('gate')}) card needs a before/after pair at 1440 and 390")
        if d.get("default_key") and not defaultable(d):
            errors.append(f"{did}: a default is not allowed on a one-way or gated card")
        if d.get("default_key") and not d.get("default_at_local"):
            errors.append(f"{did}: has a default but no default_at_local")
        if d.get("default_key") and not option(d, d["default_key"]):
            errors.append(f"{did}: default_key {d['default_key']} is not an option")
        texts = [d.get(k) or "" for k in TEXT_FIELDS] + [o.get(k) or "" for o in opts for k in ("button", "result")]
        for tx in texts:
            if UPDATE_TEXT.search(tx):
                errors.append(f"{did}: holds update/was-now text ({UPDATE_TEXT.search(tx).group(0)!r}); write one final answer")
                break
        lint_shots(errors, did, shots)
    for f in pack.get("fyi") or []:
        if f.get("shot"):
            lint_shots(errors, "FYI " + (f.get("title") or ""), [f["shot"]])
    return errors, warns


def lint_shots(errors, did, shots):
    for s in shots:
        p = s.get("path") or ""
        if not os.path.isfile(p):
            errors.append(f"{did}: missing file {p}")
        else:
            if os.path.getsize(p) > MAX_SHOT:
                errors.append(f"{did}: {p} is {os.path.getsize(p) // 1024} KB, over 600 KB (run: decision-pack shrink)")
        if not s.get("viewed"):
            errors.append(f"{did}: shot {p} not viewed (Read the PNG, then: decision-pack viewed {did} {p})")
        for c in s.get("callouts") or []:
            note = c.get("note") or ""
            m = BANNED_NOTE.search(note)
            if m:
                errors.append(f"{did}: callout {c.get('n')} note has an internal word ({m.group(0)!r})")
            if len(note) > 90:
                errors.append(f"{did}: callout {c.get('n')} note over 90 characters")


# ---------- chat mapping ----------

def map_chat(pack, text):
    """Map the user's chat reply onto cards. Returns {"answers": [{id, choice_key, kind, words}],
    "unclear": [{id, title, words}], "skipped_one_way": [ids]}."""
    out = {"answers": [], "unclear": [], "skipped_one_way": []}
    ds = decisions(pack)
    open_ds = [d for d in ds if d.get("status") in ("open", "needs_material")]
    flat = " ".join(norm(text))
    if flat in OK_WORDS:
        for d in open_ds:
            if d.get("door") == "two_way":
                rec = recommended(d)
                out["answers"].append({"id": d["id"], "choice_key": rec["key"], "kind": "ok_recommended", "words": text})
            else:
                out["skipped_one_way"].append(d["id"])
        return out
    by_order = {}
    for i, d in enumerate(ds, 1):
        by_order.setdefault(str(d.get("order", i)), d)
    by_id = {d["id"].lower(): d for d in ds}
    items = re.findall(r"(?:^|\s|[,;])((?:[A-Za-z]{1,4}-)?\d+[a-z]?)\s*[.):\-]\s*(.+?)(?=(?:\s|[,;])(?:[A-Za-z]{1,4}-)?\d+[a-z]?\s*[.):\-]\s|$)",
                       " " + " ".join(text.split()))
    if not items:
        out["unclear"].append({"id": None, "title": None, "words": text})
        return out
    for ref, val in items:
        d = by_id.get(ref.lower()) or by_order.get(ref)
        if not d:
            out["unclear"].append({"id": ref, "title": None, "words": val.strip()})
            continue
        v = " ".join(norm(val))
        rec = recommended(d)
        key = None
        kind = "pick"
        m = re.fullmatch(r"([abc])", v) or re.match(r"^(?:option )?([abc])\b", v)
        if v in OK_WORDS:
            key, kind = rec["key"], "ok_recommended"
        elif m and option(d, m.group(1).upper()):
            key = m.group(1).upper()
        elif v in NO_WORDS:
            others = [o for o in d.get("options") or [] if not o.get("recommended")]
            if len(others) == 1:
                key = others[0]["key"]
        if key:
            out["answers"].append({"id": d["id"], "choice_key": key, "kind": kind, "words": val.strip()})
        else:
            out["unclear"].append({"id": d["id"], "title": d.get("title"), "words": val.strip()})
    return out


# ---------- record ----------

def last_choice(project, did):
    prev = None
    for r in read_log():
        if r.get("project") == project and r.get("id") == did and r.get("choice_key"):
            prev = r.get("choice_key")
    return prev


def tw_bin():
    """Optional Todo Watch CLI (config `todo_watch_bin` or $DP_TW_BIN). None turns the integration off."""
    v = opt_path("DP_TW_BIN", "todo_watch_bin")
    return str(v) if v else None


def outbox(project):
    return pack_dir(project) / "outbox"


def page_update_file(pack, d):
    """The ArtifactData update for decisions/<id> sync fields; the session runs it with a fresh if_version."""
    data = {"status": d.get("status"), "outcome": d.get("outcome"), "synced_answer_at": d.get("synced_answer_at"),
            "version": d.get("version")}
    p = outbox(pack["project"]) / f"decisions-{d['id']}-update.json"
    write_json(p, data)
    return p


def record(project, did, choice_key=None, kind="pick", source="page", words="", regions=None, asset_ids=None,
           answered_at=None, standing=None, decision_version=None, use_tw=True, standing_when_decided=False):
    if source not in USER_SOURCES:
        die(f"source {source!r} refused: only the user's own words are logged ({', '.join(USER_SOURCES)})", 2)
    pack = load_pack(project)
    d = find(pack, did)
    answered_at = answered_at or iso(now_utc())
    if kind in ("ok_recommended", "disagree_go") and not choice_key:
        choice_key = recommended(d)["key"]
    opt = option(d, choice_key) if choice_key else None
    if choice_key and not opt:
        die(f"{did} has no option {choice_key}")
    prev = last_choice(project, did)
    stale = bool(decision_version and decision_version < d.get("version", 1))
    row = {"id": did, "project": project, "question": d.get("title"), "choice_key": choice_key,
           "choice_label": opt.get("button") if opt else None,
           "rejected": [o["key"] for o in d.get("options") or [] if choice_key and o["key"] != choice_key],
           "kind": kind, "words": words, "regions": regions or [], "asset_ids": asset_ids or [], "source": source,
           "answered_at": answered_at, "answered_at_local": fmt_local(answered_at),
           "decision_version": decision_version or d.get("version", 1), "standing": False,
           "changed_from": prev if (prev and choice_key and prev != choice_key) else None}
    if stale:
        row["stale"] = True
    mirrors = d.setdefault("mirrors", {})
    decided = kind in ("pick", "ok_recommended", "disagree_go", "yes_that_stands", "change", "default") and \
        (choice_key or kind == "yes_that_stands") and not stale
    if standing and (decided or not standing_when_decided):
        rule = f"{standing}: {opt.get('button')}" if (standing_when_decided and opt) else standing
        append_ledger(rule, words or (opt or {}).get("button") or "", answered_at)
        row_standing = True
    else:
        row_standing = False
    row["standing"] = row_standing
    log_path().parent.mkdir(parents=True, exist_ok=True)
    with open(log_path(), "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    mirrors["log"] = True
    if decided:
        d["status"] = "defaulted" if source == "default" else "decided"
        d["outcome"] = {"choice_key": choice_key, "label": opt.get("button") if opt else "Earlier answer stands",
                        "by": "default" if source == "default" else "user", "decided_at": answered_at,
                        "decided_at_local": fmt_local(answered_at), "result": (d.get("outcome") or {}).get("result")}
        mirrors["todo_watch"] = close_tw(d, words or (opt or {}).get("button") or kind) if use_tw else \
            not (d.get("todo_watch") or {}).get("ledger")
    elif kind == "material":
        d["status"] = "needs_material"
    if source != "default":
        # A default is not an answer from the user. Leaving synced_answer_at alone means any page answer they
        # gave before the default still reads as new, on the page and in sync_plan, and wins.
        d["synced_answer_at"] = answered_at
    mirrors["page"] = False
    save_pack(pack)
    upd = page_update_file(pack, d)
    return {"row": row, "status": d["status"], "page_update": str(upd), "stale": stale}


def close_tw(d, words):
    tw = (d.get("todo_watch") or {})
    ref = tw.get("ledger") or tw.get("task")
    if not ref or not tw_bin():
        return True
    try:
        r = subprocess.run([tw_bin(), "task", "done", ref, "--reason", "already_done", "--evidence", words[:300]],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def append_ledger(rule, words, answered_at):
    p = ledger_path()
    if not p:
        return False
    d = _local(answered_at)
    line = f"- {rule}" + (f" — \"{words}\"" if words else "") + f" ({d:%b}. {d.day}, {d.year})\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(line)


# ---------- due ----------

def due(pack, now=None, use_tw=True, responses=None, apply=True):
    """Apply past-due defaults. The user's page answers are read first: pass `responses` (a fresh
    ArtifactData query of responses/*, normalized), and every unsynced answer is recorded before any
    default, so an answer they gave before the deadline always wins. responses=None means the page was
    not read: nothing is applied (apply is forced off) and the lines say to run sync first."""
    now = now or now_utc()
    applied, stuck, synced = [], [], []
    if responses is None:
        apply = False
    else:
        for r in sync_plan(pack, responses):
            apply_response(pack["project"], r, use_tw=use_tw)
            synced.append(r["decision_id"])
        pack = load_pack(pack["project"])
    for d in decisions(pack):
        if d.get("status") != "open":
            continue
        if d.get("default_key") and defaultable(d) and d.get("default_at") and parse_iso(d["default_at"]) <= now:
            applied.append(d["id"])
        elif d.get("door") == "one_way" and d.get("sent_at") and now - parse_iso(d["sent_at"]) > dt.timedelta(hours=24):
            stuck.append(d["id"])
    lines = []
    if not apply:
        for did in applied:
            d = find(pack, did)
            lines.append(f"{did} is past due ({d.get('default_at_local')}). Not applied: read the page first with "
                         f"decision-pack sync-plan {pack['project']} (it applies defaults after their answers).")
        for did in stuck:
            lines.append(f"{did} is a one-way door, open over 24 h. It still needs your tap.")
        return {"applied": [], "past_due": applied, "stuck": stuck, "synced": synced, "lines": lines}
    for did in applied:
        d = find(load_pack(pack["project"]), did)
        k = d["default_key"]
        record(pack["project"], did, k, kind="default", source="default",
               words=f"No answer by {d.get('default_at_local')}", answered_at=d["default_at"], use_tw=use_tw)
        lines.append(f"{did} went with {k} by default (no answer by {d.get('default_at_local')}). One tap changes it.")
    for did in stuck:
        lines.append(f"{did} is a one-way door, open over 24 h. It still needs your tap.")
    return {"applied": applied, "past_due": applied, "stuck": stuck, "synced": synced, "lines": lines}


# ---------- seed ----------

def public_shot(s):
    out = {k: s.get(k) for k in ("role", "width", "kind", "px")}
    out["src"] = s.get("asset_url") or ("shots/" + os.path.basename(s.get("path") or ""))
    out["callouts"] = []
    for c in s.get("callouts") or []:
        cc = {"n": c["n"], "box": c["box"], "note": c["note"]}
        if c.get("crop_path") or c.get("crop_url"):
            cc["crop_src"] = c.get("crop_url") or ("shots/crops/" + os.path.basename(c.get("crop_path")))
        out["callouts"].append(cc)
    return out


def public_decision(d):
    keep = ("id", "order", "version", "title", "bluf", "door", "gate", "default_key", "default_at", "default_at_local",
            "cost_of_wait", "impact", "unknowns", "details_md", "no_visual_reason", "status", "outcome",
            "synced_answer_at")
    out = {k: d.get(k) for k in keep if k in d}
    out["shots"] = [public_shot(s) for s in d.get("shots") or []]
    out["options"] = [{"key": o["key"], "button": o["button"], "result": o["result"], "undo": o["undo"],
                       "recommended": bool(o.get("recommended")), "shots": [public_shot(s) for s in o.get("shots") or []]}
                      for o in d.get("options") or []]
    hc = d.get("history_check") or {}
    out["history_check"] = {"result": hc.get("result"), "quote": hc.get("quote"), "quote_date_local": hc.get("quote_date_local"),
                            "quote_choice_key": hc.get("quote_choice_key"), "new_fact": hc.get("new_fact"),
                            "checked": ", ".join(os.path.basename(x.rstrip("/*. ")) or x for x in hc.get("searched") or []),
                            "at_local": fmt_local(hc["at"]) if hc.get("at") else None}
    return out


def public_meta(pack):
    return {"project": pack["project"], "title": pack["title"], "template_version": pack.get("template_version"),
            "done_without_asking": pack.get("done_without_asking") or [],
            "fyi": [{"title": f["title"], "shot": public_shot(f["shot"])} for f in pack.get("fyi") or []],
            "timezone": zone_name(), "updated_at": iso(now_utc())}


def split_batches(writes, sizes=None):
    """Split writes into batches of <= 50 entries and <= ~1 MiB of document bytes."""
    sizes = sizes or [len(json.dumps(w.get("data") or {})) for w in writes]
    batches, cur, cur_bytes = [], [], 0
    for w, n in zip(writes, sizes):
        if cur and (len(cur) >= BATCH_WRITES or cur_bytes + n > BATCH_BYTES):
            batches.append(cur)
            cur, cur_bytes = [], 0
        cur.append(w)
        cur_bytes += n
    if cur:
        batches.append(cur)
    return batches


class SeedError(Exception):
    pass


def seed(pack, versions=None, only=None, no_meta=False):
    """Write doc files and batch manifests under <home>/<project>/seed/. versions maps
    'meta/pack' and 'decisions/LP-1' -> version (from a fresh ArtifactData list of BOTH collections);
    a doc with a version is updated with if_version, a doc without one is created with set.

    A later send (versions given) must pin meta/pack too: an unpinned set on a doc that already exists
    makes the whole ArtifactData batch write nothing. So a versions map with no 'meta/pack' key is
    refused unless no_meta=True, which leaves meta out (a card-only re-seed)."""
    versions = versions or {}
    if versions and "meta/pack" not in versions and not no_meta:
        raise SeedError("versions has no 'meta/pack' pin. List meta too (ArtifactData list collection=meta) and add "
                        "\"meta/pack\": <version>, or pass --no-meta for a card-only re-seed.")
    sd = pack_dir(pack["project"]) / "seed"
    if sd.exists():
        shutil.rmtree(sd)
    (sd / "docs").mkdir(parents=True)
    docs = [] if no_meta else [("meta", "pack", public_meta(pack))]
    for d in decisions(pack):
        if only and d["id"] not in only:
            continue
        docs.append(("decisions", d["id"], public_decision(d)))
    writes, sizes = [], []
    for coll, did, data in docs:
        f = sd / "docs" / f"{coll}__{did}.json"
        f.write_text(json.dumps(data, ensure_ascii=False))
        v = versions.get(f"{coll}/{did}")
        w = {"op": "update" if v else "set", "collection": coll, "doc_id": did, "file_path": str(f)}
        if v:
            w["if_version"] = int(v)
        writes.append(w)
        sizes.append(f.stat().st_size)
    files = []
    for i, b in enumerate(split_batches(writes, sizes), 1):
        p = sd / f"batch-{i:03d}.json"
        write_json(p, {"writes": b})
        files.append(str(p))
    return files


# ---------- render / local ----------

def render(pack):
    errs, warns = lint(pack)
    if errs:
        return None, errs, warns
    site = pack_dir(pack["project"]) / "site"
    site.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE, site / "index.html")
    files = {}
    for _, s in pack_shots(pack):
        if not s.get("asset_url"):
            files["shots/" + os.path.basename(s["path"])] = s["path"]
        for c in s.get("callouts") or []:
            if c.get("crop_path") and not c.get("crop_url"):
                files["shots/crops/" + os.path.basename(c["crop_path"])] = c["crop_path"]
    write_json(site / "files.json", files)
    return {"page": str(site / "index.html"), "files": files, "files_json": str(site / "files.json")}, errs, warns


def local_html(pack):
    data = {"meta": public_meta(pack), "decisions": [public_decision(d) for d in decisions(pack)], "responses": []}
    for d, src in zip(data["decisions"], decisions(pack)):
        pairs = list(zip(d["shots"], src.get("shots") or []))
        for o, so in zip(d["options"], src.get("options") or []):
            pairs += list(zip(o["shots"], so.get("shots") or []))
        for pub, raw in pairs:
            pub["src"] = Path(raw["path"]).as_uri()
            for pc, rc in zip(pub["callouts"], raw.get("callouts") or []):
                if rc.get("crop_path"):
                    pc["crop_src"] = Path(rc["crop_path"]).as_uri()
    for f, raw in zip(data["meta"]["fyi"], pack.get("fyi") or []):
        f["shot"]["src"] = Path(raw["shot"]["path"]).as_uri()
    html = TEMPLATE.read_text()
    inject = "<script>window.DP_LOCAL_PACK=" + json.dumps(data).replace("</", "<\\/") + ";</script>"
    html = html.replace("<!--DP_LOCAL-->", inject, 1)
    out = pack_dir(pack["project"]) / "local.html"
    out.write_text(html)
    return out


# ---------- todo watch ----------

def tw_description(pack, d):
    lines = [f"D-id: {d['id']} · Pack: {pack['project']}", d["title"]]
    for o in d.get("options") or []:
        lines.append(f"{o['key']}) {o['button']}" + (" (recommended)" if o.get("recommended") else ""))
    if d.get("default_key") and defaultable(d):
        lines.append(f"If you say nothing: {d['default_key']} at {d.get('default_at_local')}")
    else:
        lines.append("Needs your tap (no default)")
    rec = recommended(d)
    lines.append(f"Undo: {(rec or {}).get('undo', 'easy')}")
    return "\n".join(lines)


# ---------- status ----------

def all_packs():
    out = []
    for p in sorted(root().glob("*/pack.json")):
        pk = read_json(p)
        if pk:
            out.append(pk)
    return out


def status_summary():
    n_open = n_dec = not_closed = 0
    next_due = None
    stuck = []
    now = now_utc()
    for pk in all_packs():
        for d in pk.get("decisions") or []:
            st = d.get("status")
            if st in ("open", "needs_material"):
                n_open += 1
                if d.get("default_at") and defaultable(d) and d.get("default_key"):
                    t = parse_iso(d["default_at"])
                    if next_due is None or t < next_due:
                        next_due = t
                if d.get("door") == "one_way" and d.get("sent_at") and now - parse_iso(d["sent_at"]) > dt.timedelta(hours=24):
                    stuck.append(d["id"])
            elif st in ("decided", "defaulted"):
                n_dec += 1
                m = d.get("mirrors") or {}
                if not (m.get("log") and m.get("todo_watch") and m.get("page")):
                    not_closed += 1
    bits = [f"{n_open} open"]
    if next_due:
        bits.append(f"next due {clock(_local(next_due))}")
    if not_closed:
        bits.append(f"{not_closed} not closed")
    if stuck:
        bits.append(f"{len(stuck)} one-way waiting 24 h+")
    return {"open": n_open, "decided": n_dec, "not_closed": not_closed, "stuck": stuck,
            "line": "Decisions: " + " · ".join(bits)}


# ---------- sync plan ----------

def normalize_responses(src):
    p = Path(src)
    docs = []
    if p.is_dir():
        for f in sorted(p.rglob("*.json")):
            docs.append(read_json(f))
    else:
        raw = read_json(p)
        if isinstance(raw, dict):
            raw = raw.get("documents") or raw.get("docs") or raw.get("results") or [raw]
        docs = raw or []
    out = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        data = doc.get("data") if isinstance(doc.get("data"), dict) else doc
        if data.get("decision_id") or doc.get("id"):
            r = dict(data)
            r.setdefault("decision_id", doc.get("id") or doc.get("doc_id"))
            out.append(r)
    out.sort(key=lambda r: r.get("answered_at") or "")
    return out


def sync_plan(pack, responses):
    """Responses newer than the card's synced_answer_at. A default never moves synced_answer_at (it is
    not the user's answer), so a page answer given before a default applied still counts as new and wins."""
    new = []
    for r in responses:
        try:
            d = find(pack, r.get("decision_id"))
        except SystemExit:
            continue
        last = d.get("synced_answer_at")
        if r.get("answered_at") and (not last or parse_iso(r["answered_at"]) > parse_iso(last)):
            new.append(r)
    return new


def apply_response(project, r, use_tw=True):
    kind = r.get("kind") or "pick"
    d = find(load_pack(project), r["decision_id"])
    return record(project, r["decision_id"], r.get("choice_key"), kind=kind, source=r.get("source") or "page",
                  words=r.get("note") or r.get("choice_label") or "", regions=r.get("regions"),
                  asset_ids=r.get("asset_ids"), answered_at=r.get("answered_at"),
                  decision_version=r.get("decision_version"), use_tw=use_tw,
                  standing=d.get("standing_rule"), standing_when_decided=True)


# ---------- images ----------

def png_size(p):
    from PIL import Image
    with Image.open(p) as im:
        return list(im.size)


# More unique colors than this reads as photographic (gradients, photos, hero art): a 256-color palette bands it.
PHOTO_COLORS = 4096
PNG_STEPS = [{"format": "PNG", "colors": c} for c in (256, 128, 64, 32)]
# JPEG quality 88 to 82 at 4:4:4 (subsampling 0) first; 4:2:0 (subsampling 2) only when 4:4:4 cannot fit.
JPEG_STEPS = [{"format": "JPEG", "quality": q, "subsampling": sub, "progressive": True}
              for sub, qs in ((0, (88, 86, 84, 82)), (2, (86, 84, 82))) for q in qs]


def _encode(im, step):
    from PIL import Image
    kw = dict(step)
    colors = kw.pop("colors", None)
    if colors:
        im = im.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    im.save(buf, optimize=True, **kw)
    return buf.getvalue()


def _fit(im, steps, budget):
    """Best step that fits the budget; when even the last step is over, scale down and retry.
    Returns (bytes, image). Scaling jumps by the size ratio (bytes track pixel count), never under 320 wide."""
    from PIL import Image
    while True:
        for step in steps:
            data = _encode(im, step)
            if len(data) <= budget:
                return data, im
        k = min(.9, (budget / len(data)) ** .5 * .95)
        if im.width <= 320:
            return data, im
        w = max(320, round(im.width * k))
        im = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)


def shrink(p, max_kb=600, width=None, crop_height=None):
    """Re-encode a shot to fit max_kb. Flat UI and diagrams become a 256-color PNG; photographic shots
    become a progressive JPEG, since a palette bands their gradients. Returns (bytes, [w, h], out_path).
    The extension follows the format (.png or .jpg); when it changes, the old file is removed."""
    from PIL import Image
    with Image.open(p) as src:
        im = src.convert("RGB")
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    if crop_height and im.height > crop_height:
        im = im.crop((0, 0, im.width, crop_height))
    photo = im.getcolors(PHOTO_COLORS) is None
    out = str(Path(p).with_suffix(".jpg" if photo else ".png"))
    if os.path.abspath(out) != os.path.abspath(p) and os.path.exists(out):
        raise FileExistsError(f"{out} already exists; move it or {p} first")
    data, im = _fit(im, JPEG_STEPS if photo else PNG_STEPS, max_kb * 1024)
    Path(out).write_bytes(data)
    if os.path.abspath(out) != os.path.abspath(p):
        os.remove(p)
    return len(data), list(im.size), out


def set_px(shot, size):
    """Set a shot's px to the file's size, scaling its callout boxes with it (the page places hotspots in px
    space). Returns True when anything changed."""
    px = shot.get("px")
    if px == size:
        return False
    if px and px[0]:
        k = size[0] / px[0]
        for c in shot.get("callouts") or []:
            c["box"] = [round(v * k) for v in c["box"]]
    shot["px"] = size
    return True


def update_shots(moves):
    """After shrink: moves is [(old, new, [w, h])]. Point pack shots and crops at old to new and fix px and
    callout boxes. Each pack is read once and saved only when it changed. Returns the projects touched."""
    moves = {os.path.abspath(o): (os.path.abspath(n), size) for o, n, size in moves}
    moves.update({n: (n, size) for n, size in list(moves.values())})
    touched = []
    for pk in all_packs():
        hit = False
        for _, s in pack_shots(pk):
            m = moves.get(os.path.abspath(s.get("path") or ""))
            if m:
                hit |= s["path"] != m[0] or bool(s.pop("asset_url", None))  # the published copy is the old image
                s["path"] = m[0]
                hit |= set_px(s, m[1])
            for c in s.get("callouts") or []:
                cm = c.get("crop_path") and moves.get(os.path.abspath(c["crop_path"]))
                if cm:
                    hit |= c["crop_path"] != cm[0] or bool(c.pop("crop_url", None))
                    c["crop_path"] = cm[0]
        if hit:
            save_pack(pk)
            touched.append(pk["project"])
    return touched


def vendor(script, args):
    return subprocess.run([sys.executable, str(VENDOR / script)] + args, text=True)


# ---------- CLI ----------

def cmd_init(a):
    p = pack_path(a.project)
    if p.exists():
        die(f"{p} already exists")
    (pack_dir(a.project) / "shots").mkdir(parents=True, exist_ok=True)
    save_pack({"project": a.project, "title": a.title, "template_version": TEMPLATE_VERSION, "artifact_url": None,
               "owner_session": None, "done_without_asking": [], "fyi": [], "decisions": []})
    print(p)


def cmd_check(a):
    res = check(a.topic, a.days, not a.no_transcripts)
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return
    print(f"Searched for {' '.join(res['stems'])}: " + "; ".join(res["searched"]))
    if not res["hits"]:
        print("No earlier answer found. history_check.result: none")
    for h in res["hits"]:
        print(f"- [{h['source']}] {h.get('date_local') or 'no date'}: {h.get('words') or h.get('text')}")
        if h.get("text") and h.get("words"):
            print(f"    rule: {h['text']}")
        print(f"    {h['path']}")


def cmd_add(a):
    pack = load_pack(a.project)
    d = read_json(a.file)
    ds = [x for x in pack["decisions"] if x.get("id") != d.get("id")]
    pack["decisions"] = ds + [d]
    save_pack(pack)
    errs = schema.validate_decision(d, d.get("id", "decision"))
    print("\n".join(errs) or f"added {d.get('id')}")


def cmd_shoot(a):
    if not (a.out or a.project):
        die("pass --project or --out")
    out = a.out or str(pack_dir(a.project) / "shots")
    args = [a.url, "--out", out, "--name", a.name, "--widths"] + [str(w) for w in a.widths]
    if a.storage_state:
        args += ["--storage-state", a.storage_state]
    if a.before_shot:
        args += ["--before-shot"] + a.before_shot
    if a.box:
        args += ["--box"] + a.box
    r = vendor("shoot.py", args)
    if r.returncode:
        sys.exit(r.returncode)
    for w in a.widths:
        bj = Path(out) / f"{a.name}-{w}.boxes.json"
        if bj.exists():
            boxes = read_json(bj)
            write_json(Path(out) / f"{a.name}-{w}.callouts.json",
                       [{"n": i, "box": [b["x"], b["y"], b["w"], b["h"]], "note": ""} for i, b in enumerate(boxes, 1)])
            print(f"starting callouts: {Path(out) / f'{a.name}-{w}.callouts.json'} (fill in each note)")


def cmd_mark(a):
    args = ["mark", a.input, a.callouts, a.output] + (["--scale", str(a.scale)] if a.scale != 1 else [])
    r = vendor("annotate.py", args)
    if r.returncode:
        sys.exit(r.returncode)
    cs = read_json(a.callouts)
    write_json(a.output[:-4] + ".callouts.json" if a.output.endswith(".png") else a.output + ".callouts.json",
               {"px": png_size(a.output), "callouts": cs})


def cmd_pair(a):
    sys.exit(vendor("annotate.py", ["pair", a.before, a.after, a.output, "--title", a.title or ""]).returncode)


def cmd_crop(a):
    base = Path(a.input)
    cdir = base.parent / "crops"
    cdir.mkdir(exist_ok=True)
    prefix = str(cdir / (base.stem + "-c"))
    r = vendor("annotate.py", ["crop", a.input, a.callouts, prefix])
    if r.returncode:
        sys.exit(r.returncode)
    for f in glob.glob(prefix + "-*.png"):
        n = f[len(prefix) + 1:-4]
        dst = cdir / f"{base.stem}-c{n}.png"
        os.replace(f, dst)
        print(dst)


def cmd_shrink(a):
    moves = []
    try:
        for p in a.image:
            try:
                size, px, out = shrink(p, a.max_kb, a.width, a.crop_height)
            except FileExistsError as e:
                die(str(e))
            print(f"{out}: {size // 1024} KB, {px[0]}x{px[1]}" + (f"  OVER {a.max_kb} KB" if size > a.max_kb * 1024 else ""))
            moves.append((p, out, px))
    finally:  # record every finished move, even when a later image fails
        for project in update_shots(moves):
            print(f"pack {project}: shots updated")


def cmd_viewed(a):
    packs = [load_pack(a.project)] if a.project else all_packs()
    target = os.path.abspath(a.png)
    hit = False
    for pk in packs:
        for did, s in pack_shots(pk):
            if (did == a.id or a.id == "*") and os.path.abspath(s.get("path") or "") == target:
                s["viewed"] = True
                if os.path.isfile(target):
                    set_px(s, png_size(target))
                hit = True
        if hit:
            save_pack(pk)
            print(f"viewed: {a.id} {target}")
            return
    die(f"no shot {target} on {a.id}")


def cmd_lint(a):
    errs, warns = lint(load_pack(a.project))
    for w in warns:
        print("warning: " + w)
    for e in errs:
        print("error: " + e)
    print("lint clean" if not errs else f"{len(errs)} error(s)")
    sys.exit(1 if errs else 0)


def cmd_render(a):
    pack = load_pack(a.project)
    res, errs, warns = render(pack)
    for w in warns:
        print("warning: " + w)
    if errs:
        for e in errs:
            print("error: " + e)
        die("render blocked by lint")
    try:
        files = seed(pack, read_json(a.versions) if a.versions else None)
    except SeedError as e:
        die(str(e))
    print(json.dumps({"page": res["page"], "files_json": res["files_json"], "n_files": len(res["files"]),
                      "seed_batches": files,
                      "capabilities": CAPABILITIES}, indent=1))


CAPABILITIES = {"db": {"rules": [{"path": "meta", "read": "view", "write": "admin"},
                                 {"path": "decisions", "read": "view", "write": "admin"},
                                 {"path": "responses", "read": "view", "write": "owner"}]},
                "user": {}, "comments": {"customAnchors": True}, "assets": {}}


def cmd_seed(a):
    pack = load_pack(a.project)
    errs, _ = lint(pack)
    if errs and not a.force:
        for e in errs:
            print("error: " + e)
        die("seed blocked by lint")
    try:
        files = seed(pack, read_json(a.versions) if a.versions else None, a.only, no_meta=a.no_meta)
    except SeedError as e:
        die(str(e))
    for f in files:
        print(f)


def cmd_ingest(a):
    pack = load_pack(a.project)
    res = map_chat(pack, a.text)
    if a.apply:
        resp = []
        for ans in res["answers"]:
            r = record(a.project, ans["id"], ans["choice_key"], kind=ans["kind"], source=a.source, words=ans["words"],
                       use_tw=not a.no_tw)
            resp.append(response_doc(pack, ans, a.source, r["row"]["answered_at"]))
        for doc in resp:
            write_json(outbox(a.project) / f"responses-{doc['decision_id']}.json", doc)
        res["response_files"] = [str(outbox(a.project) / f"responses-{d['decision_id']}.json") for d in resp]
    print(json.dumps(res, indent=1, ensure_ascii=False))


def response_doc(pack, ans, source, answered_at):
    d = find(pack, ans["id"])
    o = option(d, ans["choice_key"])
    return {"decision_id": d["id"], "decision_version": d.get("version", 1), "kind": ans["kind"],
            "choice_key": ans["choice_key"], "choice_label": o["button"] if o else None,
            "was_recommended": bool(o and o.get("recommended")), "note": ans["words"], "regions": [], "asset_ids": [],
            "source": source, "answered_by": None, "answered_at": answered_at, "answered_at_local": fmt_local(answered_at),
            "sent_at": None}


def cmd_record(a):
    res = record(a.project, a.id, a.choice, kind=a.kind, source=a.source, words=a.words or "",
                 regions=json.loads(a.regions) if a.regions else None,
                 asset_ids=a.asset_ids.split(",") if a.asset_ids else None, answered_at=a.answered_at,
                 standing=a.standing, decision_version=a.decision_version, use_tw=not a.no_tw)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"next: ArtifactData update collection=decisions doc_id={a.id} file_path={res['page_update']} "
          f"if_version=<fresh get>, then: decision-pack mirrored {a.project} {a.id} page", file=sys.stderr)


def cmd_mirrored(a):
    pack = load_pack(a.project)
    d = find(pack, a.id)
    d.setdefault("mirrors", {})[a.what] = True
    save_pack(pack)
    print(f"{a.id}: mirrors.{a.what} = true")


def cmd_outcome(a):
    pack = load_pack(a.project)
    d = find(pack, a.id)
    d["outcome"] = dict(d.get("outcome") or {}, result=a.text)
    d.setdefault("mirrors", {})["page"] = False
    save_pack(pack)
    print(page_update_file(pack, d))


def cmd_due(a):
    """Without --responses this only reports: a default must never apply before the page is read."""
    if a.responses and not a.project:
        die("--responses needs a project")
    packs = [load_pack(a.project)] if a.project else all_packs()
    out = []
    for pk in packs:
        r = due(pk, use_tw=not a.no_tw, responses=normalize_responses(a.responses) if a.responses else None)
        out += r["lines"]
    if not a.responses and out:
        out.append("Run sync first: decision-pack sync-plan <project> prints the read step; "
                   "sync-plan --apply records their answers, then applies defaults.")
    print("\n".join(out) or "nothing due")


def cmd_status(a):
    s = status_summary()
    if a.line:
        print(s["line"])
        return
    print(s["line"])
    for pk in all_packs():
        ds = decisions(pk)
        o = sum(1 for d in ds if d.get("status") in ("open", "needs_material"))
        print(f"- {pk['project']}: {o} open, {len(ds) - o} closed · {pk.get('artifact_url') or 'not published'}")
        for d in ds:
            m = d.get("mirrors") or {}
            if d.get("status") in ("decided", "defaulted") and not (m.get("log") and m.get("todo_watch") and m.get("page")):
                miss = [k for k in ("log", "todo_watch", "page") if not m.get(k)]
                print(f"    {d['id']} not closed: {', '.join(miss)}")


def cmd_sync_plan(a):
    pack = load_pack(a.project)
    url = pack.get("artifact_url") or "<pack url>"
    if not a.responses:
        print("\n".join([
            f"0. ArtifactComments watch url={url}. Read the result. If auto-replies are NOT armed (any session that did not"
            f" publish this pack), republish the page so Send to Claude wakes this session: Artifact publish"
            f" file_path={pack_dir(a.project) / 'site' / 'index.html'} url={url} (no capabilities field: that keeps them)."
            " If that is not possible, ask the user in one line to paste the pack link. Never claim a watch the result did not confirm.",
            f"1. ArtifactData query url={url} collection=responses query={{order_by:{{field:answered_at,direction:asc}},limit:1000}} out_dir={outbox(a.project)}/read",
            f"2. decision-pack sync-plan {a.project} --responses {outbox(a.project)}/read/responses --apply"
            "  (records their answers first, then applies any past-due default; never run due on its own)",
            f"3. ArtifactComments read url={url}  (threads anchored '<ID>:' map to that card; reply and resolve only threads sent to Claude)",
            f"4. For each asset id in a response: Artifact read url={url} path=<asset id>, then Read the image",
            "5. For each page_update file printed: ArtifactData get decisions/<id>, then update with if_version,"
            f" then decision-pack mirrored {a.project} <id> page"]))
        return
    new = sync_plan(pack, normalize_responses(a.responses))
    if not a.apply:
        print(json.dumps(new, indent=1, ensure_ascii=False))
        return
    out = []
    for r in new:
        out.append(apply_response(a.project, r, use_tw=not a.no_tw))
    # The user's answers are recorded above, so defaults apply only to cards still open after them.
    d = due(load_pack(a.project), use_tw=not a.no_tw, responses=[])
    print(json.dumps({"recorded": [{"id": x["row"]["id"], "choice": x["row"]["choice_key"], "kind": x["row"]["kind"],
                                    "stale": x["stale"], "page_update": x["page_update"]} for x in out],
                      "defaults": d["lines"]}, indent=1, ensure_ascii=False))


def cmd_tw_link(a):
    pack = load_pack(a.project)
    if a.set:
        d = find(pack, a.set)
        d["todo_watch"] = {"task": a.task, "ledger": a.ledger}
        save_pack(pack)
        print(f"{a.set}: todo_watch = {d['todo_watch']}")
        return
    url = pack.get("artifact_url") or "<pack url>"
    open_ds = [d for d in decisions(pack) if d.get("status") in ("open", "needs_material")]
    print(f"Parent: TaskCreate subject='Decide: {pack['title']} ({len(open_ds)})'")
    for d in open_ds:
        shots = [s for s in all_shots(d) if s.get("width") == 1440] + [s for s in all_shots(d) if s.get("width") == 390]
        rec = recommended(d) or {}
        print(f"\n## {d['id']}\nTaskCreate subject='{d['id']}: {d['title']}' description:\n{tw_description(pack, d)}")
        print(f"tw task when '#<N>' --waiting-on-you --under '#<parent>'")
        att = " ".join(f"'{s['path']}'" for s in shots[:2])
        print(f"tw task attach '#<N>' {att} '{url}#d-{d['id']}' --caption \"I recommend {rec.get('key', '')}: {rec.get('button', '')}\"")
        print(f"decision-pack tw-link {a.project} --set {d['id']} --task '#<N>' --ledger L<n>")


def cmd_local(a):
    print(local_html(load_pack(a.project)))


def cmd_register(a):
    reg = read_json(registry_path(), {}) or {}
    pack = load_pack(a.project)
    pack["artifact_url"] = a.url
    if a.session:
        pack["owner_session"] = a.session
    save_pack(pack)
    reg[a.project] = {"artifact_url": a.url, "owner_session": a.session, "template_version": TEMPLATE_VERSION}
    write_json(registry_path(), reg)
    print(f"{a.project} -> {a.url}")


def cmd_sent(a):
    pack = load_pack(a.project)
    t = iso(now_utc())
    for d in pack["decisions"]:
        if d.get("status") == "open" and not d.get("sent_at"):
            d["sent_at"] = t
    save_pack(pack)
    print(f"sent_at {fmt_local(t)}")


# ---------- hooks (settings.json command hooks; see README "Hooks") ----------

ASK_RE = re.compile(r"(?i)\b(want me to|should i|shall i|do you want|would you like|which (one|do you|would you)|"
                    r"your call|ok to|okay to|let me know (if|which|whether|what))\b")
PACK_LINK_RE = re.compile(r"(claude\.ai/(code/)?artifact/|decision-pack|/decision-pack)")


def last_assistant_text(hook_input):
    """The final assistant message: the hook input's last_assistant_message, else the transcript tail."""
    msg = hook_input.get("last_assistant_message")
    if isinstance(msg, str) and msg.strip():
        return msg
    tp = hook_input.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        return ""
    try:
        with open(tp, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 512 * 1024))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for ln in reversed(lines):
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if obj.get("type") != "assistant" or obj.get("isSidechain"):
            continue
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
        if text.strip():
            return text
    return ""


def asks_in_chat(text):
    """True when the message ends by asking the user something and carries no pack link."""
    if not text or PACK_LINK_RE.search(text):
        return False
    lines = [ln.strip().strip("*_`> ").strip() for ln in text.strip().splitlines() if ln.strip()]
    tail = lines[-3:]
    if any(ln.endswith("?") for ln in tail):
        return True
    return any(ASK_RE.search(ln) for ln in tail)


def hook_stop(hook_input):
    """Stop hook: flag a final message that asks the user a question in chat with no pack link. Blocks once;
    stop_hook_active (set on the retry) lets the turn end, so it can never loop."""
    if hook_input.get("stop_hook_active") or str(setting("DP_STOP_HOOK", "stop_hook") or "").lower() == "off":
        return None
    if not asks_in_chat(last_assistant_text(hook_input)):
        return None
    return {"decision": "block",
            "reason": "decision-pack: your last message asks the user a question in chat. Questions for the user go through "
                      "the decision-pack skill (a card with a picture and answer buttons), never chat text. Build or "
                      "update the pack and end with one line plus the pack link. If it was not a question for the user, "
                      "end the turn again unchanged."}


def hook_session_start():
    """SessionStart hook: plain stdout goes into the model's context. Names each pack to read back,
    past-due defaults (never applied here) and the watch to arm."""
    reg = read_json(registry_path(), {}) or {}
    lines = []
    for pk in all_packs():
        url = pk.get("artifact_url") or (reg.get(pk["project"]) or {}).get("artifact_url")
        if not url:
            continue
        ds = decisions(pk)
        open_n = sum(1 for d in ds if d.get("status") in ("open", "needs_material"))
        unclosed = [d["id"] for d in ds if d.get("status") in ("decided", "defaulted")
                    and not all((d.get("mirrors") or {}).get(k) for k in ("log", "todo_watch", "page"))]
        if not open_n and not unclosed:
            continue
        r = due(pk, responses=None)
        bits = [f"{open_n} open"]
        if r["past_due"]:
            bits.append(f"past due: {', '.join(r['past_due'])}")
        if r["stuck"]:
            bits.append(f"one-way waiting 24 h+: {', '.join(r['stuck'])}")
        if unclosed:
            bits.append(f"not closed: {', '.join(unclosed)}")
        lines.append(f"- {pk['project']} ({' · '.join(bits)}): run `decision-pack sync-plan {pk['project']}` and do "
                     f"its steps (step 0 arms the Send to Claude watch on {url}).")
    if not lines:
        return ""
    return ("decision-pack: the user may have answered on these pages. Before any new ask, read them back. "
            "Defaults apply only inside sync-plan --apply, after their answers.\n" + "\n".join(lines))


def cmd_hook(a):
    if a.event == "session-start":
        out = hook_session_start()
        if out:
            print(out)
        return
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        hook_input = {}
    res = hook_stop(hook_input)
    if res:
        print(json.dumps(res))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="decision-pack", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="start a pack")
    p.add_argument("project"); p.add_argument("--title", required=True); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("check", help="search what the user already decided")
    p.add_argument("topic"); p.add_argument("--days", type=int, default=21); p.add_argument("--json", action="store_true")
    p.add_argument("--no-transcripts", action="store_true"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("add", help="add or replace one decision from a JSON file")
    p.add_argument("project"); p.add_argument("file"); p.set_defaults(fn=cmd_add)
    p = sub.add_parser("shoot", help="screenshot a URL or HTML file at 1440 and 390")
    p.add_argument("url"); p.add_argument("--name", required=True); p.add_argument("--project"); p.add_argument("--out")
    p.add_argument("--widths", nargs="+", type=int, default=[1440, 390]); p.add_argument("--storage-state")
    p.add_argument("--before-shot", nargs="*"); p.add_argument("--box", nargs="*"); p.set_defaults(fn=cmd_shoot)
    p = sub.add_parser("mark", help="burn numbered callouts into a PNG")
    p.add_argument("input"); p.add_argument("callouts"); p.add_argument("output")
    p.add_argument("--scale", type=float, default=1); p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("pair", help="before/after sheet")
    p.add_argument("before"); p.add_argument("after"); p.add_argument("output"); p.add_argument("--title")
    p.set_defaults(fn=cmd_pair)
    p = sub.add_parser("crop", help="one close-up per callout")
    p.add_argument("input"); p.add_argument("callouts"); p.set_defaults(fn=cmd_crop)
    p = sub.add_parser("shrink", help="under 600 KB: 256-color PNG for flat UI, JPEG for photographic shots")
    p.add_argument("image", nargs="+"); p.add_argument("--max-kb", type=int, default=600)
    p.add_argument("--width", type=int, default=1440); p.add_argument("--crop-height", type=int)
    p.set_defaults(fn=cmd_shrink)
    p = sub.add_parser("viewed", help="mark a shot as Read by the session")
    p.add_argument("id"); p.add_argument("png"); p.add_argument("--project"); p.set_defaults(fn=cmd_viewed)
    p = sub.add_parser("lint"); p.add_argument("project"); p.set_defaults(fn=cmd_lint)
    p = sub.add_parser("render", help="lint, copy the page, write files map and seed batches")
    p.add_argument("project"); p.add_argument("--versions"); p.set_defaults(fn=cmd_render)
    p = sub.add_parser("seed", help="write ArtifactData batch files")
    p.add_argument("project"); p.add_argument("--versions", help="JSON map {'meta/pack': N, 'decisions/LP-1': M}")
    p.add_argument("--only", nargs="*"); p.add_argument("--force", action="store_true")
    p.add_argument("--no-meta", action="store_true", help="leave meta/pack out (card-only re-seed)")
    p.set_defaults(fn=cmd_seed)
    p = sub.add_parser("ingest", help="map a chat reply onto cards")
    p.add_argument("project"); p.add_argument("--source", default="chat", choices=["chat", "comment", "todo-watch"])
    p.add_argument("--text", required=True); p.add_argument("--apply", action="store_true")
    p.add_argument("--no-tw", action="store_true"); p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("record", help="log one answer and update the pack")
    p.add_argument("project"); p.add_argument("id"); p.add_argument("--choice")
    p.add_argument("--kind", default="pick", choices=["pick", "ok_recommended", "disagree_go", "change", "material",
                                                      "something_else", "yes_that_stands", "default"])
    p.add_argument("--source", default="page"); p.add_argument("--words"); p.add_argument("--regions")
    p.add_argument("--asset-ids"); p.add_argument("--answered-at"); p.add_argument("--standing")
    p.add_argument("--decision-version", type=int); p.add_argument("--no-tw", action="store_true")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("mirrored", help="mark a mirror done after the session ran its tool call")
    p.add_argument("project"); p.add_argument("id"); p.add_argument("what", choices=["log", "todo_watch", "page"])
    p.set_defaults(fn=cmd_mirrored)
    p = sub.add_parser("outcome"); p.add_argument("project"); p.add_argument("id"); p.add_argument("text")
    p.set_defaults(fn=cmd_outcome)
    p = sub.add_parser("due", help="past-due defaults; applies only with --responses (a fresh page read)")
    p.add_argument("project", nargs="?"); p.add_argument("--no-tw", action="store_true")
    p.add_argument("--responses", help="responses dir or file from ArtifactData query (required to apply)")
    p.set_defaults(fn=cmd_due)
    p = sub.add_parser("status"); p.add_argument("--line", action="store_true"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("sync-plan", help="the read-back steps; with --responses, what is new")
    p.add_argument("project"); p.add_argument("--responses"); p.add_argument("--apply", action="store_true")
    p.add_argument("--no-tw", action="store_true"); p.set_defaults(fn=cmd_sync_plan)
    p = sub.add_parser("tw-link", help="Todo Watch descriptions and commands; --set stores the ids")
    p.add_argument("project"); p.add_argument("--set"); p.add_argument("--task"); p.add_argument("--ledger")
    p.set_defaults(fn=cmd_tw_link)
    p = sub.add_parser("local", help="view-only local HTML copy"); p.add_argument("project"); p.set_defaults(fn=cmd_local)
    p = sub.add_parser("register", help="record the published URL")
    p.add_argument("project"); p.add_argument("url"); p.add_argument("--session"); p.set_defaults(fn=cmd_register)
    p = sub.add_parser("hook", help="settings.json command hooks: session-start (context) and stop (chat-question check)")
    p.add_argument("event", choices=["session-start", "stop"]); p.set_defaults(fn=cmd_hook)
    p = sub.add_parser("sent", help="stamp sent_at on open cards after telling the user")
    p.add_argument("project"); p.set_defaults(fn=cmd_sent)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
