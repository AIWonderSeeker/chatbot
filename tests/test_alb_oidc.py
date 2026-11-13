"""ALB `x-amzn-oidc-data` verification, with locally generated ES256 keys (no network)."""

import asyncio
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth import alb_oidc
from app.auth.alb_oidc import AlbOidcVerifier, AuthError

ALB_ARN = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/test/abc"
KID = "1d8c2e5a-0000-4000-8000-000000000001"


def _keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public_pem


PRIVATE, PUBLIC_PEM = _keypair()


def _token(
    *,
    key=PRIVATE,
    signer: str = ALB_ARN,
    kid: str = KID,
    claims: dict | None = None,
    algorithm: str = "ES256",
) -> str:
    payload = {"sub": "user-123", "email": "someone@example.com", "exp": int(time.time()) + 300}
    payload.update(claims or {})
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid, "signer": signer})


def _verifier(with_cached_key: bool = True) -> AlbOidcVerifier:
    v = AlbOidcVerifier(region="us-east-1", expected_alb_arn=ALB_ARN)
    if with_cached_key:
        v._keys[KID] = PUBLIC_PEM
    return v


def _verify(v: AlbOidcVerifier, token: str | None):
    return asyncio.run(v.verify(token))


def test_valid_token_returns_user():
    user = _verify(_verifier(), _token())
    assert user.sub == "user-123" and user.email == "someone@example.com"


@pytest.mark.parametrize("token", [None, ""])
def test_missing_token_rejected(token):
    with pytest.raises(AuthError):
        _verify(_verifier(), token)


def test_malformed_header_rejected():
    with pytest.raises(AuthError):
        _verify(_verifier(), "not-a-jwt")


def test_wrong_signer_rejected():
    with pytest.raises(AuthError, match="signer"):
        _verify(
            _verifier(), _token(signer="arn:aws:elasticloadbalancing:us-east-1:999:loadbalancer/app/evil/x")
        )


@pytest.mark.parametrize("kid", ["", "../../etc/passwd", "a" * 129, "kid with spaces"])
def test_invalid_kid_rejected_before_any_fetch(kid):
    with pytest.raises(AuthError, match="kid"):
        _verify(_verifier(with_cached_key=False), _token(kid=kid))


def test_signature_from_other_key_rejected():
    other_private, _ = _keypair()
    with pytest.raises(AuthError):
        _verify(_verifier(), _token(key=other_private))


def test_expired_token_rejected():
    with pytest.raises(AuthError):
        _verify(_verifier(), _token(claims={"exp": int(time.time()) - 10}))


def test_token_without_sub_rejected():
    payload = {"exp": int(time.time()) + 300}
    token = jwt.encode(payload, PRIVATE, algorithm="ES256", headers={"kid": KID, "signer": ALB_ARN})
    with pytest.raises(AuthError):
        _verify(_verifier(), token)


def test_hs256_token_rejected():
    # Algorithm confusion: an HMAC token "signed" with the public key must not verify.
    token = jwt.encode(
        {"sub": "x", "exp": int(time.time()) + 300},
        "shared-secret-that-is-not-the-alb-public-key",
        algorithm="HS256",
        headers={"kid": KID, "signer": ALB_ARN},
    )
    with pytest.raises(AuthError):
        _verify(_verifier(), token)


def _patch_http(monkeypatch, handler) -> list[str]:
    seen: list[str] = []
    real_client = httpx.AsyncClient

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(wrapped)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(alb_oidc.httpx, "AsyncClient", factory)
    return seen


def test_public_key_fetched_from_regional_endpoint_and_cached(monkeypatch):
    seen = _patch_http(monkeypatch, lambda req: httpx.Response(200, text=PUBLIC_PEM))
    v = _verifier(with_cached_key=False)

    async def scenario():
        await v.verify(_token())
        await v.verify(_token())

    asyncio.run(scenario())
    assert seen == [f"https://public-keys.auth.elb.us-east-1.amazonaws.com/{KID}"]


def test_key_fetch_failure_is_auth_error(monkeypatch):
    _patch_http(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(AuthError):
        _verify(_verifier(with_cached_key=False), _token())
