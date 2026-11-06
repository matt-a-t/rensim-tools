"""The MCP server that is the whole of rensim for a player: Claude Desktop starts it from the
extension (manifest.json), with the game folder and who they are from the install form.

    rensim-mcp                       # stdio server

Reading: `query` (any read-only SQL), `describe` (tables, views and their columns), `list_questions`
and `ask` (the saved questions in sql/questions.sql), `what_changed`, `who_is_here`,
`my_past_orders`, `prices`, `status`, `check_data`, `plan_training`.

Changing the game folder: `intake` (pasted text: a turn result, a newsletter, anything else),
`intake_inbox` (files in inbox/), `save_claims`, `check_orders` (saves the orders when they pass),
`propose_matches` / `decide_matches`, and `refresh`, which rebuilds the database; the others call
it when they change what's loaded. One change runs at a time.

SQL is read-only twice over: the database is opened read-only, and with external access off and
the configuration locked, so SQL can't read or write other files or attach other databases. Each
call opens its own connection and closes it, so `refresh` can rebuild the database while Claude is
connected. Results come back as JSON rows, at most MAX_ROWS of them. Stdout is the protocol, so
everything the pipeline prints is captured and returned (or dropped) instead.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import decimal
import io
import re
import threading
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .ask import load_questions
from .load import connect
from .settings import BASE, HOME, MY_ACCOUNT, MY_FAMILY, ORDERS_DIR, RAW

DBT_MODELS = Path(__file__).resolve().parent / "dbt" / "models"
CLAIMS_GUIDE = Path(__file__).with_name("claims_extraction.md")
MAX_ROWS = 200
LOG_LINES = 60   # of the pipeline's output, returned when a refresh fails

INSTRUCTIONS = """Tools for one player of Renaissance, a play-by-mail game, over their own game folder.
The player isn't technical: never ask them to run commands or edit files; the tools do it.

Anything they paste or attach for the game (a GM turn result, a GM newsletter, another player's
newsletter, proclamation, letter or message) goes to `intake`, as its complete text, unchanged. GM
files are saved and loaded; anything else is saved as a player publication, and you then draw its
claims following the guide `intake` returns and save them with `save_claims`. Files they put in
the game folder's inbox/ go through `intake_inbox`. Start with `status` if you don't know what's loaded.

Questions about the game: `my_settings` holds whose data it is (account, family, home town).
Start with the v_* analysis views (v_personae_now, v_presence_changes, v_markets...), then the
fact_* and dim_* tables; call `describe` before writing SQL. Game months are dates (game_date,
as_of). Claims (fact_claims, v_claims) are what other players assert, not facts: say so when you
use them. Prefer the named tools when one answers the question.

To plan a turn, follow the `plan_turn` prompt: plain-English plan, orders checked with
`check_orders` until there are no errors, then the codes to email the GM."""

PLAN_TURN = """Help me plan and write my Renaissance orders for this turn{goal}.

1. Get up to date. Call `status`. If I attached or pasted a new turn result or newsletter, `intake`
   it first; if it's refused, tell me why rather than going on with old data. Then `what_changed`
   and `query` on v_personae_now (order by persona_id) for who is where, and `my_past_orders`
   so this turn follows on from the last plan.
2. Plan with me in plain English. Take what I ask as the goal. Count my personae from the turn
   report, not from what I say; if the numbers differ, say so and what you assumed. Unless I say
   otherwise:
   - Personae starting together train each other (`plan_training`); training runs before
     movement, so they can still leave.
   - A magistrate stays home: they collect the town's tariffs only while present.
   - Scouts stop outside the destination (`ml<Place>*`) unless my family has ingress there.
   - Land moves up to 200 miles are free of attrition; horses carry 2 cargo each and slaves 1/4.
   - Markets: buy and sell only where the persona starts the turn.
   - One new location setup request per turn, as a separate note with its fee.
   Don't explain codes and don't discuss cost unless I ask.
3. Write the orders: `#` lines first describing the plan in plain English (they're what "what
   did I do three months ago?" is answered from), then `/<account> *<family>` and one
   `*<start location> .<persona number> <codes>` line per persona. Call `check_orders` and fix
   every error until it reports none; it saves them.
4. Reply in this order: what is going to happen, persona by persona, in plain English (names, not
   numbers), with the due date; then the `send` text from `check_orders` in a code block, for me
   to copy into an email to the GM.

If I change the plan, check the new orders again: the saved orders should always be the ones sent."""


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


def my_turn_months() -> list[str]:
    """YYYY-MM of every turn result of mine saved, oldest first (from the names `add_file` gives them)."""
    if not MY_ACCOUNT:
        return []
    return sorted(p.stem.split("_", 1)[1] for p in (RAW / "turns").glob(f"{MY_ACCOUNT.lower()}_*.txt"))


def next_month(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def status_info() -> dict:
    """Who the folder is for, what's in it, and what to do next."""
    count = lambda folder, pattern="*": sum(1 for p in folder.glob(pattern) if p.is_file())
    from .claims import CLAIMS
    from .intake import INBOX
    months = my_turn_months()
    info = {"game_folder": str(BASE), "account": MY_ACCOUNT, "family": MY_FAMILY, "home": HOME,
            "my_turn_results": len(months), "latest_turn": months[-1] if months else None,
            "all_turn_results": count(RAW / "turns", "*.txt"), "gm_newsletters": count(RAW / "newsletters"),
            "player_publications": count(RAW / "player_publications"), "claims_files": count(CLAIMS, "*.json"),
            "orders_saved": count(ORDERS_DIR, "*.txt"), "files_in_inbox": count(INBOX)}
    missing = [f for f, v in (("account code", MY_ACCOUNT), ("family", MY_FAMILY), ("home town", HOME)) if not v]
    if missing:
        info["next"] = (f"Set the {', '.join(missing)} in Claude's settings: Extensions, Renaissance turns, "
                        "Configure. Then restart Claude.")
    elif not months:
        info["next"] = (f"No turn results for account {MY_ACCOUNT} yet: paste or attach the latest one "
                        "(and any GM newsletters) and it will be saved and loaded.")
    if info["files_in_inbox"]:
        info["inbox"] = "Files are waiting in inbox/: intake_inbox files them."
    return info


def tail(text: str, n: int = LOG_LINES) -> str:
    return "\n".join(text.rstrip().splitlines()[-n:])


def build_server(db: Path):
    server = MCPServer("rensim", instructions=INSTRUCTIONS, log_level="WARNING")
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    changes = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
    one_change = threading.Lock()   # tools that write the folder or the database run one at a time

    def fetch(text: str, params: list | None = None) -> dict:
        if not db.exists():
            raise ToolError("No data loaded yet: paste or attach a turn result first (see `status`).")
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

    # ---- reading the folder -----------------------------------------------------------------

    @server.tool(annotations=read_only)
    def status() -> dict:
        """Whose game folder this is (account, family, home town), how many turn results,
        newsletters, publications, claims and orders it holds, the latest turn, and what to do
        next when something is missing."""
        return status_info()

    @server.tool(annotations=read_only)
    def check_data() -> dict:
        """Checks on the loaded data: months with no turn result, files with too few rows, odd
        prices, lines the parser didn't recognise, and turn files still holding an address.
        Run after loading new turns and mention any problems to the player."""
        from .quality import run_checks
        if not db.exists():
            raise ToolError("No data loaded yet: paste or attach a turn result first.")
        con = connect(db)
        try:
            problems = run_checks(con)
        finally:
            con.close()
        return {"ok": not any(problems.values()), "problems": {k: v for k, v in problems.items() if v}}

    @server.tool(annotations=read_only)
    def plan_training(skills: list[str] | None = None) -> dict:
        """The best training for personae who start the turn together, from my latest turn: for
        each trainer -> trainee pair (once per pair per turn) the skill with the biggest gain.
        Training runs before movement. `skills` limits it (e.g. ["fortification"]). Each order's
        `code` goes on the trainer's line."""
        from .orders import latest_position
        from .training import plan
        if not latest_position():
            raise ToolError(f"No turn result for account {MY_ACCOUNT or '(not set)'} yet.")
        game_date, personae, orders = plan(set(skills) if skills else None)
        person = lambda p: f"{p['given_name']} ({p['persona_id']})"
        return {"from_turn": str(game_date), "total_gain": round(sum(o["gain"] for o in orders), 1),
                "orders": [{"trainer": person(o["trainer"]), "trainee": person(o["trainee"]), "skill": o["skill"],
                            "from": o["from"], "to": round(o["to"], 1), "code": o["code"]} for o in orders],
                "lines": [f"*{p['location']} .{p['persona_id']} " + " ".join(o["code"] for o in orders if o["trainer"] is p)
                          for p in personae if any(o["trainer"] is p for o in orders)]}

    # ---- changing the folder ----------------------------------------------------------------

    def rebuild() -> dict:
        """Load everything into the database again (call holding one_change)."""
        from .run import build
        if not any((RAW / "turns").glob("*.txt")):
            return {"loaded": False, "why": "no turn results saved yet"}
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                ok = build()
        except Exception as e:
            raise ToolError(f"Loading failed: {type(e).__name__}: {e}\n{tail(out.getvalue())}") from e
        result = {"loaded": True, "tests_passed": ok}
        unparsed = re.search(r"(\d+) line\(s\) didn't match", out.getvalue())
        if unparsed:
            result["unparsed_lines"] = int(unparsed[1])
        if not ok:
            result["log"] = tail(out.getvalue())
        return result

    @server.tool(annotations=changes)
    def refresh() -> dict:
        """Rebuild the database from every file in the game folder. The other tools do this when
        they change something, so it's only needed if files were changed some other way."""
        with one_change:
            return rebuild()

    @server.tool(annotations=changes)
    def intake(text: str, name: str | None = None) -> dict:
        """Save anything for the game the player pasted or attached: pass its complete text,
        unchanged. A GM turn result or newsletter is checked, saved (the mailing address taken
        out) and loaded; the result says if it was refused and why. Anything else (another
        player's newsletter, a proclamation, a letter or message) is saved as a player publication
        and comes back with the guide for drawing claims from it: do that, then `save_claims`.
        `name` is what to call a publication, e.g. its title or the file it came in."""
        from .intake import intake_text
        with one_change:
            result = intake_text(text, name)
            return with_next_steps([result])[0]

    @server.tool(annotations=changes)
    def intake_inbox() -> dict:
        """File everything the player put in the game folder's inbox/ folder: turn results,
        newsletters, Word documents, PDFs with text, text files. Filed files move to inbox/done/.
        Publications come back with their text and the claims guide."""
        from .intake import intake_inbox as file_inbox
        from .claims import EXTRACTED
        with one_change:
            results = file_inbox()
            for r in results:
                if r["kind"] == "publication" and r["status"] == "saved":
                    r["text"] = (EXTRACTED / (r["file"] + ".txt")).read_text(encoding="utf-8")
            return {"files": with_next_steps(results)}

    def with_next_steps(results: list[dict]) -> list[dict]:
        """Load what was saved, and attach the claims guide to new publications (holding one_change)."""
        if any(r["kind"] in ("turn", "newsletter", "ally_reference") and r["status"] == "saved" for r in results):
            loaded = rebuild()
            for r in results:
                if r["status"] == "saved" and r["kind"] != "publication":
                    r["load"] = loaded
        new_publications = [r for r in results if r["kind"] == "publication" and r["status"] == "saved"]
        for r in new_publications:
            r["next"] = (f"Draw the claims from it following claims_guide and call save_claims with "
                         f"file={r['file']!r}. Give source.kind, and the title, publisher and game_date if it says.")
        if new_publications:
            new_publications[0]["claims_guide"] = CLAIMS_GUIDE.read_text(encoding="utf-8")
        return results

    @server.tool(annotations=changes)
    def save_claims(file: str, source: dict, claims: list[dict]) -> dict:
        """Save the claims drawn from a publication `intake` saved, and load them. `file` is the
        file intake returned; `source` holds kind, title, publisher, publisher_family, game_date
        (YYYY-MM), real_date and notes; `claims` follow the claims guide. Every quote must be word
        for word in the publication's text: if any claim fails a check nothing is saved and the
        problems are listed, so fix them and send all the claims again. Saving again replaces them."""
        from .claims import save
        with one_change:
            path, problems = save(file, source, claims)
            if problems:
                raise ToolError("Not saved:\n" + "\n".join(f"- {p}" for p in problems))
            return {"saved": path.name, "claims": len(claims), "load": rebuild()}

    @server.tool(annotations=changes)
    def check_orders(text: str, month: str | None = None) -> dict:
        """Check orders against my latest turn and explain each code in plain English. Start the
        text with # lines describing the plan, then `/<account> *<family>` and one
        `*<start> .<persona> <codes>` line per persona. When there are no errors they're saved as
        the orders for `month` (YYYY-MM, default the month after my latest turn), replacing any
        saved before, and `send` is the exact text to email the GM."""
        from .orders import check, send_text
        months = my_turn_months()
        month = month or (next_month(months[-1]) if months else None)
        if month and not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ToolError("month must look like 1547-04")
        with one_change:
            rep = check(text)
            result = {"explanation": rep.lines, "errors": rep.errors, "warnings": rep.warnings, "notes": rep.notes}
            if rep.errors or not month:
                result["saved"] = None
                return result
            path = ORDERS_DIR / f"{MY_ACCOUNT.lower()}_{month}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.rstrip() + "\n", encoding="utf-8")
            return {**result, "saved": path.name, "send": send_text(text)}

    @server.tool(annotations=changes)
    def propose_matches(threshold: float | None = None) -> dict:
        """Names that may be one family, place or character written differently (typos, other
        spellings, a name without its game ID), with how likely and why. Look-alikes can be
        different families, so ask the player about each and record the answers with
        `decide_matches`. Pairs already decided aren't proposed again. `threshold` is the lowest
        probability shown (default 0.1)."""
        from . import match
        if not db.exists():
            raise ToolError("No data loaded yet: paste or attach a turn result first.")
        with one_change:
            proposals = match.find(match.THRESHOLD if threshold is None else threshold)
            match.write_proposals(proposals)
        return {"proposals": [{k: p[k] for k in ("kind", "name", "match", "persona_id", "probability", "why")}
                              for p in proposals]}

    @server.tool(annotations=changes)
    def decide_matches(decisions: list[dict]) -> dict:
        """Record the player's answers to `propose_matches`: each decision is a proposal's kind,
        name, match and persona_id plus "decision": "yes" or "no". Yes joins the names in the
        data (then reloads it); no stops the pair being proposed again."""
        from . import match
        bad = [d for d in decisions if str(d.get("decision", "")).strip().lower()[:1] not in ("y", "n")]
        if bad:
            raise ToolError(f"decision must be yes or no: {bad[0]}")
        with one_change:
            found = match.decide([{**d, "decision": str(d["decision"]).strip().lower()[:1]} for d in decisions])
            yes, no, left = match.apply()
            result = {"accepted": yes, "rejected": no, "not_found": len(decisions) - found, "still_to_decide": left}
            if yes:
                result["load"] = rebuild()
            return result

    @server.prompt(title="Plan my turn")
    def plan_turn(goal: str | None = None) -> str:
        """Plan this turn's orders with me: plain-English plan first, then checked codes to email the GM."""
        return PLAN_TURN.format(goal=f": {goal}" if goal else "")

    return server


def main(argv=None):
    argparse.ArgumentParser(prog="rensim-mcp", description=__doc__.splitlines()[0]).parse_args(argv)
    from .run import DB
    build_server(DB).run("stdio")


if __name__ == "__main__":
    main()
