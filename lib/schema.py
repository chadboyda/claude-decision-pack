"""pack.json structure checks (stdlib only).

validate(pack) returns a list of "path: problem" strings for the field rules in SPEC.md section 5.
Card-quality rules (titles, pictures, defaults, banned words) live in dp.lint, which calls this first.
"""
import re

PROJECT_RE = re.compile(r"^[a-z0-9-]+$")
ID_RE = re.compile(r"^[A-Z]{1,4}-[0-9]+[a-z]?$")
DOORS = ("one_way", "two_way")
GATES = ("none", "checkout", "pricing", "new_visual", "sign_in", "deploy", "freeze")
STATUSES = ("open", "decided", "defaulted", "needs_material", "moot")
KINDS = ("real_ui", "mock", "pair", "diagram", "chart", "output", "fact_strip")
WIDTHS = (1440, 390)
HISTORY_RESULTS = ("none", "doubtful")
KEYS = ("A", "B", "C")

DECISION_REQUIRED = ("id", "order", "version", "title", "bluf", "options", "door", "cost_of_wait",
                     "history_check", "status")
OPTION_REQUIRED = ("key", "button", "result", "undo", "shots")
SHOT_REQUIRED = ("role", "width", "kind", "path", "viewed")


def _str(v):
    return isinstance(v, str)


def _maxlen(errs, where, v, n):
    if _str(v) and len(v) > n:
        errs.append(f"{where}: {len(v)} characters, max {n}")


def validate_shot(s, where):
    errs = []
    if not isinstance(s, dict):
        return [f"{where}: not an object"]
    for k in SHOT_REQUIRED:
        if k not in s:
            errs.append(f"{where}.{k}: missing")
    if "width" in s and s["width"] not in WIDTHS:
        errs.append(f"{where}.width: must be 1440 or 390")
    if "kind" in s and s["kind"] not in KINDS:
        errs.append(f"{where}.kind: must be one of {', '.join(KINDS)}")
    if "path" in s and not (_str(s["path"]) and s["path"].startswith("/")):
        errs.append(f"{where}.path: must be an absolute path")
    if "viewed" in s and not isinstance(s["viewed"], bool):
        errs.append(f"{where}.viewed: must be true or false")
    px = s.get("px")
    if px is not None and not (isinstance(px, list) and len(px) == 2 and all(isinstance(x, int) for x in px)):
        errs.append(f"{where}.px: must be [width, height] integers")
    for i, c in enumerate(s.get("callouts") or []):
        cw = f"{where}.callouts[{i}]"
        for k in ("n", "box", "note"):
            if k not in c:
                errs.append(f"{cw}.{k}: missing")
        box = c.get("box")
        if box is not None and not (isinstance(box, list) and len(box) == 4
                                    and all(isinstance(x, (int, float)) for x in box)):
            errs.append(f"{cw}.box: must be [x, y, w, h] numbers")
        _maxlen(errs, f"{cw}.note", c.get("note"), 90)
    return errs


def validate_option(o, where):
    errs = []
    if not isinstance(o, dict):
        return [f"{where}: not an object"]
    for k in OPTION_REQUIRED:
        if k not in o:
            errs.append(f"{where}.{k}: missing")
    if "key" in o and o["key"] not in KEYS:
        errs.append(f"{where}.key: must be A, B or C")
    if o.get("undo") not in (None, "easy", "hard"):
        errs.append(f"{where}.undo: must be easy or hard")
    _maxlen(errs, f"{where}.button", o.get("button"), 18)
    _maxlen(errs, f"{where}.result", o.get("result"), 90)
    for i, s in enumerate(o.get("shots") or []):
        errs += validate_shot(s, f"{where}.shots[{i}]")
    return errs


def validate_decision(d, where):
    errs = []
    if not isinstance(d, dict):
        return [f"{where}: not an object"]
    for k in DECISION_REQUIRED:
        if k not in d:
            errs.append(f"{where}.{k}: missing")
    if "id" in d and not (_str(d["id"]) and ID_RE.match(d["id"])):
        errs.append(f"{where}.id: must look like LP-2")
    if "version" in d and not (isinstance(d["version"], int) and d["version"] >= 1):
        errs.append(f"{where}.version: must be an integer >= 1")
    if "order" in d and not isinstance(d["order"], int):
        errs.append(f"{where}.order: must be an integer")
    _maxlen(errs, f"{where}.title", d.get("title"), 60)
    _maxlen(errs, f"{where}.bluf", d.get("bluf"), 160)
    _maxlen(errs, f"{where}.cost_of_wait", d.get("cost_of_wait"), 120)
    _maxlen(errs, f"{where}.impact", d.get("impact"), 200)
    _maxlen(errs, f"{where}.unknowns", d.get("unknowns"), 200)
    _maxlen(errs, f"{where}.standing_rule", d.get("standing_rule"), 200)
    if d.get("standing_rule") is not None and not _str(d.get("standing_rule")):
        errs.append(f"{where}.standing_rule: must be a string or null")
    if "door" in d and d["door"] not in DOORS:
        errs.append(f"{where}.door: must be one_way or two_way")
    if d.get("gate") is not None and d["gate"] not in GATES:
        errs.append(f"{where}.gate: must be one of {', '.join(GATES)}")
    if "status" in d and d["status"] not in STATUSES:
        errs.append(f"{where}.status: must be one of {', '.join(STATUSES)}")
    opts = d.get("options")
    if opts is not None:
        if not isinstance(opts, list):
            errs.append(f"{where}.options: must be a list")
        else:
            for i, o in enumerate(opts):
                errs += validate_option(o, f"{where}.options[{i}]")
    hc = d.get("history_check")
    if hc is not None:
        if not isinstance(hc, dict):
            errs.append(f"{where}.history_check: must be an object")
        else:
            for k in ("searched", "result", "at"):
                if k not in hc:
                    errs.append(f"{where}.history_check.{k}: missing")
            if "result" in hc and hc["result"] not in HISTORY_RESULTS:
                errs.append(f"{where}.history_check.result: must be none or doubtful")
            _maxlen(errs, f"{where}.history_check.new_fact", hc.get("new_fact"), 200)
            if hc.get("quote_choice_key") is not None and hc["quote_choice_key"] not in KEYS:
                errs.append(f"{where}.history_check.quote_choice_key: must be A, B or C")
    for i, s in enumerate(d.get("shots") or []):
        errs += validate_shot(s, f"{where}.shots[{i}]")
    return errs


def validate(pack):
    errs = []
    if not isinstance(pack, dict):
        return ["pack: not an object"]
    for k in ("project", "title", "template_version", "decisions"):
        if k not in pack:
            errs.append(f"pack.{k}: missing")
    if "project" in pack and not (_str(pack["project"]) and PROJECT_RE.match(pack["project"])):
        errs.append("pack.project: lower-case letters, digits and dashes only")
    _maxlen(errs, "pack.title", pack.get("title"), 60)
    for i, row in enumerate(pack.get("done_without_asking") or []):
        for k in ("text", "words", "date_local", "source"):
            if k not in row:
                errs.append(f"pack.done_without_asking[{i}].{k}: missing")
    for i, f in enumerate(pack.get("fyi") or []):
        if "title" not in f:
            errs.append(f"pack.fyi[{i}].title: missing")
        if "shot" not in f:
            errs.append(f"pack.fyi[{i}].shot: missing")
        else:
            errs += validate_shot(f["shot"], f"pack.fyi[{i}].shot")
    ids = set()
    for i, d in enumerate(pack.get("decisions") or []):
        errs += validate_decision(d, f"decisions[{i}]")
        if isinstance(d, dict) and d.get("id") in ids:
            errs.append(f"decisions[{i}].id: duplicate {d.get('id')}")
        if isinstance(d, dict):
            ids.add(d.get("id"))
    return errs
