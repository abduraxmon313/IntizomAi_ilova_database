"""Sessiya tokenlari, joriy foydalanuvchi va rate limit."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.db import get_session
from app.errors import fail
from app.models import Session, User, utcnow

TOKEN_PREFIX = "iz1_"


# ─────────────────────────────────────────────────────────────
#  Sessiya tokenlari
# ─────────────────────────────────────────────────────────────
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: AsyncSession, user: User, platform: str | None) -> str:
    """Yangi qurilma sessiyasi. Token faqat bir marta (shu yerda) ochiq ko'rinadi."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        Session(
            user_id=user.id,
            token_hash=hash_token(token),
            platform=(platform or "")[:16] or None,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=config.SESSION_TTL_DAYS),
        )
    )
    return token


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        return tok or None
    return None


async def _resolve(request: Request, db: AsyncSession) -> User:
    token = _bearer(request)
    if not token or not token.startswith(TOKEN_PREFIX):
        fail(401, "unauthorized")
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        fail(401, "unauthorized")
    sess, user = row
    now = utcnow()
    if sess.revoked_at is not None or sess.expires_at <= now:
        fail(401, "unauthorized")
    # Sliding expiry: faol foydalanuvchi qayta kirmaydi. Yozuvni soatiga bir
    # martadan ko'p qilmaymiz (har so'rovda UPDATE bo'lmasin).
    if now - sess.last_used_at > timedelta(hours=1):
        sess.last_used_at = now
        sess.expires_at = now + timedelta(days=config.SESSION_TTL_DAYS)
        user.last_active_at = now
        await db.commit()
    request.state.user_id = user.id
    request.state.session_id = sess.id
    return user


async def current_user(request: Request, db: AsyncSession = Depends(get_session)) -> User:
    """Har qanday tasdiqlangan foydalanuvchi (onboarding tugamagan bo'lsa ham)."""
    return await _resolve(request, db)


async def onboarded_user(request: Request, db: AsyncSession = Depends(get_session)) -> User:
    """Profilini to'ldirgan foydalanuvchi. Aks holda 403 (ilova onboarding'ni ko'rsatadi)."""
    user = await _resolve(request, db)
    if not user.onboarded:
        fail(403, "onboarding_required")
    return user


# ─────────────────────────────────────────────────────────────
#  Rate limit (bitta instans uchun xotirada; Railway'da bitta replika)
# ─────────────────────────────────────────────────────────────
_hits: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(key: str, limit: int, window: int = config.RATE_LIMIT_WINDOW) -> int:
    """Limit oshsa — necha soniyadan keyin urinish mumkinligi; aks holda 0."""
    now = time.monotonic()
    start = now - window
    bucket = _hits.setdefault(key, [])
    while bucket and bucket[0] < start:
        bucket.pop(0)
    if len(bucket) >= limit:
        return max(1, int(bucket[0] + window - now) + 1)
    bucket.append(now)
    if len(_hits) > 20000:
        for k in [k for k, v in _hits.items() if not v or v[-1] < start]:
            _hits.pop(k, None)
    return 0


def rate_key(request: Request) -> tuple[str, int]:
    """Tasdiqlangan token bo'lsa — token bo'yicha (CGNAT ortidagi ko'p odam bir-birini
    bloklamasin), aks holda IP bo'yicha. Token DB'da tekshirilmaydi — bu faqat kalit."""
    token = _bearer(request)
    if token and token.startswith(TOKEN_PREFIX) and len(token) < 200:
        return "t:" + hash_token(token)[:24], config.RATE_LIMIT_USER
    return "ip:" + client_ip(request), config.RATE_LIMIT_IP


def limit_action(key: str, limit: int, window: int) -> None:
    """Muayyan amal uchun qo'shimcha limit (masalan username qidirish)."""
    if rate_limited("a:" + key, limit, window):
        fail(429, "too_many")


# ─────────────────────────────────────────────────────────────
#  Ichki imzolar (bot backend bilan aloqa)
# ─────────────────────────────────────────────────────────────
def sign(secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256(secret, METHOD\\nPATH\\nTIMESTAMP\\nsha256(body)) — bot tomonida ham xuddi shu."""
    msg = f"{method.upper()}\n{path}\n{timestamp}\n{hashlib.sha256(body).hexdigest()}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: str, method: str, path: str, timestamp: str, body: bytes,
                     signature: str, max_skew_ms: int = 5 * 60 * 1000) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time() * 1000) - ts) > max_skew_ms:
        return False
    expected = sign(secret, method, path, timestamp, body)
    return hmac.compare_digest(expected, signature)


# ─────────────────────────────────────────────────────────────
#  Simmetrik shifrlash (Apple refresh token uchun) — Fernet (AES-128-CBC + HMAC)
# ─────────────────────────────────────────────────────────────
def _fernet():
    if not config.SECRET_KEY:
        return None
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(("seal:" + config.SECRET_KEY).encode()).digest())
    return Fernet(key)


def seal(plaintext: str) -> str | None:
    """SECRET_KEY bilan shifrlaydi. Kalit sozlanmagan bo'lsa None (saqlanmaydi)."""
    f = _fernet()
    return f.encrypt(plaintext.encode()).decode() if f else None


def unseal(sealed: str | None) -> str | None:
    f = _fernet()
    if not sealed or f is None:
        return None
    try:
        return f.decrypt(sealed.encode()).decode()
    except Exception:
        return None


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
