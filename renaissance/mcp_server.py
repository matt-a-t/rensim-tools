"""An MCP server over your database, so Claude can answer questions about your game in plain English.

    rensim mcp                       # stdio server; Claude Code starts it for you (see README)

Tools: `query` (any read-only SQL), `describe` (tables, views and their columns), `list_questions`
and `ask` (the saved questions in sql/questions.sql), and four named ones: `what_changed`,
`who_is_here`, `my_past_orders` (saved questions of those names) and `prices` (v_markets, filtered).

Read-only twice over: the database is opened read-only, and with external access off and the
configuration locked, so SQL can't read or write other files or attach other databases. Each call
opens its own connection and closes it, so `rensim run` can rebuild the database while Claude is
connected. Results come back as JSON rows, at most MAX_ROWS of them.
"""
from __future__ import annotations

import argparse
import datetime
import decimal
import sys
from pathlib import Path

from .ask import load_questions
from .load import connect

DBT_MODELS = Path(__file__).resolve().parent / "dbt" / "models"
MAX_ROWS = 200

INSTRUCTIONS = """Read-only access to one player's database for Renaissance, a play-by-mail game.
`my_settings` holds whose it is: account, family and home town. Start with the v_* analysis views
(v_personae_now, v_presence_changes, v_markets...), then the fact_* and dim_* tables; call
`describe` before writing SQL to see what exists. Game months are dates (game_date, as_of).
Claims (fact_claims, v_claims) are what other players assert in their publications, not facts:
say so when you use them. Prefer the named tools when one answers the question."""


def _json(v):
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    return v


def run_sql(db: Path, sql: str, params: list | None = None) -> dict:
    """Rows as dicts, capped at MAX_ROWS, on a fresh locked-down read-only connection."""
    con = connect(db)
    try:
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        cur = con.execute(sql, params or [])
        if cur.description is None:
            return {"columns": [], "rows": [], "truncated": False}
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
    finally:
        con.close()
    return {"columns": cols, "rows": [dict(zip(cols, map(_json, r))) for r in rows[:MAX_ROWS]],
            "truncated": len(rows) > MAX_ROWS}


def dbt_descriptions() -> dict[str, dict]:
    """table -> {"description": ..., "columns": {column: description}} from the dbt .yml files."""
    import yaml   # comes with dbt

    out = {}
    for path in DBT_MODELS.rglob("*.yml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = list(doc.get("models", []))
        for src in doc.get("sources", []):
            entries += src.get("tables", [])
        for e in entries:
            out[e["name"]] = {"description": " ".join((e.get("description") or "").split()),
                              "columns": {c["name"]: " ".join((c.get("description") or "").split())
                                          for c in e.get("columns", []) if c.get("description")}}
    return out


def build_server(db: Path):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations

    server = MCPServer("rensim", instructions=INSTRUCTIONS, log_level="WARNING")
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    def fetch(text: str, params: list | None = None) -> dict:
        if not db.exists():
            raise ToolError(f"No database at {db}: run `rensim run` in the data folder first.")
        try:
            return run_sql(db, text, params)
        except Exception as e:   # duckdb's errors are what Claude needs to fix its SQL
            raise ToolError(f"{type(e).__name__}: {e}") from e

    def saved(name: str) -> dict:
        questions = load_questions()
        if name not in questions:
            raise ToolError(f"No saved question {name!r}. Saved questions: {', '.join(questions)}")
        desc, text = questions[name]
        return {"question": name, "description": desc, **fetch(text)}

    @server.tool(annotations=read_only)
    def query(sql: str) -> dict:
        """Run read-only SQL (DuckDB dialect) on the database and return up to 200 rows.
        Call `describe` first to see the tables, views and columns."""
        return fetch(sql)

    @server.tool(annotations=read_only)
    def describe(table: str | None = None) -> dict:
        """With no table: every table and view, with what it holds. With a table or view name:
        its columns, their types and descriptions."""
        docs = dbt_descriptions()
        if table is None:
            listed = fetch("SELECT table_name, table_type FROM information_schema.tables "
                         "WHERE table_schema = current_schema() AND table_name NOT LIKE '\\_dlt%' ESCAPE '\\' "
                         "ORDER BY table_name")["rows"]
            return {"tables": [{"name": r["table_name"], "kind": "view" if r["table_type"] == "VIEW" else "table",
                                "description": docs.get(r["table_name"], {}).get("description", "")}
                               for r in listed]}
        cols = fetch("SELECT column_name, data_type FROM information_schema.columns "
                   "WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position",
                   [table])["rows"]
        if not cols:
            raise ToolError(f"No table or view {table!r}: call describe() with no table to list them.")
        doc = docs.get(table, {})
        return {"name": table, "description": doc.get("description", ""),
                "columns": [{"name": c["column_name"], "type": c["data_type"],
                             "description": doc.get("columns", {}).get(c["column_name"], "")} for c in cols]}

    @server.tool(annotations=read_only)
    def list_questions() -> dict:
        """The saved questions `ask` can run, with what each answers."""
        return {"questions": [{"name": n, "description": d} for n, (d, _) in load_questions().items()]}

    @server.tool(annotations=read_only)
    def ask(name: str) -> dict:
        """Run a saved question by name (see `list_questions`)."""
        return saved(name)

    @server.tool(annotations=read_only)
    def what_changed() -> dict:
        """Everything that changed for my position between my last two turns: who arrived and left
        where, morale, popularity, location, skills, assets and market prices."""
        return saved("what_changed")

    @server.tool(annotations=read_only)
    def who_is_here() -> dict:
        """Everyone seen inside or outside my locations on the latest turn, mine and others'."""
        return saved("who_is_here")

    @server.tool(annotations=read_only)
    def my_past_orders() -> dict:
        """What I ordered each turn, persona by persona, in plain English, newest first."""
        return saved("my_past_orders")

    @server.tool(annotations=read_only)
    def prices(item: str | None = None, place: str | None = None, side: str | None = None) -> dict:
        """Known market prices, nearest first: for each place the most trusted, freshest source.
        Filter by item (e.g. "grain"), place (name, any case) and side ("supply" = sold there,
        "demand" = bought there). Check as_of and reliability before relying on a price."""
        if side not in (None, "supply", "demand"):
            raise ToolError('side must be "supply" or "demand"')
        where, params = [], []
        for col, val in (("item", item), ("place", place), ("side", side)):
            if val:
                where.append(f"lower({col}) = lower(?)")
                params.append(val)
        return fetch("SELECT place, item, side, price, quantity, as_of, reliability, source, miles_from_home "
                   "FROM v_markets" + (" WHERE " + " AND ".join(where) if where else "")
                   + " ORDER BY miles_from_home NULLS LAST, place, item, side", params)

    return server


def main(argv=None):
    argparse.ArgumentParser(prog="rensim mcp", description=__doc__.splitlines()[0]).parse_args(argv)
    try:
        import mcp  # noqa: F401
    except ImportError:
        print('The MCP server needs the mcp extra: pip install "rensim-tools[mcp]"', file=sys.stderr)
        return 1
    from .run import DB
    build_server(DB).run("stdio")


if __name__ == "__main__":
    main()
