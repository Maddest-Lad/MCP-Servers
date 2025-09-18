#!/usr/bin/env python3
"""Generate MCP configuration for all servers."""

import json
from pathlib import Path


def generate_config():
    """Generate MCP config for all servers."""

    servers_dir = Path(__file__).parent / "servers"
    project_root = Path(__file__).parent.parent

    config = {"mcpServers": {}}

    # Find all .py files in servers directory
    for server_file in servers_dir.glob("*.py"):
        if server_file.name.startswith("_"):
            continue

        server_name = server_file.stem.replace("_", "-")

        config["mcpServers"][server_name] = {
            "command": "python",
            "args": [str(server_file.absolute())],
        }

    # Also generate uv-based configs
    config_with_uv = {"mcpServers": {}}
    for server_file in servers_dir.glob("*.py"):
        if server_file.name.startswith("_"):
            continue

        server_name = server_file.stem.replace("_", "-")

        # Convert file path to module path for uv
        relative_path = server_file.relative_to(project_root)
        module_path = (
            str(relative_path.with_suffix("")).replace("\\", ".").replace("/", ".")
        )

        config_with_uv["mcpServers"][server_name] = {
            "command": "uv",
            "args": ["run", "-m", module_path],
            "cwd": str(project_root),
        }

    # Print both configs
    print("=" * 60)
    print("Standard Python Configuration:")
    print("=" * 60)
    print(json.dumps(config, indent=2))

    print("\n" + "=" * 60)
    print("UV-based Configuration:")
    print("=" * 60)
    print(json.dumps(config_with_uv, indent=2))

    # Save to file
    configs_dir = project_root / "configs"
    configs_dir.mkdir(exist_ok=True)

    with open(configs_dir / "mcp_config.json", "w") as f:
        json.dump(config_with_uv, f, indent=2)

    print(f"\n[OK] Config saved to {configs_dir / 'mcp_config.json'}")


if __name__ == "__main__":
    generate_config()
