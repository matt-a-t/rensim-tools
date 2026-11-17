# Hosting rensim on the web

The desktop extension serves one player from their own computer. The hosted server
(`renaissance/hosted.py`) serves many players at once from a server you run. Each player adds it
to Claude as a **connector**, signs in, and uses it from any Claude app, phone included.

```
Claude (web, phone, desktop) ──HTTPS──▶ Caddy ──▶ rensim-hosted ──stdio──▶ rensim (player A, folder A)
          │                                        │      │          └────▶ rensim (player B, folder B)
          └── sign-in ──▶ WorkOS AuthKit (or another OAuth service) ◀── checks each token
```

- **One process per player.** `rensim-hosted` doesn't run the game code itself. Each signed-in
  player gets their own `rensim` process, the same server the desktop extension runs, over their
  own game folder. The process starts on their first call and stops after `RENSIM_IDLE_MINUTES`
  unused. A player's process can't see anyone else's folder.
- **The players' folders** are under `RENSIM_ROOT` (`/data` in the container):
  `players/<id>/` has the same layout as a desktop game folder, and `registry.sqlite` maps each
  sign-in to its folder, account code, family and home town.
- **Shared turns** (a follow-up) will go in `players/<id>/data/raw/shared/<account>/turns/`, one
  folder per player sharing, so stopping one share deletes only that folder. The hosted server
  copies them in; players' own processes never read each other's folders. The loader already
  reads them, and a turn a player also saved themselves is loaded once.
- **Who runs it can read everything.** Every player's turn results and orders are on your server.
  Tell the players that before they join.

## What a player does

1. In Claude on the web: **Settings → Connectors → Add custom connector**. Name it "Renaissance"
   and give it the server's address, ending in `/mcp` (e.g. `https://rensim.example.com/mcp`).
   Custom connectors need a paid Claude plan; Anthropic's help pages list which ones.
2. **Connect** and sign in. Connectors added on the web are in the phone apps too.
3. In a chat, say "set me up for Renaissance". Claude asks for the account code, family, home
   town and the invite code, then calls `set_up`.
4. After that, it works like the desktop version: paste a turn result, ask questions, plan a turn.
   For Word documents and PDFs, Claude gives a link to an upload page (`upload_link`). Upload the
   files there, then say "file my inbox".

## Setting it up

### 1. The sign-in service

Any OAuth service that issues JWT access tokens, and lets Claude register itself (dynamic client
registration), will work. WorkOS AuthKit is the simplest; its free tier is far more than this
needs. With WorkOS:

1. Create a WorkOS account, and an environment for rensim alone (so its tokens are only for rensim).
2. Turn on AuthKit, with the sign-in methods you want (Google, email codes...).
3. Turn on **dynamic client registration**, which Claude's connectors use. WorkOS's guide for
   MCP servers shows where it is.
4. Note the AuthKit domain, like `https://your-subdomain.authkit.app`: that's `RENSIM_AUTH_ISSUER`.

The server finds the service's signing keys from the issuer's metadata. If that fails, set
`RENSIM_AUTH_JWKS_URL`. If the service puts the server's address in the tokens' audience, set
`RENSIM_AUTH_AUDIENCE` to `https://<your host>/mcp` so tokens for anything else are refused.

### 2. The server

Any Linux server with Docker and about 2 GB of memory: EC2, Google Compute Engine, a VPS. A
player's process uses about 70 MB idle and about 180 MB once their turns are loaded, and loading
runs dbt briefly on top of that. With `RENSIM_MAX_RUNNING=8` (the default), 2 GB is comfortable.

1. Point a DNS name at the server (e.g. `rensim.example.com`), and open ports 80 and 443.
2. On the server, check out this repository, then:

   ```
   cp deploy/rensim.env.example deploy/rensim.env     # fill it in: below
   RENSIM_DOMAIN=rensim.example.com docker compose -f deploy/compose.yml up -d --build
   ```

   Caddy gets the HTTPS certificate itself. The players' folders are in the `rensim-data` volume.
3. Check `https://rensim.example.com/healthz` says `ok`.

To update: `git pull`, then the same `docker compose ... up -d --build`.

### Settings (`deploy/rensim.env`)

| Variable | |
|---|---|
| `RENSIM_PUBLIC_URL` | The address players reach it at, e.g. `https://rensim.example.com`. Required. |
| `RENSIM_AUTH_ISSUER` | The sign-in service's issuer. Required. |
| `RENSIM_AUTH_AUDIENCE` | The token audience to require (optional, see above). |
| `RENSIM_AUTH_JWKS_URL` | The service's signing keys, if they can't be found from the issuer. |
| `RENSIM_INVITE_CODE` | If set, `set_up` asks for it. Give it to the players you invite: without it, anyone who can sign in could set up. |
| `RENSIM_IDLE_MINUTES` | Stop a player's process after this long unused (default 15). |
| `RENSIM_MAX_RUNNING` | At most this many player processes at once (default 8); the least recently used idle one stops to make room. |

## Looking after it

- **Who has signed up**: `docker compose -f deploy/compose.yml exec rensim python -m renaissance.players list`
- **An account code claimed by the wrong player**: `... python -m renaissance.players release ABC`.
  The real player can then `set_up` with it. The old folder stays on disk.
- **Backups**: the players keep their original turn results and newsletters, so these can be
  uploaded again. What only the server has is the claims Claude drew from publications, the
  answers to "are these the same family?", and saved orders. Back up the `rensim-data` volume
  (a disk snapshot is enough) if those matter.
- **Logs**: `docker compose -f deploy/compose.yml logs rensim`.

## Trying it on your own computer

With Docker, and a tunnel to give it a public HTTPS address (Cloudflare Tunnel, ngrok):

```
docker build -t rensim-hosted .
docker run -p 8000:8000 -v rensim-data:/data \
  -e RENSIM_PUBLIC_URL=https://<tunnel address> -e RENSIM_AUTH_ISSUER=https://<subdomain>.authkit.app \
  -e RENSIM_INVITE_CODE=test rensim-hosted
```

Then add `https://<tunnel address>/mcp` as a connector. Without Docker, `uv run rensim-hosted`
with the same variables and `RENSIM_ROOT` set to a folder.

## Not yet

- **Sharing** turn results between players: the folder layout and loader are ready, the tools
  aren't.
- **Downloading** a player's folder, so each player can keep their own backup.
- **Deleting** a player: for now, `release` their account and remove `players/<id>/` by hand.
