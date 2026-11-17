"""rensim on the web, for many players: one MCP server at https://<your host>/mcp that each player
adds to Claude as a connector, signs in to, and uses from the Claude apps, phone included.

    rensim-hosted        # settings from the environment, below; see docs/hosting.md

It doesn't run the game code itself. Each signed-in player is served by their own rensim process
over their own game folder (renaissance.players), the same server the desktop extension runs, and
this server passes the player's calls on to it. Its own tools are the ones only it can do:

- `set_up`: who the player is (account code, family, home town), which the desktop extension's
  install form asks. Every other tool asks for it first.
- `upload_link`: a link to a page where the player can upload files from their phone (Word
  documents, PDFs) into their inbox, for `intake_inbox`. Pasted text and attached text files go
  to `intake` as on the desktop.

Settings (environment variables):
    RENSIM_PUBLIC_URL      the address players reach it at, e.g. https://rensim.example.com (required)
    RENSIM_AUTH_ISSUER     the sign-in service's issuer, e.g. https://example.authkit.app (required)
    RENSIM_AUTH_AUDIENCE   the token audience to require (optional; see renaissance.auth)
    RENSIM_AUTH_JWKS_URL   the service's JWKS, if it can't be found from the issuer (optional)
    RENSIM_INVITE_CODE     if set, `set_up` asks for it: give it to the players you invite
    RENSIM_ROOT            where the players' folders and the registry go (default /data)
    RENSIM_IDLE_MINUTES    stop a player's process after this long unused (default 15)
    RENSIM_MAX_RUNNING     at most this many player processes at once (default 8)
    PORT                   the port to listen on (default 8000)
"""
from __future__ import annotations

import argparse
import hmac
import html
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel.server import Server
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import Route

from .players import Player, Pool, Registry, SetupError, child_catalog

UPLOAD_MINUTES = 30
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_SUFFIXES = {"", ".txt", ".text", ".md", ".docx", ".pdf"}   # what intake_inbox can file

HOSTED_INSTRUCTIONS = """
This is rensim on the web: each player signs in and has their own game folder on the server.
If a tool says the player isn't set up, ask for their account code (the first line of every turn
result: ACCOUNT CODE: ABC), their family and their home town, and call `set_up`{invite}.
The player has no folder to put files in. Pasted text and attached text files go to `intake` as
usual; for Word documents and PDFs, call `upload_link` and give them the link, and when they say
they've uploaded, call `intake_inbox`."""


@dataclass
class Config:
    public_url: str
    issuer: str
    root: Path = Path("/data")
    audience: str | None = None
    jwks_url: str | None = None
    invite_code: str | None = None
    idle_minutes: float = 15
    max_running: int = 8

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ
        missing = [k for k in ("RENSIM_PUBLIC_URL", "RENSIM_AUTH_ISSUER") if not env.get(k)]
        if missing:
            raise SystemExit(f"Set {' and '.join(missing)} (see docs/hosting.md).")
        return cls(public_url=env["RENSIM_PUBLIC_URL"].rstrip("/"), issuer=env["RENSIM_AUTH_ISSUER"],
                   root=Path(env.get("RENSIM_ROOT", "/data")), audience=env.get("RENSIM_AUTH_AUDIENCE") or None,
                   jwks_url=env.get("RENSIM_AUTH_JWKS_URL") or None, invite_code=env.get("RENSIM_INVITE_CODE") or None,
                   idle_minutes=float(env.get("RENSIM_IDLE_MINUTES", 15)),
                   max_running=int(env.get("RENSIM_MAX_RUNNING", 8)))


@dataclass
class Uploads:
    """Upload links: token -> (player id, expiry). In memory: a restart just means asking again."""
    links: dict[str, tuple[str, float]] = field(default_factory=dict)

    def new(self, player: Player) -> str:
        now = time.time()
        self.links = {t: v for t, v in self.links.items() if v[1] > now}
        token = secrets.token_urlsafe(24)
        self.links[token] = (player.id, now + UPLOAD_MINUTES * 60)
        return token

    def player_id(self, token: str) -> str | None:
        found = self.links.get(token)
        return found[0] if found and found[1] > time.time() else None


def safe_name(name: str) -> str | None:
    """An uploaded file's name, made safe to save (None if its kind isn't one intake_inbox files)."""
    name = Path(name.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        return None
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")[:60] or "upload"
    return stem + suffix


def text_result(text: str, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=error)


class Hosted:
    def __init__(self, config: Config):
        self.config = config
        self.registry = Registry(config.root)
        self.pool = Pool(config.root, idle_seconds=config.idle_minutes * 60, max_running=config.max_running)
        self.uploads = Uploads()
        self.tools: list[types.Tool] = []
        self.prompts: list[types.Prompt] = []
        self.child_instructions = ""

    # ---- this server's own tools ----------------------------------------------------------

    def own_tools(self) -> list[types.Tool]:
        props = {"account": {"type": "string", "description": "The account code: ACCOUNT CODE: on a turn result's first line."},
                 "family": {"type": "string", "description": "The player's family name."},
                 "home": {"type": "string", "description": "The player's home town; distances are measured from it."}}
        required = ["account", "family", "home"]
        if self.config.invite_code:
            props["invite"] = {"type": "string", "description": "The invite code the player was given."}
            required.append("invite")
        changes = types.ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
        return [
            types.Tool(name="set_up", description=(
                "Set up the player (first time) or change who they are: their account code, family and "
                "home town. Every other tool needs this done first."),
                input_schema={"type": "object", "properties": props, "required": required}, annotations=changes),
            types.Tool(name="upload_link", description=(
                f"A link, good for {UPLOAD_MINUTES} minutes, to a page where the player uploads files (Word "
                "documents, PDFs, text files) into their inbox. Give them the link; when they've uploaded, "
                "call intake_inbox."),
                input_schema={"type": "object", "properties": {}}, annotations=changes),
        ]

    def set_up(self, subject: str, args: dict) -> types.CallToolResult:
        invite = self.config.invite_code
        if invite and not hmac.compare_digest(str(args.get("invite", "")).strip(), invite):
            return text_result("That invite code isn't right: ask whoever runs this server for it.", error=True)
        try:
            p = self.registry.set_up(subject, str(args.get("account", "")), str(args.get("family", "")),
                                     str(args.get("home", "")))
        except SetupError as e:
            return text_result(str(e), error=True)
        return text_result(f"Set up: account {p.account}, family {p.family}, home town {p.home}. "
                           "Next, call `status`; if no turn result is loaded yet, ask for the latest one.")

    # ---- the MCP handlers -----------------------------------------------------------------

    @staticmethod
    def subject(ctx) -> str:
        user = getattr(ctx.request, "user", None)
        token = getattr(user, "access_token", None)
        if not token or not token.subject:
            raise PermissionError("not signed in")
        return token.subject

    def instructions(self) -> str:
        invite = ", with the invite code they were given" if self.config.invite_code else ""
        return self.child_instructions + "\n" + HOSTED_INSTRUCTIONS.format(invite=invite)

    def server(self) -> Server:
        async def list_tools(ctx, params):
            return types.ListToolsResult(tools=self.own_tools() + self.tools)

        async def call_tool(ctx, params: types.CallToolRequestParams):
            subject = self.subject(ctx)
            args = params.arguments or {}
            if params.name == "set_up":
                result = self.set_up(subject, args)
                if not result.is_error:
                    player = self.registry.get(subject)
                    await self.pool.stop(player.id)   # its next call starts with the new settings
                return result
            player = self.registry.get(subject)
            if player is None:
                return text_result("This player isn't set up yet: ask for their account code, family and "
                                   "home town, and call set_up.", error=True)
            if params.name == "upload_link":
                url = f"{self.config.public_url}/upload/{self.uploads.new(player)}"
                return text_result(f"Upload page (good for {UPLOAD_MINUTES} minutes): {url}")
            if params.name not in {t.name for t in self.tools}:
                return text_result(f"No tool {params.name!r}.", error=True)
            try:
                return await self.pool.call(player, "call_tool", params.name, args)
            except Exception as e:
                return text_result(f"rensim couldn't run {params.name}: {type(e).__name__}: {e}", error=True)

        async def list_prompts(ctx, params):
            return types.ListPromptsResult(prompts=self.prompts)

        async def get_prompt(ctx, params: types.GetPromptRequestParams):
            player = self.registry.get(self.subject(ctx))
            if player is None:
                text = "I'm not set up yet: ask me for my account code, family and home town, and call set_up."
                return types.GetPromptResult(messages=[types.PromptMessage(
                    role="user", content=types.TextContent(type="text", text=text))])
            return await self.pool.call(player, "get_prompt", params.name, params.arguments or {})

        return Server("rensim", instructions=self.instructions(), on_list_tools=list_tools,
                      on_call_tool=call_tool, on_list_prompts=list_prompts, on_get_prompt=get_prompt)

    # ---- the upload page ------------------------------------------------------------------

    def page(self, body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Upload to rensim</title>
<style>body{{font:17px/1.5 system-ui,sans-serif;max-width:32rem;margin:2rem auto;padding:0 16px}}
input,button{{font:inherit}} button{{padding:.6rem 1.2rem;margin-top:1rem}}</style></head>
<body><h1>Upload to rensim</h1>{body}</body></html>""", status_code=status)

    async def upload(self, request: Request):
        player = self.registry.by_id(self.uploads.player_id(request.path_params["token"]) or "")
        if player is None:
            return self.page("<p>This link has expired. Ask Claude for a new upload link.</p>", 404)
        if request.method == "GET":
            return self.page(f"""<p>Files for account <b>{html.escape(player.account)}</b>: turn results,
newsletters, Word documents (.docx), PDFs and text files.</p>
<form method="post" enctype="multipart/form-data">
<input type="file" name="files" multiple required><br><button type="submit">Upload</button></form>""")
        inbox = player.folder(self.config.root) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        saved, skipped = [], []
        async with request.form(max_files=20, max_part_size=MAX_UPLOAD_BYTES) as form:
            for upload in form.getlist("files"):
                if not getattr(upload, "filename", None):
                    continue
                name = safe_name(upload.filename)
                data = await upload.read(MAX_UPLOAD_BYTES + 1)
                if name is None or len(data) > MAX_UPLOAD_BYTES:
                    skipped.append(upload.filename)
                    continue
                dest = inbox / name
                n = 2
                while dest.exists():
                    dest = inbox / f"{Path(name).stem}_{n}{Path(name).suffix}"
                    n += 1
                dest.write_bytes(data)
                saved.append(dest.name)
        items = "".join(f"<li>{html.escape(s)}</li>" for s in saved)
        note = (f"<p>Not uploaded (only .docx, .pdf and text files, up to 25 MB): "
                f"{html.escape(', '.join(skipped))}</p>" if skipped else "")
        return self.page(f"<p>Uploaded:</p><ul>{items}</ul>{note}"
                         "<p>Go back to Claude and say <b>file my inbox</b>.</p>" if saved else
                         f"<p>Nothing was uploaded.</p>{note}")

    # ---- the app --------------------------------------------------------------------------

    def app(self, token_verifier: TokenVerifier | None = None) -> Starlette:
        from .auth import JWTVerifier
        c = self.config
        verifier = token_verifier or JWTVerifier(c.issuer, audience=c.audience, jwks_url=c.jwks_url)
        auth = AuthSettings(issuer_url=c.issuer, resource_server_url=f"{c.public_url}/mcp",
                            validate_token_resource=False)   # the verifier checks the audience
        routes = [Route("/upload/{token}", self.upload, methods=["GET", "POST"]),
                  Route("/healthz", lambda r: PlainTextResponse("ok"))]
        server = self.server()
        app = server.streamable_http_app(auth=auth, token_verifier=verifier, host="0.0.0.0",
                                         custom_starlette_routes=routes)
        inner = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(a):
            self.tools, self.prompts, self.child_instructions = await child_catalog(c.root)
            server.instructions = self.instructions()
            async with anyio.create_task_group() as tg:
                await tg.start(self.pool.run)
                async with inner(a) as state:
                    yield state
                tg.cancel_scope.cancel()

        app.router.lifespan_context = lifespan
        return app


def main(argv=None):
    argparse.ArgumentParser(prog="rensim-hosted", description=__doc__.splitlines()[0]).parse_args(argv)
    import uvicorn
    config = Config.from_env()
    uvicorn.run(Hosted(config).app(), host="0.0.0.0", port=int(os.environ.get("PORT", 8000)),
                proxy_headers=True, forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()
