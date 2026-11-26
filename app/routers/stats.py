from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Checkin, User
from app.security import limit_action, onboarded_user
from app.services import announcements, gamification, premium
from app.services.clock import today
from app.services.users import display_name, photo_url

router = APIRouter(tags=["stats"])

MOODS = {"🔥", "💪", "😐", "😴", "😞"}


class CheckinIn(BaseModel):
    mood: str | None = Field(default=None, max_length=16)
    energy: int | None = Field(default=None, ge=1, le=5)


@router.get("/stats")
async def stats(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return await gamification.snapshot(db, user, premium.is_premium(user))


@router.get("/announcements")
async def list_announcements(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return {"announcements": [announcements.serialize(a) for a in await announcements.for_user(db, user)]}


@router.post("/announcements/{announcement_id}/seen")
async def announcement_seen(
    announcement_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    # Ilova har e'lon uchun bir marta yuboradi; ko'rishlar soni admin panelda.
    limit_action(f"ann-seen:{user.id}", 60, 3600)
    await announcements.mark_seen(db, announcement_id)
    return {"ok": True}


@router.get("/stats/activity")
async def stats_activity(
    days: int = Query(default=98, ge=1, le=gamification.ACTIVITY_MAX_DAYS),
    user: User = Depends(onboarded_user),
    db: AsyncSession = Depends(get_session),
):
    return await gamification.activity(db, user, days)


_LB = {"all": User.total_score, "week": User.weekly_xp, "streak": User.streak}


def _first_name(u: User) -> str:
    parts = display_name(u).split()
    return (parts[0] if parts else "Foydalanuvchi")[:20]


@router.get("/leaderboard")
async def leaderboard(period: str = "all", user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    """Reyting hamma uchun ochiq (bepul ham) — o'zini boshqalar bilan solishtirish uchun.
    Premium foydalanuvchilar `is_premium` belgisi bilan ajralib turadi."""
    col = _LB.get(period, User.total_score)
    attr = col.key
    visible = (User.onboarded.is_(True), User.leaderboard_visible.is_(True))
    rows = (await db.execute(select(User).where(*visible).order_by(col.desc(), User.id).limit(20))).scalars().all()
    top = [
        {
            "rank": i + 1,
            "user_id": u.id,
            "name": _first_name(u),
            "username": u.username,
            "photo_url": photo_url(u),
            "value": int(getattr(u, attr) or 0),
            "is_me": u.id == user.id,
            "is_premium": premium.is_premium(u) and premium.purchases_visible(),
        }
        for i, u in enumerate(rows)
    ]
    my_val = int(getattr(user, attr) or 0)
    higher = await db.scalar(select(func.count(User.id)).where(*visible, col > my_val)) or 0
    total = await db.scalar(select(func.count(User.id)).where(*visible)) or 0
    return {
        "period": period if period in _LB else "all",
        "top": top,
        "me": {
            "rank": int(higher) + 1,
            "user_id": user.id,
            "name": _first_name(user),
            "username": user.username,
            "photo_url": photo_url(user),
            "value": my_val,
            "is_premium": premium.is_premium(user) and premium.purchases_visible(),
            "in_top": any(t["is_me"] for t in top),
            "visible": user.leaderboard_visible,
        },
        "total_users": int(total),
    }


@router.get("/checkin")
async def get_checkin(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    row = await db.scalar(select(Checkin).where(Checkin.user_id == user.id, Checkin.checkin_date == today()))
    if row is None:
        return None
    return {"checkin_date": row.checkin_date.isoformat(), "mood": row.mood, "energy": row.energy}


@router.post("/checkin")
async def save_checkin(body: CheckinIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    mood = body.mood if body.mood in MOODS else None
    values: dict = {"user_id": user.id, "checkin_date": today()}
    updates: dict = {}
    if mood is not None:
        values["mood"] = updates["mood"] = mood
    if body.energy is not None:
        values["energy"] = updates["energy"] = body.energy
    stmt = pg_insert(Checkin).values(**values)
    stmt = (
        stmt.on_conflict_do_update(constraint="uq_checkin_user_day", set_=updates)
        if updates
        else stmt.on_conflict_do_nothing(constraint="uq_checkin_user_day")
    )
    await db.execute(stmt)
    await db.commit()
    row = await db.scalar(select(Checkin).where(Checkin.user_id == user.id, Checkin.checkin_date == today()))
    return {"checkin_date": row.checkin_date.isoformat(), "mood": row.mood, "energy": row.energy}
