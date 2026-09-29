"""Acceptance scenarios for the plexamp server against a fake PMS + player."""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest

from src.plexamp import server
from src.plexamp.helpers import PlexClient, PlexError

PLAYER = "10.0.0.5"


def track(rk, title, artist="Artist", section=1, **extra):
    return {
        "ratingKey": str(rk),
        "key": f"/library/metadata/{rk}",
        "type": "track",
        "title": title,
        "grandparentTitle": artist,
        "parentTitle": "Album",
        "librarySectionID": section,
        **extra,
    }


def mc(**kw):
    return {"MediaContainer": kw}


class FakePlex:
    """Routes (method, path) -> JSON (or callable); records every request."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.routes = {
            ("GET", "/identity"): mc(machineIdentifier="mid"),
            ("GET", "/library/sections"): mc(
                Directory=[
                    {"key": "1", "type": "artist", "title": "Music"},
                    {"key": "2", "type": "artist", "title": "Classical"},
                    {"key": "3", "type": "movie", "title": "Movies"},
                ]
            ),
            ("GET", "/clients"): mc(Server=[]),
            ("GET", "/status/sessions"): mc(Metadata=[]),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        route = self.routes.get((request.method, request.url.path))
        if callable(route):
            return route(request)
        if route is None:
            return httpx.Response(404)
        if isinstance(route, str):
            return httpx.Response(200, text=route)
        return httpx.Response(200, json=route)

    def sent(self, method, path_prefix):
        return [
            c
            for c in self.calls
            if c.method == method and c.url.path.startswith(path_prefix)
        ]


@pytest.fixture
def fake(monkeypatch):
    fake = FakePlex()
    client = PlexClient(
        "http://pms.local:32400", "tok", transport=httpx.MockTransport(fake)
    )
    monkeypatch.setattr(server, "plex", client)

    async def no_sleep(_):
        return None

    monkeypatch.setattr("src.plexamp.players.asyncio.sleep", no_sleep)
    return fake


async def expect(code, coro):
    with pytest.raises(PlexError) as err:
        await coro
    assert err.value.code == code, str(err.value)
    return json.loads(str(err.value))


# ---------- Find ----------


async def test_duplicate_titles_are_ambiguous_until_disambiguated(fake):
    fake.routes[("GET", "/hubs/search")] = mc(
        Hub=[
            {
                "type": "track",
                "Metadata": [
                    track(10, "Intro", "The xx"),
                    track(11, "Intro", "M83"),
                    track(12, "Intro", "Movie", 3),
                ],
            }
        ]
    )
    body = await expect("ambiguous", server.music_get_item.fn(title="intro"))
    assert {c["id"] for c in body["details"]["candidates"]} == {
        "10",
        "11",
    }  # movie lib excluded

    fake.routes[("GET", "/library/metadata/11")] = mc(
        Metadata=[track(11, "Intro", "M83")]
    )
    item = await server.music_get_item.fn(title="Intro", artist="m83")
    assert item["id"] == "11"


async def test_multiple_music_libraries(fake):
    await expect("ambiguous", server.music_browse.fn())
    await expect("not_music", server.music_browse.fn(section_id="3"))

    fake.routes[("GET", "/library/sections/2/all")] = mc(
        totalSize=120,
        size=1,
        Meta={"Type": [{"type": "album", "Sort": [], "Field": []}]},
        Metadata=[{"ratingKey": "5", "type": "album", "title": "Goldberg"}],
    )
    result = await server.music_browse.fn(section_id="2", start=10, limit=1)
    assert result["truncated"] and result["total"] == 120
    last = fake.calls[-1]
    assert last.headers["X-Plex-Container-Start"] == "10"
    assert last.headers["X-Plex-Token"] == "tok" and "X-Plex-Token" not in str(last.url)


async def test_non_music_item_is_rejected(fake):
    fake.routes[("GET", "/library/metadata/99")] = mc(
        Metadata=[
            {"ratingKey": "99", "type": "movie", "title": "Heat", "librarySectionID": 3}
        ]
    )
    await expect("not_music", server.music_get_track_files.fn(id="99"))


async def test_track_files_are_returned_verbatim(fake):
    fake.routes[("GET", "/library/metadata/10")] = mc(
        Metadata=[
            track(
                10,
                "A",
                Media=[
                    {
                        "id": 7,
                        "audioCodec": "flac",
                        "Part": [{"id": 8, "file": "/data/music/a.flac"}],
                    },
                    {
                        "id": 9,
                        "audioCodec": "mp3",
                        "Part": [{"id": 11, "file": "\\\\nas\\m\\a.mp3"}],
                    },
                ],
            )
        ]
    )
    result = await server.music_get_track_files.fn(id="10")
    assert [f["file"] for f in result["files"]] == [
        "/data/music/a.flac",
        "\\\\nas\\m\\a.mp3",
    ]
    assert result["files"][1]["part_id"] == "11"


# ---------- Smart playlists ----------

META = mc(
    Meta={
        "Type": [
            {
                "type": "track",
                "Field": [
                    {"key": "genre", "type": "tag"},
                    {"key": "title", "type": "string"},
                ],
                "Filter": [
                    {"filter": "genre", "key": "/library/sections/1/genre?type=10"}
                ],
                "Sort": [{"key": "userRating"}],
            }
        ],
        "FieldType": [
            {
                "type": "tag",
                "Operator": [
                    {"key": "=", "title": "is"},
                    {"key": "!=", "title": "is not"},
                ],
            },
            {"type": "string", "Operator": [{"key": "=", "title": "contains"}]},
        ],
    }
)


async def test_smart_playlist_filters_are_validated(fake):
    fake.routes[("GET", "/library/sections/1/all")] = META
    fake.routes[("GET", "/library/sections/1/genre")] = mc(
        Directory=[{"key": "5", "title": "Jazz"}, {"key": "6", "title": "Rock"}]
    )
    smart = {"section_id": "1", "rules": [{"field": "mood", "value": "x"}]}
    await expect("invalid_filter", server.music_create_playlist.fn("P", smart=smart))
    smart["rules"] = [{"field": "title", "operator": "is not", "value": "x"}]
    await expect("invalid_filter", server.music_create_playlist.fn("P", smart=smart))
    smart["rules"] = [{"field": "genre", "operator": "is", "value": "Polka"}]
    await expect("invalid_filter", server.music_create_playlist.fn("P", smart=smart))

    fake.routes[("POST", "/playlists")] = mc(Metadata=[{"ratingKey": "50"}])
    fake.routes[("GET", "/playlists/50")] = mc(
        Metadata=[
            {"ratingKey": "50", "type": "playlist", "playlistType": "audio", "smart": 1}
        ]
    )
    fake.routes[("GET", "/playlists/50/items")] = mc(Metadata=[])
    smart = {
        "section_id": "1",
        "match": "any",
        "sort": "userRating:desc",
        "rules": [
            {"field": "genre", "operator": "is", "value": "jazz"},
            {"field": "genre", "operator": "!=", "value": "Rock"},
        ],
    }
    await server.music_create_playlist.fn("Jazzish", smart=smart)
    post = fake.sent("POST", "/playlists")[0]
    assert post.url.params["smart"] == "1" and post.url.params["type"] == "audio"
    uri = post.url.params["uri"]
    assert uri.startswith(
        "server://mid/com.plexapp.plugins.library/library/sections/1/all?"
    )
    assert unquote(uri).endswith(
        "type=10&push=1&track.genre=5&or=1&track.genre!=6&pop=1&sort=userRating:desc"
    )


# ---------- Playlist entries ----------


async def test_duplicate_playlist_entries_removed_by_item_id(fake):
    fake.routes[("GET", "/playlists/7")] = mc(
        Metadata=[{"ratingKey": "7", "type": "playlist", "playlistType": "audio"}]
    )
    fake.routes[("GET", "/playlists/7/items")] = mc(
        Metadata=[
            track(10, "Same", playlistItemID=100),
            track(10, "Same", playlistItemID=101),
        ]
    )
    fake.routes[("DELETE", "/playlists/7/items/101")] = mc()
    await server.music_remove_playlist_items.fn("7", ["101"])
    assert [c.url.path for c in fake.sent("DELETE", "/")] == ["/playlists/7/items/101"]
    await expect("not_found", server.music_remove_playlist_items.fn("7", ["10"]))


async def test_video_playlist_is_not_music(fake):
    fake.routes[("GET", "/playlists/8")] = mc(
        Metadata=[{"ratingKey": "8", "type": "playlist", "playlistType": "video"}]
    )
    await expect("not_music", server.music_get_playlist.fn("8"))


# ---------- Sonic ----------


async def test_sonic_unavailable(fake):
    fake.routes[("GET", "/library/metadata/10")] = mc(Metadata=[track(10, "A")])
    fake.routes[("GET", "/library/metadata/11")] = mc(Metadata=[track(11, "B")])
    await expect("sonic_unavailable", server.music_similar_tracks.fn(id="10"))
    fake.routes[("GET", "/library/sections/1/computePath")] = mc(size=0)
    await expect("sonic_unavailable", server.music_sonic_adventure.fn("10", "11"))


async def test_station_unavailable(fake):
    fake.routes[("GET", "/library/metadata/10")] = mc(Metadata=[track(10, "A")])
    await expect("station_unavailable", server.music_start_radio.fn(seed_id="10"))


# ---------- Queues and players ----------

QUEUE = mc(
    playQueueID=33,
    playQueueVersion=4,
    playQueueSelectedItemID=1,
    Metadata=[track(10, "A", playQueueItemID=1), track(11, "B", playQueueItemID=2)],
)


async def test_stale_queue_is_refused(fake):
    fake.routes[("GET", "/playQueues/33")] = QUEUE
    await expect(
        "stale_queue",
        server.music_remove_queue_item.fn("2", play_queue_id="33", version=3),
    )
    await expect(
        "stale_queue", server.music_move_queue_item.fn("9", play_queue_id="33")
    )
    assert not fake.sent("DELETE", "/playQueues") and not fake.sent(
        "PUT", "/playQueues"
    )


def _player_routes(fake, caps="timeline,playback", state="stopped", reachable=True):
    fake.routes[("GET", "/clients")] = mc(
        Server=[
            {
                "machineIdentifier": "amp1",
                "name": "Den",
                "product": "Plexamp",
                "address": PLAYER,
                "port": "32500",
                "protocolCapabilities": caps,
            }
        ]
    )

    def resources(request):
        if not reachable:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(
            200,
            text=(
                f'<MediaContainer><Player title="Den" machineIdentifier="amp1" '
                f'product="Plexamp" protocolCapabilities="{caps}"/></MediaContainer>'
            ),
        )

    fake.routes[("GET", "/resources")] = resources
    fake.routes[("GET", "/player/playback/playMedia")] = "<Response code='200'/>"
    fake.routes[("GET", "/player/timeline/poll")] = (
        f'<MediaContainer><Timeline type="music" state="{state}" playQueueID="33"/></MediaContainer>'
    )


async def test_offline_player(fake):
    _player_routes(fake, reachable=False)
    await expect("player_unreachable", server.music_control_player.fn("amp1", "pause"))
    players = (await server.music_list_players.fn())["players"]
    assert players[0]["controllable"] is False and players[0]["reason"] == "unreachable"


async def test_player_without_playback_capability(fake):
    _player_routes(fake, caps="timeline")
    await expect(
        "player_unsupported_command", server.music_control_player.fn("Den", "next")
    )


async def test_queue_alone_is_not_reported_as_playback(fake):
    _player_routes(fake, state="stopped")
    fake.routes[("GET", "/library/metadata/10")] = mc(Metadata=[track(10, "A")])
    fake.routes[("POST", "/playQueues")] = QUEUE
    result = await server.music_play.fn("amp1", ids=["10"])
    assert result["queue_created"] and result["playback_confirmed"] is False

    play = fake.sent("GET", "/player/playback/playMedia")[0]
    assert play.url.host == PLAYER
    assert play.headers["X-Plex-Target-Client-Identifier"] == "amp1"
    assert play.url.params["containerKey"].startswith("/playQueues/33")
    assert play.url.params["address"] == "pms.local"


async def test_confirmed_playback(fake):
    _player_routes(fake, state="playing")
    fake.routes[("GET", "/playlists/7")] = mc(
        Metadata=[{"ratingKey": "7", "type": "playlist", "playlistType": "audio"}]
    )
    fake.routes[("POST", "/playQueues")] = QUEUE
    result = await server.music_play.fn("amp1", playlist_id="7", shuffle=True)
    assert result["playback_confirmed"] is True
    assert fake.sent("POST", "/playQueues")[0].url.params["playlistID"] == "7"


async def test_control_maps_to_companion_commands(fake):
    _player_routes(fake, state="playing")
    fake.routes[("GET", "/player/playback/setParameters")] = "<Response/>"
    await server.music_control_player.fn("amp1", "volume", 40)
    sent = fake.sent("GET", "/player/playback/setParameters")[0]
    assert sent.url.params["volume"] == "40" and sent.url.params["type"] == "music"
    await expect("unsupported", server.music_control_player.fn("amp1", "volume", 140))
