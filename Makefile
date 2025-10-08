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

# Setup
install:
	uv sync --no-dev

# Development
dev:
	uv sync --all-extras

fix: dev
	uv run black .
	uv run ruff check --fix . --unsafe-fixes

run:
	uv run src/web/server.py

# Generate Config Files
config:
	uv run python src/generate_mcp_configs.py
