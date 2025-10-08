from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

# ---------- logging  ----------
logger = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


class _NullLogger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


NULL_LOGGER = _NullLogger()


@contextlib.contextmanager
def silence_stdio():
    """
    Redirect stdout to /dev/null and stderr to stderr (unchanged) so
    nothing pollutes the MCP stdout transport.
    """
    devnull = open(os.devnull, "w")
    try:
        with contextlib.redirect_stdout(devnull):
            # keep stderr as-is so logs are visible in host
            yield
    finally:
        devnull.close()


# ---------- filesystem / threading helpers ----------

DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"


def ensure_dir(path: str | None) -> Path:
    d = Path(path) if path else DEFAULT_DOWNLOAD_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


async def to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


# ---------- yt-dlp result helpers ----------


def basic_meta(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "uploader_id": info.get("uploader_id") or info.get("channel_id"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "webpage_url": info.get("webpage_url"),
    }


def pick_final_path_from_result(result: dict[str, Any]) -> Path | None:
    """
    yt-dlp often returns 'requested_downloads' with 'filepath'.
    Fall back to top-level 'filepath' if present.
    """
    try:
        req = result.get("requested_downloads") or []
        for item in req:
            fp = item.get("filepath")
            if fp:
                return Path(fp)
        fp = result.get("filepath")
        return Path(fp) if fp else None
    except Exception:
        return None


def find_cover_for(final_audio_path: Path) -> str | None:
    """
    Given a final .flac path, look for a sibling thumbnail file yt-dlp saved.
    Preference: jpg/jpeg -> png -> webp.
    """
    stem = final_audio_path.with_suffix("")  # drop .flac
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = stem.with_suffix(ext)
        if candidate.exists():
            return str(candidate)
    return None


# ---------- FLAC-only metadata ----------


def apply_flac_metadata(
    file_path: str,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    date: str | None = None,
    comment: str | None = None,
    cover_image_path: str | None = None,
) -> dict[str, Any]:
    """
    Apply Vorbis comments and (optional) embedded cover to a FLAC file.
    """
    from mutagen.flac import FLAC, Picture

    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if p.suffix.lower() != ".flac":
        raise ValueError(f"Expected a .flac file, got: {p.suffix}")

    flac = FLAC(str(p))
    updated: dict[str, Any] = {}

    def setv(key: str, val: str | None):
        if val:
            flac[key] = [val]
            updated[key] = val

    setv("title", title)
    setv("artist", artist)
    setv("album", album)
    setv("date", date)
    setv("comment", comment)

    if cover_image_path and Path(cover_image_path).exists():
        mime = _mime_from_ext(cover_image_path)
        pic = Picture()
        with open(cover_image_path, "rb") as f:
            data = f.read()
        pic.type = 3  # front cover
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = data
        flac.clear_pictures()
        flac.add_picture(pic)
        updated["cover"] = cover_image_path

    flac.save()
    return {"file": str(p), "updated": updated}


# ---------- cover helpers ----------


def ensure_png_cover(cover_path: str) -> str:
    """
    Ensure the cover image is PNG. If already .png, returns it.
    Otherwise converts next to it and returns the new .png path.
    """
    p = Path(cover_path)
    if p.suffix.lower() == ".png":
        return str(p)
    png_path = str(p.with_suffix(".png"))
    _convert_image_to_png(cover_path, png_path)
    return png_path


# ---------- cover helpers ----------


def _convert_image_to_png(src_path: str, dst_path: str) -> None:
    """
    Convert any common raster (jpg/jpeg/webp/png/etc) to PNG.
    Uses Pillow only when called.
    """
    with Image.open(src_path) as im:
        # auto-rotate based on EXIF, if present
        im = ImageOps.exif_transpose(im)
        # Preserve alpha if present; otherwise use RGB
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
        elif im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dst_path, format="PNG", optimize=True)


def _mime_from_ext(path: str) -> str:
    lower = path.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        # Some players don’t love WEBP; still set correct MIME.
        return "image/webp"
    # Fallback
    return "application/octet-stream"


# File Cleanup
def safe_remove(path: str | None) -> bool:
    """Delete a file if it exists. Returns True if removed."""
    if not path:
        return False
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
            logger.debug("Deleted file: %s", path)
            return True
    except Exception as e:
        # Log to stderr; never raise (cleanup should be best-effort)
        logger.warning("Failed to delete %s: %s", path, e)
    return False


def cleanup_files(paths: list[str]) -> dict[str, bool]:
    """
    Best-effort deletion of a list of files. Returns a map of path->deleted?.
    Designed to be called via to_thread() from the tool.
    """
    results: dict[str, bool] = {}
    for p in paths:
        results[p] = safe_remove(p)
    return results
