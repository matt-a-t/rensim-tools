"""The MCP server on the fixtures, through the SDK's in-process client: the tools answer from the
database, and SQL can't write or reach other files."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from mcp import Client
except ImportError:
    Client = None

from renaissance import mcp_server
from renaissance.mcp_server import build_server

from . import FIXTURES, fixture_db

ARRIVED_AT_HOME = """SELECT p.persona_id, p.given_name, p.family, p.troops
FROM v_presence_changes p JOIN my_settings s ON lower(p.location) = lower(s.home)
WHERE p.change = 'arrived' AND p.game_date = (SELECT MAX(game_date) FROM v_turns)"""


@unittest.skipUnless(Client, 'needs the MCP SDK: pip install "rensim-tools[mcp]"')
class Tools(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = fixture_db()   # built outside the event loop, which would flag the wait as slow

    async def call(self, tool, **args):
        """The tool's JSON reply, or (for an error) its message as a string."""
        async with Client(build_server(self.db)) as client:
            result = await client.call_tool(tool, args)
        text = result.content[0].text
        return text if result.is_error else json.loads(text)

    async def test_lists_the_tools(self):
        async with Client(build_server(self.db)) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        self.assertEqual(set(tools), {"query", "describe", "list_questions", "ask", "what_changed",
                                      "who_is_here", "my_past_orders", "prices"})
        self.assertTrue(all(t.annotations.read_only_hint for t in tools.values()))

    async def test_who_arrived_at_my_home_town_last_turn(self):
        got = await self.call("query", sql=ARRIVED_AT_HOME)
        self.assertEqual(got["rows"], [{"persona_id": 901, "given_name": "Ramsay", "family": "BOLTON", "troops": 200}])

    async def test_sql_cannot_write_or_reach_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for sql in ["CREATE TABLE x (a INT)", "INSERT INTO my_settings (account) VALUES ('X')",
                        f"COPY (SELECT 1) TO '{Path(tmp) / 'out.csv'}'",
                        f"SELECT * FROM read_csv('{FIXTURES / 'rensim.toml'}')",
                        f"ATTACH '{Path(tmp) / 'other.duckdb'}'",
                        "SET enable_external_access = true"]:
                with self.subTest(sql=sql):
                    self.assertIsInstance(await self.call("query", sql=sql), str)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    async def test_rows_are_capped(self):
        with patch.object(mcp_server, "MAX_ROWS", 3):
            got = await self.call("query", sql="SELECT * FROM range(10)")
        self.assertEqual((len(got["rows"]), got["truncated"]), (3, True))
        got = await self.call("query", sql="SELECT 1 AS one")
        self.assertEqual((got["rows"], got["truncated"]), ([{"one": 1}], False))

    async def test_dates_come_back_as_text(self):
        got = await self.call("query", sql="SELECT MAX(game_date) AS d FROM v_turns")
        self.assertEqual(got["rows"], [{"d": "1547-03-31"}])

    async def test_prices_filter_v_markets(self):
        everything = await self.call("prices")
        self.assertTrue(everything["rows"])
        row = everything["rows"][0]
        got = await self.call("prices", item=row["item"].upper(), place=row["place"].lower(), side=row["side"])
        self.assertIn(row, got["rows"])
        self.assertTrue(all((r["item"], r["place"], r["side"]) == (row["item"], row["place"], row["side"])
                            for r in got["rows"]))
        self.assertEqual((await self.call("prices", item="no such good"))["rows"], [])
        self.assertIn("supply", await self.call("prices", side="sell"))

    async def test_named_tools_match_the_saved_questions(self):
        expected = json.loads((FIXTURES / "expected_answers.json").read_text(encoding="utf-8"))
        for name in ("what_changed", "who_is_here", "my_past_orders"):
            with self.subTest(name=name):
                got = await self.call(name)
                self.assertEqual(sorted(map(list, (r.values() for r in got["rows"])), key=repr), expected[name])
        self.assertIn({"kind": "presence", "who": "Ramsay BOLTON", "detail": "arrived Carlisle with 200 T."},
                      (await self.call("what_changed"))["rows"])

    async def test_ask_and_list_questions(self):
        names = [q["name"] for q in (await self.call("list_questions"))["questions"]]
        self.assertIn("where_to_buy_grain", names)
        self.assertEqual((await self.call("ask", name="family_wealth"))["question"], "family_wealth")
        error = await self.call("ask", name="nope")
        self.assertIn("No saved question 'nope'", error)
        self.assertIn("where_to_buy_grain", error)

    async def test_describe(self):
        tables = {t["name"]: t for t in (await self.call("describe"))["tables"]}
        self.assertEqual(tables["v_presence_changes"]["kind"], "view")
        self.assertIn("arrived", tables["v_presence_changes"]["description"])
        self.assertFalse([n for n in tables if n.startswith("_dlt")])
        got = await self.call("describe", table="v_turns")
        cols = {c["name"]: c for c in got["columns"]}
        self.assertIn("1 for the first report", cols["turn_no"]["description"])
        self.assertIn("No table or view", await self.call("describe", table="nope"))

    async def test_no_database_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.db = Path(tmp) / "renaissance.duckdb"
            self.assertIn("rensim run", await self.call("what_changed"))
