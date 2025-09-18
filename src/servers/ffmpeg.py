"""FFmpeg MCP Server - Wrapper for FFmpeg media processing tools."""

import json
import logging
import os
from typing import List, Optional

from fastmcp import FastMCP

from ..utils.media_helpers import (
    check_dependencies,
    create_error_response,
    create_success_response,
    ensure_output_directory,
    run_command,
)

logger = logging.getLogger(__name__)

# Create the FastMCP server
mcp = FastMCP("ffmpeg")


@mcp.tool
async def ffmpeg_convert(
    input_file: str,
    output_file: str,
    codec: Optional[str] = None,
    quality: Optional[str] = None,
    additional_args: Optional[List[str]] = None,
) -> str:
    """
    Convert media files using FFmpeg.

    Args:
        input_file: Path to the input file
        output_file: Path to the output file
        codec: Codec to use (e.g., "libx264", "libx265", "libmp3lame")
        quality: Quality setting (e.g., "23" for CRF, "128k" for bitrate)
        additional_args: Additional FFmpeg arguments

    Returns:
        JSON string with conversion result.
    """
    if not check_dependencies("ffmpeg", "ffprobe"):
        return create_error_response("ffmpeg not found. Please install FFmpeg.")

    if not os.path.exists(input_file):
        return create_error_response(f"Input file '{input_file}' does not exist.")

    # Build command
    cmd = ["ffmpeg", "-i", input_file]

    if codec:
        cmd.extend(["-c:v", codec])

    if quality:
        if quality.endswith("k"):  # Bitrate
            cmd.extend(["-b:v", quality])
        else:  # CRF
            cmd.extend(["-crf", quality])

    if additional_args:
        cmd.extend(additional_args)

    cmd.append(output_file)

    # Create output directory if needed
    ensure_output_directory(output_file)

    result = await run_command(cmd)

    return (
        create_success_response(
            {
                "command": result["command"],
                "input_file": input_file,
                "output_file": output_file,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        )
        if result["returncode"] == 0
        else create_error_response(
            "FFmpeg conversion failed",
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )
    )


@mcp.tool
async def ffmpeg_extract_audio(
    input_file: str,
    output_file: str,
    audio_codec: str = "libmp3lame",
    bitrate: str = "192k",
    additional_args: Optional[List[str]] = None,
) -> str:
    """
    Extract audio from video files using FFmpeg.

    Args:
        input_file: Path to the input video file
        output_file: Path to the output audio file
        audio_codec: Audio codec to use (default: "libmp3lame")
        bitrate: Audio bitrate (default: "192k")
        additional_args: Additional FFmpeg arguments

    Returns:
        JSON string with extraction result.
    """
    if not check_dependencies("ffmpeg", "ffprobe"):
        return create_error_response("ffmpeg not found. Please install FFmpeg.")

    if not os.path.exists(input_file):
        return create_error_response(f"Input file '{input_file}' does not exist.")

    # Build command
    cmd = ["ffmpeg", "-i", input_file, "-vn", "-c:a", audio_codec, "-b:a", bitrate]

    if additional_args:
        cmd.extend(additional_args)

    cmd.append(output_file)

    # Create output directory if needed
    ensure_output_directory(output_file)

    result = await run_command(cmd)

    if result["returncode"] == 0:
        return create_success_response(
            {
                "command": result["command"],
                "input_file": input_file,
                "output_file": output_file,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        )
    else:
        return create_error_response(
            "FFmpeg audio extraction failed",
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )


@mcp.tool
async def ffmpeg_get_info(input_file: str) -> str:
    """
    Get detailed information about a media file using FFprobe.

    Args:
        input_file: Path to the media file

    Returns:
        JSON string with media file information.
    """
    if not check_dependencies("ffprobe"):
        return create_error_response(
            "ffprobe not found. Please install FFmpeg (includes ffprobe)."
        )

    if not os.path.exists(input_file):
        return create_error_response(f"Input file '{input_file}' does not exist.")

    # Get detailed info in JSON format
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        input_file,
    ]

    result = await run_command(cmd)

    if result["returncode"] == 0:
        try:
            info = json.loads(result["stdout"])
            return create_success_response({"file": input_file, "info": info})
        except json.JSONDecodeError:
            return create_error_response(
                "Failed to parse ffprobe output",
                stdout=result["stdout"],
                stderr=result["stderr"],
            )
    else:
        return create_error_response(
            "FFprobe failed",
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )


@mcp.resource("urn:ffmpeg:args")
async def get_ffmpeg_args() -> str:
    """Get comprehensive FFmpeg command line arguments reference."""
    return """# FFmpeg Command Line Arguments Reference

## Basic Usage
```bash
ffmpeg -i input.mp4 output.mp4
```

## Input/Output Options
- `-i <file>` - Input file
- `-y` - Overwrite output files without asking
- `-n` - Do not overwrite output files
- `-f <format>` - Force format

## Video Codec Options
- `-c:v <codec>` - Video codec
  - `libx264` - H.264 (most compatible)
  - `libx265` - H.265/HEVC (better compression)
  - `libvpx-vp9` - VP9 (for WebM)
  - `copy` - Copy without re-encoding

## Audio Codec Options
- `-c:a <codec>` - Audio codec
  - `libmp3lame` - MP3
  - `aac` - AAC
  - `libopus` - Opus
  - `copy` - Copy without re-encoding
- `-an` - Disable audio
- `-vn` - Disable video

## Quality Settings
- `-crf <value>` - Constant Rate Factor (0-51, lower = better quality)
- `-b:v <bitrate>` - Video bitrate (e.g., "2M", "1000k")
- `-b:a <bitrate>` - Audio bitrate (e.g., "192k", "128k")
- `-q:v <value>` - Variable bitrate quality (codec dependent)

## Resolution and Scaling
- `-s <size>` - Set frame size (WxH)
- `-vf scale=<width>:<height>` - Scale video
- `-vf scale=-1:720` - Scale to 720p height, maintain aspect ratio
- `-vf scale=1920:-1` - Scale to 1920px width, maintain aspect ratio

## Frame Rate
- `-r <fps>` - Set frame rate
- `-vf fps=<fps>` - Change frame rate with filtering

## Time and Seeking
- `-ss <time>` - Start time (e.g., "00:01:30", "90")
- `-t <duration>` - Duration (e.g., "00:01:00", "60")
- `-to <time>` - End time

## Filters
- `-vf <filter>` - Video filter
- `-af <filter>` - Audio filter

## Common Filter Examples
- `-vf scale=1280:720` - Resize to 720p
- `-vf crop=640:480:0:0` - Crop video
- `-vf rotate=90*PI/180` - Rotate 90 degrees
- `-af volume=0.5` - Reduce volume by half

## Preset Options (for x264/x265)
- `-preset <preset>` - Encoding speed vs compression
  - `ultrafast`, `superfast`, `veryfast`, `faster`, `fast`, `medium`, `slow`, `slower`, `veryslow`

## Tune Options (for x264/x265)
- `-tune <tune>` - Optimize for specific content
  - `film`, `animation`, `grain`, `stillimage`, `fastdecode`, `zerolatency`

## Container Formats
- `.mp4` - MP4 (H.264/AAC)
- `.mkv` - Matroska (supports many codecs)
- `.webm` - WebM (VP8/VP9/Opus)
- `.avi` - AVI (older format)
- `.mov` - QuickTime

## Examples
```bash
# Convert to MP4 with good quality
ffmpeg -i input.avi -c:v libx264 -crf 23 -c:a aac -b:a 192k output.mp4

# Extract audio as MP3
ffmpeg -i input.mp4 -vn -c:a libmp3lame -b:a 192k output.mp3

# Resize video to 720p
ffmpeg -i input.mp4 -vf scale=-1:720 -c:a copy output_720p.mp4

# Cut video from 1 minute to 2 minutes
ffmpeg -i input.mp4 -ss 00:01:00 -to 00:02:00 -c copy output_cut.mp4

# Convert to WebM for web
ffmpeg -i input.mp4 -c:v libvpx-vp9 -b:v 1M -c:a libopus output.webm
```
"""


def main():
    """Run the FFmpeg MCP server."""
    # Check dependencies on startup
    if not check_dependencies("ffmpeg", "ffprobe"):
        logger.error(
            "FFmpeg dependencies are missing. The server will still start but functions will not work."
        )

    mcp.run()


if __name__ == "__main__":
    main()
