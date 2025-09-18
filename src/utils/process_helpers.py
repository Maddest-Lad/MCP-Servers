"""Generic async process helpers for MCP servers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


def which_all(*commands: str) -> tuple[bool, list[str]]:
    """Return (ok, missing) for required binaries."""
    missing: list[str] = []
    for cmd in commands:
        if not shutil.which(cmd):
            missing.append(cmd)
    return (len(missing) == 0, missing)


def ensure_parent_dir(path: str) -> None:
    """Ensure directory for a path exists (if any)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


@dataclass(frozen=True)
class ProcSpec:
    """Specification for running a command."""

    argv: Sequence[str]
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    timeout_s: float | None = None
    # If you want to ensure output path exists (for tools that write files)
    ensure_output_path: str | None = None


@dataclass(frozen=True)
class ProcResult:
    """Structured result from a command run."""

    success: bool
    returncode: int
    argv: Sequence[str]
    command_str: str
    stdout: str
    stderr: str
    started_at: float
    ended_at: float
    duration_s: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


async def run_process(spec: ProcSpec) -> ProcResult:
    """Run a command asynchronously with timeout and structured output."""
    if spec.ensure_output_path:
        ensure_parent_dir(spec.ensure_output_path)

    started = time.time()
    cmd_str = " ".join(shlex.quote(x) for x in spec.argv)
    try:
        proc = await asyncio.create_subprocess_exec(
            *spec.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=spec.cwd,
            env=dict(os.environ, **(spec.env or {})) if spec.env else None,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.communicate()
            finally:
                ended = time.time()
                return ProcResult(
                    success=False,
                    returncode=-1,
                    argv=spec.argv,
                    command_str=cmd_str,
                    stdout="",
                    stderr=f"Process timed out after {spec.timeout_s} seconds.",
                    started_at=started,
                    ended_at=ended,
                    duration_s=ended - started,
                )

        ended = time.time()
        return ProcResult(
            success=(proc.returncode == 0),
            returncode=proc.returncode if proc.returncode is not None else -1,
            argv=spec.argv,
            command_str=cmd_str,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            started_at=started,
            ended_at=ended,
            duration_s=ended - started,
        )

    except Exception as e:  # noqa: BLE001
        ended = time.time()
        logger.exception("Command failed: %s", cmd_str)
        return ProcResult(
            success=False,
            returncode=-1,
            argv=spec.argv,
            command_str=cmd_str,
            stdout="",
            stderr=str(e),
            started_at=started,
            ended_at=ended,
            duration_s=ended - started,
        )


def ok_response(**data: Any) -> str:
    return json.dumps({"success": True, **data}, indent=2)


def err_response(error: str, **data: Any) -> str:
    return json.dumps({"success": False, "error": error, **data}, indent=2)
