from __future__ import annotations

import logging
from typing import Any, Iterable

import yt_dlp
from fastmcp import FastMCP
from yt_dlp.utils import DownloadError

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


# ---------- Internal helpers for picking a format ----------

_CODEC_PREF = [
    "opus",
    "aac",
    "mp4a",
    "vorbis",
    "ogg",
    "mp3",
    "eac3",
    "ac3",
    "dts",
    "flac",
    "alac",
    "pcm",
    "wav",
    "lpcm",
]


def _codec_rank(codec: str | None) -> int:
    if not codec:
        return -999
    lc = codec.lower()
    # some formats report like "mp4a.40.2" or "opus"
    for i, pref in enumerate(_CODEC_PREF):
        if pref in lc:
            return len(_CODEC_PREF) - i
    return 0


def _num(x) -> float:
    try:
        return float(x) if x is not None else 0.0
    except Exception:
        return 0.0


def _pick_best_audio_format(
    formats: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Returns (best_audio_only, best_muxed_with_audio)
    - best_audio_only: vcodec == 'none'
    - best_muxed_with_audio: has audio and video
    Ranking:
      1) Prefer audio-only over muxed.
      2) Prefer better codec by _codec_rank.
      3) Prefer higher audio bitrate (abr then tbr).
      4) Prefer higher asr (audio sample rate).
      5) Fallback by filesize or general tbr if present.
    """
    audio_only = []
    muxed = []
    for f in formats:
        acodec = f.get("acodec")
        if not acodec or acodec == "none":
            continue
        if (f.get("vcodec") or "") == "none":
            audio_only.append(f)
        else:
            muxed.append(f)

    def score(f: dict[str, Any]) -> tuple[int, float, float, float, float]:
        # (codec_score, abr, tbr, asr, filesize/tbr as tie breakers)
        return (
            _codec_rank(f.get("acodec")),
            _num(f.get("abr")),
            _num(f.get("tbr")),
            _num(f.get("asr")),
            _num(f.get("filesize") or f.get("filesize_approx") or f.get("tbr")),
        )

    best_audio = max(audio_only, key=score) if audio_only else None
    best_muxed = max(muxed, key=score) if muxed else None
    return best_audio, best_muxed


# ---------- Tools ----------


@mcp.tool
async def get_info(
    url: str,
    include_formats: bool = False,
    verbose: bool = False,
    cookiefile: str | None = None,
) -> dict[str, Any]:
    """
    Extract metadata without downloading. Ignores any user/system yt-dlp configs.
    """
    opts = {
        "ignoreconfig": True,  # avoid ~/.config/yt-dlp/config affecting behavior
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noprogress": True,
        "cookiefile": cookiefile,
        # Try multiple player clients (can help with some age-gated/region quirks)
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }

    def extract():
        with silence_stdio():
            with yt_dlp.YoutubeDL({**opts, "logger": NULL_LOGGER}) as ydl:  # type: ignore
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
    cookiefile: str | None = None,
) -> dict[str, Any]:
    """
    Download best available audio (programmatic selection), convert to FLAC,
    then apply FLAC metadata (optionally using LLM-provided tags).
    """
    outdir = ensure_dir(output_dir)
    tmpl = filename_template or DEFAULT_FILENAME_TEMPLATE

    base_opts = {
        "ignoreconfig": True,  # critical to avoid user/system config interference
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "prefer_ffmpeg": True,
        "outtmpl": str(outdir / tmpl),
        "writethumbnail": bool(embed_thumbnail),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "flac"}],
        "cookiefile": cookiefile,
        # Helps with some edge cases
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }

    # 1) Extract formats first (no download)
    def extract_info_only():
        with silence_stdio():
            with yt_dlp.YoutubeDL({**base_opts, "logger": NULL_LOGGER}) as ydl:  # type: ignore
                return ydl.extract_info(url, download=False)

    try:
        info = await to_thread(extract_info_only)
    except DownloadError as e:
        raise RuntimeError(f"Failed to probe formats for {url}: {e}") from e

    # 2) Decide the exact format_id to request
    fmts = info.get("formats") or []
    best_audio, best_muxed = _pick_best_audio_format(fmts)

    chosen: dict[str, Any] | None = best_audio or best_muxed
    if not chosen:
        # As a last resort, if formats list is empty (geo/age/blocked), try plain 'bestaudio/best'
        # (still more robust than failing outright)
        chosen_format_str = "bestaudio/best"
    else:
        # Prefer an explicit format_id to avoid expression filtering issues
        fmt_id = chosen.get("format_id")
        chosen_format_str = fmt_id if fmt_id else "bestaudio/best"

    # 3) Download with the chosen format
    def do_download(fmt: str):
        with silence_stdio():
            with yt_dlp.YoutubeDL({**base_opts, "format": fmt, "logger": NULL_LOGGER}) as ydl:  # type: ignore
                return ydl.extract_info(url, download=True)

    result: dict[str, Any] | None = None
    last_err: Exception | None = None

    try:
        result = await to_thread(do_download, chosen_format_str)
    except DownloadError as e:
        last_err = e
        # If we tried a specific format_id and it somehow vanished, one more shot with 'bestaudio/best'
        if chosen and chosen_format_str != "bestaudio/best":
            try:
                result = await to_thread(do_download, "bestaudio/best")
            except DownloadError as e2:
                last_err = e2

    if not result:
        raise RuntimeError(
            f"Failed to download audio for {url}. Last error: {last_err}"
        )

    # 4) Determine final .flac path (prefer yt-dlp reported path; fallback by title)
    final_path = pick_final_path_from_result(result)
    if final_path is None or final_path.suffix.lower() != ".flac":
        title = result.get("title") or info.get("title") or "audio"
        final_path = outdir / f"{title}.flac"

    # 5) Tagging
    meta = basic_meta(result if result else info)
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
        cover_converted = await to_thread(ensure_png_cover, cover_original)

    applied = await to_thread(
        apply_flac_metadata,
        str(final_path),
        title=tags["title"],
        artist=tags["artist"],
        album=tags["album"],
        date=tags["date"],
        comment=tags["comment"],
        cover_image_path=cover_converted or cover_original,
    )

    # 6) Cleanup sidecar images (optional)
    deleted: dict[str, bool] = {}
    if cleanup_covers and (cover_original or cover_converted):
        to_delete = []
        if cover_converted:
            to_delete.append(cover_converted)
        if cover_original and cover_original != cover_converted:
            to_delete.append(cover_original)
        if to_delete:
            deleted = await to_thread(cleanup_files, to_delete)

    # 7) Return
    return {
        "url": url,
        "file_path": str(final_path),
        "output_dir": str(outdir),
        "metadata_applied": applied.get("updated", {}),
        "source_metadata": meta,
        "embedded_thumbnail": bool(cover_converted or cover_original),
        "cover_cleanup": deleted,
        "chosen_format": chosen_format_str,
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
