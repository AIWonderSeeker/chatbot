"""Verify the ALB-signed `x-amzn-oidc-data` header.

The ALB authenticates the user against the IdP and forwards a JWT signed with
ES256. We check the signer ARN, fetch the public key for `kid` from the
regional ALB key endpoint (cached), and verify signature and expiry.
https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html
"""

from __future__ import annotations

import asyncio
import base64
import json
import re

import httpx
import jwt

from app.auth.user import User

_KID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")


class AuthError(Exception):
    pass


class AlbOidcVerifier:
    def __init__(self, region: str, expected_alb_arn: str) -> None:
        self._region = region
        self._alb_arn = expected_alb_arn
        self._keys: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _header(token: str) -> dict:
        try:
            segment = token.split(".")[0]
            segment += "=" * (-len(segment) % 4)
            return json.loads(base64.urlsafe_b64decode(segment))
        except Exception as exc:  # noqa: BLE001
            raise AuthError("malformed token header") from exc

    async def _public_key(self, kid: str) -> str:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            if kid not in self._keys:
                url = f"https://public-keys.auth.elb.{self._region}.amazonaws.com/{kid}"
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                self._keys[kid] = resp.text
        return self._keys[kid]

    async def verify(self, token: str | None) -> User:
        if not token:
            raise AuthError("missing x-amzn-oidc-data")
        header = self._header(token)
        if header.get("signer") != self._alb_arn:
            raise AuthError("unexpected signer")
        kid = str(header.get("kid", ""))
        if not _KID_RE.fullmatch(kid):
            raise AuthError("invalid kid")
        try:
            key = await self._public_key(kid)
            claims = jwt.decode(token, key, algorithms=["ES256"], options={"require": ["exp", "sub"]})
        except (jwt.PyJWTError, httpx.HTTPError) as exc:
            raise AuthError(type(exc).__name__) from exc
        return User(sub=str(claims["sub"]), email=claims.get("email"))
