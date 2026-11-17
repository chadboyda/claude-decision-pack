# decision-pack

A Claude Code plugin. When Claude needs your input, feedback or a decision, it stops asking in chat
text. It builds one private claude.ai page (an Artifact) per project, with one card per decision:

- the question in plain words, and Claude's pick with one line of why;
- a picture per option, with numbered callouts burned in (tap one to point at it);
- 2-3 big answer buttons, "Something else…", "Disagree but go", "Show me more first";
- a note box (dictation, paste an image), and "Point at it" to drop a pin or draw a box on the picture;
- one "Send to Claude" press that wakes the session with every saved answer and its context.

Answers save to the page's database as you tap, so nothing is lost when a session ends. "ok" in chat
takes all of Claude's picks on two-way (easy to undo) cards.

Spec: `SPEC.md`. How Claude runs it: `skills/decision-pack/SKILL.md`.

## Parts
- `skills/decision-pack/SKILL.md` and `references/` — when to run, the act-or-ask gate, card and
  picture rules, the answer round trip, runtime details, a worked example.
- `assets/pack.html` — the one page template. All content comes from the artifact db
  (`meta/pack`, `decisions/*`); answers go to `responses/<id>`. Light and dark, phone first.
- `bin/decision-pack` → `lib/dp.py` — `init, check, add, shoot, mark, pair, crop, shrink, viewed,
  lint, render, seed, ingest, record, mirrored, outcome, due, status, sync-plan, tw-link, local,
  register, sent, hook`. Stdlib plus Pillow; `shoot` needs Playwright with Chromium.
- `lib/schema.py` — pack.json checks. `lib/vendor/` — pinned copies of the screenshot and
  annotation helpers (`shoot.py`, `annotate.py`, `_page.py`).
- `hooks/hooks.json` — a SessionStart hook (names packs to read back) and a Stop hook (flags a chat
  question with no pack link), plus `hooks/register.ts`: a status line ("3 open · next due 5:00 PM
  EST · 1 not closed", UI only) and `/decision-pack [status | sync <project> | due | local <project>]`.
- Tests: `cd lib && python3 -m pytest -q test_page.py test_dp.py` (unit tests plus headless page tests).

## Requirements
- Claude Code with the claude.ai Artifact, ArtifactData and ArtifactComments tools. Publishing,
  seeding and reading answers back all go through them. Without them, `decision-pack local <project>`
  still writes a view-only copy to open in a browser, and answers come in through chat (`ingest`).
- The `artifact-capabilities` and `artifact-design` skills, when installed, are loaded before the
  first publish. The skill skips them when they are missing.
- Python 3 with Pillow for `mark`, `pair`, `crop` and `shrink`; Playwright with Chromium for
  `shoot`. Every entry point runs `python3 -I`, which ignores user site-packages and `PYTHONPATH`,
  so install Pillow and Playwright into the interpreter's own site-packages
  (`python3 -m pip install pillow playwright`, not `--user`).
- The status line and the `/decision-pack` command come from the hooks module `hooks/register.ts`.
  On a Claude Code build without hooks-module support, the skill and both command hooks still work.

## Install
This repo is its own marketplace. In Claude Code 2.1.275 or later, one line:
```
/plugin install decision-pack --marketplace chadboyda/claude-decision-pack
```
On older versions:
```
claude plugin marketplace add chadboyda/claude-decision-pack
claude plugin install decision-pack@decision-pack
```
Then `/reload-plugins` in an open session. To try it without installing:
`claude --plugin-dir /path/to/decision-pack`.

## Configuration
Everything works with no configuration. Each setting resolves in this order: environment variable,
then the config file, then the default.

Config file: `${XDG_CONFIG_HOME:-~/.config}/decision-pack/config.json`
```json
{
  "home": "~/decision-packs",
  "timezone": "America/New_York",
  "ledger_path": "~/notes/decisions-ledger.md",
  "memory_dir": "~/notes/memory",
  "transcripts_dir": "~/.claude/projects/<project-folder>",
  "todo_watch_bin": "/usr/local/bin/tw"
}
```

| Key | Env override | Default | What it does |
|---|---|---|---|
| `home` | `DECISION_PACK_HOME` | `~/.decision-pack` | Where packs, the answer log and the registry live. |
| `timezone` | `DP_TIMEZONE` | the system's local zone | IANA zone for every time label. Labels use the zone's own abbreviation (EST, CET, …). |
| `ledger_path` | `DP_LEDGER` | off | A markdown file of standing rules. `check` searches it; answers on cards with a `standing_rule` append a line to it. |
| `memory_dir` | `DP_MEMORY_DIR` | off | A folder of markdown notes `check` searches for earlier decisions. |
| `transcripts_dir` | `DP_TRANSCRIPTS_DIR` | off | A Claude Code transcript folder (`*.jsonl`); `check` searches your own turns in it. |
| `todo_watch_bin` | `DP_TW_BIN` | off | Path to a Todo Watch `tw` CLI. When set, decided cards close their linked task. |
| `stop_hook` | `DP_STOP_HOOK` | on | `"off"` turns off the Stop hook. It runs in every project once the plugin is enabled. |

Paths accept `~` and `$VARS`. An unset optional key turns that feature off cleanly: `check` skips the
source, `record` skips the ledger line or task close, and nothing reports as "not closed" because of it.

Other switches: `DP_NOW` (UTC ISO) fixes "now" for tests.

## Data
Under `home`:
```
registry.json              project -> artifact URL, owner session, template version
decisions.jsonl            every answer, append-only
<project>/pack.json        source of truth for the cards
<project>/shots/           PNG and JPEG shots, callouts, crops/
<project>/seed/            ArtifactData batch files (made by seed/render)
<project>/outbox/          page updates and response docs for the session to write
<project>/local.html       view-only copy for zooming in a browser (never published)
```
Packs written by older versions used `*_ct` time-label keys. They are read as `*_local`, and the page
template accepts both.

## Hooks
The plugin registers both hooks in `hooks/hooks.json`; nothing to add to settings.json.
- `session-start` prints into context each published pack with open or unclosed cards, what is past
  due, and the `sync-plan` command to run. It never applies a default.
- `stop` blocks the end of a turn once when the final message asks the user a question in chat
  ("…?", "Want me to…", "Should I…") with no pack link. The retry carries `stop_hook_active`, so it
  can never loop.

## Send to Claude needs an armed watch
Send to Claude posts a comment; it wakes a session only when that session's watch on the pack is
armed. Publishing arms it. `ArtifactComments watch` arms it only for a link the user pasted. So a
later session runs step 0 of `decision-pack sync-plan <project>`: watch, read the result, and if it is
not armed, republish the page (same file and url, no `capabilities`) or ask the user to paste the
link. Without an armed watch the answers still save on the page, and the next sync picks them up.
