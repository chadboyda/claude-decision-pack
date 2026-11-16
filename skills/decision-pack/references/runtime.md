# Runtime (contract 0.2.96)

## Capabilities (first publish)
```json
{"db": {"rules": [
   {"path": "meta", "read": "view", "write": "admin"},
   {"path": "decisions", "read": "view", "write": "admin"},
   {"path": "responses", "read": "view", "write": "owner"}]},
 "user": {}, "comments": {"customAnchors": true}, "assets": {}}
```
- Refused with `capabilities.comments: unavailable`: republish with `"comments": {}`. Cards and callout hotspots carry `data-comment-target`; region boxes still live in the response doc.
- Republishes omit `capabilities` to keep them.

## Docs
- `meta/pack`: project, title, done_without_asking, fyi (public shots), timezone (IANA name the page formats times in), updated_at.
- `decisions/<id>`: the public card (no local paths; each shot has `src` = asset URL or `shots/<file>`).
- `responses/<id>`: written by the page (owner only).

## Page behavior
- `claude.use()` may return null for any capability; the page still renders. db null or not owner: "View only here. Reply in chat to answer."
- 10 s with no decisions snapshot: "Could not load decisions. Reload, or tell Claude 'sync <project>'."
- One `set()` per real change (tap, note blur, pin, upload). Never from render or a snapshot callback.
- Send to Claude: `comments.canSendToClaude()` is read at load and re-checked (debounced, 4 s) whenever the send bar shows unsent answers, when the tab becomes visible, and after a rejected send (`claude_unavailable`, `forbidden`). The cached value serves only the synchronous click path. `sendToClaude({anchor, text})` runs synchronously in the click handler with an anchor built ahead by `anchorFor`. `onSendToClaude` is not subscribed. Text: `[decision-pack <project>] Q-1 A (my pick) · Q-2 B note:"…" · Q-4 callout 3 on after-1440` (under 4 KiB).
- "Take all my picks" opens an in-page sheet (cards and picks, Take / Cancel, 48 px). Never `window.confirm()`: modals may be blocked in the artifact iframe. The lightbox has Zoom and Actual size in place, no `target=_blank` popup. If zoom is not enough, `decision-pack local <project>` gives a local copy to open in Chrome.
- `customAnchors` names: `Q-2` for a card, `Q-2:after-1440#c3` for a callout.

## Seed
`decision-pack seed <project> [--versions v.json]` writes `seed/docs/*.json` and `seed/batch-NNN.json` (`{"writes": [...]}`, each entry `{op, collection, doc_id, file_path[, if_version]}`), split at 50 writes and about 1 MiB. Existing docs need `if_version` from a fresh `ArtifactData list` of `meta` and `decisions`; pass them as `{"meta/pack": 2, "decisions/Q-1": 3}`. One unpinned write to an existing doc makes the whole batch write nothing, so `seed` refuses a versions map without `meta/pack` unless `--no-meta` (card-only re-seed, meta left out).

## Verify (once, after first publish)
`ArtifactData list decisions`, `list responses`, and `list responses as_level=view`. Report in one line what was and was not exercised.

## Watch ownership
Only the main session watches. Claim a watch only when the ArtifactComments result says auto-replies are armed. A publish arms it; `watch` alone arms it only for a link the user pasted in their own message. So in a session that did not publish the pack: `ArtifactComments watch`, read the result, and if it is not armed, republish the page (same `file_path`, the pack `url`, no `capabilities` field) or ask the user in one line to paste the pack link. Without an armed watch, Send to Claude posts a comment that wakes nobody; the answers still sit in `responses/*` for the next sync.
