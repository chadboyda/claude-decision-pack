# Card rules

One card is one decision. Write for someone reading on a phone between meetings.

- **Title**: the question in plain words, 60 characters or fewer, ends in "?". No PR, ticket, branch or file IDs (lint blocks `#123`, `ABC-123`, `PR`, `x.py`).
  - Good: "Reminder emails: start with a set date?" Bad: "Merge #123 with default dates on?"
- **BLUF** (`bluf`): "I recommend A: <one-line why>." 160 characters or fewer.
- **Options**: 2 or 3. Exactly one has `recommended: true`. `button` is one or two words (18 characters max). `result` says what happens if the user picks it (90 max). `undo`: easy or hard.
- **Doors**: `two_way` when it is easy to reverse; `one_way` otherwise. One-way doors never take a default and always need their own tap.
- **Gates**: `checkout`, `pricing`, `new_visual`, `sign_in`, `deploy`, `freeze`. A gated card has no default. Items inside a change freeze are always `gate: freeze`.
- **Defaults**: two-way, ungated cards only. Set `default_key`, `default_at` (UTC ISO) and `default_at_local` ("Wed Nov 18 6:00 PM EST", from `decision-pack` in the configured zone). Check the weekday against a calendar.
- **Cost of waiting** (`cost_of_wait`, 120 max): what is stuck while the user decides.
- **Impact** (200 max): dollars, customer effect, how to undo. **Unknowns** (200 max): what we still do not know.
- **Details** (`details_md`): PR numbers, paths and links live here and only here.
- **No bundling**: "Merge it and turn on the switch?" is two cards (lint warns on "and/then + verb").
- **Plain words**: short sentences, no jargon, no internal names the user would have to look up.
- **One final answer**: when a card changes, rewrite it in place (same id, `version + 1`). Never write "Update:", "was/now" or "Nov 12 update" (lint blocks them).
- **Earlier answer** (`history_check.result: doubtful`): set `quote_choice_key` to the option the quote picked. If that is not my pick, set `new_fact`: what changed since, one short line ("in tests about half the fixes were wrong."). Never leave a bare "Yes, that stands": the same words would mean different options on two cards.
- **Standing rule** (`standing_rule`, 200 max): set it when the answer should become a standing rule. The answer then goes to the decisions ledger on its own (when `ledger_path` is configured).
- **Already agreed or covered by a standing rule?** Do not make a card. Put it in `done_without_asking` with the user's words, and act.
- **Not a decision?** Merge order, status, FYI: put it in `fyi` as a picture with no buttons.
