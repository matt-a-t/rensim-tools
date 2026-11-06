# Development

The whole app is the MCP server in `renaissance/mcp_server.py`. Players get it as a Claude
desktop extension: `manifest.json` (its `uv` server type means Claude Desktop installs Python and
the dependencies in `pyproject.toml` / `uv.lock` itself), packed into a `.mcpb` file. There's no
command line. This page covers running it from source.

## Set up and test

```bash
uv sync
uv run python -m unittest      # about 2½ minutes
```

The tests run against the made-up account in `tests/fixtures/` and never touch real game files.
`tests/test_tools.py` starts the real server the way Claude Desktop does (its own process,
configured through environment variables) on a temporary copy of the fixtures. It also checks
that nothing but protocol messages reaches stdout: anything printed there breaks the connection,
so every tool captures what the pipeline prints.

## Try the server

The tools write to the game folder, so point them at a copy of the fixtures (or of your own
game), never at `tests/fixtures` itself:

```bash
cp -r tests/fixtures /tmp/game
```

**With the MCP Inspector**, which lists the tools and prompts and calls them, outside Claude:

```bash
npx @modelcontextprotocol/inspector -e RENSIM_CONFIG=/tmp/game/rensim.toml -- \
  uv --directory "$PWD" run rensim-mcp
```

**In Claude Desktop without packing**: Settings → Developer → Edit Config, and add the server to
`claude_desktop_config.json` (then restart Claude):

```json
{
  "mcpServers": {
    "rensim-dev": {
      "command": "uv",
      "args": ["--directory", "/path/to/rensim-tools", "run", "rensim-mcp"],
      "env": { "RENSIM_CONFIG": "/tmp/game/rensim.toml" }
    }
  }
}
```

Use absolute paths: Claude starts the server from an unspecified working folder, with few of your
environment variables. Turn the installed extension off while you do this, so Claude doesn't see
two copies of every tool. Code changes need a restart of the server (or of Claude).

**Packed, as a player gets it**:

```bash
npx -y @anthropic-ai/mcpb@2.1.2 pack . rensim.mcpb
```

Then Settings → Extensions → Advanced settings → Install Extension…. `.mcpbignore` keeps the
bundle to the package, `pyproject.toml` and `uv.lock`. Bump `version` in `manifest.json` and
`pyproject.toml` together.

**Logs.** On a Mac, `~/Library/Logs/Claude/` (`main.log`, `mcp*.log`). On Windows,
`%LOCALAPPDATA%\Claude\logs\main.log`; look for `LocalMcpServerManager` and `UV Runtime`.
Installed extensions and their settings are in Claude's data folder under `Claude Extensions` and
`Claude Extensions Settings`. An extension whose form has required fields installs switched off.

## Load and dbt by hand

From a game folder (or a copy of the fixtures), `RENSIM_CONFIG` pointing at its `rensim.toml`:

```bash
uv run python -m renaissance.run                       # what the refresh tool does, with its output
uv run python -m renaissance.dbt_runner test           # any dbt command on the database
uv run python -m renaissance.dbt_runner docs generate --static   # .dbt/target/static_index.html
```

## Checking what Claude makes of a tool

The tests check what the tools return, not what Claude does with it. After changing a tool or
its description, ask Claude through the Claude Code CLI (it uses your Claude plan), in a copy of
the fixtures:

```bash
cd /tmp/game && uv --directory /path/to/rensim-tools run python -m renaissance.run
claude -p "who arrived at my home town last turn?" --strict-mcp-config \
  --mcp-config '{"mcpServers":{"rensim":{"command":"uv","args":["--directory","/path/to/rensim-tools","run","rensim-mcp"],"env":{"RENSIM_CONFIG":"/tmp/game/rensim.toml"}}}}' \
  --allowedTools 'mcp__rensim__*' < /dev/null
```

The answer should be Ramsay BOLTON, arriving in Carlisle with 200 troops, and nobody else.

## Changing things

- **A view**: edit its model in `renaissance/dbt/models/` (a `SELECT`, with `{{ ref('...') }}` for
  other models and `{{ source('game', '...') }}` for loaded tables), and its description and tests
  in the `.yml` beside it. Claude reads those descriptions through `describe`, so keep them
  accurate. If an answer is meant to change, regenerate the expected answers and review the diff:
  `UPDATE_ANSWERS=1 uv run python -m unittest tests.test_answers`.
- **A tool**: a function in `build_server`, its docstring written for Claude (it's the tool's
  description). Tools that write take the `one_change` lock and call `rebuild()` when they change
  what's loaded. Test it in `tests/test_tools.py`, or `tests/test_mcp.py` if it only reads.
- **How turns are planned**: the `PLAN_TURN` prompt and `INSTRUCTIONS` in `mcp_server.py`; how
  claims are drawn: `renaissance/claims_extraction.md`, which `intake` hands to Claude.

CI (`.github/workflows/test.yml`) runs the tests, loads the fixtures, runs `dbt build`, publishes
the dbt docs site as a download from each run's summary page, and validates and packs the
extension.

## Releasing

1. Set the new version in both `manifest.json` and `pyproject.toml`, and merge that to `main`.
2. Tag the merge and push the tag:

   ```bash
   git tag v0.2.0 origin/main && git push origin v0.2.0
   ```

`.github/workflows/release.yml` checks the tag matches both versions, runs the tests, packs the
extension and publishes a GitHub release with `rensim.mcpb` and notes generated from the merged
pull requests. The README links to `releases/latest/download/rensim.mcpb`, so players always get
the newest. Check an upgrade over an installed version keeps the extension's settings before
relying on it.

The bundle isn't signed (Claude Desktop logs "Installing unsigned extension"). Signing
(`mcpb sign`) needs a code-signing certificate.
