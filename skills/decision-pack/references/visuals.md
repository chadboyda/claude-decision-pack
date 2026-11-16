# Pictures

Every card shows what changes. The user answers from the picture, so it must look like production.

Subagents may shoot, mark, crop, shrink and lint. Only the main session uploads or publishes.

The diagram and chart rows follow the `artifact-diagramming` and `dataviz` skills when those are installed; without them, keep plain words, high contrast and one highlighted series.

| The decision changes… | `kind` | How |
|---|---|---|
| A live page or UI | `pair` (or `before` + `after` shots) | `decision-pack shoot` prod/CI for before, dev/CI build for after, then `decision-pack pair` |
| UI that does not exist yet | `mock` | Real page patched with `--before-shot` actions, or an HTML mock with site tokens. The page labels it "Mock" |
| A choice between variants | one shot per option (`role: option-A`, `option-B`) | Tapping an option row swaps the picture |
| A flow, order, "what happens if I turn this on" | `diagram` | 3-6 boxes, plain words, HTML/SVG rendered to PNG (artifact-diagramming rules) |
| Money, counts, coverage | `chart` | matplotlib PNG with the dataviz rules, the recommended option highlighted |
| Chat, email, admin or portal output | `output` | Shot of the real rendered output; phone frame at 390 when read on a phone |
| Copy | `real_ui` or `mock` | The copy in place on the page, never bare text |
| A pure cost or timing call | `fact_strip` | A small rendered strip ("3 runs × ~$0.75 = ~$2.25") plus `no_visual_reason` |

Gated cards (checkout, pricing, new visuals, sign-in) need a before/after pair at 1440 and 390.

## Where shots go
- Card-level shots: `decisions[].shots` (before/after, chart, diagram). Option shots: `options[].shots`. The page shows the recommended option's shots first, else the card's.
- Files live in `<home>/<project>/shots/`. Put scratch files in a temp dir (`mktemp -d`) and delete them after upload.

## Shot record
`{role, width: 1440|390, kind, path (absolute), px: [w, h], callouts: [{n, box: [x, y, w, h], note}], viewed}`. `box` is in the image's own pixels so page hotspots scale exactly.

## Callouts
- Numbers are burned into the PNG (`decision-pack mark`). The legend stays on the card, not in the image.
- A note says what the user sees and what happens next. 90 characters or fewer. No `PR`, `#123`, `v2`, branch names, file names, ticket IDs like `ABC-123` (lint blocks them).
- `decision-pack crop` writes one close-up per callout (`crops/<name>-c3.png`) so "callout 3" still points at the right spot if layout moves.

## Sizes
- 1440 first, then 390. A UI card needs both widths for each role.
- `decision-pack shrink` quantizes to 256 colors, keeps 1440 CSS px wide at DPR 1, and stays under 600 KB. Crop tall pages to the part that matters (`--crop-height`).

## Looks like prod (checklist)
- No localhost bar, no dev overlays, no broken images, no lorem ipsum.
- Real-looking data (names, prices, counts), never "test" or "foo".
- Both themes when the surface has both.
- After making a PNG, **Read it**, then `decision-pack viewed <ID> <png> --project <p>`. Lint blocks unviewed shots.

## Reuse first
Copy shots an earlier artifact already published (`files: {"shots/x.png": {artifact, path}}` or `from_url` + `asset_ids`). Re-annotate only where callouts carry internal words.
