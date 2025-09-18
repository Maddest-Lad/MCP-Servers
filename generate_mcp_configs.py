"""
Generate MCP server configuration examples for different transports.
This script creates JSON configuration entries for Claude Desktop, Cursor, and other MCP clients.
"""

import json
import socket
from pathlib import Path


def get_project_info():
    """Get current project information."""
    current_dir = Path.cwd()
    project_name = current_dir.name
    server_file = current_dir / "main.py"

    return {
        "project_name": project_name,
        "project_path": str(current_dir),
        "server_file": str(server_file),
        "python_path": "python",  # Could be enhanced to detect actual python path
    }


def get_available_port(start_port=8000):
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
                return port
        except OSError:
            continue
    return start_port  # Fallback


def generate_stdio_config(project_info):
    """Generate STDIO transport configuration."""
    return {
        "mcpServers": {
            project_info["project_name"]: {
                "command": "uv",
                "args": ["run", "fastmcp", "run", "main.py:mcp"],
                "cwd": project_info["project_path"],
            }
        }
    }


def generate_http_config(project_info, port=None):
    """Generate HTTP transport configuration."""
    if port is None:
        port = get_available_port(8000)

    return {
        "mcpServers": {
            f"{project_info['project_name']}-http": {
                "url": f"http://localhost:{port}/mcp"
            }
        }
    }


def generate_sse_config(project_info, port=None):
    """Generate SSE transport configuration."""
    if port is None:
        port = get_available_port(8001)

    return {
        "mcpServers": {
            f"{project_info['project_name']}-sse": {
                "url": f"http://localhost:{port}/sse"
            }
        }
    }


def generate_claude_desktop_config(project_info):
    """Generate complete Claude Desktop configuration."""
    return {
        "mcpServers": {
            project_info["project_name"]: {
                "command": "uv",
                "args": ["run", "fastmcp", "run", "main.py:mcp"],
                "cwd": project_info["project_path"],
            }
        }
    }


def generate_cursor_config(project_info):
    """Generate Cursor IDE configuration."""
    return {
        "mcpServers": {
            project_info["project_name"]: {
                "command": "uv",
                "args": ["run", "fastmcp", "run", "main.py:mcp"],
                "cwd": project_info["project_path"],
            }
        }
    }


def print_config_section(title, config, description=""):
    """Print a configuration section with formatting."""
    print(f"\n{'='*60}")
    print(f"🔧 {title}")
    print(f"{'='*60}")
    if description:
        print(f"📝 {description}")
        print()

    print("📋 Configuration JSON:")
    print(json.dumps(config, indent=2))


def print_setup_instructions(config_type, project_info, port=None):
    """Print setup instructions for each configuration type."""
    print(f"\n📚 Setup Instructions for {config_type}:")
    print("-" * 40)

    if config_type == "STDIO":
        print("1. Copy the JSON configuration above")
        print("2. Add it to your MCP client configuration file:")
        print("   • Claude Desktop: ~/.claude/claude_desktop_config.json")
        print("   • Cursor: Settings → MCP Servers")
        print("3. Restart your MCP client")
        print("4. The server will start automatically when needed")

    elif config_type == "HTTP":
        print("1. Start the HTTP server:")
        print(f"   make run-http  # or: make run-custom PORT={port}")
        print("2. Copy the JSON configuration above")
        print("3. Add it to your MCP client configuration")
        print("4. The server must be running before connecting")

    elif config_type == "SSE":
        print("1. Start the SSE server:")
        print(
            f"   make run-sse  # or: uv run fastmcp run main.py:mcp --transport sse --port {port}"
        )
        print("2. Copy the JSON configuration above")
        print("3. Add it to your MCP client configuration")
        print("4. The server must be running before connecting")


def main():
    """Main function to generate and display all configurations."""
    print("🚀 Extranet MCP Server Configuration Generator")
    print("=" * 60)

    # Get project information
    project_info = get_project_info()

    print(f"📁 Project: {project_info['project_name']}")
    print(f"📂 Path: {project_info['project_path']}")
    print(f"🐍 Server: {project_info['server_file']}")

    # Get available ports
    http_port = get_available_port(8000)
    sse_port = get_available_port(8001)

    print(f"🌐 Available HTTP Port: {http_port}")
    print(f"📡 Available SSE Port: {sse_port}")

    # Generate STDIO configuration
    stdio_config = generate_stdio_config(project_info)
    print_config_section(
        "STDIO Transport (Recommended)",
        stdio_config,
        "Best for local development. Server starts automatically when needed.",
    )
    print_setup_instructions("STDIO", project_info)

    # Generate HTTP configuration
    http_config = generate_http_config(project_info, http_port)
    print_config_section(
        "HTTP Transport",
        http_config,
        f"For remote access and web integration. Server runs on port {http_port}.",
    )
    print_setup_instructions("HTTP", project_info, http_port)

    # Generate SSE configuration
    sse_config = generate_sse_config(project_info, sse_port)
    print_config_section(
        "SSE Transport",
        sse_config,
        f"For real-time streaming connections. Server runs on port {sse_port}.",
    )
    print_setup_instructions("SSE", project_info, sse_port)

    # Generate client-specific configurations
    claude_config = generate_claude_desktop_config(project_info)
    print_config_section(
        "Claude Desktop Configuration",
        claude_config,
        "Complete configuration for Claude Desktop application.",
    )

    cursor_config = generate_cursor_config(project_info)
    print_config_section(
        "Cursor IDE Configuration",
        cursor_config,
        "Configuration for Cursor IDE MCP integration.",
    )

    # Print additional information
    print(f"\n{'='*60}")
    print("🛠️  Available Tools in this MCP Server:")
    print("=" * 60)
    tools = [
        "search_web - Web search using DuckDuckGo",
        "visit_webpage - Extract webpage content",
        "extract_urls_from_webpage - Analyze and extract URLs",
        "search_in_webpage - Search content within webpages",
        "http_request - Generic HTTP request handling",
    ]

    for tool in tools:
        print(f"   • {tool}")

    print(f"\n{'='*60}")
    print("📖 Quick Start Commands:")
    print("=" * 60)
    print("   make run          # Start with STDIO transport")
    print(f"   make run-http     # Start HTTP server on port {http_port}")
    print(f"   make run-sse      # Start SSE server on port {sse_port}")
    print("   make help         # Show all available commands")
    print("   make info         # Show project information")

    print("\n🎉 Configuration generation complete!")
    print("💡 Tip: Run this script anytime with: python generate_mcp_configs.py")


if __name__ == "__main__":
    main()
