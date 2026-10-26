# rensim-tools

Tools for players of **Renaissance**, the play-by-mail game run by Constantine Xanthos.
They turn your turn results and the GM's newsletters into a database you can query, check your
orders before you email them, and plan training. A Claude Code skill drafts each turn with you.

The code lives here. Your game files stay in your own folder (a private git repo works well):
nothing in this repo needs, or contains, anyone's turn reports, addresses or the GM's rules.

```
your data folder                              rensim-tools
  rensim.toml        who you are                rensim add      save a turn result or newsletter, checked
                                               rensim fetch    the same, straight from your email
  data/raw/turns/    turn results               rensim run      parse, load into DuckDB, build the views
  data/raw/newsletters/  GM newsletters         rensim ask      saved questions, or your own SQL
  data/raw/player_publications/  propaganda     rensim orders   check orders, print the text to email
  data/claims/*.json claims from publications   rensim training best training for personae together
  data/aliases/      names you matched          rensim quality  data checks; rensim claims  quote checks
  orders/            the orders you sent        rensim match    propose look-alike names to review
                                               rensim dbt      dbt on the views: test, docs...
                                               rensim mcp      let Claude query the database
```

## Set up

```bash
pip install git+https://github.com/matt-a-t/rensim-tools
```

Make a folder for your game and put a `rensim.toml` in it:

```toml
account = "ABC"        # your GM account code (the first line of every turn result)
family = "SMITH"       # your current family
home = "Kelso"         # your home town: distances are measured from here
data_dir = "data"      # optional; relative to this file
orders_dir = "orders"  # optional

# optional: months with no turn report on purpose, and GM messages that aren't parser gaps
[quality]
known_gaps = [{ account = "ABC", from = "1546-07", to = "1546-12", why = "changed family" }]
known_gm_messages = ['Your courier was delayed\.']
```

Run the commands from that folder or anywhere below it (`rensim config` shows what it found).
They write generated files there that don't belong in git: `data/renaissance.duckdb`, plus dlt's
and dbt's working folders `.dlt_pipelines/` and `.dbt/`.
Environment variables override the file: `RENSIM_ACCOUNT`, `RENSIM_FAMILY`, `RENSIM_HOME`,
`RENSIM_DATA_DIR`, `RENSIM_ORDERS_DIR`, and `RENSIM_CONFIG` for a config file somewhere else.

## Each turn

1. `rensim add ~/Downloads/turn.txt ~/Downloads/NEWSL640` (or `rensim add -` and paste the text).
   It reads each file straight away, so one the tool can't read fails now, saves it as
   `data/raw/turns/<account>_YYYY-MM.txt` or `data/raw/newsletters/NEWSL<issue>` from what's
   inside, and replaces your billing block and mailing address with one line saying so (the fee
   lines stay; `--keep-address` keeps it all). Add `--run` to go straight on to step 2.
   Or set up [`rensim fetch`](#fetch-from-email) once and it does this from your mailbox.
2. `rensim run` (loads everything, builds the views with dbt and runs dbt's tests), then
   `rensim ask` (every saved question) or `rensim ask what_changed`, or
   `rensim ask --sql "SELECT * FROM v_personae_now"`.
3. Write your orders in `orders/<account>_YYYY-MM.txt`, with `#` lines explaining the plan:

   ```
   # April 1547: Jon trains Arya, then scouts Edinburgh; Eddard stays home as magistrate.
   /ABC *SMITH
   *Kelso .102 i101k mlEdinburgh*
   *Kelso .104 bhorse450
   ```

4. `rensim orders orders/abc_1547-04.txt` explains every code in plain English and checks it
   against your latest turn. It catches invalid codes, personae that aren't yours or start
   somewhere else, moving or selling more than you hold, buys you can't afford, recruits without
   weapons, groups out of order (transfers, sells, buys, other, movement), training pairs that
   can't happen, tabs, unknown destinations, and land moves without enough horses to carry the
   cargo. It also shows fortification gains and costs, distances, and the action fee at your last
   billed rate.
5. `rensim orders orders/abc_1547-04.txt --send` prints the exact text to email the GM.

Next turn, `rensim ask my_past_orders` shows what you ordered, with your notes.

`rensim training` plans training for personae who start the turn together: for each trainer ->
trainee pair (once per pair per turn) it picks the skill with the biggest gain (the square root of
the gap). Training runs before movement, so personae can train and still leave the same turn.

## Fetch from email

`rensim fetch` saves new turn results and newsletters straight from your mailbox, the same way
`rensim add` does: it looks at mail from the GM's address over IMAP, skips attachments that aren't
a turn or a newsletter (PDFs and so on), and leaves files already saved alone, so it's safe to run
on a schedule. Add the mailbox to `rensim.toml`:

```toml
[fetch]
sender = "gm@example.org"     # the GM's address
user = "you@gmail.com"        # the mailbox to read (or RENSIM_IMAP_USER)
host = "imap.gmail.com"       # the default; mailbox = "INBOX" and days = 45 are too
```

The password only comes from `RENSIM_IMAP_PASSWORD`. For Gmail that's an
[app password](https://myaccount.google.com/apppasswords) (it needs 2-step verification on).
Then `rensim fetch --dry-run` shows what it would save, and `rensim fetch --run` saves it and
loads it. `--days 90` or `--since 2026-09-01` looks further back.

Because it reads mail nobody has looked at, it's careful:

- It opens the mailbox read-only (nothing is marked read, moved or deleted) and checks the
  server's certificate.
- It only trusts mail the receiving server says the GM's domain signed (`dkim=pass` or
  `dmarc=pass` in the top `Authentication-Results` header), since anyone can put the GM's address
  in a From line. `--no-verify-sender` turns that off, for servers that don't add the header.
- A turn whose address it can't find is refused rather than saved with the address in.
- Attachments over 2 MB are skipped, and the password is never printed.

An app password opens your whole mailbox and can't be narrowed. To limit what it exposes, give
it a mailbox of its own: a new address, with a filter in your main mailbox forwarding the GM's mail
to it, and point `[fetch]` at that one. (Forwarding adds its own `Authentication-Results`
header, so check with `--dry-run` that the GM's mail still shows as signed.)

### On a schedule

A data folder kept in a private GitHub repo can fetch every morning and open a pull request with
anything new, so you look at it before it's merged. Put the password in a GitHub
[environment](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
called `mail` limited to the `main` branch (never in a workflow that runs on pull requests), and
allow Actions to create pull requests (Settings > Actions > General). For example,
`.github/workflows/fetch.yml`:

```yaml
name: fetch
on:
  schedule: [{ cron: "0 7 * * *" }]
  workflow_dispatch:
permissions:
  contents: write
  pull-requests: write
jobs:
  fetch:
    runs-on: ubuntu-latest
    environment: mail
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install git+https://github.com/matt-a-t/rensim-tools
      - run: rensim fetch
        env:
          RENSIM_IMAP_PASSWORD: ${{ secrets.RENSIM_IMAP_PASSWORD }}
      - name: Open a pull request with anything new
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          git add data/raw
          git diff --cached --quiet && exit 0
          git switch -c "fetch/$(date +%F)"
          git -c user.name=github-actions -c user.email=github-actions@users.noreply.github.com \
            commit -m "New turn results and newsletters from email"
          git push origin HEAD
          gh pr create --fill --base main
```

Pin the actions to a commit SHA if you like to be strict. A pull request opened with
`github.token` doesn't start your other workflows; close and reopen it, or push to it, to run
your checks.

## With Claude Code

This repo is also a Claude Code plugin marketplace with one skill, **renaissance-turn**: tell
Claude what you want this turn and it refreshes the data, plans with you, writes the orders file,
runs the checker until it's clean, and replies with the plan in plain English and then the codes
to email. In Claude Code:

```
/plugin marketplace add matt-a-t/rensim-tools
/plugin install renaissance-turn@rensim-tools
```

Put a `CLAUDE.md` in your data folder with your current plan and how you like to work; the skill
reads it first.

### Questions in plain English

`rensim mcp` lets Claude query your database directly, so you can ask "who arrived at my home
town last turn?" or "where's the cheapest grain near me?" and get an answer from your data:

```bash
pip install "rensim-tools[mcp]"        # adds the MCP SDK
```

The renaissance-turn plugin starts it for you. Without the plugin, register it once from your
data folder: `claude mcp add rensim -- rensim mcp`. Start Claude Code in your data folder (or
below it), the same as the other commands, so it finds your `rensim.toml`.

Claude gets `query` (any SQL), `describe` (the tables and views with their descriptions),
`what_changed`, `who_is_here`, `my_past_orders`, `prices` (by item, place or supply/demand), and
`ask` for any saved question. The tools only read: the database is opened read-only, and the SQL
can't touch other files. Run `rensim run` to load new turns; Claude sees them on its next question.

## Player publications

Other players' newsletters and proclamations become **claims**, kept apart from official facts and
labelled with how firmly each is stated. Save the original in `data/raw/player_publications/`, run
`rensim extract` (needs Tesseract, LibreOffice, poppler and `pip install "rensim-tools[extract]"`)
to get its text, then write `data/claims/<name>.json` following
[`renaissance/claims_extraction.md`](renaissance/claims_extraction.md). `rensim claims` fails if any claim quotes
words that aren't in the source.

Claude can write the first draft for you:

```bash
pip install "rensim-tools[llm]"      # adds the Anthropic SDK; set ANTHROPIC_API_KEY
rensim extract-claims player_publications/grimani_1547-03.pdf
```

If you have [Claude Code](https://claude.com/claude-code) installed and logged in, you can skip the
API key: with `ANTHROPIC_API_KEY` unset (or with `--backend claude-code`), the request goes through
`claude -p` and counts against your own Claude plan's usage. That's meant for running the tool
yourself on your own login; for anything else, use an API key.

It sends the text and the same guide to Claude and writes `data/claims/drafts/<name>.json`.
Any claim with a quote that isn't in the source, or with a missing field or unknown type or hedge, is
dropped and listed. Drafts aren't loaded, so review the draft, fix it, and move it up to `data/claims/`.
`--game-date`, `--publisher`, `--family` and similar flags set the source details when the text doesn't give them.

To see how good the drafts are, score one against a claims file you wrote by hand:
`rensim eval-claims data/claims/x.json --draft data/claims/drafts/x.json`. Leave out `--draft` to
extract the publication again and score that. A drafted claim matches a hand-written one when it has
the same type and the quotes overlap. Precision is the share of drafted claims that match, and recall
is the share of yours it found. Hedges are compared on matched pairs.

## Matching names

The same family, place or character is often written differently: OCR typos in a scanned
publication, regional spellings, a newsletter naming a character without their game ID. Look-alike
names can also be different families (Habsburg, HABSBOURG and HAPSBOURG hold separate towers in
one game), so nothing is merged until you say so:

```bash
pip install "rensim-tools[match]"   # adds Splink
rensim match                        # writes data/aliases/proposed.csv
```

Each row of `proposed.csv` is a pair with a match probability and why (spelling, places in
common, where each name was seen). Put `y` or `n` in its `decision` column, then
`rensim match --apply`: accepted pairs go to `data/aliases/families.csv`, `places.csv` or
`personae.csv`, rejected ones to `rejected.csv` so they aren't proposed again. Run `rensim run`
to use them. Two different names that both appear in the GM's own reports are never proposed: the
GM doesn't misspell.

To see how well it does on your game, label some pairs by hand in a CSV
(`kind,name,match,persona_id,is_match`, with `kind` one of family / place / persona and
`is_match` 1 or 0) and run `rensim match --score labels.csv` for precision and recall.
`--threshold` changes how likely a pair must be to be proposed (default 0.1). How the scoring
works is explained at the top of [`renaissance/match.py`](renaissance/match.py).

## Tables

| Table | One row per |
|---|---|
| `sources` | file loaded: kind, game date, real date, reliability |
| `turn_persona_status` | your character per turn: morale, popularity, location, office |
| `turn_persona_skills` / `_assets` / `_allies` | skill / asset / ally per character per turn |
| `turn_location_status` | location per turn: defenses, tariffs, population, wealth, loyalty |
| `turn_location_ingress` / `_egress_denied` | family holding ingress / denied egress, per location per turn |
| `turn_location_officials` | office holder per location per turn, with how the magistrate is chosen |
| `turn_location_towers`, `turn_location_roads` | family towers; roads and their length |
| `turn_market` | good demanded or supplied per turn, with price |
| `turn_presence` | character seen inside/outside a location (anyone's), with troops and ships |
| `turn_lookups` | answer to a "where is X" order, with miles from home |
| `news_events`, `news_sieges`, `news_siege_results`, `news_notices` | the GM's newsletter |
| `orders_sent` | each code you sent, with its plain-English meaning |
| `claims`, `claim_market` | a claim from a player publication, with its verbatim quote |
| `ref_*` | an ally's compiled city and character data, if you have one |
| `dim_places`, `dim_personae`, `dim_families` | each place / character / family once |
| `my_settings` | your account, family and home, for the views |
| `unparsed_lines` | any line no pattern matched, so nothing is silently dropped |

The views are a star schema over those tables (see [Data model](docs/architecture.md#data-model)),
built by the [dbt](https://docs.getdbt.com/) project in [`renaissance/dbt/`](renaissance/dbt/):

- Dimensions: `dim_sources`, `dim_turns` (one row per game month), plus the history of offices,
  `dim_persona_offices` and `dim_place_officials`.
- Facts: `fact_persona_status`, `fact_persona_skills`, `fact_persona_assets`, `fact_presence`,
  `fact_lookups`, `fact_market_prices`, `fact_events`, `fact_sieges`, `fact_orders`, `fact_claims`.

Analysis views on top: `v_turns`, `v_personae_now`, `v_persona_changes`, `v_skill_changes`, `v_asset_changes`,
`v_presence_changes`, `v_market_changes`, `v_roads`, `v_events`, `v_claims`, `v_family_sources`,
`v_markets` (every known price, freshest source per place, with miles from home).
How it's built and why: [`docs/architecture.md`](docs/architecture.md); game terms:
[`docs/glossary.md`](docs/glossary.md).

`rensim dbt` runs any dbt command against your database: `rensim dbt test` checks every fact is
unique at its grain, every key finds its dimension and categories hold known values;
`rensim dbt docs generate --static` writes a browsable site of every table, view, column and test,
with a lineage graph, to `.dbt/target/static_index.html`.

## Notes on the data

- **Your address is dropped.** `rensim add` (and `rensim fetch`) takes the billing and mailing block at the top of each
  turn result out of the saved file, and the parser never reads it anyway. `rensim quality` fails
  on a turn file that still has it; `rensim add --force data/raw/turns/*.txt` cleans up files saved
  before `rensim add` did this. Still, keep your data folder private.
- **Coordinates are degrees.minutes.** `N55.57` is 55°57' = 55.95°.
- **Place names join by key.** `PEGU`/`Pegu` and `pNompenh`/`Pnompenh` are the same place;
  `dim_places.spellings` lists every variant seen, including ones you matched (`rensim match`).
- **Characters are keyed by game ID**, however the name is written. Newsletter actors and
  claim subjects get one (`actor_persona_id`, `subject_persona_id`) once you've matched the name.
- **One account, many families.** Every turn row carries `account` and `player_family`, so a
  change of family keeps its own history; reports allies share with you load alongside yours.

## Developing

```bash
pip install -e ".[match]"
python -m unittest      # runs against the made-up account in tests/fixtures
```

The tests never touch real game files; add tests against your own files in your data folder.
CI runs the tests, every command on the fixtures, `rensim dbt build`, and the dbt docs site
(downloadable from each run's summary page).

The tests check what the MCP server returns, not what Claude makes of it. To check that by hand
after changing a tool or its description (it uses your Claude plan), run in `tests/fixtures`:

```bash
rensim run
claude -p "who arrived at my home town last turn?" --strict-mcp-config \
  --mcp-config '{"mcpServers":{"rensim":{"command":"rensim","args":["mcp"]}}}' \
  --allowedTools 'mcp__rensim__*' < /dev/null
```

The answer should be Ramsay BOLTON, arriving in Carlisle with 200 troops, and nobody else.

To change a view, edit its model in `renaissance/dbt/models/` (a `SELECT`, with
`{{ ref('...') }}` for other models and `{{ source('game', '...') }}` for loaded tables), and its
description and tests in the `.yml` file beside it. If an answer is meant to change, regenerate the
expected answers (see `tests/test_answers.py`) and review the diff.

MIT licence.
