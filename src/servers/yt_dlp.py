"""Media MCP Server - FFmpeg and yt-dlp wrapper for media processing and downloading."""

import json
import logging
import os
from typing import List, Optional

from fastmcp import FastMCP

from ..utils.media_helpers import (
    check_dependencies,
    create_error_response,
    create_success_response,
    run_command,
)

logger = logging.getLogger(__name__)

# Create the FastMCP server
mcp = FastMCP("yt-dlp")


@mcp.tool
async def yt_dlp_download(
    url: str,
    output_dir: str = ".",
    format_selector: str = "best",
    extract_audio: bool = False,
    audio_format: str = "mp3",
    additional_args: Optional[List[str]] = None,
) -> str:
    """
    Download videos or audio using yt-dlp.

    Args:
        url: The URL to download from (YouTube, etc.)
        output_dir: Directory to save downloads (default: current directory)
        format_selector: Format selector string (default: "best")
        extract_audio: Whether to extract audio only (default: False)
        audio_format: Audio format when extracting audio (default: "mp3")
        additional_args: Additional yt-dlp arguments

    Returns:
        JSON string with download result and file information.
    """
    if not check_dependencies("yt-dlp"):
        return create_error_response("yt-dlp not found. Please install yt-dlp.")

    # Build command
    cmd = ["yt-dlp", "--print-json"]

    if extract_audio:
        cmd.extend(["--extract-audio", "--audio-format", audio_format])
    else:
        cmd.extend(["-f", format_selector])

    cmd.extend(["-o", f"{output_dir}/%(title)s.%(ext)s"])

    if additional_args:
        cmd.extend(additional_args)

    cmd.append(url)

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    result = await run_command(cmd, cwd=output_dir)

    if result["returncode"] == 0:
        return create_success_response(
            {
                "command": result["command"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "output_directory": output_dir,
            }
        )
    else:
        return create_error_response(
            "yt-dlp download failed",
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )


@mcp.tool
async def yt_dlp_get_info(url: str) -> str:
    """
    Get information about a video without downloading it using yt-dlp.

    Args:
        url: The URL to get information about

    Returns:
        JSON string with video information.
    """
    if not check_dependencies("yt-dlp"):
        return create_error_response("yt-dlp not found. Please install yt-dlp.")

    cmd = ["yt-dlp", "--dump-json", "--no-download", url]

    result = await run_command(cmd)

    if result["returncode"] == 0:
        try:
            # yt-dlp returns one JSON object per line for playlists
            lines = result["stdout"].strip().split("\n")
            videos = [json.loads(line) for line in lines if line.strip()]

            return create_success_response({"url": url, "videos": videos})
        except json.JSONDecodeError:
            return create_error_response(
                "Failed to parse yt-dlp output",
                stdout=result["stdout"],
                stderr=result["stderr"],
            )
    else:
        return create_error_response(
            "yt-dlp get info failed",
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )


def main():
    """Run the MCP server."""
    # Check dependencies on startup
    if not check_dependencies("yt-dlp"):
        logger.error(
            "Some required dependencies are missing. The server will still start but some functions may not work."
        )

    mcp.run()


if __name__ == "__main__":
    main()
