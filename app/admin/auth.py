"""Admin panel xavfsizligi: parol (scrypt), 2FA (TOTP), sessiya cookie, CSRF, rollar, audit.

Sessiya tokeni faqat SHA-256 xesh ko'rinishida saqlanadi. Cookie: HttpOnly,
SameSite=Strict, productionda Secure, faqat /admin yo'li uchun.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import AdminAudit, AdminSession, AdminUser, utcnow
from app.security import as_utc, client_ip, hash_token, seal, unseal

log = logging.getLogger(__name__)

COOKIE = "iz_admin"
SESSION_HOURS = 12
IDLE_MINUTES = 120
MAX_FAILED = 5
LOCK_MINUTES = 15

ROLES = {"support": 1, "admin": 2, "owner": 3}
ROLE_LABELS = {"owner": "Egasi", "admin": "Admin", "support": "Yordamchi"}


class LoginRequired(Exception):
    pass


# ─────────────────────────────────────────────────────────────
#  Parol
# ─────────────────────────────────────────────────────────────
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def password_problem(pw: str, username: str = "") -> str | None:
    if len(pw) < 10:
        return "Parol kamida 10 belgi bo'lsin."
    if len(pw) > 128:
        return "Parol juda uzun."
    if username and username.lower() in pw.lower():
        return "Parolda login bo'lmasin."
    classes = sum(bool(any(f(c) for c in pw)) for f in (str.islower, str.isupper, str.isdigit))
    if classes < 2:
        return "Parolda harf va raqam aralash bo'lsin."
    return None


# ─────────────────────────────────────────────────────────────
#  TOTP (RFC 6238) — Google Authenticator, Authy, 1Password
# ─────────────────────────────────────────────────────────────
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[o : o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def verify_totp(secret: str, code: str) -> bool:
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return False
    now = int(time.time()) // 30
    return any(hmac.compare_digest(_totp(secret, now + d), code) for d in (-1, 0, 1))


def totp_uri(secret: str, username: str) -> str:
    return f"otpauth://totp/IntizomAI%20Admin:{quote(username)}?secret={secret}&issuer=IntizomAI%20Admin"


def admin_totp_secret(admin: AdminUser) -> str | None:
    return unseal(admin.totp_secret) if admin.totp_secret else None


def seal_totp(secret: str) -> str | None:
    return seal(secret)


# ─────────────────────────────────────────────────────────────
#  Sessiya
# ─────────────────────────────────────────────────────────────
async def create_session(db: AsyncSession, admin: AdminUser, request: Request) -> str:
    token = "iza_" + secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        AdminSession(
            admin_id=admin.id,
            token_hash=hash_token(token),
            csrf_token=secrets.token_urlsafe(24),
            ip=client_ip(request)[:64],
            user_agent=(request.headers.get("user-agent") or "")[:200],
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=SESSION_HOURS),
        )
    )
    admin.last_login_at = now
    admin.failed_logins = 0
    admin.locked_until = None
    await db.commit()
    return token


def set_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="strict",
        secure=config.IS_PRODUCTION, path="/admin",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/admin")


class Ctx:
    """So'rov uchun joriy admin va sessiya."""

    def __init__(self, admin: AdminUser, session: AdminSession, request: Request):
        self.admin = admin
        self.session = session
        self.request = request

    @property
    def csrf(self) -> str:
        return self.session.csrf_token

    def can(self, role: str) -> bool:
        return ROLES.get(self.admin.role, 0) >= ROLES[role]

    @property
    def ip(self) -> str:
        return client_ip(self.request)[:64]


async def resolve(request: Request, db: AsyncSession) -> Ctx:
    token = request.cookies.get(COOKIE) or ""
    if not token.startswith("iza_"):
        raise LoginRequired()
    row = (
        await db.execute(
            select(AdminSession, AdminUser)
            .join(AdminUser, AdminUser.id == AdminSession.admin_id)
            .where(AdminSession.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        raise LoginRequired()
    sess, admin = row
    now = utcnow()
    if (
        sess.revoked_at is not None
        or as_utc(sess.expires_at) <= now
        or now - as_utc(sess.last_seen_at) > timedelta(minutes=IDLE_MINUTES)
        or not admin.is_active
    ):
        raise LoginRequired()
    if now - as_utc(sess.last_seen_at) > timedelta(minutes=1):
        sess.last_seen_at = now
        await db.commit()
    return Ctx(admin, sess, request)


class Form:
    """Forma maydonlarini xavfsiz o'qish (matn kesiladi, son/belgi aniq turga o'tkaziladi)."""

    def __init__(self, data):
        self._d = data

    def str(self, key: str, max_len: int = 500) -> str:
        v = self._d.get(key)
        return v.strip()[:max_len] if isinstance(v, str) else ""

    def raw(self, key: str, max_len: int = 5000) -> str:
        v = self._d.get(key)
        return v[:max_len] if isinstance(v, str) else ""

    def int(self, key: str, default: int | None = None) -> int | None:
        s = self.str(key, 20).replace(" ", "").replace("_", "")
        try:
            return int(s) if s else default
        except ValueError:
            return default

    def bool(self, key: str) -> bool:
        return self.str(key, 10).lower() in ("1", "true", "on", "yes")

    def list(self, key: str) -> list[str]:
        return [v.strip()[:64] for v in self._d.getlist(key) if isinstance(v, str) and v.strip()]


async def check_csrf(request: Request, ctx: Ctx) -> Form:
    """POST forma: Origin va CSRF token tekshiruvi."""
    origin = request.headers.get("origin")
    if origin and origin != "null":
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        if origin.split("://", 1)[-1] != host:
            raise HTTPException(403, "So'rov boshqa saytdan keldi (Origin mos emas).")
    form = await request.form()
    if not hmac.compare_digest(str(form.get("csrf") or ""), ctx.csrf):
        raise HTTPException(403, "Xavfsizlik tokeni eskirgan. Sahifani yangilab qayta urinib ko'ring.")
    return Form(form)


async def revoke_admin_sessions(db: AsyncSession, admin_id: int, except_id: int | None = None) -> None:
    rows = (
        await db.execute(
            select(AdminSession).where(AdminSession.admin_id == admin_id, AdminSession.revoked_at.is_(None))
        )
    ).scalars()
    now = utcnow()
    for s in rows:
        if s.id != except_id:
            s.revoked_at = now


# ─────────────────────────────────────────────────────────────
#  Audit
# ─────────────────────────────────────────────────────────────
async def audit(
    db: AsyncSession,
    ctx: Ctx | None,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object = None,
    summary: str | None = None,
    ip: str | None = None,
    commit: bool = True,
) -> None:
    db.add(
        AdminAudit(
            admin_id=ctx.admin.id if ctx else None,
            admin_username=ctx.admin.username if ctx else None,
            action=action[:48],
            target_type=target_type,
            target_id=None if target_id is None else str(target_id)[:64],
            summary=(summary or "")[:500] or None,
            ip=(ip or (ctx.ip if ctx else None)),
        )
    )
    if commit:
        await db.commit()


# ─────────────────────────────────────────────────────────────
#  Birinchi admin (Railway Variables orqali)
# ─────────────────────────────────────────────────────────────
async def bootstrap(db: AsyncSession) -> None:
    """ADMIN_USERNAME + ADMIN_PASSWORD berilgan bo'lsa:
    • adminlar jadvali bo'sh — shu login bilan "Egasi" yaratiladi;
    • ADMIN_RESET_PASSWORD=true — shu loginning paroli tiklanadi (2FA ham o'chadi).
    """
    username = config.ADMIN_USERNAME.strip().lower()
    password = config.ADMIN_PASSWORD
    if not username or not password:
        return
    count = await db.scalar(select(func.count(AdminUser.id))) or 0
    existing = await db.scalar(select(AdminUser).where(AdminUser.username == username))
    if count == 0:
        db.add(AdminUser(username=username, full_name="Egasi", password_hash=hash_password(password), role="owner"))
        db.add(AdminAudit(action="admin.bootstrap", target_type="admin", target_id=username, summary="Birinchi admin yaratildi"))
        await db.commit()
        log.info("Admin panel: birinchi admin yaratildi (%s)", username)
    elif existing is not None and config.ADMIN_RESET_PASSWORD:
        existing.password_hash = hash_password(password)
        existing.totp_secret = None
        existing.is_active = True
        existing.role = "owner"
        existing.failed_logins = 0
        existing.locked_until = None
        await revoke_admin_sessions(db, existing.id)
        db.add(AdminAudit(action="admin.reset_env", target_type="admin", target_id=username, summary="Parol muhit o'zgaruvchisi orqali tiklandi"))
        await db.commit()
        log.warning("Admin panel: %s paroli ADMIN_RESET_PASSWORD orqali tiklandi — o'zgaruvchini o'chiring!", username)
