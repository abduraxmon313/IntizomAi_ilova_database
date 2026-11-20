"""Profil: username qoidalari, ism/yosh, avatar va ilovaga yuboriladigan ko'rinish."""
from __future__ import annotations

import base64
import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import AuthIdentity, Avatar, User
from app.services import premium
from app.services.clock import today

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,19}$")
RESERVED = {
    "admin", "administrator", "root", "system", "support", "help", "moderator", "mod",
    "intizom", "intizomai", "intizom_ai", "official", "api", "app", "null", "none",
    "undefined", "me", "you", "user", "users", "settings", "premium", "bot", "test",
}
MAX_AVATAR_BYTES = 700_000


def normalize_username(raw: str | None) -> str:
    return (raw or "").strip().lstrip("@").lower()


def username_problem(u: str) -> str | None:
    """Xato kaliti (i18n) yoki None."""
    if not USERNAME_RE.match(u) or u.endswith("_") or "__" in u:
        return "username_invalid"
    if u in RESERVED or u.startswith("intizom"):
        return "username_reserved"
    return None


async def username_taken(db: AsyncSession, u: str, except_user_id: int | None = None) -> bool:
    q = select(User.id).where(func.lower(User.username) == u)
    if except_user_id is not None:
        q = q.where(User.id != except_user_id)
    return bool(await db.scalar(q))


def clean_name(raw: str | None) -> str | None:
    name = " ".join((raw or "").split())
    if not (2 <= len(name) <= 60):
        return None
    # Boshqaruv belgilari va HTML yo'q.
    if any(ord(c) < 32 for c in name) or "<" in name or ">" in name:
        return None
    return name


def age_to_birth_year(age: int) -> int:
    return today().year - age


def birth_year_to_age(by: int | None) -> int | None:
    return None if by is None else today().year - by


def display_name(u: User) -> str:
    return (u.full_name or "").strip() or (f"@{u.username}" if u.username else "Foydalanuvchi")


def photo_url(u: User) -> str | None:
    """Nisbiy yo'l (ilova bazaviy URL qo'shadi) yoki provayder rasmi."""
    if u.avatar_key:
        return f"/api/v1/avatars/{u.avatar_key}"
    return u.provider_photo_url


def referral_link(u: User) -> str | None:
    if not u.username or not config.PUBLIC_BASE_URL:
        return None
    return f"{config.PUBLIC_BASE_URL}/i/{u.username}"


def public_card(u: User) -> dict:
    """Boshqa foydalanuvchilarga ko'rinadigan minimal ma'lumot."""
    return {"user_id": u.id, "username": u.username, "name": display_name(u), "photo_url": photo_url(u)}


async def profile_json(db: AsyncSession, u: User) -> dict:
    providers = list(
        (await db.execute(select(AuthIdentity.provider).where(AuthIdentity.user_id == u.id))).scalars()
    )
    return {
        "id": u.id,
        "full_name": display_name(u),
        "username": u.username,
        "age": birth_year_to_age(u.birth_year),
        "email": u.email,
        "photo_url": photo_url(u),
        "custom_photo_url": f"/api/v1/avatars/{u.avatar_key}" if u.avatar_key else None,
        "providers": sorted(set(providers)),
        "onboarded": u.onboarded,
        "notifications_enabled": u.notifications_enabled,
        "leaderboard_visible": u.leaderboard_visible,
        "referral_link": referral_link(u),
        "referral_count": u.referral_count or 0,
        "is_premium": premium.is_premium(u),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def decode_image(data_url: str) -> tuple[str, bytes] | None:
    """`data:image/jpeg;base64,...` → (content_type, bytes). Faqat JPEG/PNG, hajm cheklangan."""
    m = re.match(r"^data:(image/(?:jpeg|jpg|png));base64,(.+)$", data_url.strip(), re.S)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception:
        return None
    if not raw or len(raw) > MAX_AVATAR_BYTES:
        return None
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg", raw
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", raw
    return None


async def set_avatar(db: AsyncSession, u: User, content_type: str, data: bytes) -> None:
    old = u.avatar_key
    key = secrets.token_urlsafe(18)
    db.add(Avatar(key=key, user_id=u.id, content_type=content_type, data=data))
    u.avatar_key = key
    if old:
        old_row = await db.get(Avatar, old)
        if old_row:
            await db.delete(old_row)


async def remove_avatar(db: AsyncSession, u: User) -> None:
    if u.avatar_key:
        row = await db.get(Avatar, u.avatar_key)
        if row:
            await db.delete(row)
    u.avatar_key = None
