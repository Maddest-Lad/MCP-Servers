"""Universal FFmpeg MCP Server.

Features:
- Generic single/bulk execution tools (works for ffmpeg/ffprobe and friends)
- Resources for help/manpages and FFmpeg capability catalogs
- Optional convenience wrappers for convert/extract/info
"""

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
mcp = FastMCP("ffmpeg")


def _need(*bins: str) -> str | None:
    ok, missing = which_all(*bins)
    if not ok:
        return err_response(f"Missing dependencies: {', '.join(missing)}")
    return None


@mcp.resource("urn:proc:help")
async def resource_proc_help(command: str, flags: list[str] | None = None) -> str:
    """
    Return stdout/stderr of `<command> <flag>` where flag is tried in order.
    Defaults to trying: ['--help', '-h', '-H'] and then `man <command>` (best effort).
    """
    tried: list[dict[str, Any]] = []
    choices = flags or ["--help", "-h", "-H"]

    # First try `command {flag}`
    for f in choices:
        res = await run_process(ProcSpec(argv=[command, f], timeout_s=12))
        tried.append({"argv": [command, f], "returncode": res.returncode})
        if res.success or res.returncode in (0, 1):  # some tools return 1 for help
            return ok_response(
                command=res.command_str, stdout=res.stdout, stderr=res.stderr
            )

    # Fallback to `man command` (may not exist on Windows or slim containers)
    res = await run_process(ProcSpec(argv=["man", command], timeout_s=12))
    tried.append({"argv": ["man", command], "returncode": res.returncode})
    if res.success:
        return ok_response(
            command=res.command_str, stdout=res.stdout, stderr=res.stderr
        )

    return err_response("Help/man not available", attempts=tried)


@mcp.resource("urn:ffmpeg:catalog")
async def resource_ffmpeg_catalog(include: list[str] | None = None) -> str:
    """
    Return FFmpeg capability catalogs. `include` can contain any of:
    - 'codecs', 'encoders', 'decoders', 'formats', 'filters', 'pix_fmts', 'bsfs', 'protocols'
    Defaults to a useful subset if not provided.
    """
    need_err = _need("ffmpeg")
    if need_err:
        return need_err

    subset = include or ["codecs", "formats", "filters", "pix_fmts"]
    flag_map = {
        "codecs": ["-codecs"],
        "encoders": ["-encoders"],
        "decoders": ["-decoders"],
        "formats": ["-formats"],
        "filters": ["-filters"],
        "pix_fmts": ["-pix_fmts"],
        "bsfs": ["-bsfs"],
        "protocols": ["-protocols"],
    }

    out: dict[str, dict[str, str]] = {}
    for key in subset:
        flags = flag_map.get(key)
        if not flags:
            out[key] = {"error": "unknown catalog"}
            continue
        res = await run_process(
            ProcSpec(argv=["ffmpeg", "-hide_banner", *flags], timeout_s=15)
        )
        out[key] = {
            "success": str(res.success).lower(),
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    return ok_response(catalog=out)


# ---------------------------
# INTERNAL IMPLEMENTATIONS (UNDECORATED)
# ---------------------------


async def _run_ffmpeg_command_impl(
    argv: list[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = 0,
    ensure_output_path: str | None = None,
) -> str:
    """
    Implementation behind the tool. Safe for internal calls.
    """
    if not argv:
        return err_response("argv must not be empty")

    # Dependency hinting: if argv[0] is 'ffmpeg' or 'ffprobe', check presence
    bin0 = argv[0]
    if bin0 in {"ffmpeg", "ffprobe"}:
        need_err = _need(bin0)
        if need_err:
            return need_err

    res: ProcResult = await run_process(
        ProcSpec(
            argv=argv,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s or None,
            ensure_output_path=ensure_output_path,
        )
    )
    return res.to_json()


async def _run_ffmpeg_bulk_impl(
    commands: list[dict[str, Any]],
    stop_on_error: bool = True,
) -> str:
    """
    Implementation behind the bulk tool. Safe for internal calls.
    """
    if not commands:
        return err_response("commands must not be empty")

    results: list[dict[str, Any]] = []
    for idx, spec in enumerate(commands):
        argv = spec.get("argv")
        if not argv:
            results.append(
                {"success": False, "error": f"commands[{idx}].argv is required"}
            )
            if stop_on_error:
                break
            continue

        bin0 = argv[0]
        if bin0 in {"ffmpeg", "ffprobe"}:
            need_err = _need(bin0)
            if need_err:
                results.append(json.loads(need_err))
                if stop_on_error:
                    break
                continue

        res = await run_process(
            ProcSpec(
                argv=argv,
                cwd=spec.get("cwd"),
                env=spec.get("env"),
                timeout_s=spec.get("timeout_s") or None,
                ensure_output_path=spec.get("ensure_output_path"),
            )
        )
        results.append(json.loads(res.to_json()))
        if stop_on_error and not res.success:
            break

    return ok_response(results=results)


# ---------------------------
# TOOLS (DECORATED WRAPPERS THAT DELEGATE)
# ---------------------------


@mcp.tool
async def run_ffmpeg_command(
    argv: list[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = 0,
    ensure_output_path: str | None = None,
) -> str:
    return await _run_ffmpeg_command_impl(
        argv=argv,
        cwd=cwd,
        env=env,
        timeout_s=timeout_s,
        ensure_output_path=ensure_output_path,
    )


@mcp.tool
async def run_ffmpeg_bulk(
    commands: list[dict[str, Any]],
    stop_on_error: bool = True,
) -> str:
    return await _run_ffmpeg_bulk_impl(commands=commands, stop_on_error=stop_on_error)


# ---------------------------
# CONVENIENCE WRAPPERS (call the IMPLEMENTATIONS)
# ---------------------------


@mcp.tool
async def ffmpeg_extract_audio(
    input_file: str,
    output_file: str,
    audio_codec: str = "libmp3lame",
    bitrate: str = "192k",
    extra: list[str] | None = None,
    timeout_s: float | None = 0,
) -> str:
    """Back-compat wrapper built on run_ffmpeg_command implementation."""
    if not os.path.exists(input_file):
        return err_response(f"Input file '{input_file}' does not exist.")

    argv: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        input_file,
        "-vn",
        "-c:a",
        audio_codec,
        "-b:a",
        bitrate,
    ]
    if extra:
        argv += extra
    argv.append(output_file)

    return await _run_ffmpeg_command_impl(
        argv=argv, ensure_output_path=output_file, timeout_s=timeout_s
    )


@mcp.tool
async def ffprobe_get_info(
    input_file: str, pretty_json: bool = True, timeout_s: float | None = 0
) -> str:
    """FFprobe info (structured JSON)."""
    if not os.path.exists(input_file):
        return err_response(f"Input file '{input_file}' does not exist.")

    # Use ffprobe JSON output, then wrap in our standard envelope.
    argv = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        input_file,
    ]
    raw = await _run_ffmpeg_command_impl(argv=argv, timeout_s=timeout_s)
    data = json.loads(raw)
    if not data.get("success"):
        return raw

    try:
        info = json.loads(data.get("stdout", "{}"))
    except json.JSONDecodeError:
        return err_response(
            "Failed to parse ffprobe JSON",
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
        )

    return ok_response(
        file=input_file,
        info=info if not pretty_json else json.loads(json.dumps(info, indent=2)),
        meta={
            k: data[k] for k in ("returncode", "command_str", "duration_s") if k in data
        },
    )


def main():
    _ = which_all("ffmpeg", "ffprobe")
    mcp.run()


if __name__ == "__main__":
    main()
