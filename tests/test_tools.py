"""The tools that change the game folder, through the real server: started the way Claude Desktop
starts it (its own process, the folder from RENSIM_CONFIG), on a copy of the fixtures."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from mcp import Client, StdioServerParameters

from renaissance.orders import check, send_text
from renaissance.training import plan

from . import FIXTURES

ENV = {"RUNTIME__DLTHUB_TELEMETRY": "false", "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
DOWNLOADED_TURN = FIXTURES / "downloads" / "tst_1547-01.txt"   # still has its address
WATCH = """THE WINTERFELL WATCH (a made-up publication for the tests)
Published by Roose Bolton, March 1547.

It is rumored that Lord Ramsay Bolton holds the Magistracy of Hawick.
"""


def env(folder: Path, **extra) -> dict:
    return {**os.environ, **ENV, "RENSIM_CONFIG": str(folder / "rensim.toml"), **extra}


def server(folder: Path, **extra) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "renaissance.mcp_server"],
                                 env=env(folder, **extra), cwd=str(folder))


async def call(client, tool, **args):
    """The tool's JSON reply, or (for an error) its message as a string."""
    result = await client.call_tool(tool, args)
    text = result.content[0].text
    return text if result.is_error else json.loads(text)


class Tools(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(shutil.copytree(FIXTURES, Path(cls.tmp.name) / "game"))
        shutil.rmtree(cls.folder / "orders")
        built = subprocess.run([sys.executable, "-m", "renaissance.run"], env=env(cls.folder), cwd=cls.folder,
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stdout + built.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def client(self, **extra):
        return Client(server(self.folder, **extra))

    async def test_lists_what_changes_the_folder(self):
        async with self.client() as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            prompts = [p.name for p in (await c.list_prompts()).prompts]
        changing = {n for n, t in tools.items() if not t.annotations.read_only_hint}
        self.assertEqual(changing, {"refresh", "intake", "intake_inbox", "save_claims", "check_orders",
                                    "propose_matches", "decide_matches"})
        self.assertEqual(prompts, ["plan_turn"])

    async def test_an_empty_folder_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as empty:
            async with Client(StdioServerParameters(
                    command=sys.executable, args=["-m", "renaissance.mcp_server"], cwd=empty,
                    env={**env(Path(empty)), "RENSIM_ACCOUNT": "TST", "RENSIM_FAMILY": "STARK",
                         "RENSIM_HOME": "Carlisle"})) as c:
                got = await call(c, "status")
                self.assertEqual((got["account"], got["my_turn_results"]), ("TST", 0))
                self.assertIn("paste or attach", got["next"])
                self.assertIn("paste or attach", await call(c, "who_is_here"))
                self.assertEqual((await call(c, "refresh"))["loaded"], False)
                self.assertTrue((await call(c, "check_orders", text="/TST *STARK"))["errors"])

    async def test_a_pasted_turn_is_saved_without_its_address_and_loaded(self):
        saved = self.folder / "data" / "raw" / "turns" / "tst_1547-01.txt"
        saved.unlink()
        pasted = "Fwd: your turn\n\n" + DOWNLOADED_TURN.read_bytes().decode("cp1252")
        async with self.client() as c:
            got = await call(c, "intake", text=pasted)
            self.assertEqual((got["kind"], got["status"], got["file"]), ("turn", "saved", "data/raw/turns/tst_1547-01.txt"))
            self.assertEqual((got["load"]["loaded"], got["load"]["tests_passed"]), (True, True))
            self.assertNotIn(b"Example Street", saved.read_bytes())
            self.assertTrue(saved.read_bytes().startswith(b"ACCOUNT CODE:"))
            self.assertEqual((await call(c, "intake", text=pasted))["status"], "already saved")
            self.assertTrue((await call(c, "who_is_here"))["rows"])

    def test_stdout_carries_only_the_protocol(self):
        """A refresh prints a lot (dlt, dbt); none of it may reach stdout, which is the protocol."""
        proc = subprocess.Popen([sys.executable, "-m", "renaissance.mcp_server"], env=env(self.folder),
                                cwd=self.folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        watchdog = threading.Timer(300, proc.kill)
        watchdog.start()
        try:
            for msg in [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "refresh", "arguments": {}}}]:
                proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            lines = []
            while not lines or lines[-1].get("id") != 2:
                line = proc.stdout.readline()
                self.assertTrue(line, "the server stopped before answering")
                lines.append(json.loads(line))   # fails on anything that isn't a protocol message
            self.assertFalse(lines[-1]["result"].get("isError"), lines[-1])
        finally:
            watchdog.cancel()
            proc.kill()
            proc.wait()
            proc.stdin.close()
            proc.stdout.close()

    async def test_a_publication_and_its_claims(self):
        claim = {"id": "c1", "type": "office", "predicate": "holds_office", "object": "Magistrate of Hawick",
                 "subject": {"name": "Ramsay Bolton", "given": "Ramsay", "family": "BOLTON"}, "place": "Hawick",
                 "hedge": "rumored", "quote": "Lord Ramsay Bolton holds the Magistracy of Hawick"}
        source = {"kind": "player_newsletter", "title": "The Winterfell Watch", "publisher": "Roose Bolton",
                  "publisher_family": "BOLTON", "game_date": "1547-03"}
        claims_file = self.folder / "data" / "claims" / "Winterfell_Watch.json"
        async with self.client() as c:
            got = await call(c, "intake", text=WATCH, name="Winterfell Watch")
            self.assertEqual((got["kind"], got["status"], got["file"]),
                             ("publication", "saved", "player_publications/Winterfell_Watch.txt"))
            self.assertIn("## File format", got["claims_guide"])
            self.assertEqual((await call(c, "intake", text=WATCH, name="Winterfell Watch"))["status"], "already saved")

            made_up = await call(c, "save_claims", file=got["file"], source=source,
                                 claims=[{**claim, "quote": "Lord Ramsay Bolton is Magistrate of Kelso"}])
            self.assertIn("quote not in source", made_up)
            self.assertFalse(claims_file.exists())
            self.assertIn("isn't a saved publication",
                          await call(c, "save_claims", file="../../rensim.toml", source=source, claims=[claim]))

            saved = await call(c, "save_claims", file=got["file"], source=source, claims=[claim])
            self.assertEqual((saved["saved"], saved["load"]["tests_passed"]), ("Winterfell_Watch.json", True))
            rows = (await call(c, "query", sql="SELECT hedge, quote FROM claims WHERE source_id = 'pub:Winterfell_Watch'"))["rows"]
            self.assertEqual(rows, [{"hedge": "rumored", "quote": claim["quote"]}])

    async def test_check_orders_saves_orders_that_pass(self):
        text = (FIXTURES / "orders" / "tst_1547-04.txt").read_text(encoding="utf-8")
        async with self.client() as c:
            got = await call(c, "check_orders", text=text)
            self.assertEqual(got["errors"], check(text).errors)
            self.assertEqual((got["saved"], got["send"]), ("tst_1547-04.txt", send_text(text)))
            self.assertEqual((self.folder / "orders" / "tst_1547-04.txt").read_text(encoding="utf-8"), text)

            bad = await call(c, "check_orders", text="/TST *STARK\n*Carlisle .999 zzz\n", month="1547-05")
            self.assertTrue(bad["errors"])
            self.assertIsNone(bad["saved"])
            self.assertFalse((self.folder / "orders" / "tst_1547-05.txt").exists())
            self.assertIn("1547-04", await call(c, "check_orders", text=text, month="April"))

    async def test_the_inbox(self):
        from docx import Document
        inbox = self.folder / "inbox"
        inbox.mkdir(exist_ok=True)
        letter = Document()
        letter.add_paragraph("A letter from Lord Tywin Lannister to House STARK.")
        letter.add_paragraph("We hear that Lord Eddard Stark marches on Kelso.")
        letter.save(inbox / "tywin letter.docx")
        shutil.copy(FIXTURES / "data" / "raw" / "newsletters" / "NEWSL901", inbox)
        (inbox / "map.png").write_bytes(b"\x89PNG not really")
        async with self.client() as c:
            files = {f["inbox_file"]: f for f in (await call(c, "intake_inbox"))["files"]}
        self.assertEqual((files["NEWSL901"]["kind"], files["NEWSL901"]["status"]), ("newsletter", "already saved"))
        letter = files["tywin letter.docx"]
        self.assertEqual((letter["kind"], letter["status"], letter["file"]),
                         ("publication", "saved", "player_publications/tywin_letter.docx"))
        self.assertIn("marches on Kelso", letter["text"])
        self.assertIn("claims_guide", letter)
        self.assertEqual(files["map.png"]["status"], "refused")
        self.assertEqual(sorted(p.name for p in inbox.iterdir()), ["done", "map.png"])
        self.assertEqual(sorted(p.name for p in (inbox / "done").iterdir()), ["NEWSL901", "tywin letter.docx"])

    async def test_training_is_the_planners(self):
        async with self.client() as c:
            got = await call(c, "plan_training")
        self.assertEqual([o["code"] for o in got["orders"]], [o["code"] for o in plan()[2]])

    async def test_matches_answered_no_are_not_proposed_again(self):
        async with self.client() as c:
            proposals = (await call(c, "propose_matches"))["proposals"]
            self.assertTrue(proposals)
            first = proposals[0]
            got = await call(c, "decide_matches", decisions=[{**first, "decision": "no"}])
            self.assertEqual((got["accepted"], got["rejected"], got["not_found"]), (0, 1, 0))
            self.assertNotIn(first, (await call(c, "propose_matches"))["proposals"])
            self.assertIn("yes or no", await call(c, "decide_matches", decisions=[{**first, "decision": "maybe"}]))


if __name__ == "__main__":
    unittest.main()
