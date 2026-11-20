from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import fail
from app.models import Goal, User, utcnow
from app.security import onboarded_user
from app.services import premium
from app.services.clock import today
from app.services.plans import clean_desc, clean_title

router = APIRouter(tags=["goals"])


class GoalIn(BaseModel):
    title: str = Field(max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    goal_type: str = Field(max_length=10)
    period: str = Field(max_length=10)


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    completed: bool | None = None


def _period_key(goal_type: str, period: str) -> tuple[int, int] | None:
    if goal_type == "yearly" and re.fullmatch(r"\d{4}", period):
        return int(period), 0
    if goal_type == "monthly" and re.fullmatch(r"\d{4}-\d{2}", period):
        y, m = int(period[:4]), int(period[5:])
        if 1 <= m <= 12:
            return y, m
    return None


def _cmp_now(goal_type: str, period: str) -> int:
    """-1 o'tgan davr, 0 joriy, 1 kelajak."""
    key = _period_key(goal_type, period)
    if key is None:
        return 0
    d = today()
    now = (d.year, 0) if goal_type == "yearly" else (d.year, d.month)
    return (key > now) - (key < now)


def serialize(g: Goal) -> dict:
    return {
        "id": g.id,
        "title": g.title,
        "description": g.description,
        "goal_type": g.goal_type,
        "period": g.period,
        "completed": g.completed,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


async def _own(db: AsyncSession, user: User, goal_id: int) -> Goal:
    g = await db.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id))
    if g is None:
        fail(404, "goal_not_found")
    return g


@router.get("/goals")
async def list_goals(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Goal).where(Goal.user_id == user.id).order_by(Goal.created_at))).scalars()
    return [serialize(g) for g in rows]


@router.post("/goals")
async def create_goal(body: GoalIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    gt = body.goal_type.strip().lower()
    if gt not in ("yearly", "monthly"):
        fail(422, "goal_type_invalid")
    period = body.period.strip()
    if _period_key(gt, period) is None or _cmp_now(gt, period) < 0:
        fail(422, "goal_period_invalid")
    lim = await premium.goal_limit(db, user)
    if not lim.allowed:
        fail(402, "goal_limit", limit=lim.limit)
    g = Goal(
        user_id=user.id, title=clean_title(body.title), description=clean_desc(body.description),
        goal_type=gt, period=period,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return serialize(g)


@router.put("/goals/{goal_id}")
async def update_goal(
    goal_id: int, body: GoalPatch, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    g = await _own(db, user, goal_id)
    if body.completed and _cmp_now(g.goal_type, g.period) > 0:
        fail(409, "goal_future")
    if body.title is not None:
        g.title = clean_title(body.title)
    if body.description is not None:
        g.description = clean_desc(body.description)
    if body.completed is not None and body.completed != g.completed:
        g.completed = body.completed
        g.completed_at = utcnow() if body.completed else None
    await db.commit()
    await db.refresh(g)
    return serialize(g)


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    g = await _own(db, user, goal_id)
    await db.delete(g)
    await db.commit()
    return {"ok": True}

