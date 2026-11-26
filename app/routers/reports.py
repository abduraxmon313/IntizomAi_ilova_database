"""Shikoyatlar: foydalanuvchi yoki guruh ustidan (App Store Guideline 1.2).

Shikoyat admin panelda (/admin/reports) ko'rib chiqiladi. Foydalanuvchi ustidan
shikoyat qilinganda uni darhol bloklash ham mumkin (`block: true`).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import fail
from app.models import Group, Report, User, UserBlock
from app.security import limit_action, onboarded_user

router = APIRouter(tags=["reports"])

REASONS = ("spam", "abuse", "inappropriate", "other")


class ReportIn(BaseModel):
    target_type: Literal["user", "group"]
    target_id: int
    reason: str = Field(max_length=16)
    details: str | None = Field(default=None, max_length=500)
    block: bool = False


@router.post("/reports")
async def create_report(body: ReportIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    limit_action(f"report:{user.id}", 10, 3600)
    if body.reason not in REASONS:
        fail(422, "report_reason_unknown")

    report = Report(
        reporter_user_id=user.id,
        target_type=body.target_type,
        reason=body.reason,
        details=(body.details or "").strip()[:500] or None,
    )
    blocked = False
    if body.target_type == "user":
        if body.target_id == user.id:
            fail(422, "report_self")
        if await db.get(User, body.target_id) is None:
            fail(404, "report_target_not_found")
        report.target_user_id = body.target_id
        if body.block:
            exists = await db.scalar(
                select(UserBlock.id).where(
                    UserBlock.blocker_user_id == user.id, UserBlock.blocked_user_id == body.target_id
                )
            )
            if not exists:
                db.add(UserBlock(blocker_user_id=user.id, blocked_user_id=body.target_id))
            blocked = True
    else:
        if await db.get(Group, body.target_id) is None:
            fail(404, "report_target_not_found")
        report.target_group_id = body.target_id

    db.add(report)
    await db.commit()
    return {"ok": True, "id": report.id, "blocked": blocked}
