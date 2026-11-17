"""The hosted server: players set up and kept apart, sign-in checked, calls passed to each player's
own rensim process, the upload page, and turns shared with a player loaded once."""
import asyncio
import contextlib
import io
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio
import httpx2 as httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken

from renaissance import run
from renaissance.auth import JWTVerifier
from renaissance.hosted import Config, Hosted, safe_name
from renaissance.players import Registry, SetupError

from . import FIXTURES

TURNS = FIXTURES / "data" / "raw" / "turns"


class Registering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = Registry(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_up_writes_the_players_folder(self):
        p = self.reg.set_up("alice", " tst ", "Stark", " Carlisle ")
        self.assertEqual((p.account, p.family, p.home), ("TST", "STARK", "Carlisle"))
        toml = (p.folder(self.reg.root) / "rensim.toml").read_text(encoding="utf-8")
        self.assertIn('account = "TST"', toml)
        again = self.reg.set_up("alice", "TST", "BOLTON", "Dreadfort")   # same player, new details
        self.assertEqual((again.id, again.family), (p.id, "BOLTON"))

    def test_an_account_belongs_to_one_player(self):
        self.reg.set_up("alice", "TST", "STARK", "Carlisle")
        with self.assertRaises(SetupError):
            self.reg.set_up("mallory", "tst", "STARK", "Carlisle")
        self.assertTrue(self.reg.release("TST"))
        self.assertEqual(self.reg.set_up("mallory", "TST", "STARK", "Carlisle").subject, "mallory")

    def test_account_codes_are_letters_and_digits(self):
        for bad in ["", "../x", "A B", "TOOLONGACCOUNTCODE"]:
            with self.subTest(bad=bad), self.assertRaises(SetupError):
                self.reg.set_up("alice", bad, "STARK", "Carlisle")

    def test_upload_names(self):
        self.assertEqual(safe_name("../../etc/My Turn (1).TXT"), "My_Turn_1.txt")
        self.assertEqual(safe_name("NEWSL901"), "NEWSL901")
        self.assertEqual(safe_name("C:\\Users\\me\\city data.docx"), "city_data.docx")
        self.assertIsNone(safe_name("run.sh"))


class Tokens(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        class Keys:   # stands in for the issuer's JWKS
            def get_signing_key_from_jwt(_, token):
                return type("K", (), {"key": self.key.public_key()})()

        self.verifier = JWTVerifier("https://auth.example", audience="https://rensim.example/mcp", jwks_client=Keys())

    def token(self, **claims):
        claims = {"iss": "https://auth.example", "sub": "user_1", "aud": "https://rensim.example/mcp",
                  "exp": int(time.time()) + 600, "scope": "openid email", **claims}
        return jwt.encode({k: v for k, v in claims.items() if v is not None}, self.key, algorithm="RS256")

    async def test_a_good_token_names_the_player(self):
        got = await self.verifier.verify_token(self.token())
        self.assertEqual((got.subject, got.scopes), ("user_1", ["openid", "email"]))

    async def test_bad_tokens_are_refused(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        bad = {"wrong issuer": self.token(iss="https://evil.example"),
               "expired": self.token(exp=int(time.time()) - 3600),
               "for another server": self.token(aud="https://other.example/mcp"),
               "no subject": self.token(sub=None),
               "wrong key": jwt.encode({"iss": "https://auth.example", "sub": "x", "aud": "https://rensim.example/mcp",
                                        "exp": int(time.time()) + 600}, other, algorithm="RS256"),
               "not a jwt": "hello"}
        for why, token in bad.items():
            with self.subTest(why):
                self.assertIsNone(await self.verifier.verify_token(token))


class BearerIsSubject:
    """For the tests: the bearer token is the player's subject."""
    async def verify_token(self, token):
        return AccessToken(token=token, client_id="test", scopes=[], subject=token) if token else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OverHttp(unittest.IsolatedAsyncioTestCase):
    """The real server on a local port, with each player's own rensim process behind it."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self.hosted = Hosted(Config(public_url=self.url, issuer="https://auth.example", root=Path(self.tmp.name),
                                    invite_code="lute"))
        self.server = uvicorn.Server(uvicorn.Config(self.hosted.app(BearerIsSubject()), port=port, log_level="warning"))
        self.serving = asyncio.create_task(self.server.serve())   # setUp and tearDown run in different tasks
        while not self.server.started:
            await anyio.sleep(0.05)

    async def asyncTearDown(self):
        self.server.should_exit = True
        await self.serving
        self.tmp.cleanup()

    @contextlib.asynccontextmanager
    async def player(self, subject):
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {subject}"}, timeout=120) as http:
            async with Client(streamable_http_client(f"{self.url}/mcp", http_client=http), cache=None) as c:
                yield c

    async def call(self, client, tool, **args):
        result = await client.call_tool(tool, args)
        return result.is_error, result.content[0].text

    async def test_signing_in_is_required(self):
        async with httpx.AsyncClient() as http:
            r = await http.post(f"{self.url}/mcp", json={})
            self.assertEqual(r.status_code, 401)
            self.assertIn("resource_metadata", r.headers["www-authenticate"])

    async def test_a_player_sets_up_uploads_and_asks(self):
        async with self.player("alice") as alice:
            tools = {t.name for t in (await alice.list_tools()).tools}
            self.assertLessEqual({"set_up", "upload_link", "status", "intake", "query"}, tools)
            self.assertIn("set_up", alice.instructions)

            error, text = await self.call(alice, "status")
            self.assertTrue(error)
            self.assertIn("set_up", text)
            error, text = await self.call(alice, "set_up", account="TST", family="Stark", home="Carlisle", invite="nope")
            self.assertTrue(error)
            error, text = await self.call(alice, "set_up", account="TST", family="Stark", home="Carlisle", invite="lute")
            self.assertFalse(error, text)

            error, text = await self.call(alice, "upload_link")
            link = text.split(": ", 1)[1]
            async with httpx.AsyncClient() as http:
                self.assertIn("TST", (await http.get(link)).text)
                page = await http.post(link, files=[("files", ("my turn.txt", (TURNS / "tst_1547-01.txt").read_bytes())),
                                                    ("files", ("evil.sh", b"rm -rf /"))])
                self.assertIn("my_turn.txt", page.text)
                self.assertIn("evil.sh", page.text)   # listed as not uploaded
                self.assertEqual((await http.get(f"{self.url}/upload/made-up")).status_code, 404)

            error, text = await self.call(alice, "intake_inbox")
            self.assertFalse(error, text)
            self.assertIn('"status": "saved"', text)
            error, text = await self.call(alice, "query", sql="SELECT account FROM v_turns")
            self.assertIn('"TST"', text)

        async with self.player("bob") as bob:   # another player sees none of it
            await self.call(bob, "set_up", account="BOB", family="Bolton", home="Dreadfort", invite="lute")
            error, text = await self.call(bob, "status")
            self.assertIn('"my_turn_results": 0', text)
            self.assertIn('"all_turn_results": 0', text)
            error, text = await self.call(bob, "set_up", account="TST", family="Stark", home="Carlisle", invite="lute")
            self.assertTrue(error)
            self.assertIn("already set up", text)


class SharedTurns(unittest.TestCase):
    """Turns shared with a player (data/raw/shared/<account>/turns/) load next to their own, and a
    turn they also saved themselves is loaded once."""

    def test_shared_turns_load_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = base / "data" / "raw"
            (raw / "turns").mkdir(parents=True)
            shutil.copy(TURNS / "tst_1547-01.txt", raw / "turns")
            shared = raw / "shared" / "TST" / "turns"
            shared.mkdir(parents=True)
            shutil.copy(TURNS / "tst_1547-01.txt", shared)   # a copy of one they have
            shutil.copy(TURNS / "tst_1547-02.txt", shared)   # and one they don't
            with patch.multiple(run, RAW=raw, SHARED=raw / "shared", BASE=base, ORDERS_DIR=base / "orders",
                                load_claims=lambda: {"claims": [], "sources": []}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    tables = run.parse_all()
            dates = sorted(s["game_date"] for s in tables["sources"] if s["kind"] == "turn_report")
            self.assertEqual(dates, sorted(set(dates)))
            self.assertEqual(len(dates), 2)
            self.assertIn("skipped (already loaded from another file): data/raw/shared/TST/turns/tst_1547-01.txt",
                          out.getvalue())


if __name__ == "__main__":
    unittest.main()
