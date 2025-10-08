from __future__ import annotations

import logging
from typing import Any

import yt_dlp
from fastmcp import FastMCP

from src.youtube.helpers import (
    NULL_LOGGER,
    apply_flac_metadata,
    basic_meta,
    cleanup_files,
    ensure_dir,
    ensure_png_cover,
    find_cover_for,
    pick_final_path_from_result,
    silence_stdio,
    to_thread,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

mcp = FastMCP("youtube")

DEFAULT_FILENAME_TEMPLATE = "%(title)s.%(ext)s"


@mcp.tool
async def get_info(
    url: str,
    include_formats: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Extract metadata without downloading.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

    def extract():
        with silence_stdio():
            with yt_dlp.YoutubeDL({**opts, "logger": NULL_LOGGER, "noprogress": True}) as ydl:  # type: ignore
                return ydl.extract_info(url, download=False)

    info = await to_thread(extract)
    out: dict[str, Any] = {"url": url, "metadata": basic_meta(info)}
    if include_formats:
        out["formats"] = info.get("formats", [])
    if verbose:
        out["thumbnails"] = info.get("thumbnails")
        out["description"] = info.get("description")
    return out


@mcp.tool
async def download_audio_flac(
    url: str,
    output_dir: str | None = None,
    filename_template: str | None = None,
    embed_thumbnail: bool = True,
    llm_tags: dict[str, str] | None = None,
    cleanup_covers: bool = True,
) -> dict[str, Any]:
    """
    Download best audio, convert to FLAC, then apply FLAC metadata (optionally using LLM-provided tags).
    """
    outdir = ensure_dir(output_dir)
    tmpl = filename_template or DEFAULT_FILENAME_TEMPLATE

    # Convert to FLAC regardless of source

    # For FLAC we’ll embed cover ourselves; still ask yt-dlp to fetch thumbnails
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,  # <— important
        "format": "bestaudio/best",
        "outtmpl": str(outdir / tmpl),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "flac"}],
        "writethumbnail": bool(embed_thumbnail),
        "prefer_ffmpeg": True,
    }

    def download():
        with silence_stdio():
            with yt_dlp.YoutubeDL({**opts, "logger": NULL_LOGGER}) as ydl:  # type: ignore
                return ydl.extract_info(url, download=True)

    result = await to_thread(download)

    # Determine final .flac path (prefer yt-dlp reported path; fallback by title)
    final_path = pick_final_path_from_result(result)
    if final_path is None or final_path.suffix.lower() != ".flac":
        title = result.get("title") or "audio"
        final_path = outdir / f"{title}.flac"

    meta = basic_meta(result)
    tags = {
        "title": (llm_tags or {}).get("title") or meta.get("title"),
        "artist": (llm_tags or {}).get("artist") or meta.get("uploader"),
        "album": (llm_tags or {}).get("album")
        or (llm_tags or {}).get("title")
        or meta.get("title"),
        "date": (llm_tags or {}).get("date") or meta.get("upload_date"),
        "comment": (llm_tags or {}).get("comment")
        or f"Source: {meta.get('webpage_url')}",
    }

    cover_original = find_cover_for(final_path) if embed_thumbnail else None
    cover_converted: str | None = None

    if cover_original:
        # Convert to PNG (off the main loop)
        cover_converted = await to_thread(ensure_png_cover, cover_original)

    applied = await to_thread(
        apply_flac_metadata,
        str(final_path),
        title=tags["title"],
        artist=tags["artist"],
        album=tags["album"],
        date=tags["date"],
        comment=tags["comment"],
        cover_image_path=cover_converted or cover_original,  # prefer PNG
    )

    # Best-effort cleanup after successful tagging
    deleted: dict[str, bool] = {}
    if cleanup_covers and (cover_original or cover_converted):
        to_delete = []
        # Delete converted PNG
        if cover_converted:
            to_delete.append(cover_converted)
        # Delete original if different from converted
        if cover_original and cover_original != cover_converted:
            to_delete.append(cover_original)
        if to_delete:
            deleted = await to_thread(cleanup_files, to_delete)

    return {
        "url": url,
        "file_path": str(final_path),
        "output_dir": str(outdir),
        "metadata_applied": applied.get("updated", {}),
        "source_metadata": meta,
        "embedded_thumbnail": bool(cover_converted or cover_original),
        "cover_cleanup": deleted,
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
    FLAC-only metadata setter exposed as a tool.
    (Internal code should call the helper via to_thread directly.)
    """
    return await to_thread(
        apply_flac_metadata,
        file_path,
        title,
        artist,
        album,
        date,
        comment,
        cover_image_path,
    )


def main() -> None:
    logger.info("youtube MCP server starting")
    mcp.run()


if __name__ == "__main__":
    main()
