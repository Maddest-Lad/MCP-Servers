"""Plex sign-in for the plexamp server, exposed as MCP OAuth.

MCP clients (e.g. Claude Code: /mcp -> Authenticate) register with this server,
are sent to Plex's PIN approval page, and come back with a token for *this*
server. The Plex token itself stays in AUTH_FILE on this machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from src.plexamp.helpers import (
    PLEX_CLIENT_ID,
    PLEX_TOKEN,
    PLEX_URL,
    PlexError,
    plex_headers,
)

PLEX_TV = "https://plex.tv"
PLEX_CLIENTS = "https://clients.plex.tv"
AUTH_FILE = Path(
    os.getenv("PLEX_AUTH_FILE") or Path.home() / ".plexamp-mcp" / "auth.json"
)
PLEX_SERVER = os.getenv("PLEX_SERVER")

PIN_TTL = 15 * 60  # how long a started sign-in stays valid
PIN_WAIT = 10.0  # how long the callback waits for Plex to mark the PIN claimed
CODE_TTL = 5 * 60
ACCESS_TTL = 60 * 60

_transport: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport


def auth_required() -> PlexError:
    return PlexError(
        "auth_required",
        "Not signed in to Plex. Authenticate the plexamp server from your "
        "MCP client (e.g. /mcp -> plexamp -> Authenticate).",
    )


# ---------- Store ----------


def load() -> dict[str, Any]:
    try:
        return json.loads(AUTH_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save(data: dict[str, Any]) -> None:
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    tmp.replace(AUTH_FILE)


def forget_server() -> None:
    data = load()
    if data.pop("server", None) is not None:
        save(data)


def sign_out() -> None:
    data = load()
    data.pop("plex", None)
    data.pop("server", None)
    save(data)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- plex.tv ----------


def _http(token: str | None = None, timeout: float = 10.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=plex_headers(token), timeout=timeout, transport=_transport
    )


async def start_pin() -> dict[str, Any]:
    async with _http() as http:
        resp = await http.post(f"{PLEX_TV}/api/v2/pins", params={"strong": "true"})
    resp.raise_for_status()
    return resp.json()


def approval_url(pin_code: str, forward_url: str) -> str:
    return "https://app.plex.tv/auth#?" + urlencode(
        {
            "clientID": PLEX_CLIENT_ID,
            "code": pin_code,
            "forwardUrl": forward_url,
            "context[device][product]": "plexamp-mcp",
        }
    )


async def claim_pin(pin: dict[str, Any]) -> str | None:
    async with _http() as http:
        resp = await http.get(
            f"{PLEX_TV}/api/v2/pins/{pin['id']}", params={"code": pin["code"]}
        )
    return resp.json().get("authToken") if resp.is_success else None


async def account(token: str) -> dict[str, Any] | None:
    async with _http(token) as http:
        resp = await http.get(f"{PLEX_TV}/api/v2/user")
    return resp.json() if resp.is_success else None


async def discover_server(token: str) -> dict[str, Any]:
    """Pick the Plex server (PLEX_SERVER, else the owned/only one) and the
    first connection that answers, preferring local over remote."""
    async with _http(token) as http:
        resp = await http.get(
            f"{PLEX_CLIENTS}/api/v2/resources", params={"includeHttps": 1}
        )
    if resp.status_code == 401:
        sign_out()
        raise auth_required()
    resp.raise_for_status()
    servers = [
        r for r in resp.json() if "server" in (r.get("provides") or "").split(",")
    ]
    if PLEX_SERVER:
        servers = [
            s for s in servers if s.get("name", "").casefold() == PLEX_SERVER.casefold()
        ]
    elif len(servers) > 1:
        servers = [s for s in servers if s.get("owned")] or servers
    if len(servers) != 1:
        raise PlexError(
            "ambiguous" if servers else "not_found",
            "Set PLEX_SERVER to choose a Plex server",
            servers=[s.get("name") for s in servers],
        )
    server = servers[0]
    server_token = server.get("accessToken") or token
    connections = sorted(
        server.get("connections", []),
        key=lambda c: (bool(c.get("relay")), not c.get("local")),
    )
    async with _http(server_token, timeout=3.0) as http:
        for conn in connections:
            with contextlib.suppress(httpx.HTTPError):
                if (await http.get(f"{conn['uri']}/identity")).is_success:
                    return {
                        "name": server.get("name"),
                        "machine_id": server.get("clientIdentifier"),
                        "url": conn["uri"],
                        "access_token": server_token,
                    }
    raise PlexError(
        "pms_error",
        f"No connection to {server.get('name')} answered",
        tried=[c.get("uri") for c in connections],
    )


async def resolve() -> tuple[str, str, bool]:
    """Return (server url, token, from_store) for PMS requests.

    Env PLEX_URL/PLEX_TOKEN win; otherwise the signed-in account is used and
    its server is discovered once and cached.
    """
    data = load()
    token = PLEX_TOKEN or data.get("plex", {}).get("token")
    if not token:
        raise auth_required()
    if PLEX_URL:
        return PLEX_URL, token, PLEX_TOKEN is None
    server = data.get("server")
    if not server:
        server = data["server"] = await discover_server(token)
        save(data)
    return server["url"], server["access_token"], PLEX_TOKEN is None


def signed_in() -> bool:
    return bool(PLEX_TOKEN or load().get("plex", {}).get("token"))


# ---------- MCP OAuth provider ----------


class PlexOAuthProvider(OAuthProvider):
    """OAuth authorization server whose login step is Plex PIN approval."""

    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.callback_url = f"{base_url.rstrip('/')}/plex/callback"
        self._pending: dict[str, dict[str, Any]] = {}
        self._codes: dict[str, AuthorizationCode] = {}

    # Clients (dynamic registration), persisted so restarts keep working.

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = load().get("clients", {}).get(client_id)
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        data = load()
        data.setdefault("clients", {})[client_info.client_id] = client_info.model_dump(
            mode="json", exclude_none=True
        )
        save(data)

    # Authorization: hand off to Plex, finish in `callback`.

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        try:
            pin = await start_pin()
        except httpx.HTTPError as e:
            raise AuthorizeError("server_error", f"Could not reach plex.tv: {e}")
        txn = secrets.token_urlsafe(16)
        self._pending[txn] = {
            "pin": pin,
            "client_id": client.client_id,
            "params": params,
            "expires_at": time.time() + PIN_TTL,
        }
        return approval_url(pin["code"], f"{self.callback_url}?txn={txn}")

    async def callback(self, request: Request) -> Response:
        pending = self._pending.pop(request.query_params.get("txn", ""), None)
        if not pending or pending["expires_at"] < time.time():
            return _page("This sign-in link expired. Start again from your MCP client.")
        deadline = time.monotonic() + PIN_WAIT
        while not (token := await claim_pin(pending["pin"])):
            if time.monotonic() > deadline:
                return _page("Plex did not confirm the sign-in. Please try again.")
            await asyncio.sleep(1)
        user = await account(token) or {}
        data = load()
        data["plex"] = {"token": token, "username": user.get("username")}
        data.pop("server", None)
        save(data)

        params: AuthorizationParams = pending["params"]
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL,
            client_id=pending["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return RedirectResponse(
            construct_redirect_uri(
                str(params.redirect_uri), code=code, state=params.state
            ),
            status_code=302,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if (
            code
            and code.client_id == client.client_id
            and code.expires_at > time.time()
        ):
            return code
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "Authorization code already used")
        return self._issue(client.client_id, authorization_code.scopes)

    # Tokens: opaque, stored as hashes, valid only while Plex sign-in exists.

    def _issue(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        data = load()
        tokens = {
            h: rec
            for h, rec in data.get("mcp_tokens", {}).items()
            if rec["expires_at"] is None or rec["expires_at"] > now
        }
        common = {"client_id": client_id, "scopes": scopes}
        tokens[_hash(access)] = common | {
            "kind": "access",
            "expires_at": now + ACCESS_TTL,
            "pair": _hash(refresh),
        }
        tokens[_hash(refresh)] = common | {
            "kind": "refresh",
            "expires_at": None,
            "pair": _hash(access),
        }
        data["mcp_tokens"] = tokens
        save(data)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )

    def _lookup(self, token: str, kind: str) -> dict[str, Any] | None:
        rec = load().get("mcp_tokens", {}).get(_hash(token))
        if not rec or rec["kind"] != kind or not signed_in():
            return None
        if rec["expires_at"] is not None and rec["expires_at"] <= time.time():
            return None
        return rec

    async def load_access_token(self, token: str) -> AccessToken | None:
        rec = self._lookup(token, "access")
        return rec and AccessToken(
            token=token,
            client_id=rec["client_id"],
            scopes=rec["scopes"],
            expires_at=rec["expires_at"],
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        rec = self._lookup(refresh_token, "refresh")
        if not rec or rec["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token, client_id=rec["client_id"], scopes=rec["scopes"]
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "Scopes exceed the original grant")
        self._revoke(refresh_token.token)
        return self._issue(client.client_id, scopes or refresh_token.scopes)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._revoke(token.token)

    def _revoke(self, token: str) -> None:
        data = load()
        tokens = data.get("mcp_tokens", {})
        rec = tokens.pop(_hash(token), None)
        if rec:
            tokens.pop(rec.get("pair"), None)
            save(data)


def _page(message: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><title>plexamp-mcp</title><p>{message}</p>", status_code=400
    )
