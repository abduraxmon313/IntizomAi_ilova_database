from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.errors import fail
from app.models import Plan, User
from app.services import premium
from app.services.clock import normalize_hm, now_local, parse_date, today


def serialize(p: Plan) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "scheduled_time": p.scheduled_time,
        "plan_date": p.plan_date.isoformat(),
        "status": p.status,
        "score_value": p.score_value,
        "created_by_user_id": p.created_by_user_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def block_reason(plan_date: date, scheduled_time: str | None) -> str | None:
    """None — belgilash mumkin; 'past' — o'tgan kun; 'future' — vaqti kelmagan."""
    now = now_local()
    d0 = now.date()
    if plan_date < d0:
        return "past"
    if plan_date > d0:
        return "future"
    if scheduled_time and now.strftime("%H:%M") < scheduled_time:
        return "future"
    return None


def clean_title(raw: str | None, max_len: int = 300) -> str:
    title = " ".join((raw or "").split())[:max_len]
    if not title:
        fail(422, "title_required")
    return title


def clean_desc(raw: str | None, max_len: int = 1000) -> str | None:
    if raw is None:
        return None
    s = raw.strip()[:max_len]
    return s or None


def parse_time_or_fail(raw: str | None) -> str | None:
    try:
        return normalize_hm(raw)
    except ValueError:
        fail(422, "bad_time")


def parse_plan_date(raw: str | None) -> date:
    try:
        d = parse_date(raw) or today()
    except ValueError:
        fail(422, "bad_date")
    if d < today():
        fail(409, "plan_past_create")
    if d > today() + timedelta(days=366):
        fail(422, "plan_too_far")
    return d


async def create(
    db: AsyncSession,
    owner: User,
    *,
    title: str,
    description: str | None,
    scheduled_time: str | None,
    plan_date: str | None,
    created_by: User | None = None,
) -> Plan:
    d = parse_plan_date(plan_date)
    lim = await premium.plan_limit(db, owner, d)
    if not lim.allowed:
        key = "member_plan_limit" if created_by is not None else "plan_limit"
        fail(402, key, limit=lim.limit)
    plan = Plan(
        user_id=owner.id,
        title=clean_title(title),
        description=clean_desc(description),
        scheduled_time=parse_time_or_fail(scheduled_time),
        plan_date=d,
        status="pending",
        score_value=config.PLAN_DONE_SCORE,
        created_by_user_id=created_by.id if created_by else None,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan
