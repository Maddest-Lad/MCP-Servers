"""Common utilities for media processing MCP servers."""

import asyncio
import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def check_dependencies(*commands: str) -> bool:
    """Check if specified commands are available.

    Args:
        *commands: Variable number of command names to check

    Returns:
        True if all commands are found, False otherwise
    """
    missing = []

    for cmd in commands:
        if not shutil.which(cmd):
            missing.append(cmd)

    if missing:
        logger.warning(f"Missing dependencies: {', '.join(missing)}")
        return False

    return True


async def run_command(cmd: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
    """Run a command asynchronously and return result."""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout, stderr = await process.communicate()

        return {
            "returncode": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "command": " ".join(cmd),
        }
    except Exception as e:
        logger.error(f"Command execution failed: {cmd} - Error: {str(e)}")
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "command": " ".join(cmd),
            "error": "Command execution failed",
        }


def create_success_response(data: Dict[str, Any]) -> str:
    """Create a standardized success response."""
    response = {"success": True, **data}
    return json.dumps(response, indent=2)


def create_error_response(error: str, **kwargs) -> str:
    """Create a standardized error response."""
    response = {"success": False, "error": error, **kwargs}
    return json.dumps(response, indent=2)


def ensure_output_directory(file_path: str) -> None:
    """Ensure the output directory exists for the given file path."""
    output_dir = os.path.dirname(file_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
