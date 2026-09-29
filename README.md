# MCP Servers

A collection of small [FastMCP](https://gofastmcp.com) servers, one per folder under `src/`.

| Server | What it does |
|---|---|
| `web` | Web search (DuckDuckGo), fetch pages as HTML/markdown, extract links, search within pages, raw HTTP requests |
| `youtube` | Inspect videos with yt-dlp, download best audio as tagged FLAC (cover art embedded), set FLAC tags |
| `plexamp` | Music-only Plex control: search, sonic similarity, stations, audio/smart playlists, play queues, Plexamp player control. See [docs/plexamp-mcp-spec.md](docs/plexamp-mcp-spec.md) |

## Setup

Requires Python 3.10+ and [uv](https://github.com/astral-sh/uv). `youtube` also needs `ffmpeg` on your PATH.

```bash
uv sync --all-extras          # or: make dev
cp .env.template .env         # optional overrides
make config                   # writes configs/mcp_config.json for every server
```

Paste the entries from `configs/mcp_config.json` into your MCP client (Claude Desktop, Claude Code, …). `web` and `youtube` are stdio servers the client launches itself:

```json
"web": { "command": "uv", "args": ["run", "-m", "src.web.server"], "cwd": "/path/to/MCP-Servers" }
```

### plexamp: HTTP server with Plex sign-in

`plexamp` runs as a local HTTP server, so you can sign in from the client's MCP menu instead of pasting a token:

1. Start it: `uv run -m src.plexamp.server` (listens on `http://127.0.0.1:8765/mcp`).
2. Add it: `claude mcp add --transport http plexamp http://127.0.0.1:8765/mcp`.
3. In Claude Code, open `/mcp` → **plexamp** → **Authenticate**, then approve on the Plex page that opens.

The Plex token and server are saved in `~/.plexamp-mcp/auth.json`, so you stay signed in across restarts. You can revoke access any time under Plex → *Authorized Devices* ("plexamp-mcp").

To keep the server running, add a Task Scheduler task that runs at log on: program `uv`, arguments `run -m src.plexamp.server`, start in the repo folder. For clients without OAuth, `uv run -m src.plexamp.server --stdio` works with `PLEX_URL` and `PLEX_TOKEN` set.

## Configuration

All settings come from environment variables. plexamp also reads `.env`.

| Server | Variable | Default |
|---|---|---|
| web | `REQUEST_TIMEOUT` / `MAX_PAGE_LENGTH` / `MAX_REDIRECTS` / `MAX_CONTENT_BYTES` | `30` / `10000` / `5` / `10485760` |
| youtube | none (downloads go to `~/Downloads` unless `output_dir` is passed) | |
| plexamp | all optional: `PLEX_SERVER`, `PLEXAMP_PORT`, `PLEX_MUSIC_SECTIONS`, `PLEXAMP_PLAYERS`, overrides `PLEX_URL`/`PLEX_TOKEN`, … | see `.env.template` |

## Development

```bash
uv run -m src.<server>.server            # run (stdio; plexamp: HTTP)
uv run fastmcp dev src/<server>/server.py  # MCP Inspector
uv run pytest                            # tests
make fix                                 # black + ruff
```

Adding a server is covered in [CLAUDE.md](CLAUDE.md).

## Notes

- `web` has **no network restrictions**; it can reach any URL. It still caps response size and redirects.
- `plexamp` returns Plex file paths exactly as the server reports them. It only acts on music libraries.
