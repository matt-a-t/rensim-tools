"""Load the normalized tables into DuckDB with dlt, the open-source Python loading library. The
views on top are dbt models (renaissance/dbt/), built by `rensim run` after the load.

Every run reloads everything from the raw files (write_disposition="replace"), so the database is
always a pure function of the raw files: fix a parser, rerun, done.
"""
from __future__ import annotations

from pathlib import Path

from .settings import BASE

# Schema inside the database. Must differ from the .duckdb file name, which DuckDB uses as
# the catalog name ("renaissance.renaissance" would be ambiguous). The dbt models are built here
# too, and their views name tables as "renaissance"."game"."...", so keep the file's name.
DATASET = "game"

# Tables the dbt models use that only some players have rows for (no newsletters yet, no claims,
# no ally's reference, no saved orders). They're created empty so the models and questions still run.
OPTIONAL_TABLES = {
    "claim_market": "source_id text, claim_id text, game_date text, location text, side text, item text, bq double, "
                    "price bigint, location_key text",
    "claims": "source_id text, claim_id text, claim_type text, subject_name text, subject_given text, "
              "subject_family text, subject_place text, predicate text, object text, place text, game_date text, "
              "hedge text, claimed_by text, quote text, refs text, lat_text text, lon_text text, data_json text, "
              "place_key text, subject_place_key text, subject_family_key text",
    "news_events": "source_id text, issue bigint, game_month text, event_type text, actor text, actor_given text, "
                   "actor_family text, actor_title text, place text, place_from text, place_to text, miles bigint, "
                   "quantity bigint, target_family text, truncated bool, raw_text text, seq bigint, place_key text, "
                   "place_from_key text, place_to_key text, actor_family_key text, target_family_key text, route_key text",
    "news_siege_results": "source_id text, issue bigint, game_month text, place text, outcome text, attrition_pct bigint, "
                          "detail text, place_key text",
    "news_sieges": "source_id text, issue bigint, game_month text, place text, besieger text, besieger_given text, "
                   "besieger_family text, besieger_title text, raw_text text, place_key text, besieger_family_key text",
    "ref_city_market": "source_id text, city text, as_of text, side text, item text, bq double, price bigint, "
                       "quantity bigint, city_key text",
    "turn_lookups": "source_id text, game_date text, account text, player_family text, family text, place text, "
                    "is_set_up bool, lat double, lon double, place_key text, family_key text, miles_from_home double",
    "orders_sent": "source_id text, game_month text, account text, family text, persona_id bigint, "
                   "start_location text, line_no bigint, seq bigint, code text, order_group text, explanation text, "
                   "start_location_key text",
    "turn_location_officials": "source_id text, game_date text, account text, player_family text, location text, "
                               "role text, holder text, tower bigint, selection_code text, selected_by text, "
                               "location_key text",
}


DUCKDB_TYPES = {"bigint": "BIGINT", "double": "DOUBLE", "bool": "BOOLEAN", "text": "VARCHAR"}


def create_empty_tables(con, tables: dict[str, list[dict]]) -> None:
    """Create each OPTIONAL_TABLES table that has no rows this run (replacing any stale copy)."""
    for name, cols in OPTIONAL_TABLES.items():
        if tables.get(name):
            continue
        col_defs = ", ".join(f'"{c}" {DUCKDB_TYPES[t]}' for c, t in (col.split() for col in cols.split(", ")))
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        con.execute(f'CREATE TABLE "{name}" ({col_defs})')


def infer_columns(rows: list[dict]) -> dict[str, str]:
    """Column -> type across all rows ('bigint', 'double', 'bool', 'text').
    Done explicitly so a column that happens to be all-NULL today still gets created."""
    seen: dict[str, set] = {}
    for row in rows:
        for k, v in row.items():
            kinds = seen.setdefault(k, set())
            if v is not None:
                kinds.add("bool" if isinstance(v, bool) else "bigint" if isinstance(v, int)
                          else "double" if isinstance(v, float) else "text")
    cols = {}
    for k, kinds in seen.items():
        for t in ("text", "double", "bigint", "bool"):   # widest type wins
            if t in kinds:
                cols[k] = t
                break
        else:
            cols[k] = "text"
    return cols


def load_duckdb(tables: dict[str, list[dict]], db_path: Path, pipelines_dir: Path = BASE / ".dlt_pipelines") -> None:
    import dlt
    import duckdb

    pipeline = dlt.pipeline(pipeline_name="renaissance", destination=dlt.destinations.duckdb(str(db_path)),
                            dataset_name=DATASET, pipelines_dir=str(pipelines_dir))

    def resources():
        for name, rows in tables.items():
            if rows:
                cols = {c: {"data_type": t, "nullable": True} for c, t in infer_columns(rows).items()}
                yield dlt.resource(rows, name=name, write_disposition="replace", columns=cols)

    info = pipeline.run(list(resources()))
    print(info)
    with duckdb.connect(str(db_path)) as con:
        con.execute(f"SET schema = '{DATASET}'")
        create_empty_tables(con, tables)


def connect(db_path: Path):
    """Open the database read-only for querying, with the dataset as the default schema."""
    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute(f"SET schema = '{DATASET}'")
    return con
