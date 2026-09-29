from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastmcp import FastMCP

from src.plexamp import players
from src.plexamp.helpers import (
    MUSIC_TYPES,
    TYPE_IDS,
    PlexClient,
    PlexError,
    build_filters,
    clamp,
    create_queue,
    fresh_queue,
    get_queue,
    item_summary,
    page,
    query_string,
    queue_summary,
    resolve_id,
    search,
    stations_in,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

mcp = FastMCP("plexamp")
plex = PlexClient()

MusicType = Literal["artist", "album", "track"]
Action = Literal[
    "play",
    "pause",
    "stop",
    "next",
    "previous",
    "seek",
    "shuffle",
    "repeat",
    "volume",
    "skip_to",
]


async def _player(player_id: str | None) -> dict | None:
    return await players.get_player(plex, player_id) if player_id else None


async def _play_or_queue(queue: dict, player: dict | None) -> dict:
    """Return the queue, handed to a player (with confirmation) when given."""
    out: dict[str, Any] = {"queue_created": True, "queue": queue_summary(queue)}
    if player:
        out |= {"player_id": player["id"]} | await players.play_queue_on(
            plex, player, queue
        )
    return out


# ---------- Find and inspect ----------


@mcp.tool
async def music_search(
    query: str,
    types: list[MusicType] | None = None,
    section_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search music libraries. Returns artists, albums and tracks grouped by
    type, each with a stable Plex id (ratingKey) to pass to other tools."""
    return await search(plex, query, types or list(TYPE_IDS), section_id, limit)


@mcp.tool
async def music_browse(
    type: MusicType = "album",
    section_id: str | None = None,
    sort: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    match: Literal["all", "any"] = "all",
    start: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Browse a music library.

    sort: a PMS sort key with optional direction, e.g. "titleSort", "addedAt:desc".
    filters: rules like {"field": "genre", "operator": "is", "value": "Jazz"} or
    {"field": "artist.title", "operator": "contains", "value": "Miles"}; they are
    validated against the library's own filter metadata.
    """
    section = await plex.resolve_section(section_id)
    params = await build_filters(plex, section, type, filters, match, sort)
    limit = clamp(limit)
    data = await plex.request(
        "GET", f"/library/sections/{section}/all", params, start=start, size=limit
    )
    items = [item_summary(m) for m in data.get("Metadata", [])]
    return page(items, data, start, limit)


@mcp.tool
async def music_get_item(
    id: str | None = None,
    title: str | None = None,
    type: MusicType = "track",
    artist: str | None = None,
    include_children: bool = False,
) -> dict[str, Any]:
    """Get an artist, album or track by id, or by exact title (+ artist).
    Ambiguous titles return an `ambiguous` error listing candidates."""
    rating_key = await resolve_id(plex, id, title, type, artist)
    item = await plex.metadata(rating_key)
    out = item_summary(item) | {
        k: item[k] for k in ("summary", "studio", "addedAt", "userRating") if k in item
    }
    out["genres"] = [g["tag"] for g in item.get("Genre", [])]
    if include_children and item["type"] != "track":
        kids = await plex.request("GET", f"/library/metadata/{rating_key}/children")
        out["children"] = [item_summary(m) for m in kids.get("Metadata", [])]
    return out


@mcp.tool
async def music_get_track_files(
    id: str | None = None,
    title: str | None = None,
    type: MusicType = "track",
    artist: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List every media part file path exactly as Plex reports it (server-side
    paths, no drive mapping). Albums and artists expand to their tracks."""
    rating_key = await resolve_id(plex, id, title, type, artist)
    item = await plex.metadata(rating_key)
    limit = clamp(limit)
    tracks, total = [item], 1
    if item["type"] != "track":
        leaves = await plex.request(
            "GET", f"/library/metadata/{rating_key}/allLeaves", start=0, size=limit
        )
        tracks = leaves.get("Metadata", [])
        total = int(leaves.get("totalSize", len(tracks)))
    files = [
        {
            "track_id": str(t.get("ratingKey")),
            "title": t.get("title"),
            "media_id": str(media.get("id")),
            "part_id": str(part.get("id")),
            "file": part.get("file"),
            "container": part.get("container") or media.get("container"),
            "codec": media.get("audioCodec"),
            "bitrate": media.get("bitrate"),
            "size": part.get("size"),
        }
        for t in tracks
        for media in t.get("Media", [])
        for part in media.get("Part", [])
    ]
    return {"files": files, "track_count": total, "truncated": len(tracks) < total}


@mcp.tool
async def music_get_recent(
    type: MusicType = "album", section_id: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Recently added music across music libraries (or one section)."""
    limit = clamp(limit)
    sections = (
        [await plex.resolve_section(section_id)]
        if section_id
        else await plex.section_ids()
    )
    results = await asyncio.gather(
        *(
            plex.request(
                "GET",
                f"/library/sections/{s}/all",
                {"type": TYPE_IDS[type], "sort": "addedAt:desc"},
                start=0,
                size=limit,
            )
            for s in sections
        )
    )
    items = sorted(
        (m for r in results for m in r.get("Metadata", [])),
        key=lambda m: m.get("addedAt", 0),
        reverse=True,
    )[:limit]
    return {"items": [item_summary(m) | {"added_at": m.get("addedAt")} for m in items]}


@mcp.tool
async def music_get_hubs(
    section_id: str | None = None, per_hub: int = 10
) -> dict[str, Any]:
    """Music hubs (recently played, mixes, stations, ...) as Plex shows them."""
    sections = (
        [await plex.resolve_section(section_id)]
        if section_id
        else await plex.section_ids()
    )
    hubs = []
    for s in sections:
        data = await plex.request(
            "GET",
            f"/hubs/sections/{s}",
            {"includeStations": 1, "count": clamp(per_hub, 50)},
        )
        for h in data.get("Hub", []):
            hubs.append(
                {
                    "section_id": s,
                    "title": h.get("title"),
                    "context": h.get("context"),
                    "hub_id": h.get("hubIdentifier"),
                    "items": [
                        item_summary(m)
                        for m in h.get("Metadata", []) + h.get("Directory", [])
                    ],
                }
            )
    return {"hubs": hubs}


# ---------- Discover and play ----------


@mcp.tool
async def music_list_stations(
    section_id: str | None = None, seed_id: str | None = None
) -> dict[str, Any]:
    """Stations the server actually offers: library stations from music hubs,
    or stations seeded by an artist/album/track when seed_id is given."""
    if seed_id:
        item = await plex.metadata(seed_id, includeStations=1)
        stations = stations_in(item)
    else:
        sections = (
            [await plex.resolve_section(section_id)]
            if section_id
            else await plex.section_ids()
        )
        stations = []
        for s in sections:
            data = await plex.request(
                "GET", f"/hubs/sections/{s}", {"includeStations": 1}
            )
            for hub in data.get("Hub", []):
                if hub.get("context") == "hub.music.stations":
                    stations += stations_in({"Stations": hub})
    return {"stations": stations, "available": bool(stations)}


@mcp.tool
async def music_start_radio(
    station_key: str | None = None,
    seed_id: str | None = None,
    player_id: str | None = None,
    shuffle: bool = False,
) -> dict[str, Any]:
    """Start a station (key from music_list_stations, or the first station of a
    seed item). Plays on player_id if given, else only creates a PMS queue."""
    if not station_key and seed_id:
        found = stations_in(await plex.metadata(seed_id, includeStations=1))
        station_key = found[0]["key"] if found else None
    if not station_key:
        raise PlexError(
            "station_unavailable", "No station is available for that request"
        )
    player = await _player(player_id)
    queue = await create_queue(
        plex, uri=await plex.library_uri(station_key), shuffle=shuffle
    )
    return await _play_or_queue(queue, player)


@mcp.tool
async def music_similar_tracks(
    id: str | None = None,
    title: str | None = None,
    artist: str | None = None,
    limit: int = 25,
    max_distance: float = 0.25,
) -> dict[str, Any]:
    """Sonically similar items (needs Plex Pass sonic analysis)."""
    rating_key = await resolve_id(plex, id, title, "track", artist)
    await plex.metadata(rating_key)
    try:
        data = await plex.request(
            "GET",
            f"/library/metadata/{rating_key}/nearest",
            {"limit": clamp(limit, 50), "maxDistance": max_distance},
        )
    except PlexError as e:
        if e.code != "not_found":
            raise
        data = {}
    items = [
        item_summary(m) | {"distance": m.get("distance")}
        for m in data.get("Metadata", [])
    ]
    if not items:
        raise PlexError(
            "sonic_unavailable",
            "No sonic neighbors; the library may not be sonically analyzed",
        )
    return {"items": items}


@mcp.tool
async def music_sonic_adventure(
    start_id: str,
    end_id: str,
    player_id: str | None = None,
    create_queue_only: bool = False,
) -> dict[str, Any]:
    """Build a sonic path between two tracks of the same library. Returns the
    tracks; with player_id plays them, with create_queue_only just queues them."""
    start, end = await plex.check_music([start_id, end_id])
    section = str(start.get("librarySectionID"))
    if start["type"] != "track" or end["type"] != "track":
        raise PlexError("unsupported", "Sonic adventure needs two tracks")
    if section != str(end.get("librarySectionID")):
        raise PlexError("unsupported", "Both tracks must be in the same music library")
    try:
        data = await plex.request(
            "GET",
            f"/library/sections/{section}/computePath",
            {"startID": start_id, "endID": end_id},
        )
    except PlexError as e:
        if e.code != "not_found":
            raise
        data = {}
    tracks = data.get("Metadata", [])
    if not tracks:
        raise PlexError(
            "sonic_unavailable",
            "Plex could not compute a path; sonic analysis may be missing",
        )
    out: dict[str, Any] = {"items": [item_summary(m) for m in tracks]}
    if player_id or create_queue_only:
        player = await _player(player_id)
        queue = await create_queue(
            plex, uri=await plex.items_uri(str(m["ratingKey"]) for m in tracks)
        )
        out |= await _play_or_queue(queue, player)
    return out


# ---------- Playlists ----------


async def _playlist_view(playlist_id: str, start: int = 0, limit: int = 50) -> dict:
    pl = await plex.playlist(playlist_id)
    limit = clamp(limit)
    data = await plex.request(
        "GET", f"/playlists/{playlist_id}/items", start=start, size=limit
    )
    out = item_summary(pl) | {"summary": pl.get("summary")}
    if pl.get("smart"):
        out["filter_uri"] = pl.get("content")
    return out | page(
        [item_summary(m) for m in data.get("Metadata", [])], data, start, limit
    )


async def _smart_uri(smart: dict) -> str:
    section = await plex.resolve_section(smart.get("section_id"))
    libtype = smart.get("libtype", "track")
    params = await build_filters(
        plex,
        section,
        libtype,
        smart.get("rules"),
        smart.get("match", "all"),
        smart.get("sort"),
    )
    if smart.get("limit"):
        params.append(("limit", str(int(smart["limit"]))))
    return await plex.library_uri(
        f"/library/sections/{section}/all?{query_string(params)}"
    )


@mcp.tool
async def music_list_playlists(
    smart: bool | None = None, start: int = 0, limit: int = 50
) -> dict[str, Any]:
    """Audio playlists (optionally only smart or only regular)."""
    limit = clamp(limit)
    data = await plex.request(
        "GET",
        "/playlists",
        {"playlistType": "audio", "smart": smart},
        start=start,
        size=limit,
    )
    return page([item_summary(m) for m in data.get("Metadata", [])], data, start, limit)


@mcp.tool
async def music_get_playlist(
    id: str, start: int = 0, limit: int = 50
) -> dict[str, Any]:
    """Playlist details and items. Each item has a playlist_item_id, which is
    what removal and reordering use (duplicates share the same track id)."""
    return await _playlist_view(id, start, limit)


@mcp.tool
async def music_create_playlist(
    title: str,
    track_ids: list[str] | None = None,
    smart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an audio playlist from track/album/artist ids, or a smart playlist.

    smart: {"section_id"?, "libtype": "track", "rules": [{"field", "operator",
    "value"}], "match": "all"|"any", "sort"?: "userRating:desc", "limit"?: 100}
    """
    if bool(track_ids) == bool(smart):
        raise PlexError("unsupported", "Pass exactly one of track_ids or smart")
    if smart:
        uri = await _smart_uri(smart)
    else:
        await plex.check_music(track_ids)
        uri = await plex.items_uri(track_ids)
    data = await plex.request(
        "POST",
        "/playlists",
        {"type": "audio", "title": title, "smart": bool(smart), "uri": uri},
    )
    created = (data.get("Metadata") or [{}])[0]
    return await _playlist_view(str(created["ratingKey"]))


@mcp.tool
async def music_update_playlist(
    id: str,
    title: str | None = None,
    summary: str | None = None,
    smart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rename/describe a playlist, or replace a smart playlist's filters
    (same `smart` shape as music_create_playlist)."""
    pl = await plex.playlist(id)
    if title is not None or summary is not None:
        await plex.request(
            "PUT", f"/playlists/{id}", {"title": title, "summary": summary}
        )
    if smart:
        if not pl.get("smart"):
            raise PlexError("unsupported", "Only smart playlists have filters")
        await plex.request(
            "PUT", f"/playlists/{id}/items", {"uri": await _smart_uri(smart)}
        )
    return await _playlist_view(id)


@mcp.tool
async def music_add_playlist_tracks(id: str, track_ids: list[str]) -> dict[str, Any]:
    """Append tracks (or albums/artists) to a regular audio playlist."""
    if (await plex.playlist(id)).get("smart"):
        raise PlexError(
            "unsupported", "Smart playlists are filter-driven; update filters instead"
        )
    await plex.check_music(track_ids)
    await plex.request(
        "PUT", f"/playlists/{id}/items", {"uri": await plex.items_uri(track_ids)}
    )
    return await _playlist_view(id)


async def _playlist_item_ids(playlist_id: str) -> set[str]:
    data = await plex.request("GET", f"/playlists/{playlist_id}/items")
    return {str(m.get("playlistItemID")) for m in data.get("Metadata", [])}


@mcp.tool
async def music_remove_playlist_items(
    id: str, playlist_item_ids: list[str]
) -> dict[str, Any]:
    """Remove entries by playlist_item_id (not track id, so only the chosen
    copy of a duplicated track is removed)."""
    if (await plex.playlist(id)).get("smart"):
        raise PlexError("unsupported", "Cannot remove items from a smart playlist")
    known = await _playlist_item_ids(id)
    missing = [i for i in playlist_item_ids if str(i) not in known]
    if missing:
        raise PlexError("not_found", "Unknown playlist_item_ids", missing=missing)
    for item_id in playlist_item_ids:
        await plex.request("DELETE", f"/playlists/{id}/items/{item_id}")
    return await _playlist_view(id)


@mcp.tool
async def music_move_playlist_item(
    id: str, playlist_item_id: str, after_playlist_item_id: str | None = None
) -> dict[str, Any]:
    """Move an entry after another entry; omit after_playlist_item_id to move
    it to the top."""
    known = await _playlist_item_ids(id)
    missing = [
        i
        for i in (playlist_item_id, after_playlist_item_id)
        if i and str(i) not in known
    ]
    if missing:
        raise PlexError("not_found", "Unknown playlist_item_ids", missing=missing)
    await plex.request(
        "PUT",
        f"/playlists/{id}/items/{playlist_item_id}/move",
        {"after": after_playlist_item_id},
    )
    return await _playlist_view(id)


@mcp.tool
async def music_delete_playlist(id: str, confirm: bool = False) -> dict[str, Any]:
    """Delete an audio playlist. Requires confirm=true."""
    pl = await plex.playlist(id)
    if not confirm:
        raise PlexError(
            "unsupported", "Set confirm=true to delete", playlist=item_summary(pl)
        )
    await plex.request("DELETE", f"/playlists/{id}")
    return {"deleted": item_summary(pl)}


# ---------- Queue and players ----------


async def _queue_target(
    play_queue_id: str | None, player_id: str | None
) -> tuple[str, dict | None]:
    player = await players.get_player(plex, player_id) if player_id else None
    if play_queue_id:
        return str(play_queue_id), player
    if player:
        queue_id = (await players.timeline(plex, player)).get("playQueueID")
        if queue_id:
            return str(queue_id), player
        raise PlexError("not_found", "That player has no active play queue")
    raise PlexError("not_found", "Pass play_queue_id or player_id")


async def _after_queue_edit(queue_id: str, player: dict | None) -> dict:
    if player:
        await players.send(
            plex, player, "playback/refreshPlayQueue", {"playQueueID": queue_id}
        )
    return queue_summary(await get_queue(plex, queue_id))


@mcp.tool
async def music_play(
    player_id: str,
    ids: list[str] | None = None,
    playlist_id: str | None = None,
    station_key: str | None = None,
    shuffle: bool = False,
    repeat: Literal[0, 1, 2] = 0,
    start_id: str | None = None,
) -> dict[str, Any]:
    """Play tracks/albums/artists, a playlist, or a station on a player.

    Success means the player confirmed playback; otherwise the result says
    playback_confirmed=false with the reason (a PMS queue alone is not casting).
    """
    if sum(map(bool, (ids, playlist_id, station_key))) != 1:
        raise PlexError(
            "unsupported", "Pass exactly one of ids, playlist_id, station_key"
        )
    player = await players.get_player(plex, player_id)
    if ids:
        await plex.check_music(ids)
        queue = await create_queue(
            plex,
            uri=await plex.items_uri(ids),
            shuffle=shuffle,
            repeat=repeat,
            start_key=f"/library/metadata/{start_id}" if start_id else None,
        )
    elif playlist_id:
        await plex.playlist(playlist_id)
        queue = await create_queue(
            plex, playlist_id=playlist_id, shuffle=shuffle, repeat=repeat
        )
    else:
        queue = await create_queue(
            plex,
            uri=await plex.library_uri(station_key),
            shuffle=shuffle,
            repeat=repeat,
        )
    return await _play_or_queue(queue, player)


@mcp.tool
async def music_get_queue(
    play_queue_id: str | None = None, player_id: str | None = None, window: int = 50
) -> dict[str, Any]:
    """A play queue by id, or the queue currently loaded on a player. Pass the
    returned `version` to queue edit tools to detect stale queues."""
    queue_id, _ = await _queue_target(play_queue_id, player_id)
    return queue_summary(await get_queue(plex, queue_id, window))


@mcp.tool
async def music_queue_items(
    ids: list[str],
    play_queue_id: str | None = None,
    player_id: str | None = None,
    play_next: bool = False,
    version: int | None = None,
) -> dict[str, Any]:
    """Add tracks/albums to a queue (end, or next when play_next=true)."""
    queue_id, player = await _queue_target(play_queue_id, player_id)
    await fresh_queue(plex, queue_id, version)
    await plex.check_music(ids)
    await plex.request(
        "PUT",
        f"/playQueues/{queue_id}",
        {"uri": await plex.items_uri(ids), "next": play_next},
    )
    return await _after_queue_edit(queue_id, player)


@mcp.tool
async def music_move_queue_item(
    play_queue_item_id: str,
    after_item_id: str | None = None,
    play_queue_id: str | None = None,
    player_id: str | None = None,
    version: int | None = None,
) -> dict[str, Any]:
    """Move a queue entry after another (omit after_item_id for the top)."""
    queue_id, player = await _queue_target(play_queue_id, player_id)
    await fresh_queue(plex, queue_id, version, (play_queue_item_id, after_item_id))
    await plex.request(
        "PUT",
        f"/playQueues/{queue_id}/items/{play_queue_item_id}/move",
        {"after": after_item_id},
    )
    return await _after_queue_edit(queue_id, player)


@mcp.tool
async def music_remove_queue_item(
    play_queue_item_id: str,
    play_queue_id: str | None = None,
    player_id: str | None = None,
    version: int | None = None,
) -> dict[str, Any]:
    """Remove one entry from a queue by play_queue_item_id."""
    queue_id, player = await _queue_target(play_queue_id, player_id)
    await fresh_queue(plex, queue_id, version, (play_queue_item_id,))
    await plex.request("DELETE", f"/playQueues/{queue_id}/items/{play_queue_item_id}")
    return await _after_queue_edit(queue_id, player)


@mcp.tool
async def music_list_players(probe: bool = True) -> dict[str, Any]:
    """Players known to PMS (/clients, music sessions) plus PLEXAMP_PLAYERS,
    with reachability and whether they accept playback commands."""
    return {"players": await players.discover(plex, probe)}


@mcp.tool
async def music_now_playing(player_id: str | None = None) -> dict[str, Any]:
    """What is playing: music sessions on the server, plus the live timeline
    of player_id when it is reachable."""
    data = await plex.request("GET", "/status/sessions")
    sessions = [
        {
            "player_id": m.get("Player", {}).get("machineIdentifier"),
            "player": m.get("Player", {}).get("title"),
            "state": m.get("Player", {}).get("state"),
            "position_ms": m.get("viewOffset"),
            "track": item_summary(m),
        }
        for m in data.get("Metadata", [])
        if m.get("type") in MUSIC_TYPES
    ]
    out: dict[str, Any] = {"sessions": sessions}
    if player_id:
        player = await players.get_player(plex, player_id)
        out["sessions"] = [s for s in sessions if s["player_id"] == player["id"]]
        out["timeline"] = await players.timeline(plex, player)
    return out


@mcp.tool
async def music_control_player(
    player_id: str, action: Action, value: float | str | None = None
) -> dict[str, Any]:
    """Control a player: play, pause, stop, next, previous, seek (value=ms),
    shuffle (0/1), repeat (0 off, 1 one, 2 all), volume (0-100),
    skip_to (value=play queue item key)."""
    command, param = players.ACTIONS[action]
    if param and value is None:
        raise PlexError("unsupported", f"{action} needs a value")
    if action == "volume" and not 0 <= float(value) <= 100:
        raise PlexError("unsupported", "volume must be 0-100")
    player = await players.get_player(plex, player_id)
    params = {param: int(value) if action != "skip_to" else value} if param else None
    await players.send(plex, player, command, params)
    return {
        "player_id": player["id"],
        "action": action,
        "timeline": await players.timeline(plex, player),
    }


def main() -> None:
    logger.info("plexamp MCP server starting")
    mcp.run()


if __name__ == "__main__":
    main()
