"""Plexamp / Plex Companion player discovery and control over the LAN."""

from __future__ import annotations

import asyncio
import itertools
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import httpx

from src.plexamp.helpers import (
    LIBRARY_PROVIDER,
    PLEXAMP_PLAYERS,
    PlexClient,
    PlexError,
    plex_headers,
)

PLEXAMP_PORT = 32500
PROBE_TIMEOUT = 2.0
_command_ids = itertools.count(1)

# action -> (Companion command, value param)
ACTIONS: dict[str, tuple[str, str | None]] = {
    "play": ("playback/play", None),
    "pause": ("playback/pause", None),
    "stop": ("playback/stop", None),
    "next": ("playback/skipNext", None),
    "previous": ("playback/skipPrevious", None),
    "seek": ("playback/seekTo", "offset"),
    "shuffle": ("playback/setParameters", "shuffle"),
    "repeat": ("playback/setParameters", "repeat"),
    "volume": ("playback/setParameters", "volume"),
    "skip_to": ("playback/skipTo", "key"),
}

NOT_CONTROLLABLE = (
    "Player exposes no reachable Plex Companion endpoint (common for mobile and "
    "remote apps); only LAN Plexamp / Plexamp Headless can be controlled"
)


def _caps(value: str | None) -> list[str]:
    return [c for c in (value or "").split(",") if c]


def _player_client(
    plex: PlexClient, target_id: str | None, timeout: float
) -> httpx.AsyncClient:
    headers = plex_headers(plex.token) | {"Accept": "application/xml"}
    if target_id:
        headers["X-Plex-Target-Client-Identifier"] = target_id
    return httpx.AsyncClient(headers=headers, timeout=timeout, transport=plex.transport)


async def _get_xml(
    plex: PlexClient,
    url: str,
    params: dict | None = None,
    target_id: str | None = None,
    timeout: float = PROBE_TIMEOUT,
) -> ET.Element:
    async with _player_client(plex, target_id, timeout) as http:
        resp = await http.get(url, params=params)
    resp.raise_for_status()
    return (
        ET.fromstring(resp.text) if resp.text.strip() else ET.Element("MediaContainer")
    )


async def _probe(plex: PlexClient, player: dict) -> None:
    """Fill in reachability and capabilities from the player's /resources."""
    port = player.get("port") or PLEXAMP_PORT
    if not player.get("address"):
        player["reachable"] = False
        return
    try:
        root = await _get_xml(plex, f"http://{player['address']}:{port}/resources")
    except (httpx.HTTPError, ET.ParseError):
        player["reachable"] = False
        return
    player |= {"reachable": True, "port": port}
    for p in root.iter("Player"):
        player.setdefault("name", p.get("title"))
        player.setdefault("product", p.get("product"))
        player["capabilities"] = _caps(p.get("protocolCapabilities")) or player.get(
            "capabilities", []
        )


async def discover(plex: PlexClient, probe: bool = True) -> list[dict]:
    """Merge players from PMS /clients, music sessions and PLEXAMP_PLAYERS."""
    players: dict[str, dict] = {}

    def merge(pid: str | None, source: str, **fields: Any) -> None:
        if not pid:
            return
        p = players.setdefault(pid, {"id": pid, "source": []})
        p["source"].append(source)
        for k, v in fields.items():
            if v not in (None, "", []) and not p.get(k):
                p[k] = v

    clients = await plex.request("GET", "/clients")
    for s in clients.get("Server", []):
        merge(
            s.get("machineIdentifier"),
            "clients",
            name=s.get("name"),
            product=s.get("product"),
            address=s.get("address") or s.get("host"),
            port=_int(s.get("port")),
            capabilities=_caps(s.get("protocolCapabilities")),
        )
    sessions = await plex.request("GET", "/status/sessions")
    for m in sessions.get("Metadata", []):
        pl = m.get("Player", {})
        if m.get("type") == "track":
            merge(
                pl.get("machineIdentifier"),
                "sessions",
                name=pl.get("title"),
                product=pl.get("product"),
                address=pl.get("address") if pl.get("local") else None,
                state=pl.get("state"),
            )
    for hostport in PLEXAMP_PLAYERS:
        host, _, port = hostport.partition(":")
        port_n = int(port or PLEXAMP_PORT)
        try:
            root = await _get_xml(plex, f"http://{host}:{port_n}/resources")
        except (httpx.HTTPError, ET.ParseError):
            merge(f"config:{hostport}", "config", address=host, port=port_n)
            continue
        for p in root.iter("Player"):
            merge(
                p.get("machineIdentifier"),
                "config",
                name=p.get("title"),
                product=p.get("product"),
                address=host,
                port=port_n,
                capabilities=_caps(p.get("protocolCapabilities")),
            )

    result = list(players.values())
    if probe:
        await asyncio.gather(*(_probe(plex, p) for p in result))
    for p in result:
        p.setdefault("capabilities", [])
        p["controllable"] = bool(
            p.get("address")
            and "playback" in p["capabilities"]
            and p.get("reachable", True)
        )
        if not p["controllable"]:
            p["reason"] = (
                "unreachable"
                if p.get("reachable") is False and p.get("address")
                else NOT_CONTROLLABLE
            )
    return result


async def get_player(plex: PlexClient, player_id: str) -> dict:
    """Find a controllable player by machine identifier (or unique exact name)."""
    players = await discover(plex, probe=False)
    matches = [p for p in players if p["id"] == player_id] or [
        p for p in players if (p.get("name") or "").casefold() == player_id.casefold()
    ]
    if not matches:
        raise PlexError(
            "not_found",
            f"No player '{player_id}'",
            players=[{"id": p["id"], "name": p.get("name")} for p in players],
        )
    if len(matches) > 1:
        raise PlexError(
            "ambiguous", f"Several players named '{player_id}'", candidates=matches
        )
    player = matches[0]
    await _probe(plex, player)
    if not player.get("reachable"):
        raise PlexError(
            "player_unreachable",
            f"{player.get('name') or player['id']} did not answer on "
            f"{player.get('address')}:{player.get('port') or PLEXAMP_PORT}",
            reason=(
                NOT_CONTROLLABLE if not player.get("address") else "offline or blocked"
            ),
        )
    if "playback" not in player.get("capabilities", []):
        raise PlexError(
            "player_unsupported_command",
            f"{player.get('name')} does not advertise playback control",
            capabilities=player.get("capabilities"),
        )
    return player


async def send(
    plex: PlexClient,
    player: dict,
    command: str,
    params: dict | None = None,
    timeout: float = 5.0,
) -> None:
    """Send a Companion command directly to the player."""
    params = {**(params or {}), "commandID": next(_command_ids)}
    if command.startswith("playback/"):
        params.setdefault("type", "music")
    url = f"http://{player['address']}:{player['port']}/player/{command}"
    try:
        async with _player_client(plex, player["id"], timeout) as http:
            resp = await http.get(url, params=params)
    except httpx.RequestError as e:
        raise PlexError("player_unreachable", f"{command} failed: {e}")
    if resp.status_code >= 400:
        raise PlexError(
            "player_unsupported_command",
            f"{player.get('name')} rejected {command} (HTTP {resp.status_code})",
        )


async def timeline(plex: PlexClient, player: dict) -> dict:
    """Current music timeline of a player (state, position, queue)."""
    try:
        root = await _get_xml(
            plex,
            f"http://{player['address']}:{player['port']}/player/timeline/poll",
            {"wait": 0, "commandID": next(_command_ids)},
            target_id=player["id"],
        )
    except (httpx.HTTPError, ET.ParseError) as e:
        raise PlexError("player_unreachable", f"Timeline poll failed: {e}")
    t = next((t for t in root.iter("Timeline") if t.get("type") == "music"), None)
    if t is None:
        return {"state": "stopped"}
    keep = (
        "state",
        "time",
        "duration",
        "volume",
        "shuffle",
        "repeat",
        "playQueueID",
        "playQueueItemID",
        "playQueueVersion",
        "ratingKey",
        "key",
    )
    return {k: t.get(k) for k in keep if t.get(k) is not None}


async def play_queue_on(
    plex: PlexClient, player: dict, queue: dict, confirm_seconds: float = 6.0
) -> dict:
    """Hand a PMS play queue to a player and confirm it actually started.

    Uses modern initiation (playMedia with a play-queue containerKey), never a
    bare legacy playMedia. Creating the queue alone is not success.
    """
    queue_id = str(queue["playQueueID"])
    selected = next(
        (
            m
            for m in queue.get("Metadata", [])
            if m.get("playQueueItemID") == queue.get("playQueueSelectedItemID")
        ),
        (queue.get("Metadata") or [{}])[0],
    )
    server = urlparse(plex.base_url)
    await send(
        plex,
        player,
        "playback/playMedia",
        {
            "providerIdentifier": LIBRARY_PROVIDER,
            "machineIdentifier": await plex.machine_id(),
            "protocol": server.scheme,
            "address": server.hostname,
            "port": server.port or (443 if server.scheme == "https" else 32400),
            "key": selected.get("key"),
            "offset": 0,
            "containerKey": f"/playQueues/{queue_id}?own=1&window=200",
            "token": plex.token,
        },
    )
    state: dict = {}
    for _ in range(int(confirm_seconds / 0.5)):
        await asyncio.sleep(0.5)
        state = await timeline(plex, player)
        if (
            state.get("state") in ("playing", "buffering")
            and state.get("playQueueID", queue_id) == queue_id
        ):
            return {"playback_confirmed": True, "timeline": state}
    return {
        "playback_confirmed": False,
        "timeline": state,
        "reason": "Player did not report this queue as playing",
    }


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
