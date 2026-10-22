# rensim-tools

Tools for players of **Renaissance**, the play-by-mail game run by Constantine Xanthos.
They turn your turn results and the GM's newsletters into a database you can query, check your
orders before you email them, and plan training. A Claude Code skill drafts each turn with you.

The code lives here. Your game files stay in your own folder (a private git repo works well):
nothing in this repo needs, or contains, anyone's turn reports, addresses or the GM's rules.

```
your data folder                              rensim-tools
  rensim.toml        who you are                rensim run      parse, load into DuckDB, build the views
  data/raw/turns/    turn results               rensim ask      saved questions, or your own SQL
  data/raw/newsletters/  GM newsletters         rensim orders   check orders, print the text to email
  data/raw/player_publications/  propaganda     rensim training best training for personae together
  data/claims/*.json claims from publications   rensim quality  data checks; rensim claims  quote checks
  orders/            the orders you sent        rensim dbt      dbt on the views: test, docs...
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

1. Save the turn result in `data/raw/turns/` and the newsletter in `data/raw/newsletters/`.
   File names don't matter: dates and accounts are read from inside the files.
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

## Player publications

Other players' newsletters and proclamations become **claims**, kept apart from official facts and
labelled with how firmly each is stated. Save the original in `data/raw/player_publications/`, run
`rensim extract` (needs Tesseract, LibreOffice, poppler and `pip install "rensim-tools[extract]"`)
to get its text, then write `data/claims/<name>.json` following
[`docs/claims_extraction.md`](docs/claims_extraction.md). `rensim claims` fails if any claim quotes
words that aren't in the source.

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

- **Your address is dropped.** The billing and mailing block at the top of each turn result never
  leaves the parser. Still, keep your data folder private.
- **Coordinates are degrees.minutes.** `N55.57` is 55°57' = 55.95°.
- **Place names join by key.** `PEGU`/`Pegu` and `pNompenh`/`Pnompenh` are the same place;
  `dim_places.spellings` lists every variant seen.
- **Characters are keyed by game ID**, however the name is written.
- **One account, many families.** Every turn row carries `account` and `player_family`, so a
  change of family keeps its own history; reports allies share with you load alongside yours.

## Developing

```bash
pip install -e .
python -m unittest      # runs against the made-up account in tests/fixtures
```

The tests never touch real game files; add tests against your own files in your data folder.
CI runs the tests, every command on the fixtures, `rensim dbt build`, and the dbt docs site
(downloadable from each run's summary page).

To change a view, edit its model in `renaissance/dbt/models/` (a `SELECT`, with
`{{ ref('...') }}` for other models and `{{ source('game', '...') }}` for loaded tables), and its
description and tests in the `.yml` file beside it. If an answer is meant to change, regenerate the
expected answers (see `tests/test_answers.py`) and review the diff.

MIT licence.
