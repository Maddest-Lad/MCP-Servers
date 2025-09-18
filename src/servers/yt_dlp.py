"""
Media MCP Server - yt-dlp minimal toolkit.

Tools:
- get_info: Extract metadata from URL
- download_video: Download video content
- download_audio: Download audio content
- set_metadata: Edit tags post-download
- get_agent_instructions: LLM usage guidance
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any

import yt_dlp
from fastmcp import FastMCP
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.id3._frames import APIC, COMM, TALB, TDRC, TIT2, TPE1
from mutagen.id3._util import ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

logger = logging.getLogger(__name__)
mcp = FastMCP("yt-dlp-min")


# ---------------------------
# Enums
# ---------------------------
class VideoPreset(Enum):
    BEST = "bestvideo+bestaudio/best"
    P1080 = "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
    P720 = "bestvideo[height<=720]+bestaudio/best[height<=720]"
    P480 = "bestvideo[height<=480]+bestaudio/best[height<=480]"
    P360 = "bestvideo[height<=360]+bestaudio/best[height<=360]"


class AudioCodec(Enum):
    MP3 = "mp3"
    M4A = "m4a"
    OPUS = "opus"
    FLAC = "flac"
    WAV = "wav"
    BEST = "best"


# ---------------------------
# Defaults
# ---------------------------
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"
DEFAULT_FILENAME_TEMPLATE = "%(title)s.%(ext)s"


# ---------------------------
# Helpers
# ---------------------------
def get_download_dir(output_dir: str | None = None) -> Path:
    """Get and ensure download directory exists."""
    dir_path = Path(output_dir) if output_dir else DEFAULT_DOWNLOAD_DIR
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def get_output_template(output_dir: Path, template: str | None = None) -> str:
    """Build full output path template."""
    tmpl = template or DEFAULT_FILENAME_TEMPLATE
    return str(output_dir / tmpl)


def extract_basic_metadata(info: dict[str, Any]) -> dict[str, Any]:
    """Extract essential metadata fields."""
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "uploader_id": info.get("uploader_id") or info.get("channel_id"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "webpage_url": info.get("webpage_url"),
    }


async def run_yt_dlp(func, *args, **kwargs):
    """Run yt-dlp function in thread pool."""
    return await asyncio.to_thread(func, *args, **kwargs)


def get_final_path(info: dict[str, Any], opts: dict[str, Any]) -> Path | None:
    """Calculate the actual output filepath after all postprocessing."""
    try:
        # Use yt-dlp's prepare_filename to get the actual path
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            filename = ydl.prepare_filename(info)  # type: ignore

            # For audio extraction, update extension
            if any(
                pp.get("key") == "FFmpegExtractAudio"
                for pp in opts.get("postprocessors", [])
            ):
                for pp in opts["postprocessors"]:
                    if pp.get("key") == "FFmpegExtractAudio":
                        codec = pp.get("preferredcodec", "mp3")
                        # Handle 'best' codec
                        if codec == "best":
                            codec = info.get("ext", "mp3")
                        filename = Path(filename).with_suffix(f".{codec}")
                        break

            # For video merge format
            elif opts.get("merge_output_format"):
                filename = Path(filename).with_suffix(f'.{opts["merge_output_format"]}')

            return Path(filename)
    except Exception as e:
        logger.warning(f"Could not determine final path: {e}")
        return None


# ---------------------------
# Tools
# ---------------------------
@mcp.tool
async def get_info(
    url: str,
    include_formats: bool = False,
    list_subtitles: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Get video/audio metadata from URL.

    Args:
        url: Media URL to analyze
        include_formats: Include available format details
        list_subtitles: List available subtitles
        verbose: Include extended metadata

    Returns:
        Metadata dictionary with basic info, optional formats, and thumbnails
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    if list_subtitles:
        opts["listsubtitles"] = True

    start_time = time.time()

    def extract():
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            return ydl.extract_info(url, download=False)

    info = await run_yt_dlp(extract)
    elapsed = round(time.time() - start_time, 3)

    result = {
        "url": url,
        "metadata": extract_basic_metadata(info),
        "extraction_time_s": elapsed,
    }

    if include_formats:
        result["formats"] = info.get("formats", [])

    if verbose:
        result["thumbnails"] = info.get("thumbnails", [])[:3]
        result["description"] = info.get("description")

    return result


@mcp.tool
async def download_video(
    url: str,
    output_dir: str | None = None,
    filename_template: str | None = None,
    preset: VideoPreset = VideoPreset.BEST,
    merge_format: str = "mp4",
) -> dict[str, Any]:
    """
    Download video content.

    Args:
        url: Video URL to download
        output_dir: Directory for output (defaults to ~/Downloads)
        filename_template: Output filename template (yt-dlp format)
        preset: Video quality preset
        merge_format: Output container format (mp4, mkv, webm, etc)

    Returns:
        Download results with file path and metadata
    """
    dir_path = get_download_dir(output_dir)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": preset.value,
        "outtmpl": get_output_template(dir_path, filename_template),
        "merge_output_format": merge_format,
        "postprocessors": [
            {"key": "FFmpegVideoConvertor", "preferedformat": merge_format}
        ],
    }

    start_time = time.time()

    # Extract info first to get the final path
    def extract():
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            return ydl.extract_info(url, download=False)

    info = await run_yt_dlp(extract)

    # Calculate the actual output path
    final_path = get_final_path(info, opts)

    # Now download
    def download():
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            ydl.download([url])

    await run_yt_dlp(download)

    elapsed = round(time.time() - start_time, 3)

    return {
        "url": url,
        "file_path": str(final_path) if final_path else None,
        "output_dir": str(dir_path),
        "metadata": extract_basic_metadata(info),
        "preset": preset.name,
        "format": preset.value,
        "merge_format": merge_format,
        "download_time_s": elapsed,
    }


@mcp.tool
async def download_audio(
    url: str,
    output_dir: str | None = None,
    filename_template: str | None = None,
    codec: AudioCodec = AudioCodec.MP3,
    quality_kbps: int | None = None,
    embed_thumbnail: bool = False,
) -> dict[str, Any]:
    """
    Download and extract audio.

    Args:
        url: Media URL to download
        output_dir: Directory for output (defaults to ~/Downloads)
        filename_template: Output filename template (yt-dlp format)
        codec: Audio codec/format
        quality_kbps: Bitrate in kbps (defaults to best available)
        embed_thumbnail: Embed thumbnail in audio file

    Returns:
        Download results with file path and metadata
    """
    dir_path = get_download_dir(output_dir)

    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": codec.value,
            "preferredquality": str(quality_kbps) if quality_kbps else "0",
        }
    ]

    if embed_thumbnail:
        postprocessors.append({"key": "EmbedThumbnail"})

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": get_output_template(dir_path, filename_template),
        "postprocessors": postprocessors,
    }

    start_time = time.time()

    # Extract info first
    def extract():
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            return ydl.extract_info(url, download=False)

    info = await run_yt_dlp(extract)

    # Calculate the actual output path
    final_path = get_final_path(info, opts)

    # Download
    def download():
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            ydl.download([url])

    await run_yt_dlp(download)

    elapsed = round(time.time() - start_time, 3)

    return {
        "url": url,
        "file_path": str(final_path) if final_path else None,
        "output_dir": str(dir_path),
        "metadata": extract_basic_metadata(info),
        "codec": codec.value,
        "quality_kbps": quality_kbps,
        "download_time_s": elapsed,
    }


@mcp.tool
async def set_metadata(
    file_path: str,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    date: str | None = None,
    comment: str | None = None,
    cover_image_path: str | None = None,
) -> dict[str, Any]:
    """
    Update metadata tags on audio files.

    Args:
        file_path: Path to audio file
        title: Track title
        artist: Artist name
        album: Album name
        date: Release date
        comment: Comment text
        cover_image_path: Path to cover image file

    Returns:
        Updated fields confirmation
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower().lstrip(".")
    updated = {}

    # Update function to reduce duplication
    def update_field(field_name: str, value: Any) -> None:
        if value:
            updated[field_name] = value

    if ext == "mp3":
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()

        if title:
            tags["TIT2"] = TIT2(encoding=3, text=title)
            update_field("title", title)
        if artist:
            tags["TPE1"] = TPE1(encoding=3, text=artist)
            update_field("artist", artist)
        if album:
            tags["TALB"] = TALB(encoding=3, text=album)
            update_field("album", album)
        if date:
            tags["TDRC"] = TDRC(encoding=3, text=date)
            update_field("date", date)
        if comment:
            tags["COMM"] = COMM(encoding=3, lang="eng", desc="", text=comment)
            update_field("comment", comment)
        if cover_image_path and Path(cover_image_path).exists():
            with open(cover_image_path, "rb") as f:
                tags["APIC"] = APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="Cover",
                    data=f.read(),
                )
            update_field("cover", cover_image_path)
        tags.save(str(path))

    elif ext in ("m4a", "mp4", "aac"):
        tags = MP4(str(path))

        field_map = {
            "title": ("\xa9nam", title),
            "artist": ("\xa9ART", artist),
            "album": ("\xa9alb", album),
            "date": ("\xa9day", date),
            "comment": ("\xa9cmt", comment),
        }

        for field, (tag, value) in field_map.items():
            if value:
                tags[tag] = [value]
                update_field(field, value)

        if cover_image_path and Path(cover_image_path).exists():
            with open(cover_image_path, "rb") as f:
                tags["covr"] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
            update_field("cover", cover_image_path)

        tags.save()

    elif ext in ("flac", "ogg", "opus"):
        # Handle Vorbis-style tags
        if ext == "flac":
            tags = FLAC(str(path))
        elif ext == "ogg":
            tags = OggVorbis(str(path))
        else:  # opus
            tags = OggOpus(str(path))

        field_map = {
            "title": title,
            "artist": artist,
            "album": album,
            "date": date,
            "comment": comment,
        }

        for field, value in field_map.items():
            if value:
                tags[field] = [value]
                update_field(field, value)

        tags.save()

    else:
        raise ValueError(f"Unsupported file type for tagging: .{ext}")

    return {"file": str(path), "updated": updated}


@mcp.tool
async def get_agent_instructions() -> str:
    """
    Get LLM usage instructions for this toolkit.
    """
    return """
# Media Download Assistant Instructions

## REQUIRED WORKFLOW (always follow in order):

### 1. GET INFO FIRST (mandatory)
```
result = await get_info(url)
```
- **ALWAYS** call this before any download
- Analyze the metadata and available formats
- Use `include_formats=True` to see all quality options

### 2. DOWNLOAD CONTENT

**For video:**
```
result = await download_video(
    url,
    output_dir="/path/to/dir",  # optional, defaults to ~/Downloads
    filename_template="%(title)s - %(uploader)s.%(ext)s",  # optional
    preset=VideoPreset.P1080,  # or BEST, P720, P480, P360
    merge_format="mp4"  # or mkv, webm, etc
)
```

**For audio:**
```
result = await download_audio(
    url,
    output_dir="/path/to/dir",  # optional
    codec=AudioCodec.MP3,  # or M4A, OPUS, FLAC, WAV
    quality_kbps=320,  # optional, defaults to best
    embed_thumbnail=True  # optional
)
```

### 3. SET METADATA (automatic for audio)

**For audio files (do this automatically):**
```
await set_metadata(
    file_path=result["file_path"],
    title=info["metadata"]["title"],
    artist=info["metadata"]["uploader"],
    album="Downloaded Audio",
    date=info["metadata"]["upload_date"],
    comment=f"Source: {url}"
)
```

## Key Points:
- The file_path in download results is the final output file after all processing
- Always use the metadata from get_info to populate set_metadata
- For audio, always set metadata unless user says not to
- Return the final file path to the user
""".strip()


# ---------------------------
# Entry point
# ---------------------------
def main() -> None:
    logger.info("yt-dlp-min server starting")
    mcp.run()


if __name__ == "__main__":
    main()
