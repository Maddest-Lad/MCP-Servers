"""Plex Media Server access for the plexamp MCP server (music libraries only)."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import uuid
from typing import Any, Iterable
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from fastmcp.exceptions import ToolError

load_dotenv()

PLEX_URL = os.getenv("PLEX_URL", "http://127.0.0.1:32400").rstrip("/")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
PLEX_CLIENT_ID = os.getenv("PLEX_CLIENT_ID") or str(
    uuid.uuid5(uuid.NAMESPACE_DNS, f"plexamp-mcp.{platform.node()}")
)
PLEX_TIMEOUT = float(os.getenv("PLEX_TIMEOUT", "15"))
PLEX_MUSIC_SECTIONS = {
    s.strip() for s in os.getenv("PLEX_MUSIC_SECTIONS", "").split(",") if s.strip()
}
PLEXAMP_PLAYERS = [
    p.strip() for p in os.getenv("PLEXAMP_PLAYERS", "").split(",") if p.strip()
]

MAX_LIMIT = 200
TYPE_IDS = {"artist": 8, "album": 9, "track": 10}
MUSIC_TYPES = {"artist", "album", "track"}
LIBRARY_PROVIDER = "com.plexapp.plugins.library"


class PlexError(ToolError):
    """Tool error with a stable machine-readable code (see spec: error codes)."""

    def __init__(self, code: str, message: str, **details: Any):
        self.code = code
        self.details = details
        payload = {"code": code, "message": message}
        if details:
            payload["details"] = details
        super().__init__(json.dumps(payload, default=str))


def plex_headers(token: str = PLEX_TOKEN) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Plex-Token": token,
        "X-Plex-Client-Identifier": PLEX_CLIENT_ID,
        "X-Plex-Product": "plexamp-mcp",
        "X-Plex-Version": "1.0.0",
        "X-Plex-Device-Name": f"plexamp-mcp ({platform.node()})",
        "X-Plex-Platform": platform.system(),
    }


def clamp(limit: int, maximum: int = MAX_LIMIT) -> int:
    return max(1, min(maximum, int(limit)))


def _clean_params(params: dict[str, Any] | Iterable | None) -> Any:
    if params is None or not isinstance(params, dict):
        return params
    return {
        k: int(v) if isinstance(v, bool) else v
        for k, v in params.items()
        if v is not None
    }


class PlexClient:
    """Thin async wrapper around the PMS HTTP API with JSON responses."""

    def __init__(
        self,
        base_url: str = PLEX_URL,
        token: str = PLEX_TOKEN,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url
        self.token = token
        self.transport = transport  # shared with player requests (tests)
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=plex_headers(token),
            timeout=PLEX_TIMEOUT,
            transport=transport,
        )
        self._machine_id: str | None = None
        self._sections: list[dict] | None = None
        self._meta: dict[tuple[str, str], dict] = {}

    async def request(
        self,
        method: str,
        path: str,
        params: Any = None,
        *,
        start: int | None = None,
        size: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """Return the response `MediaContainer` (or `{}` for empty bodies)."""
        headers = dict(headers or {})
        if size is not None:  # PMS ignores Size unless Start is also sent
            headers["X-Plex-Container-Start"] = str(start or 0)
            headers["X-Plex-Container-Size"] = str(size)
        try:
            resp = await self._http.request(
                method, path, params=_clean_params(params), headers=headers
            )
        except httpx.RequestError as e:
            raise PlexError("pms_error", f"Cannot reach Plex at {self.base_url}: {e}")
        if resp.status_code == 404:
            raise PlexError("not_found", f"{method} {path} returned 404")
        if resp.status_code == 401:
            raise PlexError("pms_error", "Plex rejected the token (401)")
        if resp.status_code >= 400:
            raise PlexError(
                "pms_error",
                f"{method} {path} failed with HTTP {resp.status_code}",
                body=resp.text[:300],
            )
        if not resp.content.strip():
            return {}
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data.get("MediaContainer", data) if isinstance(data, dict) else {}

    async def machine_id(self) -> str:
        if self._machine_id is None:
            self._machine_id = (await self.request("GET", "/identity"))[
                "machineIdentifier"
            ]
        return self._machine_id

    async def library_uri(self, path: str) -> str:
        return f"server://{await self.machine_id()}/{LIBRARY_PROVIDER}{path}"

    async def items_uri(self, rating_keys: Iterable[str]) -> str:
        return await self.library_uri(f"/library/metadata/{','.join(rating_keys)}")

    async def music_sections(self) -> list[dict]:
        if self._sections is None:
            data = await self.request("GET", "/library/sections")
            self._sections = [
                {"id": str(d["key"]), "title": d.get("title"), "uuid": d.get("uuid")}
                for d in data.get("Directory", [])
                if d.get("type") == "artist"
                and (not PLEX_MUSIC_SECTIONS or str(d["key"]) in PLEX_MUSIC_SECTIONS)
            ]
        return self._sections

    async def section_ids(self) -> list[str]:
        return [s["id"] for s in await self.music_sections()]

    async def resolve_section(self, section_id: str | None) -> str:
        """Return one music section id, refusing to guess between several."""
        sections = await self.music_sections()
        ids = [s["id"] for s in sections]
        if section_id is not None:
            if str(section_id) not in ids:
                raise PlexError(
                    "not_music",
                    f"Section {section_id} is not an allowed music library",
                    music_sections=sections,
                )
            return str(section_id)
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise PlexError("not_found", "No music libraries found on this server")
        raise PlexError(
            "ambiguous",
            "Several music libraries exist; pass section_id",
            candidates=sections,
        )

    async def metadata(self, rating_key: str, **params: Any) -> dict:
        """Fetch one item and enforce the music-only guard."""
        data = await self.request("GET", f"/library/metadata/{rating_key}", params)
        items = data.get("Metadata") or []
        if not items:
            raise PlexError("not_found", f"No item with id {rating_key}")
        item = items[0]
        section = str(item.get("librarySectionID", data.get("librarySectionID", "")))
        if item.get("type") not in MUSIC_TYPES or (
            section and section not in await self.section_ids()
        ):
            raise PlexError(
                "not_music",
                f"Item {rating_key} is a {item.get('type')}, not music in an allowed library",
            )
        return item

    async def check_music(self, rating_keys: Iterable[str]) -> list[dict]:
        return list(await asyncio.gather(*(self.metadata(k) for k in rating_keys)))

    async def playlist(self, playlist_id: str) -> dict:
        data = await self.request("GET", f"/playlists/{playlist_id}")
        items = data.get("Metadata") or []
        if not items:
            raise PlexError("not_found", f"No playlist with id {playlist_id}")
        if items[0].get("playlistType") != "audio":
            raise PlexError(
                "not_music", f"Playlist {playlist_id} is not an audio playlist"
            )
        return items[0]

    async def filter_meta(self, section_id: str, libtype: str) -> dict:
        """Filter/sort/field metadata for a section and libtype (cached)."""
        cache_key = (section_id, libtype)
        if cache_key not in self._meta:
            data = await self.request(
                "GET",
                f"/library/sections/{section_id}/all",
                {"type": TYPE_IDS[libtype], "includeMeta": 1, "includeAdvanced": 1},
                start=0,
                size=0,
            )
            meta = data.get("Meta", {})
            self._meta[cache_key] = {
                "types": {t.get("type"): t for t in meta.get("Type", [])},
                "operators": {
                    ft["type"]: ft.get("Operator", [])
                    for ft in meta.get("FieldType", [])
                },
            }
        return self._meta[cache_key]

    async def aclose(self) -> None:
        await self._http.aclose()


# ---------- Normalizers ----------


def item_summary(m: dict) -> dict:
    """Compact, model-friendly view of a PMS metadata item."""
    kind = m.get("type")
    out: dict[str, Any] = {
        "id": _str(m.get("ratingKey")),
        "type": kind,
        "title": m.get("title"),
    }
    if kind == "track":
        out |= {
            "artist": m.get("originalTitle") or m.get("grandparentTitle"),
            "album": m.get("parentTitle"),
            "album_id": _str(m.get("parentRatingKey")),
            "artist_id": _str(m.get("grandparentRatingKey")),
            "index": m.get("index"),
            "disc": m.get("parentIndex"),
            "duration_ms": m.get("duration"),
            "year": m.get("parentYear") or m.get("year"),
        }
    elif kind == "album":
        out |= {
            "artist": m.get("parentTitle"),
            "artist_id": _str(m.get("parentRatingKey")),
            "year": m.get("year"),
            "track_count": m.get("leafCount"),
        }
    elif kind == "artist":
        out["album_count"] = m.get("childCount")
    elif kind == "playlist":
        out |= {
            "smart": bool(m.get("smart")),
            "item_count": m.get("leafCount"),
            "duration_ms": m.get("duration"),
        }
    if out["id"] is None:
        out["key"] = m.get("key")
    out["section_id"] = _str(m.get("librarySectionID"))
    out["playlist_item_id"] = _str(m.get("playlistItemID"))
    out["play_queue_item_id"] = _str(m.get("playQueueItemID"))
    return {k: v for k, v in out.items() if v is not None}


def page(items: list, container: dict, start: int, limit: int) -> dict:
    total = int(container.get("totalSize", container.get("size", len(items))) or 0)
    return {
        "items": items,
        "total": total,
        "start": start,
        "limit": limit,
        "truncated": start + len(items) < total,
    }


def queue_summary(q: dict) -> dict:
    return {
        "play_queue_id": _str(q.get("playQueueID")),
        "version": q.get("playQueueVersion"),
        "selected_item_id": _str(q.get("playQueueSelectedItemID")),
        "shuffled": bool(q.get("playQueueShuffled")),
        "total": q.get("playQueueTotalCount"),
        "items": [item_summary(m) for m in q.get("Metadata", [])],
    }


def stations_in(obj: dict) -> list[dict]:
    """Station entries found under a `Stations` element or a stations hub."""
    found = []
    stations = obj.get("Stations")
    for group in stations if isinstance(stations, list) else [stations or {}]:
        found += group.get("Metadata", []) + group.get("Directory", [])
    return [
        {"title": s.get("title"), "key": s.get("key"), "type": s.get("type")}
        for s in found
        if s.get("key")
    ]


def _str(v: Any) -> str | None:
    return None if v is None else str(v)


# ---------- Search / ambiguity ----------


async def search(
    plex: PlexClient,
    query: str,
    types: Iterable[str] = ("artist", "album", "track"),
    section_id: str | None = None,
    limit: int = 20,
) -> dict[str, list[dict]]:
    params = {"query": query, "limit": clamp(limit), "searchTypes": "music"}
    if section_id is not None:
        params["sectionId"] = await plex.resolve_section(section_id)
    data = await plex.request("GET", "/hubs/search", params)
    allowed = set(await plex.section_ids())
    wanted = set(types)
    results: dict[str, list[dict]] = {t: [] for t in wanted}
    for hub in data.get("Hub", []):
        for m in hub.get("Metadata", []):
            if m.get("type") in wanted and str(m.get("librarySectionID")) in allowed:
                results[m["type"]].append(item_summary(m))
    return results


async def resolve_id(
    plex: PlexClient,
    item_id: str | None,
    title: str | None,
    kind: str = "track",
    artist: str | None = None,
) -> str:
    """Use `item_id` as-is, else find exactly one `kind` titled `title`."""
    if item_id:
        return str(item_id)
    if not title:
        raise PlexError("not_found", "Pass an id or a title")
    hits = (await search(plex, title, [kind], limit=50))[kind]
    exact = [
        h
        for h in hits
        if h["title"].casefold() == title.casefold()
        and (
            not artist
            or (h.get("artist") or h["title"]).casefold() == artist.casefold()
        )
    ]
    if len(exact) == 1:
        return exact[0]["id"]
    if not hits:
        raise PlexError("not_found", f"No {kind} matching '{title}'")
    raise PlexError(
        "ambiguous",
        f"{len(exact) or 'No exact'} {kind} matches for '{title}'; pass an id",
        candidates=(exact or hits)[:10],
    )


# ---------- Filters (browse + smart playlists) ----------


async def build_filters(
    plex: PlexClient,
    section_id: str,
    libtype: str,
    rules: list[dict] | None,
    match: str = "all",
    sort: str | None = None,
) -> list[tuple[str, str]]:
    """Validate rules against the section's filter metadata and return params.

    A rule is `{"field": "genre" | "artist.title", "operator": "=" | "is" ...,
    "value": ...}`. Operators may be given by PMS key or title.
    """
    meta = await plex.filter_meta(section_id, libtype)
    params: list[tuple[str, str]] = [("type", str(TYPE_IDS[libtype]))]
    parts = []
    for rule in rules or []:
        parts.append(await _rule_param(plex, meta, libtype, rule))
    if match == "any" and len(parts) > 1:
        params.append(("push", "1"))
        for i, p in enumerate(parts):
            params += [("or", "1")] if i else []
            params.append(p)
        params.append(("pop", "1"))
    else:
        params += parts
    if sort:
        params.append(("sort", _validate_sort(meta, libtype, sort)))
    return params


async def _rule_param(
    plex: PlexClient, meta: dict, libtype: str, rule: dict
) -> tuple[str, str]:
    field = str(rule.get("field", ""))
    ftype_name, _, name = field.rpartition(".")
    ftype_name = ftype_name or libtype
    type_meta = meta["types"].get(ftype_name)
    if type_meta is None:
        raise PlexError("invalid_filter", f"Unknown libtype in field '{field}'")
    fields = {f["key"].rpartition(".")[2]: f for f in type_meta.get("Field", [])}
    if name not in fields:
        raise PlexError(
            "invalid_filter",
            f"Unknown {ftype_name} field '{name}'",
            fields=sorted(fields),
        )
    fdef = fields[name]
    operators = meta["operators"].get(fdef.get("type"), [])
    op_in = str(rule.get("operator", "=")).casefold()
    op = next(
        (
            o["key"]
            for o in operators
            if op_in in (o["key"], o.get("title", "").casefold())
        ),
        None,
    )
    if op is None:
        raise PlexError(
            "invalid_filter",
            f"Operator '{rule.get('operator')}' not valid for {field}",
            operators=[{"key": o["key"], "title": o.get("title")} for o in operators],
        )
    value = rule.get("value")
    values = value if isinstance(value, list) else [value]
    if fdef.get("type") == "tag":
        values = [await _tag_key(plex, type_meta, name, v) for v in values]
    elif fdef.get("type") == "boolean":
        values = [int(bool(v)) for v in values]
    key = f"{ftype_name}.{name}{op[:-1]}"
    return key, ",".join(str(v) for v in values)


async def _tag_key(plex: PlexClient, type_meta: dict, name: str, value: Any) -> str:
    """Map a tag title (e.g. genre 'Jazz') to its PMS filter key."""
    fdef = next(
        (f for f in type_meta.get("Filter", []) if f.get("filter") == name), None
    )
    if fdef is None or not fdef.get("key"):
        return str(value)
    choices = (await plex.request("GET", fdef["key"])).get("Directory", [])
    wanted = str(value).casefold()
    for c in choices:
        if wanted in (str(c.get("key")).casefold(), str(c.get("title")).casefold()):
            return str(c["key"]).rpartition("/")[2]
    raise PlexError(
        "invalid_filter",
        f"No {name} named '{value}'",
        examples=[c.get("title") for c in choices[:20]],
    )


def _validate_sort(meta: dict, libtype: str, sort: str) -> str:
    field, _, direction = sort.partition(":")
    known = {s.get("key") for s in meta["types"].get(libtype, {}).get("Sort", [])}
    if known and field not in known:
        raise PlexError(
            "invalid_filter", f"Unknown sort '{field}'", sorts=sorted(known)
        )
    if direction not in ("", "asc", "desc"):
        raise PlexError("invalid_filter", "Sort direction must be asc or desc")
    return f"{field}:{direction}" if direction else field


def query_string(params: list[tuple[str, str]]) -> str:
    """Join filter params the way PMS expects inside a smart-playlist URI."""
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)


# ---------- Play queues ----------


async def create_queue(
    plex: PlexClient,
    *,
    uri: str | None = None,
    playlist_id: str | None = None,
    shuffle: bool = False,
    repeat: int = 0,
    start_key: str | None = None,
) -> dict:
    return await plex.request(
        "POST",
        "/playQueues",
        {
            "type": "audio",
            "uri": uri,
            "playlistID": playlist_id,
            "shuffle": shuffle,
            "repeat": repeat,
            "continuous": 0,
            "key": start_key,
        },
    )


async def get_queue(plex: PlexClient, queue_id: str, window: int = 50) -> dict:
    return await plex.request(
        "GET", f"/playQueues/{queue_id}", {"window": clamp(window), "own": 1}
    )


async def fresh_queue(
    plex: PlexClient,
    queue_id: str,
    version: int | None,
    item_ids: Iterable[str | None] = (),
) -> dict:
    """Re-read a queue and refuse to mutate it if it changed underneath us."""
    q = await get_queue(plex, queue_id, window=MAX_LIMIT)
    present = {str(m.get("playQueueItemID")) for m in q.get("Metadata", [])}
    missing = [i for i in item_ids if i is not None and str(i) not in present]
    if (version is not None and q.get("playQueueVersion") != version) or missing:
        raise PlexError(
            "stale_queue",
            "The play queue changed; re-read it and retry",
            missing_items=missing,
            queue=queue_summary(q),
        )
    return q
