# Answers

## Response doc (the page writes it)
`responses/<decision_id>`, explicit doc id, never `add()`:
```json
{"decision_id":"Q-2","decision_version":1,
 "kind":"pick | ok_recommended | disagree_go | change | material | something_else | yes_that_stands (always with the choice_key the earlier words picked)",
 "choice_key":"B","choice_label":"Pro only","was_recommended":false,"note":"only Pro, not Starter",
 "regions":[{"shot_role":"after","width":1440,"callout_n":3,"box_px":[412,180,220,90],"anchor":"Q-2:after-1440#c3"}],
 "asset_ids":["<32 hex>"],"source":"page","answered_by":"u_...",
 "answered_at":"2026-11-16T21:12:04Z","answered_at_local":"Mon Nov 16 4:12 PM EST","sent_at":null}
```
A drawn box has `callout_n: null`, `near_callout: <n>` and a `box_px` in image pixels; a pin has width and height 0.

The page owns `responses/*`. Claude writes sync state on `decisions/<id>` (`status`, `outcome`, `synced_answer_at`) so a later page `set()` cannot wipe it.

## Answer log
`<home>/decisions.jsonl`, append-only, one row per answer:
`{id, project, question, choice_key, choice_label, rejected, kind, words (verbatim), regions, asset_ids, source, answered_at, answered_at_local, decision_version, standing, changed_from}`.
`source` is one of page, todo-watch, chat, comment, default. `record --source agent` exits non-zero: an agent's spec is never the user's call.

## Mapping
- **Page**: doc id = decision id. `decision_version` lower than the card's version: the row is logged as `stale`, the card stays open; ask about that card only, on the page.
- **Todo Watch** (optional): a `[todo-watch message]` with the `D-id:` line. Run `decision-pack record <p> <id> --choice X --source todo-watch --words "<the user's words>"`, then write `responses/<id>` yourself so page and log agree.
- **Chat**: `decision-pack ingest <p> --text "<the user's words>" --apply`.
  - "ok", "yes", "sure", "nice", "looks good", "ship it": my pick on every open two-way card. One-way cards stay open.
  - "1. yes 2. B 3. no": by card order. "yes" = my pick; a letter = that option; "no" = the only non-pick option, else unclear.
  - A line that does not map: unclear for that card only. Never spread a "yes" across cards.
- **Conflicts**: the newest answer wins on any surface, logged with `changed_from`. Never ask which one was meant.

## Record order (once per answer, before acting)
1. Log row (`mirrors.log`).
2. Standing rule (only when `ledger_path` is configured): a card with `standing_rule` writes one ledger line when a page, chat or Todo Watch answer decides it (`- <rule>: <the pick> — "<the user's words>" (Nov. 16, 2026)`), inside `sync-plan --apply`. For an answer that sets a rule with no card field, `record --standing "<rule>"` writes `- <rule> — "<the user's words>" (Nov. 16, 2026)`.
3. Close the Todo Watch child when `todo_watch_bin` is configured: `tw task done L<n> --reason already_done --evidence "<the user's words>"` (`mirrors.todo_watch`; true when the integration is off).
4. `ArtifactData update decisions/<id>` with the printed file and a fresh `if_version`; then `decision-pack mirrored <p> <id> page`.
5. Reply on and resolve the comment thread, if one was sent to Claude.

`decision-pack status` lists decided cards with a mirror still false ("N not closed").
