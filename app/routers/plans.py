from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import fail
from app.models import Plan, User, utcnow
from app.security import onboarded_user
from app.services import gamification, plans as plan_svc, referrals
from app.services.clock import parse_date, today

router = APIRouter(tags=["plans"])


class PlanIn(BaseModel):
    title: str = Field(max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    scheduled_time: str | None = Field(default=None, max_length=8)
    plan_date: str | None = Field(default=None, max_length=10)


class PlanPatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    scheduled_time: str | None = Field(default=None, max_length=8)
    status: Literal["pending", "done", "failed"] | None = None


async def _own(db: AsyncSession, user: User, plan_id: int) -> Plan:
    plan = await db.scalar(select(Plan).where(Plan.id == plan_id, Plan.user_id == user.id))
    if plan is None:
        fail(404, "plan_not_found")
    return plan


@router.get("/plans")
async def list_plans(
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(onboarded_user),
    db: AsyncSession = Depends(get_session),
):
    try:
        d1 = parse_date(date_from) or today()
        d2 = parse_date(date_to) or d1
    except ValueError:
        fail(422, "bad_date")
    if d2 < d1:
        d1, d2 = d2, d1
    d2 = min(d2, d1 + timedelta(days=92))
    rows = (
        await db.execute(
            select(Plan)
            .where(Plan.user_id == user.id, Plan.plan_date >= d1, Plan.plan_date <= d2)
            .order_by(Plan.plan_date, Plan.scheduled_time.nulls_last(), Plan.id)
        )
    ).scalars()
    return {"plans": [plan_svc.serialize(p) for p in rows]}


@router.post("/plans")
async def create_plan(body: PlanIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    plan = await plan_svc.create(
        db, user,
        title=body.title, description=body.description,
        scheduled_time=body.scheduled_time, plan_date=body.plan_date,
    )
    return plan_svc.serialize(plan)


@router.put("/plans/{plan_id}")
async def update_plan(
    plan_id: int, body: PlanPatch, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    plan = await _own(db, user, plan_id)
    reason = plan_svc.block_reason(plan.plan_date, plan.scheduled_time)
    if reason == "past":
        fail(409, "plan_past_edit")

    if body.status is not None:
        if body.status == "done" and reason == "future":
            fail(409, "plan_not_due")
        if body.status != plan.status:
            plan.status = body.status
            plan.completed_at = utcnow() if body.status == "done" else None
            await db.commit()
            await gamification.recompute(db, user, completed_now=body.status == "done")
            if body.status == "done":
                await referrals.maybe_activate(db, user)
            await db.refresh(plan)
        return plan_svc.serialize(plan)

    if body.title is not None:
        plan.title = plan_svc.clean_title(body.title)
    if body.description is not None:
        plan.description = plan_svc.clean_desc(body.description)
    if body.scheduled_time is not None:
        plan.scheduled_time = plan_svc.parse_time_or_fail(body.scheduled_time)
    await db.commit()
    await db.refresh(plan)
    return plan_svc.serialize(plan)


@router.delete("/plans/{plan_id}")
async def delete_plan(plan_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    plan = await _own(db, user, plan_id)
    if plan_svc.block_reason(plan.plan_date, plan.scheduled_time) == "past":
        fail(409, "plan_past_edit")
    await db.delete(plan)
    await db.commit()
    # Bajarilmagan reja o'chsa ham bugungi holat (mukammal kun, intizom bali) o'zgaradi.
    await gamification.recompute(db, user)
    return {"ok": True}
