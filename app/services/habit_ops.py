"""Odat yaratish/tahrirlash (o'zi uchun va guruhda a'zo uchun umumiy)."""
from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import fail
from app.models import Habit, User
from app.services import premium
from app.services.clock import today
from app.services.habits import normalize_weekdays
from app.services.plans import clean_desc, clean_title, parse_time_or_fail


class HabitIn(BaseModel):
    title: str = Field(max_length=300)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=16)
    reminder_time: str | None = Field(default=None, max_length=8)
    clear_reminder: bool | None = None
    frequency: str | None = Field(default=None, max_length=10)
    weekdays: list[int] | None = Field(default=None, max_length=7)
    duration_type: str | None = Field(default=None, max_length=12)
    target_days: int | None = Field(default=None, ge=1, le=3650)


def _icon(raw: str | None) -> str:
    s = (raw or "").strip()
    return s[:16] if s else "✅"


async def create(db: AsyncSession, owner: User, body: HabitIn, created_by: User | None = None) -> Habit:
    lim = await premium.habit_limit(db, owner)
    if not lim.allowed:
        fail(402, "member_habit_limit" if created_by else "habit_limit", limit=lim.limit)
    freq = body.frequency if body.frequency in ("daily", "weekly") else "daily"
    dur = body.duration_type if body.duration_type in ("permanent", "days") else "permanent"
    weekdays = normalize_weekdays(body.weekdays) if freq == "weekly" else None
    if freq == "weekly" and not weekdays:
        freq = "daily"
    max_order = await db.scalar(select(func.coalesce(func.max(Habit.sort_order), 0)).where(Habit.user_id == owner.id))
    h = Habit(
        user_id=owner.id,
        title=clean_title(body.title, 200),
        description=clean_desc(body.description, 500),
        icon=_icon(body.icon),
        reminder_time=parse_time_or_fail(body.reminder_time),
        frequency=freq,
        weekdays=weekdays,
        duration_type=dur,
        target_days=body.target_days if dur == "days" else None,
        start_date=today(),
        sort_order=(max_order or 0) + 1,
        created_by_user_id=created_by.id if created_by else None,
    )
    if dur == "days" and not h.target_days:
        h.duration_type = "permanent"
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return h


def apply_update(h: Habit, body: HabitIn) -> None:
    h.title = clean_title(body.title, 200)
    if body.description is not None:
        h.description = clean_desc(body.description, 500)
    if body.icon is not None:
        h.icon = _icon(body.icon)
    if body.clear_reminder:
        h.reminder_time = None
    elif body.reminder_time is not None:
        h.reminder_time = parse_time_or_fail(body.reminder_time)
    if body.frequency in ("daily", "weekly"):
        h.frequency = body.frequency
    if h.frequency == "weekly":
        if body.weekdays is not None:
            h.weekdays = normalize_weekdays(body.weekdays)
        if not h.weekdays:
            h.frequency, h.weekdays = "daily", None
    else:
        h.weekdays = None
    if body.duration_type in ("permanent", "days"):
        h.duration_type = body.duration_type
    if h.duration_type == "days":
        if body.target_days:
            h.target_days = body.target_days
        if not h.target_days:
            h.duration_type = "permanent"
    if h.duration_type == "permanent":
        h.target_days = None
