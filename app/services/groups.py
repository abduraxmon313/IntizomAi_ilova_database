"""Guruhlar — faqat ilova ichida. Taklif username orqali yuboriladi va qabul qilinadi."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.services import runtime_settings as rs
from app.errors import fail
from app.models import (
    Group,
    GroupInvite,
    GroupMember,
    GroupPermission,
    Habit,
    HabitLog,
    Plan,
    User,
    UserBlock,
    utcnow,
)
from app.services import habits as habit_rules
from app.services.clock import today
from app.services.users import display_name, photo_url, public_card


# ─────────────────────────────────────────────────────────────
#  Yordamchilar
# ─────────────────────────────────────────────────────────────
async def get_group(db: AsyncSession, group_id: int) -> Group:
    g = await db.get(Group, group_id)
    if g is None:
        fail(404, "group_not_found")
    return g


async def membership(db: AsyncSession, group_id: int, user_id: int) -> GroupMember | None:
    return await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )


async def require_member(db: AsyncSession, group_id: int, user_id: int) -> GroupMember:
    m = await membership(db, group_id, user_id)
    if m is None:
        fail(403, "group_not_member")
    return m


async def require_owner(db: AsyncSession, group_id: int, user: User) -> Group:
    g = await get_group(db, group_id)
    if g.owner_user_id != user.id:
        fail(403, "group_owner_only")
    return g


def clean_group_name(raw: str | None) -> str:
    name = " ".join((raw or "").split())[:80]
    if not name:
        fail(422, "group_name_required")
    return name


def clean_group_desc(raw: str | None) -> str | None:
    s = (raw or "").strip()[:300]
    return s or None


async def member_count(db: AsyncSession, group_id: int) -> int:
    return int(await db.scalar(select(func.count(GroupMember.id)).where(GroupMember.group_id == group_id)) or 0)


async def _groups_of(db: AsyncSession, user_id: int) -> int:
    return int(await db.scalar(select(func.count(GroupMember.id)).where(GroupMember.user_id == user_id)) or 0)


# ─────────────────────────────────────────────────────────────
#  Ro'yxat / yaratish
# ─────────────────────────────────────────────────────────────
async def list_my_groups(db: AsyncSession, user: User) -> list[dict]:
    counts = (
        select(GroupMember.group_id, func.count(GroupMember.id).label("n"))
        .group_by(GroupMember.group_id)
        .subquery()
    )
    rows = await db.execute(
        select(Group, GroupMember.role, counts.c.n)
        .join(GroupMember, and_(GroupMember.group_id == Group.id, GroupMember.user_id == user.id))
        .join(counts, counts.c.group_id == Group.id)
        .order_by(Group.created_at.desc())
    )
    return [
        {
            "id": g.id,
            "name": g.name,
            "description": g.description,
            "member_count": int(n),
            "is_owner": g.owner_user_id == user.id,
            "role": role,
        }
        for g, role, n in rows.all()
    ]


async def create_group(db: AsyncSession, owner: User, name: str, description: str | None) -> Group:
    own = int(await db.scalar(select(func.count(Group.id)).where(Group.owner_user_id == owner.id)) or 0)
    if own >= rs.get("MAX_GROUPS_PER_OWNER"):
        fail(409, "group_owner_limit", limit=rs.get("MAX_GROUPS_PER_OWNER"))
    if await _groups_of(db, owner.id) >= rs.get("MAX_GROUPS_PER_USER"):
        fail(409, "group_user_limit", limit=rs.get("MAX_GROUPS_PER_USER"))
    g = Group(name=clean_group_name(name), description=clean_group_desc(description), owner_user_id=owner.id)
    db.add(g)
    await db.flush()
    db.add(GroupMember(group_id=g.id, user_id=owner.id, role="owner"))
    await db.commit()
    await db.refresh(g)
    return g


# ─────────────────────────────────────────────────────────────
#  Guruh sahifasi
# ─────────────────────────────────────────────────────────────
def _empty_summary() -> dict:
    return {"plans_total": 0, "plans_done": 0, "habits_total": 0, "habits_done_today": 0}


async def _today_summaries(db: AsyncSession, user_ids: list[int]) -> dict[int, dict]:
    out = {uid: _empty_summary() for uid in user_ids}
    if not user_ids:
        return out
    d0 = today()
    done = func.sum(case((Plan.status == "done", 1), else_=0))
    for uid, total, dn in (
        await db.execute(
            select(Plan.user_id, func.count(Plan.id), done)
            .where(Plan.user_id.in_(user_ids), Plan.plan_date == d0)
            .group_by(Plan.user_id)
        )
    ).all():
        out[uid]["plans_total"] = int(total or 0)
        out[uid]["plans_done"] = int(dn or 0)
    # Odatlar: faqat BUGUN rejalashtirilganlari (haftalik odatning dam kuni hisoblanmaydi).
    habits = (
        await db.execute(select(Habit).where(Habit.user_id.in_(user_ids), Habit.archived.is_(False)))
    ).scalars().all()
    due = [h for h in habits if habit_rules.is_due_on(h, d0)]
    for h in due:
        out[h.user_id]["habits_total"] += 1
    if due:
        for uid, n in (
            await db.execute(
                select(HabitLog.user_id, func.count(HabitLog.id))
                .where(HabitLog.habit_id.in_([h.id for h in due]), HabitLog.log_date == d0)
                .group_by(HabitLog.user_id)
            )
        ).all():
            out[uid]["habits_done_today"] = int(n or 0)
    return out


async def group_detail(db: AsyncSession, user: User, group_id: int) -> dict:
    g = await get_group(db, group_id)
    await require_member(db, group_id, user.id)
    is_owner = g.owner_user_id == user.id
    rows = (
        await db.execute(
            select(GroupMember, User)
            .join(User, User.id == GroupMember.user_id)
            .where(GroupMember.group_id == group_id)
            .order_by(GroupMember.joined_at)
        )
    ).all()
    if not is_owner:
        # Pauzadagi a'zolar egadan boshqalarga umuman ko'rinmaydi.
        rows = [(m, u) for m, u in rows if m.is_active or u.id == user.id]
    ids = [u.id for _, u in rows]
    can_manage_for = set(
        (
            await db.execute(
                select(GroupPermission.grantor_user_id).where(
                    GroupPermission.group_id == group_id,
                    GroupPermission.grantee_user_id == user.id,
                    GroupPermission.can_manage.is_(True),
                )
            )
        ).scalars()
    )
    summaries = await _today_summaries(db, ids)
    members = []
    for m, u in rows:
        visible = u.id == user.id or m.is_active or is_owner
        members.append(
            {
                "user_id": u.id,
                "username": u.username,
                "name": display_name(u),
                "photo_url": photo_url(u),
                "role": m.role,
                "is_me": u.id == user.id,
                "streak": int(u.streak or 0),
                "visible": visible,
                "is_active": m.is_active,
                "can_i_manage": u.id != user.id and u.id in can_manage_for,
                "summary": summaries.get(u.id, _empty_summary()) if visible else _empty_summary(),
            }
        )
    pending = []
    if is_owner:
        inv = (
            await db.execute(
                select(GroupInvite, User)
                .join(User, User.id == GroupInvite.invitee_user_id)
                .where(
                    GroupInvite.group_id == group_id,
                    GroupInvite.status == "pending",
                    GroupInvite.expires_at > utcnow(),
                )
                .order_by(GroupInvite.created_at.desc())
            )
        ).all()
        pending = [
            {"id": i.id, "user": public_card(u), "created_at": i.created_at.isoformat()} for i, u in inv
        ]
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "is_owner": is_owner,
        "owner_user_id": g.owner_user_id,
        "members": members,
        "pending_invites": pending,
    }


async def member_view(db: AsyncSession, user: User, group_id: int, target_id: int, on: date | None) -> dict:
    g = await get_group(db, group_id)
    await require_member(db, group_id, user.id)
    tm = await membership(db, group_id, target_id)
    target = await db.get(User, target_id)
    if tm is None or target is None:
        fail(404, "group_member_not_found")
    is_owner = g.owner_user_id == user.id
    visible = target.id == user.id or tm.is_active or is_owner
    real_today = today()
    d = on or real_today
    d = max(real_today - timedelta(days=60), min(real_today + timedelta(days=30), d))
    can_manage = target.id != user.id and bool(
        await db.scalar(
            select(GroupPermission.can_manage).where(
                GroupPermission.group_id == group_id,
                GroupPermission.grantor_user_id == target.id,
                GroupPermission.grantee_user_id == user.id,
            )
        )
    )
    base = {
        "group_id": group_id,
        "member": {
            "user_id": target.id,
            "username": target.username,
            "name": display_name(target),
            "photo_url": photo_url(target),
            "streak": int(target.streak or 0),
            "total_score": int(target.total_score or 0),
            "is_me": target.id == user.id,
            "is_active": tm.is_active,
        },
        "date": d.isoformat(),
        "is_today": d == real_today,
        "is_past": d < real_today,
        "is_future": d > real_today,
    }
    if not visible:
        return {**base, "plans": [], "habits": [], "can_manage": False, "visible": False}

    plans = (
        await db.execute(
            select(Plan)
            .where(Plan.user_id == target.id, Plan.plan_date == d)
            .order_by(Plan.scheduled_time.nulls_last(), Plan.id)
        )
    ).scalars().all()
    habits = [
        h
        for h in await habit_rules.list_active(db, target.id)
        if habit_rules.is_due_on(h, d) and not habit_rules.is_finished(h, d)
    ]
    habits.sort(key=lambda h: (0, h.reminder_time) if h.reminder_time else (1, ""))
    logged = set()
    if habits:
        logged = set(
            (
                await db.execute(
                    select(HabitLog.habit_id).where(
                        HabitLog.habit_id.in_([h.id for h in habits]), HabitLog.log_date == d
                    )
                )
            ).scalars()
        )
    return {
        **base,
        "plans": [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "scheduled_time": p.scheduled_time,
                "status": p.status,
                "created_by_user_id": p.created_by_user_id,
            }
            for p in plans
        ],
        "habits": [
            {
                "id": h.id,
                "title": h.title,
                "icon": h.icon or "✅",
                "frequency": h.frequency,
                "reminder_time": h.reminder_time,
                "done_today": h.id in logged,
                "created_by_user_id": h.created_by_user_id,
            }
            for h in habits
        ],
        "can_manage": can_manage,
        "visible": True,
    }


# ─────────────────────────────────────────────────────────────
#  Tahrirlash / chiqish / topshirish
# ─────────────────────────────────────────────────────────────
async def _drop_member(db: AsyncSession, group_id: int, user_id: int) -> None:
    await db.execute(delete(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))
    await db.execute(
        delete(GroupPermission).where(
            GroupPermission.group_id == group_id,
            or_(GroupPermission.grantor_user_id == user_id, GroupPermission.grantee_user_id == user_id),
        )
    )


async def leave(db: AsyncSession, user: User, group_id: int) -> None:
    g = await get_group(db, group_id)
    await require_member(db, group_id, user.id)
    if g.owner_user_id == user.id:
        fail(409, "group_owner_leave")
    await _drop_member(db, group_id, user.id)
    await db.commit()


async def remove_member(db: AsyncSession, actor: User, group_id: int, target_id: int) -> None:
    await require_owner(db, group_id, actor)
    if target_id == actor.id:
        fail(409, "group_self")
    if await membership(db, group_id, target_id) is None:
        fail(404, "group_member_not_found")
    await _drop_member(db, group_id, target_id)
    await db.commit()


async def set_active(db: AsyncSession, actor: User, group_id: int, target_id: int, active: bool) -> dict:
    await require_owner(db, group_id, actor)
    m = await membership(db, group_id, target_id)
    if m is None:
        fail(404, "group_member_not_found")
    m.is_active = active
    await db.commit()
    return {"group_id": group_id, "user_id": target_id, "is_active": m.is_active}


async def transfer(db: AsyncSession, actor: User, group_id: int, target_id: int) -> None:
    g = await require_owner(db, group_id, actor)
    if target_id == actor.id:
        fail(409, "group_self")
    new = await membership(db, group_id, target_id)
    if new is None:
        fail(404, "group_member_not_found")
    old = await membership(db, group_id, actor.id)
    g.owner_user_id = target_id
    new.role, new.is_active = "owner", True
    if old is not None:
        old.role = "member"
    await db.commit()


# ─────────────────────────────────────────────────────────────
#  Ruxsatlar: "kim men uchun reja/odat yarata oladi"
# ─────────────────────────────────────────────────────────────
async def list_permissions(db: AsyncSession, user: User, group_id: int) -> dict:
    await require_member(db, group_id, user.id)
    others = (
        await db.execute(
            select(User)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(GroupMember.group_id == group_id, User.id != user.id)
            .order_by(GroupMember.joined_at)
        )
    ).scalars().all()
    out_map = dict(
        (
            await db.execute(
                select(GroupPermission.grantee_user_id, GroupPermission.can_manage).where(
                    GroupPermission.group_id == group_id, GroupPermission.grantor_user_id == user.id
                )
            )
        ).all()
    )
    in_map = dict(
        (
            await db.execute(
                select(GroupPermission.grantor_user_id, GroupPermission.can_manage).where(
                    GroupPermission.group_id == group_id, GroupPermission.grantee_user_id == user.id
                )
            )
        ).all()
    )

    def entry(u: User, cm: bool) -> dict:
        return {**public_card(u), "can_manage": bool(cm)}

    return {
        "grants_out": [entry(u, out_map.get(u.id, False)) for u in others],
        "grants_in": [entry(u, in_map.get(u.id, False)) for u in others],
    }


async def set_permission(db: AsyncSession, user: User, group_id: int, grantee_id: int, can_manage: bool) -> None:
    await require_member(db, group_id, user.id)
    if grantee_id == user.id:
        fail(409, "group_self")
    if await membership(db, group_id, grantee_id) is None:
        fail(404, "group_member_not_found")
    row = await db.scalar(
        select(GroupPermission).where(
            GroupPermission.group_id == group_id,
            GroupPermission.grantor_user_id == user.id,
            GroupPermission.grantee_user_id == grantee_id,
        )
    )
    if row is None:
        db.add(GroupPermission(group_id=group_id, grantor_user_id=user.id, grantee_user_id=grantee_id, can_manage=can_manage))
    else:
        row.can_manage = can_manage
    await db.commit()


async def manage_target(db: AsyncSession, actor: User, group_id: int, target_id: int) -> User:
    """actor target uchun yarata oladimi — target'ni qaytaradi."""
    await require_member(db, group_id, actor.id)
    m = await membership(db, group_id, target_id)
    target = await db.get(User, target_id)
    if m is None or target is None:
        fail(404, "group_member_not_found")
    if target_id != actor.id:
        ok = await db.scalar(
            select(GroupPermission.can_manage).where(
                GroupPermission.group_id == group_id,
                GroupPermission.grantor_user_id == target_id,
                GroupPermission.grantee_user_id == actor.id,
            )
        )
        if not ok:
            fail(403, "group_no_permission")
    return target


# ─────────────────────────────────────────────────────────────
#  Takliflar (username orqali)
# ─────────────────────────────────────────────────────────────
async def expire_old_invites(db: AsyncSession) -> int:
    res = await db.execute(
        update(GroupInvite)
        .where(GroupInvite.status == "pending", GroupInvite.expires_at <= utcnow())
        .values(status="expired", responded_at=utcnow())
    )
    await db.commit()
    return res.rowcount or 0


async def invite(db: AsyncSession, actor: User, group_id: int, username: str) -> dict:
    g = await require_owner(db, group_id, actor)
    target = await db.scalar(select(User).where(User.username == username, User.onboarded.is_(True)))
    if target is None:
        fail(404, "user_not_found")
    if target.id == actor.id:
        fail(409, "invite_self")
    if await membership(db, group_id, target.id) is not None:
        fail(409, "invite_already_member")
    if await member_count(db, group_id) >= rs.get("MAX_MEMBERS_PER_GROUP"):
        fail(409, "group_full", limit=rs.get("MAX_MEMBERS_PER_GROUP"))
    if await db.scalar(
        select(UserBlock.id).where(UserBlock.blocker_user_id == target.id, UserBlock.blocked_user_id == actor.id)
    ):
        fail(403, "invite_blocked")
    now = utcnow()
    pending = await db.scalar(
        select(GroupInvite.id).where(
            GroupInvite.group_id == group_id,
            GroupInvite.invitee_user_id == target.id,
            GroupInvite.status == "pending",
            GroupInvite.expires_at > now,
        )
    )
    if pending:
        fail(409, "invite_pending")
    recently_declined = await db.scalar(
        select(GroupInvite.id).where(
            GroupInvite.group_id == group_id,
            GroupInvite.invitee_user_id == target.id,
            GroupInvite.status == "declined",
            GroupInvite.responded_at > now - timedelta(days=config.DECLINED_INVITE_COOLDOWN_DAYS),
        )
    )
    if recently_declined:
        fail(409, "invite_cooldown")
    sent_today = int(
        await db.scalar(
            select(func.count(GroupInvite.id)).where(
                GroupInvite.inviter_user_id == actor.id, GroupInvite.created_at > now - timedelta(days=1)
            )
        )
        or 0
    )
    if sent_today >= config.MAX_INVITES_PER_DAY:
        fail(429, "invite_daily_limit", limit=config.MAX_INVITES_PER_DAY)

    inv = GroupInvite(
        group_id=g.id,
        inviter_user_id=actor.id,
        invitee_user_id=target.id,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=config.INVITE_TTL_DAYS),
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return {"id": inv.id, "user": public_card(target), "created_at": inv.created_at.isoformat()}


async def cancel_invite(db: AsyncSession, actor: User, group_id: int, invite_id: int) -> None:
    await require_owner(db, group_id, actor)
    inv = await db.scalar(
        select(GroupInvite).where(GroupInvite.id == invite_id, GroupInvite.group_id == group_id)
    )
    if inv is None or inv.status != "pending":
        fail(404, "invite_not_found")
    inv.status = "cancelled"
    inv.responded_at = utcnow()
    await db.commit()


async def my_invites(db: AsyncSession, user: User) -> list[dict]:
    counts = (
        select(GroupMember.group_id, func.count(GroupMember.id).label("n"))
        .group_by(GroupMember.group_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(GroupInvite, Group, User, counts.c.n)
            .join(Group, Group.id == GroupInvite.group_id)
            .join(User, User.id == GroupInvite.inviter_user_id)
            .join(counts, counts.c.group_id == Group.id)
            .where(
                GroupInvite.invitee_user_id == user.id,
                GroupInvite.status == "pending",
                GroupInvite.expires_at > utcnow(),
            )
            .order_by(GroupInvite.created_at.desc())
        )
    ).all()
    return [
        {
            "id": inv.id,
            "group": {"id": g.id, "name": g.name, "description": g.description, "member_count": int(n)},
            "inviter": public_card(u),
            "created_at": inv.created_at.isoformat(),
            "expires_at": inv.expires_at.isoformat(),
        }
        for inv, g, u, n in rows
    ]


async def pending_invites_count(db: AsyncSession, user: User) -> int:
    return int(
        await db.scalar(
            select(func.count(GroupInvite.id)).where(
                GroupInvite.invitee_user_id == user.id,
                GroupInvite.status == "pending",
                GroupInvite.expires_at > utcnow(),
            )
        )
        or 0
    )


async def _my_pending(db: AsyncSession, user: User, invite_id: int) -> GroupInvite:
    inv = await db.scalar(
        select(GroupInvite)
        .where(GroupInvite.id == invite_id, GroupInvite.invitee_user_id == user.id)
        .with_for_update()
    )
    if inv is None or inv.status != "pending" or inv.expires_at <= utcnow():
        fail(404, "invite_not_found")
    return inv


async def accept_invite(db: AsyncSession, user: User, invite_id: int) -> dict:
    inv = await _my_pending(db, user, invite_id)
    g = await db.get(Group, inv.group_id)
    if g is None:
        fail(404, "invite_not_found")
    if await membership(db, g.id, user.id) is None:
        if await member_count(db, g.id) >= rs.get("MAX_MEMBERS_PER_GROUP"):
            fail(409, "group_full", limit=rs.get("MAX_MEMBERS_PER_GROUP"))
        if await _groups_of(db, user.id) >= rs.get("MAX_GROUPS_PER_USER"):
            fail(409, "group_user_limit", limit=rs.get("MAX_GROUPS_PER_USER"))
        db.add(GroupMember(group_id=g.id, user_id=user.id, role="member"))
    inv.status = "accepted"
    inv.responded_at = utcnow()
    await db.commit()
    return {"id": g.id, "name": g.name}


async def decline_invite(db: AsyncSession, user: User, invite_id: int, block: bool) -> None:
    inv = await _my_pending(db, user, invite_id)
    inv.status = "declined"
    inv.responded_at = utcnow()
    if block:
        exists = await db.scalar(
            select(UserBlock.id).where(
                UserBlock.blocker_user_id == user.id, UserBlock.blocked_user_id == inv.inviter_user_id
            )
        )
        if not exists:
            db.add(UserBlock(blocker_user_id=user.id, blocked_user_id=inv.inviter_user_id))
        # Shu odamdan kelgan boshqa kutilayotgan takliflar ham yopiladi.
        await db.execute(
            update(GroupInvite)
            .where(
                GroupInvite.invitee_user_id == user.id,
                GroupInvite.inviter_user_id == inv.inviter_user_id,
                GroupInvite.status == "pending",
            )
            .values(status="declined", responded_at=utcnow())
        )
    await db.commit()


async def list_blocks(db: AsyncSession, user: User) -> list[dict]:
    rows = (
        await db.execute(
            select(User)
            .join(UserBlock, UserBlock.blocked_user_id == User.id)
            .where(UserBlock.blocker_user_id == user.id)
            .order_by(UserBlock.created_at.desc())
        )
    ).scalars()
    return [public_card(u) for u in rows]


async def unblock(db: AsyncSession, user: User, blocked_id: int) -> None:
    await db.execute(
        delete(UserBlock).where(UserBlock.blocker_user_id == user.id, UserBlock.blocked_user_id == blocked_id)
    )
    await db.commit()
