"""Plex sign-in exposed as MCP OAuth, against a fake plex.tv and PMS."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.auth.provider import AuthorizationParams, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request

from src.plexamp import auth
from src.plexamp.helpers import PlexClient, PlexError

REDIRECT = "http://localhost:33418/callback"


class FakePlexTv:
    """Routes (host, method, path) -> JSON, callable, or exception."""

    def __init__(self):
        self.routes = {
            ("plex.tv", "POST", "/api/v2/pins"): {"id": 1, "code": "ABC"},
            ("plex.tv", "GET", "/api/v2/pins/1"): {"id": 1, "authToken": "acct"},
            ("plex.tv", "GET", "/api/v2/user"): {"username": "sam"},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        route = self.routes.get((request.url.host, request.method, request.url.path))
        if callable(route):
            route = route(request)
        if isinstance(route, Exception):
            raise route
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(404) if route is None else httpx.Response(200, json=route)


@pytest.fixture
def plextv(monkeypatch, tmp_path):
    fake = FakePlexTv()
    monkeypatch.setattr(auth, "AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(auth, "_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(auth, "PLEX_TOKEN", None)
    monkeypatch.setattr(auth, "PLEX_URL", None)
    monkeypatch.setattr(auth, "PLEX_SERVER", None)
    monkeypatch.setattr(auth, "PIN_WAIT", 0)
    return fake


async def signed_in_client(provider):
    client = OAuthClientInformationFull(client_id="c1", redirect_uris=[REDIRECT])
    await provider.register_client(client)
    url = await provider.authorize(
        client,
        AuthorizationParams(
            state="xyz",
            scopes=[],
            code_challenge="challenge",
            redirect_uri=REDIRECT,
            redirect_uri_provided_explicitly=True,
        ),
    )
    return client, url


def callback_request(url: str) -> Request:
    forward = parse_qs(url.split("#?", 1)[1])["forwardUrl"][0]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/plex/callback",
            "headers": [],
            "query_string": urlparse(forward).query.encode(),
        }
    )


async def test_full_sign_in_flow(plextv):
    provider = auth.PlexOAuthProvider("http://127.0.0.1:8765")
    client, url = await signed_in_client(provider)
    assert url.startswith("https://app.plex.tv/auth#?")
    assert (
        "code=ABC" in url
        and "forwardUrl=http%3A%2F%2F127.0.0.1%3A8765%2Fplex%2Fcallback" in url
    )

    resp = await provider.callback(callback_request(url))
    assert resp.status_code == 302
    location = urlparse(resp.headers["location"])
    params = parse_qs(location.query)
    assert location.path == "/callback" and params["state"] == ["xyz"]
    assert auth.load()["plex"] == {"token": "acct", "username": "sam"}

    code = await provider.load_authorization_code(client, params["code"][0])
    tokens = await provider.exchange_authorization_code(client, code)
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, code)

    # tokens survive a restart (persisted as hashes, never in plain text)
    restarted = auth.PlexOAuthProvider("http://127.0.0.1:8765")
    assert await restarted.get_client("c1")
    assert await restarted.verify_token(tokens.access_token)
    assert tokens.access_token not in auth.AUTH_FILE.read_text()

    refresh = await restarted.load_refresh_token(client, tokens.refresh_token)
    rotated = await restarted.exchange_refresh_token(client, refresh, [])
    assert await restarted.verify_token(rotated.access_token)
    assert not await restarted.verify_token(tokens.access_token)

    auth.sign_out()  # Plex sign-in gone -> MCP tokens rejected -> client re-auths
    assert not await restarted.verify_token(rotated.access_token)


async def test_callback_rejects_unknown_or_unapproved(plextv):
    provider = auth.PlexOAuthProvider("http://127.0.0.1:8765")
    bad = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"txn=nope",
        }
    )
    assert (await provider.callback(bad)).status_code == 400

    plextv.routes[("plex.tv", "GET", "/api/v2/pins/1")] = {"id": 1, "authToken": None}
    _, url = await signed_in_client(provider)
    assert (await provider.callback(callback_request(url))).status_code == 400
    assert "plex" not in auth.load()


def resources(*servers):
    return lambda request: httpx.Response(200, json=list(servers))


SHARED = {
    "name": "Friend",
    "provides": "server",
    "owned": False,
    "accessToken": "f",
    "connections": [{"uri": "https://friend:32400", "local": False}],
}
OWNED = {
    "name": "Home",
    "provides": "server,player",
    "owned": True,
    "clientIdentifier": "mid",
    "accessToken": "srv",
    "connections": [
        {"uri": "https://relay:8443", "relay": True},
        {"uri": "https://remote:32400", "local": False},
        {"uri": "http://10.0.0.9:32400", "local": True},
    ],
}


async def test_server_discovery(plextv, monkeypatch):
    auth.save({"plex": {"token": "acct"}})
    plextv.routes[("clients.plex.tv", "GET", "/api/v2/resources")] = resources(
        SHARED, OWNED, {"name": "Phone", "provides": "client,player"}
    )
    plextv.routes[("10.0.0.9", "GET", "/identity")] = httpx.ConnectError("down")
    plextv.routes[("remote", "GET", "/identity")] = {}
    plextv.routes[("friend", "GET", "/identity")] = {}

    assert await auth.resolve() == ("https://remote:32400", "srv", True)
    assert auth.load()["server"]["name"] == "Home"  # cached

    auth.forget_server()
    monkeypatch.setattr(auth, "PLEX_SERVER", "friend")
    assert await auth.resolve() == ("https://friend:32400", "f", True)


async def test_env_override_and_missing_credentials(plextv, monkeypatch):
    with pytest.raises(PlexError) as err:
        await auth.resolve()
    assert err.value.code == "auth_required"

    monkeypatch.setattr(auth, "PLEX_TOKEN", "envtok")
    monkeypatch.setattr(auth, "PLEX_URL", "http://pms:32400")
    assert await auth.resolve() == ("http://pms:32400", "envtok", False)


async def test_revoked_token_signs_out(plextv):
    auth.save(
        {
            "plex": {"token": "acct"},
            "server": {"url": "http://pms:32400", "access_token": "srv"},
        }
    )
    plextv.routes[("pms", "GET", "/library/sections")] = httpx.Response(401)
    plextv.routes[("clients.plex.tv", "GET", "/api/v2/resources")] = httpx.Response(401)

    plex = PlexClient(transport=auth._transport)
    with pytest.raises(PlexError) as err:
        await plex.music_sections()
    assert err.value.code == "auth_required"
    assert "plex" not in auth.load() and "server" not in auth.load()
