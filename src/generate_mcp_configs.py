#!/usr/bin/env python3
"""Generate MCP configuration for all servers."""

import importlib
import json
import sys
from pathlib import Path


def generate_config():
    """Generate MCP config for all servers.

    Servers run over stdio via uv unless their server module defines
    `MCP_CONFIG` (e.g. an HTTP server with OAuth), which is used as-is.
    """

    src_dir = Path(__file__).parent
    project_root = Path(__file__).parent.parent
    sys.path.insert(0, str(project_root))

    config_with_uv = {"mcpServers": {}}

    # Find all server directories (containing server.py files)
    for server_dir in src_dir.iterdir():
        if not server_dir.is_dir() or server_dir.name.startswith("_"):
            continue

        server_file = server_dir / "server.py"
        if not server_file.exists():
            continue

        server_name = server_dir.name.replace("_", "-")

        # Convert file path to module path for uv
        relative_path = server_file.relative_to(project_root)
        module_path = (
            str(relative_path.with_suffix("")).replace("\\", ".").replace("/", ".")
        )

        override = getattr(importlib.import_module(module_path), "MCP_CONFIG", None)
        config_with_uv["mcpServers"][server_name] = override or {
            "command": "uv",
            "args": ["run", "-m", module_path],
            "cwd": str(project_root),
        }

    # Save to file
    configs_dir = project_root / "configs"
    configs_dir.mkdir(exist_ok=True)

    with open(configs_dir / "mcp_config.json", "w") as f:
        json.dump(config_with_uv, f, indent=2)

    print(f"\nConfig saved to {configs_dir / 'mcp_config.json'}")


if __name__ == "__main__":
    generate_config()
