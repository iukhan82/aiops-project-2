"""P09.02 / P08.04: a small OpenID Connect client that behaves like the browser
does, used by the verification scripts.

It performs the real Authorization Code + PKCE (S256) flow against Keycloak:
load the login page, post the credentials the way the login form does, read the
authorization code off the redirect, and exchange it with the code verifier. No
password grant, no test-only back door - the same path `keycloak-js` takes in the
browser. It only exists in verification code; the UI never sees a password.

Keycloak advertises itself as `http://localhost:8180` (its configured public
hostname, which is what ends up in the token's `iss`). On this Windows host
`localhost` resolves to IPv6 first while the container port is published on
IPv4 only, so this client connects to `127.0.0.1:8180` - the same server - and
rewrites the advertised host on the URLs the server hands back, keeping every
request (and its cookies) on one host. A browser handles this by trying both
address families; the token contents are identical either way.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt

PUBLIC = "http://localhost:8180"
BASE = "http://127.0.0.1:8180"
REALM = "aiops"
REALM_URL = f"{BASE}/realms/{REALM}"
AUTH_URL = f"{REALM_URL}/protocol/openid-connect/auth"
TOKEN_URL = f"{REALM_URL}/protocol/openid-connect/token"
LOGOUT_URL = f"{REALM_URL}/protocol/openid-connect/logout"
REDIRECT_URI = "http://localhost:5173/callback"


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    id_token: str
    expires_in: int

    @property
    def claims(self) -> dict:
        return jwt.decode(self.access_token, options={"verify_signature": False})


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


class LoginFailed(Exception):
    pass


def local(url: str) -> str:
    return url.replace(PUBLIC, BASE)


def authorization_request(
    client: httpx.Client,
    challenge: str | None,
    method: str | None = "S256",
    client_id: str = "aiops-ui",
    redirect_uri: str = REDIRECT_URI,
    response_type: str = "code",
    state: str = "s",
) -> httpx.Response:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": response_type,
        "scope": "openid",
        "state": state,
        "nonce": secrets.token_urlsafe(8),
    }
    if challenge is not None:
        params["code_challenge"] = challenge
    if method is not None:
        params["code_challenge_method"] = method
    return client.get(f"{AUTH_URL}?{urlencode(params)}", follow_redirects=False)


def submit_credentials(
    client: httpx.Client, login_page: httpx.Response, username: str, password: str
) -> httpx.Response:
    match = re.search(
        r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', login_page.text
    ) or re.search(r'action="([^"]*login-actions/authenticate[^"]*)"', login_page.text)
    if match is None:
        raise LoginFailed("no login form on the page (is the client/redirect misconfigured?)")
    # Keycloak marks its session cookies `Secure` even here; a browser accepts that on http://localhost, but a
    # plain HTTP client's jar will not send them back over http, so replay them explicitly, as the browser does.
    cookie_header = "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)
    return client.post(
        local(html.unescape(match.group(1))),
        data={"username": username, "password": password, "credentialId": ""},
        headers={"Cookie": cookie_header},
        follow_redirects=False,
    )


def code_from(response: httpx.Response) -> str:
    location = response.headers.get("location", "")
    query = parse_qs(urlparse(location).query)
    if "code" not in query:
        raise LoginFailed(
            f"no authorization code: status {response.status_code}, location {location[:120]!r}"
        )
    return query["code"][0]


def exchange_code(
    client: httpx.Client,
    code: str,
    verifier: str,
    client_id: str = "aiops-ui",
    redirect_uri: str = REDIRECT_URI,
) -> httpx.Response:
    return client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )


def login(username: str, password: str, client_id: str = "aiops-ui") -> Tokens:
    """The whole browser flow, ending in tokens. Raises LoginFailed on any refusal."""
    verifier, challenge = pkce_pair()
    with httpx.Client(timeout=30) as client:
        page = authorization_request(client, challenge)
        if page.status_code != 200:
            raise LoginFailed(f"authorization endpoint answered {page.status_code}")
        posted = submit_credentials(client, page, username, password)
        code = code_from(posted)
        token = exchange_code(client, code, verifier, client_id)
        if token.status_code != 200:
            raise LoginFailed(f"token endpoint answered {token.status_code}: {token.text[:160]}")
        body = token.json()
        return Tokens(
            body["access_token"], body["refresh_token"], body["id_token"], int(body["expires_in"])
        )


def refresh(refresh_token: str, client_id: str = "aiops-ui") -> httpx.Response:
    return httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        timeout=30,
    )


def client_credentials(client_id: str, secret: str) -> Tokens:
    body = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
        timeout=30,
    )
    body.raise_for_status()
    data = body.json()
    return Tokens(data["access_token"], "", "", int(data["expires_in"]))


def end_session(id_token: str, refresh_token: str) -> httpx.Response:
    return httpx.post(
        LOGOUT_URL,
        data={"client_id": "aiops-ui", "id_token_hint": id_token, "refresh_token": refresh_token},
        timeout=30,
        follow_redirects=False,
    )
