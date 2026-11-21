from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import fail
from app.models import Habit, HabitLog, User
from app.security import onboarded_user
from app.services import gamification, habit_ops, habits as rules, referrals
from app.services.clock import parse_date, today
from app.services.habit_ops import HabitIn

router = APIRouter(tags=["habits"])

# Necha kun orqaga belgilash mumkin (unutib qo'yilgan kunni to'g'rilash uchun).
BACKFILL_DAYS = 7


class ToggleIn(BaseModel):
    done: bool | None = None
    date: str | None = Field(default=None, max_length=10)


async def _own(db: AsyncSession, user: User, habit_id: int) -> Habit:
    h = await db.scalar(
        select(Habit).where(Habit.id == habit_id, Habit.user_id == user.id, Habit.archived.is_(False))
    )
    if h is None:
        fail(404, "habit_not_found")
    return h


async def _snap(db: AsyncSession, h: Habit) -> dict:
    return (await rules.snapshots_for(db, [h]))[0]


@router.get("/habits")
async def list_habits(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return await rules.snapshots_for(db, await rules.list_active(db, user.id))


@router.post("/habits")
async def create_habit(body: HabitIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    h = await habit_ops.create(db, user, body)
    return await _snap(db, h)


@router.put("/habits/{habit_id}")
async def update_habit(
    habit_id: int, body: HabitIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    h = await _own(db, user, habit_id)
    habit_ops.apply_update(h, body)
    await db.commit()
    await db.refresh(h)
    return await _snap(db, h)


@router.post("/habits/{habit_id}/toggle")
async def toggle_habit(
    habit_id: int, body: ToggleIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    h = await _own(db, user, habit_id)
    try:
        d = parse_date(body.date) or today()
    except ValueError:
        fail(422, "bad_date")
    if d > today():
        fail(409, "habit_future")
    if d < today() - timedelta(days=BACKFILL_DAYS):
        fail(409, "habit_too_old")

    existing = await db.scalar(select(HabitLog).where(HabitLog.habit_id == h.id, HabitLog.log_date == d))
    on = (existing is None) if body.done is None else body.done
    if on and existing is None and not rules.is_due_on(h, d):
        fail(409, "habit_not_due_day")

    changed = False
    if on and existing is None:
        # Parallel ikki bosishda ham bitta yozuv (UNIQUE habit_id+log_date).
        await db.execute(
            pg_insert(HabitLog)
            .values(habit_id=h.id, user_id=user.id, log_date=d)
            .on_conflict_do_nothing(constraint="uq_habit_log_day")
        )
        changed = True
    elif not on and existing is not None:
        await db.delete(existing)
        changed = True
    if changed:
        await db.commit()
        await gamification.recompute(db, user, completed_now=on and d == today())
        if on:
            await referrals.maybe_activate(db, user)
    return await _snap(db, h)


@router.delete("/habits/{habit_id}")
async def delete_habit(habit_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    # Arxivlanadi: tarix, ball va seriya saqlanadi.
    h = await _own(db, user, habit_id)
    h.archived = True
    h.archived_on = today()
    await db.commit()
    await gamification.recompute(db, user)
    return {"ok": True}
