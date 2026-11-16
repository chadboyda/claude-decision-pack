# Example: a text-only review section, rewritten as a pack

Before ("Decisions only you can make" in a long review doc): four text blocks under one heading. Block 4 bundled three asks (create a chat channel, rerun test jobs, a list of feature switches). No block had a picture of its own, no answer buttons and no place to point at the screen. PR numbers and environment names sat in the question text.

After: `<home>/billing-revamp/pack.json`, six cards plus one FYI picture.

| Old block | New card | Picture | Door, default |
|---|---|---|---|
| 1. Reminder emails: default date or not | Q-1 "Reminder emails: start with a set date?" | Real reminder email, 6 callouts; callout 4 is the date line | two-way, A at Wed Nov 18 6:00 PM EST |
| 2. New tiers on Starter and Pro | Q-2 "New tiers on Starter and Pro?" | Real pricing card; callout 6 on the button customers get refused on | two-way, needs a tap |
| 3. Five of 36 checks still fail | Q-3 "Five of 36 checks still fail: fix now or later?" | 36-cell grid, the 5 failures outlined and numbered | two-way, A by default |
| 4a. Create a digest channel | Q-4 "Create a channel for the daily digest?" | The digest rendered from its real payload | two-way, needs a tap |
| 4b. Rerun 3 test jobs | Q-5 "Rerun the 3 test jobs (about $2.25)?" | Fact strip of the three costs; "You said yes to A on Sep 30" | two-way, A by default |
| 4c. Switches | Q-6 "Turn on the 4 billing switches?" | Admin page at 1440 and 390, customer home card | one-way, needs a tap |
| Merge order | FYI | 7-step diagram, no buttons | none |

What changed, rule by rule:
- One decision per card (block 4 became Q-4, Q-5, Q-6).
- Titles are plain questions; environment names and PR numbers moved to Details.
- Every card leads with "I recommend X: why".
- Burned-in legends with internal words (endpoint names, function names) were cropped off; plain callout notes live on the card.
- History check on each: Q-5 matched an earlier rule about rerunning jobs (Sep 30) and Q-6 matched "turn flags on when live" (Nov 6), so both show "You said yes to A on …" with a "Keep my … answer: A" button (it saves A) instead of a fresh ask.
