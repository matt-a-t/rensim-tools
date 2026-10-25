---
name: renaissance-turn
description: "Plan and write a Renaissance play-by-mail turn (orders for the GM) from the player's data folder: plain-English plan, then checked codes to email."
---

# Making a Renaissance turn

Use when the player asks to make, plan, draft or change their orders for a turn of Renaissance.
It needs [rensim-tools](https://github.com/matt-a-t/rensim-tools) installed (`pip install
git+https://github.com/matt-a-t/rensim-tools`) and their data folder: the one with `rensim.toml`,
`data/raw/turns/` and `orders/`. If the folder has a CLAUDE.md, read it first: it holds the
player's current plan and how they like to work, and it wins over the defaults here.

## 1. Get up to date
- `rensim config` shows whose account this is (account code, family, home). Use those values
  below wherever this says `<account>`; if no config is found, ask for them and write `rensim.toml`.
- If they attached a new turn result or newsletter, save it in `data/raw/turns/` or
  `data/raw/newsletters/` first (the file name doesn't matter; dates are read from inside).
- `rensim run`, then `rensim ask --sql "select * from v_personae_now order by persona_id"` for who
  is where, and `rensim ask nearest_places` / `rensim ask what_changed` as needed.
- If the `rensim` MCP tools are available (`what_changed`, `who_is_here`, `prices`, `query`...), use
  them for questions about the data instead of `rensim ask`: they return rows you can read directly.
  They are read-only, so still use `rensim run` to load new files.
- Read the last orders sent (`rensim ask my_past_orders`, or the newest `orders/<account>_YYYY-MM.txt`)
  so the new turn follows on from the plan.

## 2. Plan with them, in plain English
- Take their instructions as the goal (e.g. "send everyone somewhere different nearby", "train as much as possible").
- Count personae from the turn report, not from what they say; if the numbers differ, say so and say what you assumed.
- Defaults unless they say otherwise:
  - Personae starting together train each other (`rensim training`); training runs before movement, so they can still leave.
  - A magistrate stays home: they collect the town's tariffs only while present.
  - Scouts stop outside the destination (`ml<Place>*`) unless the family has ingress there.
  - Land moves up to 200 miles are free of attrition; horses carry 2 cargo each and slaves 1/4.
  - Markets: buy and sell only where the persona starts the turn.
  - One new location setup request per turn, as a separate note with its fee.
- Don't explain codes and don't discuss cost unless they ask.

## 3. Write and check
- Write `orders/<account>_YYYY-MM.txt` (the game month being ordered). Start it with `#` lines
  describing the plan in plain English: these notes are what "what did I do 3 months ago?" is answered from.
- `rensim orders orders/<account>_YYYY-MM.txt` must report no errors; fix and re-run until it does.
  It checks code shapes, the order of groups (transfers, sells, buys, other, movement), holdings,
  affordability, training pairs, distances and cargo on land moves.

## 4. Reply in this order
1. What is going to happen, persona by persona, in plain English (names, not numbers), plus the due date.
2. The codes to copy into an email to the GM: the output of `rensim orders <file> --send`, in a code block.

## 5. Keep it
- If the data folder is a git repository, commit the orders file the way its CLAUDE.md says
  (for example on a branch with a pull request). If they change the plan, update the same file:
  the saved file should always be the version actually sent.

## Looking back
"What did I do in <month>?" → `rensim ask my_past_orders` (table `orders_sent`), plus the `#` notes in the
`sources` table (`kind = 'orders'`), and the following turn report for what actually happened.
