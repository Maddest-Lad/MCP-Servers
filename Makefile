# Extranet MCP Server Makefile

.PHONY: help install dev-install run run-http fix config

# Default target
help:
	@echo "Available commands:"
	@echo "  install      - Install production dependencies"
	@echo "  dev-install  - Install all dependencies"
	@echo "  run          - Run server (STDIO transport)"
	@echo "  run-http     - Run server (HTTP on port 8000)"
	@echo "  fix          - Format code with black and ruff"
	@echo "  config       - Generate MCP client configuration"

# Installation
install:
	uv sync --no-dev

dev:
	uv sync --all-extras

# Server
run:
	uv run fastmcp run extranet.py:mcp

run-http:
	uv run fastmcp run extranet.py:mcp --transport http --port 8000


fix: dev-install
	uv run black .
	uv run ruff check --fix .

# Configuration
config:
	uv run python generate_mcp_configs.py
