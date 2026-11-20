"""Odatlar: takrorlanish/davomiylik qoidalari, streak va ilovaga yuboriladigan ko'rinish."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Habit, HabitLog
from app.services.clock import today

ALL_DAYS = frozenset(range(7))


def parse_weekdays(raw: str | None) -> set[int]:
    out = {int(p) for p in (raw or "").split(",") if p.strip().isdigit() and 0 <= int(p) <= 6}
    return out or set(ALL_DAYS)


def normalize_weekdays(values) -> str | None:
    if values is None:
        return None
    vals = sorted({int(v) for v in values if 0 <= int(v) <= 6})
    return ",".join(str(v) for v in vals) if vals else None


def start_date(h: Habit) -> date:
    return h.start_date or (h.created_at.date() if h.created_at else today())


def end_date(h: Habit) -> date | None:
    if h.duration_type != "days" or not h.target_days or h.target_days <= 0:
        return None
    return start_date(h) + timedelta(days=h.target_days - 1)


def is_finished(h: Habit, d: date | None = None) -> bool:
    end = end_date(h)
    return bool(end and (d or today()) > end)


def is_due_on(h: Habit, d: date) -> bool:
    if d < start_date(h):
        return False
    end = end_date(h)
    if end and d > end:
        return False
    if h.frequency == "weekly":
        return d.weekday() in parse_weekdays(h.weekdays)
    return True


def current_streak(h: Habit, dates: set[date], d0: date) -> int:
    """Faqat rejalashtirilgan kunlar bo'yicha. Bugun hali belgilanmagan bo'lsa — uzilmaydi."""
    if not dates:
        return 0
    streak = 0
    d = d0
    first = start_date(h)
    for _ in range(800):
        if is_due_on(h, d):
            if d in dates:
                streak += 1
            elif d != d0:
                break
        if d <= first:
            break
        d -= timedelta(days=1)
    return streak


async def log_dates(db: AsyncSession, habit_ids: list[int], since: date) -> dict[int, set[date]]:
    out: dict[int, set[date]] = defaultdict(set)
    if not habit_ids:
        return out
    rows = await db.execute(
        select(HabitLog.habit_id, HabitLog.log_date).where(
            HabitLog.habit_id.in_(habit_ids), HabitLog.log_date >= since
        )
    )
    for hid, d in rows.all():
        out[hid].add(d)
    return out


def snapshot(h: Habit, dates: set[date]) -> dict:
    d0 = today()
    end = end_date(h)
    finished = bool(end and d0 > end)
    days_left = None
    if end:
        days_left = 0 if finished else max(0, (end - d0).days + 1)
    return {
        "id": h.id,
        "title": h.title,
        "description": h.description,
        "icon": h.icon or "✅",
        "reminder_time": h.reminder_time,
        "frequency": h.frequency or "daily",
        "weekdays": sorted(parse_weekdays(h.weekdays)) if h.frequency == "weekly" else [],
        "duration_type": h.duration_type or "permanent",
        "target_days": h.target_days,
        "start_date": start_date(h).isoformat(),
        "end_date": end.isoformat() if end else None,
        "days_left": days_left,
        "finished": finished,
        "due_today": (not finished) and is_due_on(h, d0),
        "done_today": d0 in dates,
        "streak": current_streak(h, dates, d0),
        "total_done": len(dates),
        "log_dates": sorted(d.isoformat() for d in dates if d >= d0 - timedelta(days=45)),
        "created_by_user_id": h.created_by_user_id,
    }


async def list_active(db: AsyncSession, user_id: int) -> list[Habit]:
    return list(
        (
            await db.execute(
                select(Habit)
                .where(Habit.user_id == user_id, Habit.archived.is_(False))
                .order_by(Habit.sort_order, Habit.id)
            )
        ).scalars()
    )


async def snapshots_for(db: AsyncSession, habits: list[Habit]) -> list[dict]:
    """N+1'siz: barcha odatlar loglari bitta so'rovda (streak uchun ~2 yil) + jami soni."""
    ids = [h.id for h in habits]
    since = today() - timedelta(days=800)
    logs = await log_dates(db, ids, since)
    totals: dict[int, int] = {}
    if ids:
        rows = await db.execute(
            select(HabitLog.habit_id, func.count(HabitLog.id))
            .where(HabitLog.habit_id.in_(ids))
            .group_by(HabitLog.habit_id)
        )
        totals = {hid: int(n) for hid, n in rows.all()}
    out = []
    for h in habits:
        snap = snapshot(h, logs.get(h.id, set()))
        snap["total_done"] = totals.get(h.id, snap["total_done"])
        out.append(snap)
    return out
