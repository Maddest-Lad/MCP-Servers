# CLAUDE.md

A collection of Python FastMCP servers. Each server is self-contained in `src/<name>/`.

## Layout

```
src/<name>/__init__.py      # empty
src/<name>/server.py        # mcp = FastMCP("<name>"), @mcp.tool functions, main()
src/<name>/helpers.py       # everything that isn't a tool: config, clients, parsing
src/<name>/<topic>.py       # optional split when helpers mixes unrelated concerns (e.g. plexamp/players.py)
src/generate_mcp_configs.py # auto-discovers src/*/server.py -> configs/mcp_config.json (gitignored)
tests/<name>/test_*.py      # pytest, asyncio_mode=auto
docs/                       # longer specs (e.g. plexamp-mcp-spec.md)
```

Servers are run as modules from the repo root (`uv run -m src.<name>.server`), so imports are absolute: `from src.<name>.helpers import ...`.

## Adding a server

1. Copy the shape of `src/youtube/server.py`:
   ```python
   mcp = FastMCP("<name>")

   @mcp.tool
   async def do_thing(arg: str, limit: int = 20) -> dict[str, Any]:
       """First line is what the model sees; explain args/ids the model must pass."""

   def main() -> None:
       mcp.run()

   if __name__ == "__main__":
       main()
   ```
2. Add `<name>-mcp = "src.<name>.server:main"` under `[project.scripts]` in `pyproject.toml`, and add any dependencies there (`uv lock` afterwards).
3. If it needs config, read it with `os.getenv` at module top in `helpers.py` (plexamp calls `load_dotenv()`), and add the variables to `.env.template`.
4. Run `make config`, add a row to the README table, and write tests.

## Servers that need a user login (HTTP + OAuth)

Stdio servers can't show "Authenticate" in a client's `/mcp` menu; only HTTP servers can. `src/plexamp` is the template for this:
- `auth.py` subclasses `fastmcp.server.auth.OAuthProvider`. FastMCP then serves discovery metadata, `/register`, `/authorize`, `/token`, and the bearer check on `/mcp`. `authorize()` redirects to the upstream login page, and a `@mcp.custom_route` callback finishes it and redirects back with a code.
- Store upstream credentials outside the repo (`~/.<name>-mcp/`) and persist issued MCP tokens as hashes so restarts don't force re-auth.
- In `main()`, set `mcp.auth = Provider(base_url)` and call `mcp.run(transport="http", host=..., port=...)`. Keep a `--stdio` path that uses env credentials.
- Define `MCP_CONFIG = {"type": "http", "url": ".../mcp"}` in `server.py`; `generate_mcp_configs.py` emits it instead of a uv command.
- HTTP servers must already be running; the client won't launch them.

## Conventions

- **Tools are thin.** Validation, HTTP, and normalization belong in helpers. Tools compose helpers and return plain `dict`s (no model classes).
- **Async everywhere.** Use `httpx.AsyncClient` for HTTP. Wrap blocking libraries (yt-dlp, mutagen) with `to_thread` (see `src/youtube/helpers.py`).
- **Never write to stdout.** It is the MCP stdio transport. Log with `logging`, and wrap noisy libraries in `silence_stdio()`.
- **Errors.** Raise `fastmcp.exceptions.ToolError` (or a subclass). `src/plexamp/helpers.py:PlexError` gives a stable JSON `{code, message, details}` the model can act on. Prefer a coded error with candidates over guessing (e.g. `ambiguous`).
- **Model-friendly output.** Return compact summaries with stable ids. Paginate lists with `start`/`limit` (capped) and return `total`/`truncated`. Write tools return the re-read post-state. Destructive tools require `confirm=True`.
- Style: black + ruff (line length 88), type hints with `X | None`, and `from __future__ import annotations`.

## Testing

- Call a tool's function directly: `await server.<tool>.fn(...)`.
- Fake HTTP with `httpx.MockTransport`. Inject the transport into the server's client and `monkeypatch` the module-level instance (see the `FakePlex` fixture in `tests/plexamp/test_server.py`).
- `uv run pytest -q`; `uv run ruff check src tests && uv run black --check src tests`.
- For manual end-to-end checks, use `uv run fastmcp dev src/<name>/server.py` (MCP Inspector).

## Gotchas

- Windows dev box: use the Bash tool with `.venv/Scripts/python`, or use `uv run`.
- Don't add an `__init__.py` at the repo root. It makes pytest import the root as a package and breaks collection.
- `configs/` and `.env` are gitignored; `.env.template` is committed.
- plexamp credentials live in `~/.plexamp-mcp/auth.json` (override with `PLEX_AUTH_FILE`). Tests point `auth.AUTH_FILE` at `tmp_path`.
- Modules in `src/*/server.py` get imported by `generate_mcp_configs.py`, so keep them free of side effects at import (no network, no `run()`).
