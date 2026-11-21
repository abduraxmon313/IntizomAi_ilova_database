"""Kirish: iOS — Apple yoki Google, Android — Google.

Ilova provayderdan `id_token` oladi va shu yerga yuboradi. Server tokenni
tekshiradi, foydalanuvchini topadi yoki yaratadi va qurilma sessiya tokenini
qaytaradi (`Authorization: Bearer <token>`).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.db import get_session
from app.errors import fail
from app.models import AuthIdentity, Session, User, utcnow
from app.security import client_ip, create_session, current_user, hash_token, limit_action, seal
from app.services import oauth
from app.services.users import clean_name, profile_json

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleIn(BaseModel):
    id_token: str = Field(min_length=20, max_length=8000)


class AppleIn(BaseModel):
    identity_token: str = Field(min_length=20, max_length=8000)
    raw_nonce: str = Field(min_length=16, max_length=128)
    authorization_code: str | None = Field(default=None, max_length=2000)
    # Apple ismni faqat BIRINCHI kirishda beradi — ilova uni shu yerda yuboradi.
    given_name: str | None = Field(default=None, max_length=60)
    family_name: str | None = Field(default=None, max_length=60)


def _platform(request: Request) -> str | None:
    p = (request.headers.get("x-app-platform") or "").lower()
    return p if p in ("ios", "android") else None


async def _sign_in(
    db: AsyncSession,
    request: Request,
    ident: oauth.VerifiedIdentity,
    *,
    name_hint: str | None = None,
    apple_refresh_token: str | None = None,
) -> dict:
    now = utcnow()
    row = await db.scalar(
        select(AuthIdentity).where(AuthIdentity.provider == ident.provider, AuthIdentity.subject == ident.subject)
    )
    user: User | None = None
    is_new = False
    if row is not None:
        user = await db.get(User, row.user_id)
    elif ident.email and ident.email_verified:
        # Bir xil TASDIQLANGAN pochta — bitta odam (masalan iPhone'da Apple, Android'da Google).
        linked = await db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.email == ident.email.lower(), AuthIdentity.email_verified.is_(True)
            )
        )
        if linked is not None:
            user = await db.get(User, linked.user_id)

    if user is not None and user.banned_at is not None:
        fail(403, "account_banned")

    if user is None:
        user = User(
            full_name=clean_name(name_hint or ident.name),
            email=ident.email.lower() if ident.email else None,
            provider_photo_url=ident.picture,
            created_at=now,
        )
        db.add(user)
        await db.flush()
        is_new = True

    if row is None:
        row = AuthIdentity(
            user_id=user.id,
            provider=ident.provider,
            subject=ident.subject,
            email=ident.email.lower() if ident.email else None,
            email_verified=ident.email_verified,
        )
        db.add(row)
    row.last_login_at = now
    if ident.email and not row.email:
        row.email = ident.email.lower()
    if apple_refresh_token:
        row.apple_refresh_token = seal(apple_refresh_token) or row.apple_refresh_token
    if ident.picture and not user.avatar_key:
        user.provider_photo_url = ident.picture
    if not user.full_name and (name_hint or ident.name):
        user.full_name = clean_name(name_hint or ident.name)
    if not user.email and ident.email:
        user.email = ident.email.lower()
    user.last_active_at = now

    token = await create_session(db, user, _platform(request))
    try:
        await db.commit()
    except IntegrityError:
        # Bir vaqtda ikki marta bosilgan — qayta urinish xavfsiz.
        await db.rollback()
        fail(409, "auth_invalid_token")
    await db.refresh(user)
    log.info("Kirish: user=%s provider=%s new=%s", user.id, ident.provider, is_new)
    return {
        "token": token,
        "is_new": is_new,
        "needs_onboarding": not user.onboarded,
        "profile": await profile_json(db, user),
    }


@router.post("/google")
async def google(body: GoogleIn, request: Request, db: AsyncSession = Depends(get_session)):
    limit_action("auth:" + client_ip(request), config.RATE_LIMIT_AUTH, 60)
    if not config.GOOGLE_CLIENT_IDS:
        fail(503, "auth_not_configured")
    try:
        ident = await oauth.verify_google(body.id_token)
    except oauth.OAuthError as e:
        log.info("Google token rad etildi: %s", e)
        fail(401, "auth_invalid_token")
    if ident.email and not ident.email_verified:
        fail(401, "auth_email_unverified")
    return await _sign_in(db, request, ident)


@router.post("/apple")
async def apple(body: AppleIn, request: Request, db: AsyncSession = Depends(get_session)):
    limit_action("auth:" + client_ip(request), config.RATE_LIMIT_AUTH, 60)
    try:
        ident = await oauth.verify_apple(body.identity_token, body.raw_nonce)
    except oauth.OAuthError as e:
        log.info("Apple token rad etildi: %s", e)
        fail(401, "auth_invalid_token")
    name = " ".join(p for p in (body.given_name, body.family_name) if p and p.strip()) or None
    refresh = await oauth.apple_exchange_code(body.authorization_code) if body.authorization_code else None
    return await _sign_in(db, request, ident, name_hint=name, apple_refresh_token=refresh)


@router.post("/logout")
async def logout(request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    token = (request.headers.get("authorization") or "")[7:].strip()
    await db.execute(
        update(Session).where(Session.token_hash == hash_token(token)).values(revoked_at=utcnow())
    )
    await db.commit()
    return {"ok": True}
