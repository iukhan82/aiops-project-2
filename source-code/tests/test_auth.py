"""P08.04 / P09.02: token validation and capability enforcement logic, with
tokens signed by a locally generated key (no Keycloak, no network). That every
one of these refusals also happens against the real identity provider, real
tokens and the real running API is proven by backend/api/verify_auth.py
(docs/evidence/p08_04_auth.json)."""

import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.api import auth
from backend.api.authz import capabilities_of, inventory, policy_for

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC = KEY.public_key()
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def local_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    client = types.SimpleNamespace(
        get_signing_key_from_jwt=lambda token: types.SimpleNamespace(key=PUBLIC)
    )
    monkeypatch.setattr(auth, "_jwks_client", lambda: client)


def token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": auth.ISSUER,
        "aud": auth.AUDIENCE,
        "sub": "u1",
        "iat": now,
        "exp": now + 300,
        "azp": "aiops-ui",
        "preferred_username": "alex",
        "realm_access": {"roles": ["operator"]},
    }
    key = overrides.pop("_key", PEM)
    algorithm = overrides.pop("_alg", "RS256")
    for name, value in overrides.items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    return jwt.encode(claims, key, algorithm=algorithm, headers=overrides.get("_headers"))


def denial(raw: str) -> auth.Denied:
    with pytest.raises(auth.Denied) as caught:
        auth.principal_from_claims(auth.verify_token(raw))
    return caught.value


def test_a_valid_token_yields_a_principal_with_the_roles_capabilities() -> None:
    principal = auth.principal_from_claims(auth.verify_token(token()))
    assert principal.username == "alex" and principal.roles == ("operator",)
    assert (
        "commands.request" in principal.capabilities
        and "commands.review" in principal.capabilities
        and "audit.view" not in principal.capabilities
    )


def test_an_expired_token_is_refused() -> None:
    assert denial(token(exp=int(time.time()) - 60)).code == "token_expired"


def test_a_token_for_another_audience_is_refused() -> None:
    assert denial(token(aud="account")).code == "invalid_audience"


def test_a_token_from_another_issuer_is_refused() -> None:
    assert denial(token(iss="http://evil.example/realms/aiops")).code == "invalid_issuer"


def test_a_token_signed_by_another_key_is_refused() -> None:
    forged = jwt.encode(
        {
            "iss": auth.ISSUER,
            "aud": auth.AUDIENCE,
            "sub": "u1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "azp": "aiops-ui",
        },
        OTHER,
        algorithm="RS256",
    )
    assert denial(forged).status == 401


def test_the_none_algorithm_is_refused() -> None:
    unsigned = jwt.encode(
        {"iss": auth.ISSUER, "aud": auth.AUDIENCE, "sub": "u1", "exp": int(time.time()) + 300},
        key=None,
        algorithm="none",
    )
    assert denial(unsigned).code == "invalid_token"


def test_hs256_signed_with_the_public_key_is_refused_key_confusion() -> None:
    """The classic attack: sign with HMAC using the published public key as the secret. PyJWT refuses to *make*
    such a token, so it is built by hand - the verifier must still refuse it."""
    import base64
    import hashlib
    import hmac
    import json

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    public_pem = PUBLIC.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(
        json.dumps(
            {
                "iss": auth.ISSUER,
                "aud": auth.AUDIENCE,
                "sub": "u1",
                "exp": int(time.time()) + 300,
                "azp": "aiops-ui",
                "realm_access": {"roles": ["operator"]},
            }
        ).encode()
    )
    signature = b64(hmac.new(public_pem, header + b"." + payload, hashlib.sha256).digest())
    assert denial((header + b"." + payload + b"." + signature).decode()).code == "invalid_token"


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "aud", "iss"])
def test_a_token_missing_a_required_claim_is_refused(missing: str) -> None:
    assert denial(token(**{missing: None})).status == 401


def test_a_token_issued_to_an_unknown_client_is_refused() -> None:
    assert denial(token(azp="some-other-app")).code == "invalid_client"


def test_a_token_with_no_operating_role_is_forbidden_not_unauthenticated() -> None:
    err = denial(token(realm_access={"roles": ["offline_access", "uma_authorization"]}))
    assert err.status == 403 and err.code == "no_operating_role"


def test_only_known_operating_roles_count_and_unknown_ones_grant_nothing() -> None:
    principal = auth.principal_from_claims(
        auth.verify_token(token(realm_access={"roles": ["operator", "superuser", "admin"]}))
    )
    assert principal.roles == ("operator",)


def test_a_malformed_token_is_refused() -> None:
    assert denial("not.a.jwt").code == "invalid_token"


def test_an_unreachable_identity_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(_token: str):
        raise jwt.PyJWKClientConnectionError("connection refused")

    monkeypatch.setattr(
        auth, "_jwks_client", lambda: types.SimpleNamespace(get_signing_key_from_jwt=down)
    )
    err = denial(token())
    assert err.status == 503 and err.code == "identity_provider_unavailable"


def test_capabilities_add_up_across_roles_and_service_identities_hold_none() -> None:
    assert capabilities_of(("operator", "auditor")) >= {"commands.request", "audit.view"}
    assert capabilities_of(("system:command-executor",)) == frozenset()


def test_every_role_in_the_realm_definition_is_a_role_the_backend_knows() -> None:
    import json
    from pathlib import Path

    realm = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "infra"
            / "platform"
            / "keycloak"
            / "realm"
            / "aiops-realm.json"
        ).read_text(encoding="utf-8")
    )
    assert {r["name"] for r in realm["roles"]["realm"]} == set(auth.KNOWN_ROLES)


def test_the_realm_is_pkce_only_with_no_implicit_or_password_grant_for_the_ui_client() -> None:
    import json
    from pathlib import Path

    realm = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "infra"
            / "platform"
            / "keycloak"
            / "realm"
            / "aiops-realm.json"
        ).read_text(encoding="utf-8")
    )
    ui = next(c for c in realm["clients"] if c["clientId"] == "aiops-ui")
    assert (
        ui["publicClient"]
        and ui["standardFlowEnabled"]
        and not ui["implicitFlowEnabled"]
        and not ui["directAccessGrantsEnabled"]
    )
    assert ui["attributes"]["pkce.code.challenge.method"] == "S256" and "secret" not in ui
    assert not any(
        "*" in u.split("//", 1)[1].split("/", 1)[0] for u in ui["redirectUris"]
    )  # no wildcard hosts
    assert (
        realm["accessTokenLifespan"] <= 300
        and realm["sslRequired"] == "external"
        and realm["bruteForceProtected"]
    )


def test_every_served_route_is_in_the_inventory_or_denied_by_default() -> None:
    assert policy_for("GET", "/api/v1/devices").capability == "map.view"
    assert policy_for("GET", "/api/v1/health").public
    assert policy_for("GET", "/api/v1/nope") is None
    assert inventory()["schema_version"]
