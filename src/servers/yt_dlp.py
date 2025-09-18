"""Media MCP Server - yt-dlp wrapper using shared process utilities."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping

from fastmcp import FastMCP

from ..utils.process_helpers import (
    ProcResult,
    ProcSpec,
    err_response,
    ok_response,
    run_process,
    which_all,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("yt-dlp")


# ---------------------------
# Small helper for dependency checks
# ---------------------------
def _need(*bins: str) -> str | None:
    ok, missing = which_all(*bins)
    if not ok:
        return err_response(f"Missing dependencies: {', '.join(missing)}")
    return None


# ---------------------------
# RESOURCES
# ---------------------------


@mcp.resource("urn:proc:help")
async def resource_proc_help(command: str, flags: list[str] | None = None) -> str:
    """
    Return stdout/stderr of `<command> <flag>` where flag is tried in order.
    Tries: ['--help', '-h', '-H'] then `man <command>` as best effort.
    """
    tried: list[dict[str, Any]] = []
    choices = flags or ["--help", "-h", "-H"]

    for f in choices:
        res = await run_process(ProcSpec(argv=[command, f], timeout_s=12))
        tried.append({"argv": [command, f], "returncode": res.returncode})
        if res.success or res.returncode in (0, 1):
            return ok_response(
                command=res.command_str, stdout=res.stdout, stderr=res.stderr
            )

    # Fallback to `man`
    res = await run_process(ProcSpec(argv=["man", command], timeout_s=12))
    tried.append({"argv": ["man", command], "returncode": res.returncode})
    if res.success:
        return ok_response(
            command=res.command_str, stdout=res.stdout, stderr=res.stderr
        )

    return err_response("Help/man not available", attempts=tried)


@mcp.resource("urn:yt-dlp:version")
async def resource_yt_dlp_version() -> str:
    """Return yt-dlp version and basic diagnostic flags."""
    need_err = _need("yt-dlp")
    if need_err:
        return need_err
    res = await run_process(ProcSpec(argv=["yt-dlp", "--version"], timeout_s=10))
    return ok_response(
        success=res.success,
        stdout=res.stdout,
        stderr=res.stderr,
        command=res.command_str,
    )


@mcp.resource("urn:yt-dlp:extractors")
async def resource_yt_dlp_extractors() -> str:
    """List all available extractors."""
    need_err = _need("yt-dlp")
    if need_err:
        return need_err
    res = await run_process(
        ProcSpec(argv=["yt-dlp", "--list-extractors"], timeout_s=30)
    )
    return ok_response(
        success=res.success,
        stdout=res.stdout,
        stderr=res.stderr,
        command=res.command_str,
    )


@mcp.resource("urn:yt-dlp:formats")
async def resource_yt_dlp_formats(url: str) -> str:
    """List available formats for a specific URL (`yt-dlp -F`)."""
    need_err = _need("yt-dlp")
    if need_err:
        return need_err
    res = await run_process(ProcSpec(argv=["yt-dlp", "-F", url], timeout_s=60))
    return ok_response(
        success=res.success,
        stdout=res.stdout,
        stderr=res.stderr,
        command=res.command_str,
        url=url,
    )


# ---------------------------
# TOOLS
# ---------------------------


@mcp.tool
async def yt_dlp_get_info(
    url: str,
    playlist_items: str | None = None,
    flat_playlist: bool = False,
    timeout_s: float | None = 0,
) -> str:
    """
    Get structured info for a video/playlist without downloading (uses `-J` to return a single JSON tree).

    Args:
      url: Video/playlist URL
      playlist_items: e.g. "1-5" or "1,3,7" to limit items (optional)
      flat_playlist: If True, do not resolve each video, just entries.
      timeout_s: Optional timeout (0/None = no timeout)
    """
    need_err = _need("yt-dlp")
    if need_err:
        return need_err

    argv: list[str] = ["yt-dlp", "-J", "--no-download", url]
    if playlist_items:
        argv += ["--playlist-items", playlist_items]
    if flat_playlist:
        argv.append("--flat-playlist")

    res: ProcResult = await run_process(
        ProcSpec(argv=argv, timeout_s=timeout_s or None)
    )
    if not res.success:
        return err_response(
            "yt-dlp get info failed",
            command=res.command_str,
            stdout=res.stdout,
            stderr=res.stderr,
        )

    # yt-dlp -J returns a single JSON object (playlist or video)
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return err_response(
            "Failed to parse yt-dlp JSON", stdout=res.stdout, stderr=res.stderr
        )

    return ok_response(
        url=url,
        info=data,
        meta={
            "returncode": res.returncode,
            "command": res.command_str,
            "duration_s": res.duration_s,
        },
    )


@mcp.tool
async def yt_dlp_download(
    url: str,
    output_dir: str = ".",
    format_selector: str = "best",
    extract_audio: bool = False,
    audio_format: str = "mp3",
    additional_args: list[str] | None = None,
    output_template: str | None = None,
    timeout_s: float | None = 0,
    env: Mapping[str, str] | None = None,
) -> str:
    """
    Download media via yt-dlp with consistent, structured results.

    Args:
      url: Source URL
      output_dir: Directory to save downloads (created if missing)
      format_selector: Format selector (ignored if extract_audio=True)
      extract_audio: If True, use --extract-audio
      audio_format: Target audio format (mp3, flac, m4a, opus, ...)
      additional_args: Extra args forwarded to yt-dlp
      output_template: Custom template; defaults to "%(title)s.%(ext)s" inside output_dir
      timeout_s: Kill after N seconds (0/None = no timeout)
      env: Extra environment vars for the process
    """
    need_err = _need("yt-dlp")
    if need_err:
        return need_err

    os.makedirs(output_dir, exist_ok=True)
    template = output_template or "%(title)s.%(ext)s"

    argv: list[str] = ["yt-dlp", "--print-json", "--newline", "-o", template]

    if extract_audio:
        argv += ["--extract-audio", "--audio-format", audio_format]
    else:
        argv += ["-f", format_selector]

    if additional_args:
        argv += additional_args

    argv.append(url)

    # Run with cwd=output_dir so template resolves inside it
    res: ProcResult = await run_process(
        ProcSpec(
            argv=argv,
            cwd=output_dir,
            env=env,
            timeout_s=timeout_s or None,
        )
    )

    if not res.success:
        return err_response(
            "yt-dlp download failed",
            command=res.command_str,
            stdout=res.stdout,
            stderr=res.stderr,
            output_directory=output_dir,
        )

    # stdout may contain multiple JSON lines (progress + final). Keep only JSON objects.
    videos: list[dict[str, Any]] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                videos.append(obj)
        except json.JSONDecodeError:
            # progress or non-JSON line; ignore
            pass

    return ok_response(
        command=res.command_str,
        output_directory=output_dir,
        items=videos,
        stderr=res.stderr,
        meta={"returncode": res.returncode, "duration_s": res.duration_s},
    )


@mcp.tool
async def yt_dlp_bulk(
    urls: list[str],
    output_dir: str = ".",
    format_selector: str = "best",
    extract_audio: bool = False,
    audio_format: str = "mp3",
    shared_additional_args: list[str] | None = None,
    per_item_additional_args: list[list[str]] | None = None,
    output_template: str | None = None,
    timeout_s: float | None = 0,
    stop_on_error: bool = True,
) -> str:
    """
    Download multiple URLs in sequence.

    Args:
      urls: List of URLs
      output_dir: Common directory (created if missing)
      format_selector / extract_audio / audio_format: same as single download
      shared_additional_args: args applied to every item
      per_item_additional_args: list aligned with `urls`, each a list of extra args
      output_template: Custom yt-dlp -o template (defaults to "%(title)s.%(ext)s")
      timeout_s: Optional timeout per item
      stop_on_error: Stop on first failure if True
    """
    need_err = _need("yt-dlp")
    if need_err:
        return need_err

    if not urls:
        return err_response("urls must not be empty")

    os.makedirs(output_dir, exist_ok=True)
    template = output_template or "%(title)s.%(ext)s"

    results: list[dict[str, Any]] = []

    for idx, url in enumerate(urls):
        argv: list[str] = ["yt-dlp", "--print-json", "--newline", "-o", template]

        if extract_audio:
            argv += ["--extract-audio", "--audio-format", audio_format]
        else:
            argv += ["-f", format_selector]

        if shared_additional_args:
            argv += shared_additional_args

        if (
            per_item_additional_args
            and idx < len(per_item_additional_args)
            and per_item_additional_args[idx]
        ):
            argv += per_item_additional_args[idx]

        argv.append(url)

        res: ProcResult = await run_process(
            ProcSpec(argv=argv, cwd=output_dir, timeout_s=timeout_s or None)
        )

        if res.success:
            items: list[dict[str, Any]] = []
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        items.append(obj)
                except json.JSONDecodeError:
                    pass

            results.append(
                {
                    "success": True,
                    "url": url,
                    "items": items,
                    "stderr": res.stderr,
                    "meta": {
                        "returncode": res.returncode,
                        "command": res.command_str,
                        "duration_s": res.duration_s,
                    },
                }
            )
        else:
            results.append(
                {
                    "success": False,
                    "url": url,
                    "error": "yt-dlp download failed",
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                    "meta": {
                        "returncode": res.returncode,
                        "command": res.command_str,
                        "duration_s": res.duration_s,
                    },
                }
            )
            if stop_on_error:
                break

    return ok_response(results=results, output_directory=output_dir)


def main():
    # Soft check (server can still start)
    _ = which_all("yt-dlp")
    mcp.run()


if __name__ == "__main__":
    main()
