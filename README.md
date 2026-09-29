# MCP Web Server

A Model Context Protocol (MCP) server that provides web search and HTTP request capabilities. This server provides tools for web scraping, search, and making HTTP requests to any URL without network restrictions.

## Project Structure

```
src/
├── servers/
│   └── web.py              # Main web server with MCP tools
├── utils/
│   └── web_helpers.py      # Helper utilities for web operations
└── generate_mcp_configs.py # Generate MCP client configurations
```

## Features

### Web Search Tools
- **search_web**: Perform web searches using DuckDuckGo
- **visit_webpage**: Fetch and process webpage content with optional markdown conversion
- **extract_urls_from_webpage**: Extract all URLs and domains from a webpage
- **search_in_webpage**: Search for specific content within a webpage

### HTTP Request Tools
- **http_request**: Make HTTP requests (GET, POST, PUT, DELETE, PATCH) to any URL

## Plexamp Music Server (`src/plexamp`)

A music-only server for Plex Media Server over the LAN. It covers search and browse, sonic similarity and Sonic Adventure, stations, audio and smart playlists, play queues, and control of Plexamp / Plexamp Headless players. The full tool and endpoint reference is in [docs/plexamp-mcp-spec.md](docs/plexamp-mcp-spec.md).

Copy `.env.template` to `.env` in the repo root and fill it in:

```env
PLEX_URL=http://192.168.1.10:32400   # LAN address (players connect back to it)
PLEX_TOKEN=your-plex-token
PLEX_MUSIC_SECTIONS=                 # optional: comma-separated section ids
PLEXAMP_PLAYERS=192.168.1.20:32500   # optional: headless players PMS doesn't list
```

Run it with `uv run -m src.plexamp.server`, or inspect it with `uv run fastmcp dev src/plexamp/server.py`.

- **Find**: `music_search`, `music_browse`, `music_get_item`, `music_get_track_files`, `music_get_recent`, `music_get_hubs`
- **Discover**: `music_list_stations`, `music_start_radio`, `music_similar_tracks`, `music_sonic_adventure`
- **Playlists**: `music_list_playlists`, `music_get_playlist`, `music_create_playlist`, `music_update_playlist`, `music_add_playlist_tracks`, `music_remove_playlist_items`, `music_move_playlist_item`, `music_delete_playlist`
- **Queue and players**: `music_play`, `music_get_queue`, `music_queue_items`, `music_move_queue_item`, `music_remove_queue_item`, `music_list_players`, `music_now_playing`, `music_control_player`

## Installation

This server requires Python 3.10 or higher and uses [uv](https://github.com/astral-sh/uv) for dependency management.

### Using uv (Recommended)

1. Clone or download the standalone directory
2. Navigate to the standalone directory
3. Install dependencies:
   ```bash
   uv sync
   ```

### Manual Installation

If you prefer to use pip:

```bash
pip install mcp httpx beautifulsoup4 markdownify duckduckgo-search
```

## Configuration

The server can be configured using environment variables or a `.env` file. Default values are provided for all settings.

### Environment Variables

- `REQUEST_TIMEOUT`: HTTP request timeout in seconds (default: 30)
- `MAX_PAGE_LENGTH`: Maximum page length in characters before truncation (default: 10000)
- `MAX_REDIRECTS`: Maximum number of redirects to follow (default: 5)
- `MAX_CONTENT_BYTES`: Maximum content size in bytes (default: 10485760 = 10MB)

### Example .env file

```env
REQUEST_TIMEOUT=30
MAX_PAGE_LENGTH=10000
MAX_REDIRECTS=5
MAX_CONTENT_BYTES=10485760
```

## Usage

### Running the Server

The project includes a Makefile with convenient commands:

```bash
# Run server with STDIO transport (default)
make run

# Run server with HTTP transport on port 8000
make run-http

# Install dependencies
make install          # Production dependencies only
make dev-install      # All dependencies including dev tools

# Generate MCP client configuration examples
make config

# Code formatting and cleanup
make fix              # Format code with black and ruff

# View all available commands
make help
```

You can also run the server directly:

```bash
# Using uv (recommended)
uv run src/servers/web.py

# Or directly with Python
python src/servers/web.py
```

### Using with MCP Clients

Add the server to your MCP client configuration. For example, with Claude Desktop:

```json
{
  "mcpServers": {
    "web": {
      "command": "uv",
      "args": ["run", "/path/to/MCP-Servers/src/servers/web.py"],
      "cwd": "/path/to/MCP-Servers"
    }
  }
}
```

Or use the configuration generator:

```bash
make config
```

This will generate both standard Python and uv-based configurations that you can copy into your MCP client.

## Tools Reference

### search_web

Perform a web search and return formatted results.

**Parameters:**
- `query` (string, required): The search query
- `max_results` (integer, optional): Maximum results to return (1-100, default: 10)

**Example:**
```json
{
  "query": "python web scraping",
  "max_results": 5
}
```

### visit_webpage

Visit a webpage and return its content.

**Parameters:**
- `url` (string, required): The webpage URL
- `timeout` (integer, optional): Request timeout in seconds
- `max_length` (integer, optional): Maximum content length (-1 for unlimited)
- `convert_to_markdown` (boolean, optional): Convert HTML to markdown

**Example:**
```json
{
  "url": "https://example.com",
  "convert_to_markdown": true,
  "max_length": 5000
}
```

### extract_urls_from_webpage

Extract all URLs from a webpage.

**Parameters:**
- `url` (string, required): The webpage URL to analyze
- `include_domains` (boolean, optional): Include domain list (default: true)
- `include_internal_only` (boolean, optional): Only internal URLs (default: false)
- `timeout` (integer, optional): Request timeout in seconds

### search_in_webpage

Search for specific content within a webpage.

**Parameters:**
- `url` (string, required): The webpage URL to search
- `search_pattern` (string, required): Text pattern to search for
- `case_sensitive` (boolean, optional): Case sensitive search (default: false)
- `context_window` (integer, optional): Characters around matches (default: 100)
- `max_matches` (integer, optional): Maximum matches to return (default: 10)
- `clean_html` (boolean, optional): Clean HTML before searching (default: true)

### http_request

Make HTTP requests to any URL.

**Parameters:**
- `method` (string, required): HTTP method (GET, POST, PUT, DELETE, PATCH)
- `url` (string, required): The target URL
- `headers` (object, optional): HTTP headers
- `body` (string, optional): Request body for non-JSON data
- `json_data` (object, optional): JSON data to send
- `params` (object, optional): Query parameters
- `timeout` (integer, optional): Request timeout in seconds

**Example:**
```json
{
  "method": "POST",
  "url": "https://api.example.com/data",
  "json_data": {"key": "value"},
  "headers": {"Authorization": "Bearer token"}
}
```

## Security Notes

- This server has **no network restrictions** - it can make requests to any URL
- Content size limits are enforced to prevent memory issues
- Redirect limits prevent infinite redirect loops
- HTML content is cleaned to remove scripts and styles by default

## Development

The Makefile provides convenient development commands:

```bash
# Install development dependencies
make dev-install

# Format and fix code issues
make fix

# Generate MCP configuration examples
make config

# View all available commands
make help
```

You can also run commands directly:

```bash
# Running tests
uv run pytest

# Manual code formatting
uv run black .
uv run ruff check .
```