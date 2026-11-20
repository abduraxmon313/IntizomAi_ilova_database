from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.db import get_session
from app.errors import fail
from app.i18n import t
from app.models import Avatar, User, utcnow
from app.security import current_user, limit_action, onboarded_user
from app.services import account, users
from app.services.users import (
    age_to_birth_year,
    clean_name,
    decode_image,
    normalize_username,
    profile_json,
    public_card,
    username_problem,
    username_taken,
)

router = APIRouter(tags=["profile"])


class OnboardingIn(BaseModel):
    full_name: str = Field(max_length=80)
    age: int
    username: str = Field(max_length=32)
    referrer_username: str | None = Field(default=None, max_length=32)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=80)
    username: str | None = Field(default=None, max_length=32)
    age: int | None = None
    notifications_enabled: bool | None = None
    leaderboard_visible: bool | None = None
    custom_photo_url: str | None = Field(default=None, max_length=1_100_000)
    remove_custom_photo: bool | None = None


def _check_age(age: int) -> None:
    if not (config.MIN_AGE <= age <= config.MAX_AGE):
        fail(422, "age_invalid", min=config.MIN_AGE, max=config.MAX_AGE)


async def _check_username(db: AsyncSession, raw: str, user: User) -> str:
    u = normalize_username(raw)
    problem = username_problem(u)
    if problem:
        fail(422, problem)
    if await username_taken(db, u, except_user_id=user.id):
        fail(409, "username_taken")
    return u


@router.get("/profile")
async def get_profile(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    return await profile_json(db, user)


@router.post("/profile/onboarding")
async def onboarding(body: OnboardingIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    name = clean_name(body.full_name)
    if not name:
        fail(422, "name_invalid")
    _check_age(body.age)
    username = await _check_username(db, body.username, user)

    referrer = None
    if body.referrer_username and not user.onboarded:
        ref_u = normalize_username(body.referrer_username)
        if ref_u:
            referrer = await db.scalar(select(User).where(User.username == ref_u, User.onboarded.is_(True)))
            if referrer is None or referrer.id == user.id:
                fail(404, "referrer_not_found")

    user.full_name = name
    user.birth_year = age_to_birth_year(body.age)
    if user.username != username:
        user.username = username
        if user.onboarded:
            user.username_changed_at = utcnow()
    if referrer is not None and user.referred_by_id is None:
        user.referred_by_id = referrer.id
    if not user.onboarded:
        user.onboarded = True
        user.onboarded_at = utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        fail(409, "username_taken")
    await db.refresh(user)
    return await profile_json(db, user)


@router.put("/profile")
async def update_profile(body: ProfileUpdate, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    if body.full_name is not None:
        name = clean_name(body.full_name)
        if not name:
            fail(422, "name_invalid")
        user.full_name = name
    if body.age is not None:
        _check_age(body.age)
        user.birth_year = age_to_birth_year(body.age)
    if body.username is not None:
        u = await _check_username(db, body.username, user)
        if u != user.username:
            last = user.username_changed_at
            if user.username and last and utcnow() - last < timedelta(days=config.USERNAME_CHANGE_COOLDOWN_DAYS):
                fail(409, "username_cooldown", days=config.USERNAME_CHANGE_COOLDOWN_DAYS)
            user.username = u
            user.username_changed_at = utcnow()
    if body.notifications_enabled is not None:
        user.notifications_enabled = body.notifications_enabled
    if body.leaderboard_visible is not None:
        user.leaderboard_visible = body.leaderboard_visible
    if body.remove_custom_photo:
        await users.remove_avatar(db, user)
    elif body.custom_photo_url:
        img = decode_image(body.custom_photo_url)
        if img is None:
            fail(422, "photo_invalid")
        await users.set_avatar(db, user, *img)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        fail(409, "username_taken")
    await db.refresh(user)
    return await profile_json(db, user)


@router.delete("/profile")
async def delete_profile(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    await account.delete_account(db, user)
    return {"ok": True}


@router.get("/users/username-available")
async def username_available(
    username: str = Query(max_length=32),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    limit_action(f"uname:{user.id}", 60, 60)
    u = normalize_username(username)
    problem = username_problem(u)
    if problem:
        return {"username": u, "available": False, "reason": t(problem)}
    taken = await username_taken(db, u, except_user_id=user.id)
    return {"username": u, "available": not taken, "reason": t("username_taken") if taken else None}


@router.get("/users/lookup")
async def lookup_user(
    username: str = Query(max_length=32),
    me: User = Depends(onboarded_user),
    db: AsyncSession = Depends(get_session),
):
    """Aniq username bo'yicha qidiruv (ro'yxat emas) — foydalanuvchilarni yig'ib olishga qarshi."""
    limit_action(f"lookup:{me.id}", 30, 300)
    u = normalize_username(username)
    target = await db.scalar(select(User).where(User.username == u, User.onboarded.is_(True)))
    if target is None:
        fail(404, "user_not_found")
    return public_card(target)


@router.get("/avatars/{key}")
async def avatar(key: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Profil rasmi. Kalit tasodifiy va har yuklashda yangi — shuning uchun uzoq keshlanadi."""
    row = await db.get(Avatar, key[:40])
    if row is None:
        return Response(status_code=404)
    etag = f'"{key}"'
    headers = {"Cache-Control": "public, max-age=31536000, immutable", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=row.data, media_type=row.content_type, headers=headers)
