"""Media MCP Server - FFmpeg and yt-dlp wrapper for media processing and downloading."""

import asyncio
import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Create the FastMCP server
mcp = FastMCP("media")


def check_dependencies():
    """Check if required tools are available."""
    missing = []

    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg")

    if not shutil.which("yt-dlp"):
        missing.append("yt-dlp")

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
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "command": " ".join(cmd),
            "error": "Command execution failed",
        }


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
    if not shutil.which("yt-dlp"):
        return json.dumps({"error": "yt-dlp not found. Please install yt-dlp."})

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

    return json.dumps(
        {
            "success": result["returncode"] == 0,
            "command": result["command"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "output_directory": output_dir,
        },
        indent=2,
    )


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
    if not shutil.which("ffmpeg"):
        return json.dumps({"error": "ffmpeg not found. Please install FFmpeg."})

    if not os.path.exists(input_file):
        return json.dumps({"error": f"Input file '{input_file}' does not exist."})

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
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    result = await run_command(cmd)

    return json.dumps(
        {
            "success": result["returncode"] == 0,
            "command": result["command"],
            "input_file": input_file,
            "output_file": output_file,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
        indent=2,
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
    if not shutil.which("ffmpeg"):
        return json.dumps({"error": "ffmpeg not found. Please install FFmpeg."})

    if not os.path.exists(input_file):
        return json.dumps({"error": f"Input file '{input_file}' does not exist."})

    # Build command
    cmd = ["ffmpeg", "-i", input_file, "-vn", "-c:a", audio_codec, "-b:a", bitrate]

    if additional_args:
        cmd.extend(additional_args)

    cmd.append(output_file)

    # Create output directory if needed
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    result = await run_command(cmd)

    return json.dumps(
        {
            "success": result["returncode"] == 0,
            "command": result["command"],
            "input_file": input_file,
            "output_file": output_file,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
        indent=2,
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
    if not shutil.which("ffprobe"):
        return json.dumps(
            {"error": "ffprobe not found. Please install FFmpeg (includes ffprobe)."}
        )

    if not os.path.exists(input_file):
        return json.dumps({"error": f"Input file '{input_file}' does not exist."})

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
            return json.dumps(
                {"success": True, "file": input_file, "info": info}, indent=2
            )
        except json.JSONDecodeError:
            return json.dumps(
                {
                    "success": False,
                    "error": "Failed to parse ffprobe output",
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                },
                indent=2,
            )
    else:
        return json.dumps(
            {
                "success": False,
                "command": result["command"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            },
            indent=2,
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
    if not shutil.which("yt-dlp"):
        return json.dumps({"error": "yt-dlp not found. Please install yt-dlp."})

    cmd = ["yt-dlp", "--dump-json", "--no-download", url]

    result = await run_command(cmd)

    if result["returncode"] == 0:
        try:
            # yt-dlp returns one JSON object per line for playlists
            lines = result["stdout"].strip().split("\n")
            videos = [json.loads(line) for line in lines if line.strip()]

            return json.dumps({"success": True, "url": url, "videos": videos}, indent=2)
        except json.JSONDecodeError:
            return json.dumps(
                {
                    "success": False,
                    "error": "Failed to parse yt-dlp output",
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                },
                indent=2,
            )
    else:
        return json.dumps(
            {
                "success": False,
                "command": result["command"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            },
            indent=2,
        )


@mcp.resource("urn:media:ffmpeg-args")
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


@mcp.resource("urn:media:yt-dlp-args")
async def get_yt_dlp_args() -> str:
    """Get comprehensive yt-dlp command line arguments reference."""
    return """# yt-dlp Command Line Arguments Reference

## Basic Usage
```bash
yt-dlp "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Output Options
- `-o <template>` - Output filename template
  - `%(title)s.%(ext)s` - Use video title
  - `%(uploader)s - %(title)s.%(ext)s` - Include uploader
  - `%(upload_date)s - %(title)s.%(ext)s` - Include upload date

## Format Selection
- `-f <format>` - Video format to download
  - `best` - Best quality available
  - `worst` - Worst quality available
  - `bestvideo+bestaudio` - Best video + best audio
  - `mp4` - Best mp4 format available
  - `"best[height<=720]"` - Best quality up to 720p

## Audio Options
- `--extract-audio` - Extract audio only
- `--audio-format <format>` - Audio format (mp3, aac, flac, m4a, opus, vorbis, wav)
- `--audio-quality <quality>` - Audio quality (0-9, 0 is best)

## Download Options
- `--playlist-start <number>` - Start downloading from playlist item number
- `--playlist-end <number>` - Stop downloading at playlist item number
- `--max-downloads <number>` - Maximum number of downloads
- `--download-archive <file>` - Track downloaded videos to avoid re-downloading

## Information Options
- `--list-formats` - List available formats for video
- `--dump-json` - Output video info as JSON
- `--get-title` - Get video title
- `--get-url` - Get video URL
- `--get-description` - Get video description
- `--get-duration` - Get video duration
- `--get-filename` - Get output filename

## Subtitle Options
- `--write-subs` - Download subtitle files
- `--write-auto-subs` - Download auto-generated subtitle files
- `--sub-langs <langs>` - Subtitle languages to download (e.g., "en,es,fr")
- `--sub-format <format>` - Subtitle format (srt, vtt, ass)

## Quality and Size Limits
- `--format-sort "height:720"` - Prefer 720p videos
- `--format-sort "filesize:1G"` - Prefer files under 1GB
- `--max-filesize <size>` - Skip files larger than size
- `--min-filesize <size>` - Skip files smaller than size

## Network Options
- `--proxy <url>` - Use proxy
- `--socket-timeout <seconds>` - Socket timeout
- `--retries <number>` - Number of retries
- `--fragment-retries <number>` - Number of retries for fragments

## Post-Processing
- `--embed-subs` - Embed subtitles in video
- `--embed-thumbnail` - Embed thumbnail in audio file
- `--add-metadata` - Add metadata to file
- `--no-mtime` - Do not use Last-modified header to set file modification time

## Authentication
- `--username <user>` - Login username
- `--password <pass>` - Login password
- `--netrc` - Use .netrc authentication data
- `--cookies <file>` - File to read cookies from

## Supported Sites Examples
- YouTube: `https://www.youtube.com/watch?v=VIDEO_ID`
- YouTube Playlist: `https://www.youtube.com/playlist?list=PLAYLIST_ID`
- Vimeo: `https://vimeo.com/VIDEO_ID`
- Twitch: `https://www.twitch.tv/videos/VIDEO_ID`
- Twitter: `https://twitter.com/user/status/TWEET_ID`

## Common Examples
```bash
# Download best quality video
yt-dlp -f "best" "URL"

# Download audio only as MP3
yt-dlp --extract-audio --audio-format mp3 "URL"

# Download with custom filename
yt-dlp -o "%(uploader)s - %(title)s.%(ext)s" "URL"

# Download playlist starting from item 5
yt-dlp --playlist-start 5 "PLAYLIST_URL"

# Download with subtitles
yt-dlp --write-subs --sub-langs en "URL"

# Download 720p or lower quality
yt-dlp -f "best[height<=720]" "URL"

# Get video information without downloading
yt-dlp --dump-json --no-download "URL"

# Download from file containing URLs
yt-dlp -a urls.txt

# Download with rate limiting
yt-dlp --limit-rate 1M "URL"
```

## Format String Examples
```bash
# Basic template
%(title)s.%(ext)s

# With uploader and date
%(uploader)s/%(upload_date)s - %(title)s.%(ext)s

# With resolution
%(title)s [%(height)sp].%(ext)s

# Organized by uploader
%(uploader)s/%(title)s.%(ext)s
```
"""


def main():
    """Run the MCP server."""
    # Check dependencies on startup
    if not check_dependencies():
        logger.error(
            "Some required dependencies are missing. The server will still start but some functions may not work."
        )

    mcp.run()


if __name__ == "__main__":
    main()
