"""Google va Apple ID tokenlarini tekshirish (imzo, aud, iss, muddat).

Ilova provayderdan olgan `id_token`ni yuboradi; server uni provayderning ochiq
kalitlari (JWKS) bilan tekshiradi. Parol yoki provayder access tokeni serverga
kelmaydi.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

import httpx
import jwt

from app import config

log = logging.getLogger(__name__)

GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
APPLE_JWKS = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


class OAuthError(Exception):
    pass


@dataclass
class VerifiedIdentity:
    provider: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None = None
    picture: str | None = None


class _JwksCache:
    def __init__(self, url: str, ttl: int = 6 * 3600):
        self.url = url
        self.ttl = ttl
        self._keys: dict[str, dict] = {}
        self._fetched = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(self.url)
            r.raise_for_status()
            self._keys = {k["kid"]: k for k in r.json().get("keys", []) if "kid" in k}
            self._fetched = time.monotonic()

    async def get(self, kid: str) -> dict:
        async with self._lock:
            stale = time.monotonic() - self._fetched > self.ttl
            if stale or kid not in self._keys:
                # Kalit almashgan bo'lishi mumkin — bir marta yangilaymiz.
                await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise OAuthError("unknown kid")
        return key


_google_keys = _JwksCache(GOOGLE_JWKS)
_apple_keys = _JwksCache(APPLE_JWKS)


async def _decode(token: str, cache: _JwksCache, audiences: list[str], issuers) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise OAuthError(f"bad header: {e}") from e
    kid = header.get("kid")
    alg = header.get("alg")
    if not kid or alg not in ("RS256", "ES256"):
        raise OAuthError("unsupported token")
    try:
        jwk = await cache.get(kid)
    except httpx.HTTPError as e:
        raise OAuthError(f"jwks fetch failed: {e}") from e
    key = jwt.PyJWK(jwk).key
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=[alg],
            audience=audiences,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=60,
        )
    except jwt.PyJWTError as e:
        raise OAuthError(f"invalid token: {e}") from e
    if claims.get("iss") not in issuers:
        raise OAuthError("bad issuer")
    return claims


def _truthy(v) -> bool:
    return v is True or str(v).lower() == "true"


async def verify_google(id_token: str) -> VerifiedIdentity:
    if not config.GOOGLE_CLIENT_IDS:
        raise OAuthError("google not configured")
    c = await _decode(id_token, _google_keys, config.GOOGLE_CLIENT_IDS, GOOGLE_ISSUERS)
    return VerifiedIdentity(
        provider="google",
        subject=str(c["sub"]),
        email=(c.get("email") or None),
        email_verified=_truthy(c.get("email_verified")),
        name=(c.get("name") or c.get("given_name") or None),
        picture=(c.get("picture") or None),
    )


def apple_nonce_hash(raw_nonce: str) -> str:
    return hashlib.sha256(raw_nonce.encode()).hexdigest()


async def verify_apple(identity_token: str, raw_nonce: str | None) -> VerifiedIdentity:
    c = await _decode(identity_token, _apple_keys, config.APPLE_BUNDLE_IDS, (APPLE_ISSUER,))
    # Replay himoyasi: ilova SHA-256(raw_nonce) ni Apple'ga beradi, bizga raw_nonce'ni.
    if raw_nonce is not None:
        if c.get("nonce") != apple_nonce_hash(raw_nonce):
            raise OAuthError("nonce mismatch")
    email = c.get("email") or None
    return VerifiedIdentity(
        provider="apple",
        subject=str(c["sub"]),
        email=email,
        # Apple pochtani o'zi tasdiqlaydi; private relay manzili baribir haqiqiy hisobga bog'lanmaydi.
        email_verified=_truthy(c.get("email_verified")) and not _truthy(c.get("is_private_email")),
    )


# ─────────────────────────────────────────────────────────────
#  Apple: refresh token olish va bekor qilish (App Store 5.1.1(v))
# ─────────────────────────────────────────────────────────────
def apple_revocation_configured() -> bool:
    return bool(config.APPLE_TEAM_ID and config.APPLE_KEY_ID and config.APPLE_PRIVATE_KEY)


def _apple_client_secret(client_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": config.APPLE_TEAM_ID, "iat": now, "exp": now + 300, "aud": APPLE_ISSUER, "sub": client_id},
        config.APPLE_PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": config.APPLE_KEY_ID},
    )


async def apple_exchange_code(authorization_code: str) -> str | None:
    """authorization_code → refresh_token. Sozlanmagan yoki xato bo'lsa None."""
    if not apple_revocation_configured() or not authorization_code:
        return None
    client_id = config.APPLE_BUNDLE_IDS[0]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://appleid.apple.com/auth/token",
                data={
                    "client_id": client_id,
                    "client_secret": _apple_client_secret(client_id),
                    "code": authorization_code,
                    "grant_type": "authorization_code",
                },
            )
        if r.status_code != 200:
            log.warning("Apple token exchange %s: %s", r.status_code, r.text[:200])
            return None
        return r.json().get("refresh_token")
    except Exception as e:  # tarmoq xatosi kirishni to'xtatmasin
        log.warning("Apple token exchange xato: %s", e)
        return None


async def apple_revoke(refresh_token: str) -> bool:
    if not apple_revocation_configured() or not refresh_token:
        return False
    client_id = config.APPLE_BUNDLE_IDS[0]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://appleid.apple.com/auth/revoke",
                data={
                    "client_id": client_id,
                    "client_secret": _apple_client_secret(client_id),
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                },
            )
        return r.status_code == 200
    except Exception as e:
        log.warning("Apple revoke xato: %s", e)
        return False
