"""The players of the hosted server (renaissance.hosted): who they are, where their game folders
are, and the rensim process that serves each one.

Every player has their own game folder, laid out exactly like a desktop player's, under
<root>/players/<id>/, with a rensim.toml saying whose it is. The registry (<root>/registry.sqlite)
maps a sign-in (the token's subject) to that folder and to the account, family and home town the
player gave in `set_up`. An account code belongs to one player only.

Each player is served by their own `python -m renaissance.mcp_server` process, started on their
first call with RENSIM_CONFIG pointing at their folder, and stopped after a while unused. So the
game code still serves one player per process, as on the desktop, and one player's process can't
read another's folder by mistake. Anything that crosses folders (sharing, later) is done here, by
the hosted server, never by a player's process.

    python -m renaissance.players list               # who has signed up
    python -m renaissance.players release ABC        # free an account code claimed by mistake
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import anyio
import mcp_types as types
from mcp import Client, StdioServerParameters

ACCOUNT_RE = re.compile(r"[A-Za-z0-9]{1,12}")
# The child's environment: enough to run Python, nothing of the hosted server's own settings.
CHILD_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "VIRTUAL_ENV")


class SetupError(Exception):
    """A set_up the registry refused, and why (shown to the player)."""


@dataclass(frozen=True)
class Player:
    subject: str
    id: str
    account: str
    family: str
    home: str

    def folder(self, root: Path) -> Path:
        return root / "players" / self.id


def toml_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Registry:
    """The players, in <root>/registry.sqlite. One connection per call: sqlite is quick to open,
    and calls come from more than one thread."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        with self.db() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS players (
                subject TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, account TEXT NOT NULL UNIQUE,
                family TEXT NOT NULL, home TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")

    @contextlib.contextmanager
    def db(self):
        """A connection, committed and closed at the end of the with block."""
        con = sqlite3.connect(self.root / "registry.sqlite")
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def _player(self, row) -> Player | None:
        return row and Player(row["subject"], row["id"], row["account"], row["family"], row["home"])

    def get(self, subject: str) -> Player | None:
        with self.db() as con:
            return self._player(con.execute("SELECT * FROM players WHERE subject = ?", [subject]).fetchone())

    def by_account(self, account: str) -> Player | None:
        with self.db() as con:
            return self._player(con.execute("SELECT * FROM players WHERE account = ?", [account.upper()]).fetchone())

    def by_id(self, player_id: str) -> Player | None:
        with self.db() as con:
            return self._player(con.execute("SELECT * FROM players WHERE id = ?", [player_id]).fetchone())

    def all(self) -> list[Player]:
        with self.db() as con:
            return [self._player(r) for r in con.execute("SELECT * FROM players ORDER BY account")]

    def set_up(self, subject: str, account: str, family: str, home: str) -> Player:
        """Register a player, or change their details; writes their folder's rensim.toml."""
        account, family, home = account.strip().upper(), " ".join(family.split()).upper(), " ".join(home.split())
        if not ACCOUNT_RE.fullmatch(account):
            raise SetupError("The account code is the letters and digits after ACCOUNT CODE: on the first "
                             "line of a turn result.")
        if not family or not home:
            raise SetupError("Give the family name and the home town too.")
        taken = self.by_account(account)
        if taken and taken.subject != subject:
            raise SetupError(f"Account {account} is already set up by another player. If it's yours, "
                             "ask whoever runs this server to release it.")
        now = time.time()
        old = self.get(subject)
        player = Player(subject, old.id if old else secrets.token_hex(8), account, family, home)
        with self.db() as con:
            con.execute("""INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (subject) DO UPDATE SET account = excluded.account, family = excluded.family,
                home = excluded.home, updated_at = excluded.updated_at""",
                        [subject, player.id, account, family, home, now, now])
        folder = player.folder(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "rensim.toml").write_text(
            "# Written by the hosted server from the player's set_up; change it with set_up, not here.\n"
            f"account = {toml_string(account)}\nfamily = {toml_string(family)}\nhome = {toml_string(home)}\n",
            encoding="utf-8")
        return player

    def release(self, account: str) -> bool:
        """Forget who claimed an account code (their folder stays on disk)."""
        with self.db() as con:
            return con.execute("DELETE FROM players WHERE account = ?", [account.upper()]).rowcount > 0


class Worker:
    """One player's rensim process, and the MCP client talking to it over stdio."""

    def __init__(self, player: Player, folder: Path):
        self.player, self.folder = player, folder
        self.client: Client | None = None
        self.stop = anyio.Event()
        self.ready = anyio.Event()
        self.done = anyio.Event()
        self.last_used = time.monotonic()
        self.busy = 0
        self.error: BaseException | None = None

    def params(self) -> StdioServerParameters:
        env = {k: os.environ[k] for k in CHILD_ENV if k in os.environ}
        env.update(RENSIM_CONFIG=str(self.folder / "rensim.toml"), RENSIM_HOSTED="1",
                   RUNTIME__DLTHUB_TELEMETRY="false", PYTHONUNBUFFERED="1")
        return StdioServerParameters(command=sys.executable, args=["-m", "renaissance.mcp_server"],
                                     env=env, cwd=str(self.folder))

    async def run(self):
        try:
            async with Client(self.params(), cache=None, mode="legacy") as client:
                self.client = client
                self.ready.set()
                await self.stop.wait()
        except BaseException as e:   # a process that died: the next call starts a new one
            self.error = e
            if not isinstance(e, Exception):
                raise
        finally:
            self.client = None
            self.ready.set()
            self.done.set()


class Pool:
    """The running workers, one per player, started on demand and stopped when idle. Lives in the
    hosted server's task group (`run`)."""

    def __init__(self, root: Path, idle_seconds: float = 15 * 60, max_running: int = 8):
        self.root, self.idle_seconds, self.max_running = root, idle_seconds, max_running
        self.workers: dict[str, Worker] = {}
        self.lock = anyio.Lock()
        self.tg: anyio.abc.TaskGroup | None = None

    async def run(self, *, task_status=anyio.TASK_STATUS_IGNORED):
        async with anyio.create_task_group() as tg:
            self.tg = tg
            task_status.started()
            try:
                while True:
                    await anyio.sleep(min(60, self.idle_seconds))
                    await self.reap()
            finally:
                for w in self.workers.values():
                    w.stop.set()

    async def reap(self, keep: int | None = None):
        """Stop workers idle longer than idle_seconds, and the least recently used idle ones while
        more than `keep` are running."""
        now = time.monotonic()
        idle = sorted((w for w in self.workers.values() if not w.busy), key=lambda w: w.last_used)
        excess = len(self.workers) - keep if keep is not None else 0
        for n, w in enumerate(idle):
            if n < excess or now - w.last_used > self.idle_seconds:
                await self.stop(w.player.id)

    async def stop(self, player_id: str):
        w = self.workers.pop(player_id, None)
        if w:
            w.stop.set()
            await w.done.wait()

    async def worker(self, player: Player) -> Worker:
        """The player's running worker, started if need be, marked busy (the caller unmarks it) so
        it isn't stopped as idle while in use."""
        async with self.lock:
            w = self.workers.get(player.id)
            if w and (w.client is None and w.ready.is_set() or w.player != player):
                await self.stop(player.id)   # died, or its settings changed: start afresh
                w = None
            if w is None:
                if len(self.workers) >= self.max_running:
                    await self.reap(keep=self.max_running - 1)
                w = Worker(player, player.folder(self.root))
                self.workers[player.id] = w
                assert self.tg is not None, "Pool.run isn't running"
                self.tg.start_soon(w.run)
            w.busy += 1
        await w.ready.wait()
        if w.client is None:
            w.busy -= 1
            raise RuntimeError(f"rensim didn't start: {w.error!r}")
        return w

    async def call(self, player: Player, method: str, *args, **kwargs):
        """Client.<method>(*args) on the player's worker, e.g. call(p, "call_tool", "status", {})."""
        w = await self.worker(player)
        try:
            return await getattr(w.client, method)(*args, **kwargs)
        finally:
            w.busy -= 1
            w.last_used = time.monotonic()


async def child_catalog(root: Path) -> tuple[list[types.Tool], list[types.Prompt], str]:
    """The tools, prompts and instructions a player's rensim offers, from a throwaway process on an
    empty folder (they don't depend on the player)."""
    folder = root / "catalog"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rensim.toml").write_text("", encoding="utf-8")
    w = Worker(Player("", "catalog", "", "", ""), folder)
    async with Client(w.params(), cache=None, mode="legacy") as client:
        tools = (await client.list_tools()).tools
        prompts = (await client.list_prompts()).prompts
        return tools, prompts, client.instructions or ""


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m renaissance.players", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("RENSIM_ROOT", "/data")))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("release").add_argument("account")
    args = parser.parse_args(argv)
    reg = Registry(args.root)
    if args.command == "list":
        for p in reg.all():
            print(f"{p.account:8} {p.family:20} {p.home:20} players/{p.id}")
    elif not reg.release(args.account):
        print(f"No player has account {args.account.upper()}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
