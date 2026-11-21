from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import runtime_settings as rs
from app.db import get_session
from app.errors import fail
from app.models import User
from app.security import limit_action, onboarded_user
from app.services import groups as svc, habit_ops, habits as habit_rules, plans as plan_svc, premium
from app.services.clock import parse_date
from app.services.habit_ops import HabitIn
from app.services.users import normalize_username

router = APIRouter(tags=["groups"])


class GroupIn(BaseModel):
    name: str = Field(max_length=120)
    description: str | None = Field(default=None, max_length=600)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=600)


class InviteIn(BaseModel):
    username: str = Field(max_length=32)


class DeclineIn(BaseModel):
    block: bool = False


class TransferIn(BaseModel):
    user_id: int


class PermissionIn(BaseModel):
    can_manage: bool


class ActiveIn(BaseModel):
    is_active: bool


class MemberPlanIn(BaseModel):
    title: str = Field(max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    scheduled_time: str | None = Field(default=None, max_length=8)
    plan_date: str | None = Field(default=None, max_length=10)


# ── Guruhlar ────────────────────────────────────────────────
@router.get("/groups")
async def list_groups(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return {
        "groups": await svc.list_my_groups(db, user),
        "invites_count": await svc.pending_invites_count(db, user),
        "can_create": premium.is_premium(user) or not rs.get("GROUP_CREATE_PREMIUM_ONLY"),
    }


@router.post("/groups")
async def create_group(body: GroupIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    if rs.get("GROUP_CREATE_PREMIUM_ONLY") and not premium.is_premium(user):
        fail(402, "group_create_premium")
    g = await svc.create_group(db, user, body.name, body.description)
    return {"id": g.id, "name": g.name, "description": g.description, "member_count": 1, "is_owner": True}


@router.get("/groups/{group_id}")
async def group_detail(group_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return await svc.group_detail(db, user, group_id)


@router.patch("/groups/{group_id}")
async def update_group(
    group_id: int, body: GroupPatch, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    g = await svc.require_owner(db, group_id, user)
    if body.name is not None:
        g.name = svc.clean_group_name(body.name)
    if body.description is not None or "description" in body.model_fields_set:
        g.description = svc.clean_group_desc(body.description)
    await db.commit()
    return {"id": g.id, "name": g.name, "description": g.description}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    g = await svc.require_owner(db, group_id, user)
    await db.delete(g)
    await db.commit()
    return {"ok": True}


@router.post("/groups/{group_id}/leave")
async def leave_group(group_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    await svc.leave(db, user, group_id)
    return {"ok": True}


@router.post("/groups/{group_id}/transfer")
async def transfer_group(
    group_id: int, body: TransferIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    await svc.transfer(db, user, group_id, body.user_id)
    return {"ok": True}


# ── Takliflar ───────────────────────────────────────────────
@router.post("/groups/{group_id}/invites")
async def send_invite(
    group_id: int, body: InviteIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    limit_action(f"invite:{user.id}", 20, 60)
    username = normalize_username(body.username)
    if not username:
        fail(404, "user_not_found")
    return await svc.invite(db, user, group_id, username)


@router.delete("/groups/{group_id}/invites/{invite_id}")
async def cancel_invite(
    group_id: int, invite_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    await svc.cancel_invite(db, user, group_id, invite_id)
    return {"ok": True}


@router.get("/invites")
async def my_invites(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return {"invites": await svc.my_invites(db, user)}


@router.post("/invites/{invite_id}/accept")
async def accept_invite(invite_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    if rs.get("GROUP_JOIN_PREMIUM_ONLY") and not premium.is_premium(user):
        fail(402, "group_join_premium")
    return await svc.accept_invite(db, user, invite_id)


@router.post("/invites/{invite_id}/decline")
async def decline_invite(
    invite_id: int, body: DeclineIn | None = None,
    user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session),
):
    await svc.decline_invite(db, user, invite_id, block=bool(body and body.block))
    return {"ok": True}


@router.get("/blocks")
async def list_blocks(user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return {"users": await svc.list_blocks(db, user)}


@router.delete("/blocks/{user_id}")
async def unblock(user_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    await svc.unblock(db, user, user_id)
    return {"ok": True}


# ── A'zolar ─────────────────────────────────────────────────
@router.get("/groups/{group_id}/members/{user_id}")
async def member_view(
    group_id: int, user_id: int, date: str | None = None,
    user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session),
):
    try:
        d = parse_date(date)
    except ValueError:
        fail(422, "bad_date")
    return await svc.member_view(db, user, group_id, user_id, d)


@router.delete("/groups/{group_id}/members/{user_id}")
async def remove_member(
    group_id: int, user_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    await svc.remove_member(db, user, group_id, user_id)
    return {"ok": True}


@router.put("/groups/{group_id}/members/{user_id}/active")
async def set_active(
    group_id: int, user_id: int, body: ActiveIn,
    user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session),
):
    return await svc.set_active(db, user, group_id, user_id, body.is_active)


@router.get("/groups/{group_id}/permissions")
async def permissions(group_id: int, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    return await svc.list_permissions(db, user, group_id)


@router.put("/groups/{group_id}/permissions/{grantee_id}")
async def set_permission(
    group_id: int, grantee_id: int, body: PermissionIn,
    user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session),
):
    await svc.set_permission(db, user, group_id, grantee_id, body.can_manage)
    return {"ok": True}


@router.post("/groups/{group_id}/members/{user_id}/plans")
async def create_plan_for_member(
    group_id: int, user_id: int, body: MemberPlanIn,
    user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session),
):
    target = await svc.manage_target(db, user, group_id, user_id)
    plan = await plan_svc.create(
        db, target,
        title=body.title, description=body.description,
        scheduled_time=body.scheduled_time, plan_date=body.plan_date,
        created_by=user if target.id != user.id else None,
    )
    return plan_svc.serialize(plan)


@router.post("/groups/{group_id}/members/{user_id}/habits")
async def create_habit_for_member(
    group_id: int, user_id: int, body: HabitIn,
    user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session),
):
    target = await svc.manage_target(db, user, group_id, user_id)
    h = await habit_ops.create(db, target, body, created_by=user if target.id != user.id else None)
    return (await habit_rules.snapshots_for(db, [h]))[0]
