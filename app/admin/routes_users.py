"""Foydalanuvchilar: qidiruv, filtr, eksport, batafsil ko'rish va amallar."""
from __future__ import annotations

import csv
import io
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import false, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.stats import SOURCE_LABELS, premium_by_source
from app.admin.web import NotFound, Page, fdt, guard, paginate, post_guard, redirect, render, secure_headers
from app.db import get_session
from app.models import (
    Announcement,
    AuthIdentity,
    Goal,
    Group,
    GroupMember,
    Habit,
    HabitLog,
    PaymentOrder,
    Plan,
    PromoRedemption,
    Promocode,
    Session,
    Subscription,
    User,
    utcnow,
)
from app.services import account, gamification, premium, pricing
from app.services.clock import today
from app.services.users import clean_name, normalize_username, username_problem, username_taken

router = APIRouter()

FILTERS = {
    "all": "Hammasi",
    "premium": "Premium",
    "free": "Bepul",
    "active7": "7 kunda faol",
    "inactive30": "30 kundan beri kirmagan",
    "not_onboarded": "Ro'yxatdan o'tmagan",
    "banned": "Bloklangan",
}
SORTS = {
    "new": ("Yangi", User.created_at.desc()),
    "old": ("Eski", User.created_at.asc()),
    "active": ("Oxirgi faollik", User.last_active_at.desc().nulls_last()),
    "score": ("Ball", User.total_score.desc()),
    "streak": ("Seriya", User.streak.desc()),
    "premium_until": ("Premium tugashi", User.premium_until.desc().nulls_last()),
}


def _query(q: str, flt: str, sort: str):
    now = utcnow()
    stmt = select(User)
    q = q.strip()
    if q:
        uname = normalize_username(q)
        like = f"%{q.lower()}%"
        conds = [
            func.lower(User.full_name).like(like),
            User.username.like(f"%{uname}%") if uname else false(),
            func.lower(User.email).like(like),
        ]
        if q.lstrip("#").isdigit():
            conds.append(User.id == int(q.lstrip("#")))
        stmt = stmt.where(or_(*conds))
    stmt = {
        "premium": lambda s: s.where(User.premium_until > now),
        "free": lambda s: s.where(or_(User.premium_until.is_(None), User.premium_until <= now), User.onboarded.is_(True)),
        "active7": lambda s: s.where(User.last_active_at > now - timedelta(days=7)),
        "inactive30": lambda s: s.where(or_(User.last_active_at.is_(None), User.last_active_at < now - timedelta(days=30))),
        "not_onboarded": lambda s: s.where(User.onboarded.is_(False)),
        "banned": lambda s: s.where(User.banned_at.is_not(None)),
    }.get(flt, lambda s: s)(stmt)
    return stmt.order_by(SORTS.get(sort, SORTS["new"])[1], User.id.desc())


@router.get("/users")
async def users(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    qp = request.query_params
    q, flt, sort = qp.get("q", "")[:80], qp.get("f", "all"), qp.get("sort", "new")
    page: Page = await paginate(db, _query(q, flt, sort), request, {"q": q, "f": flt, "sort": sort})
    return render(request, "users.html", ctx, page=page, q=q, f=flt, sort=sort, FILTERS=FILTERS,
                  SORTS={k: v[0] for k, v in SORTS.items()}, now=utcnow())


@router.get("/users.csv")
async def users_csv(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    qp = request.query_params
    rows = (await db.execute(_query(qp.get("q", "")[:80], qp.get("f", "all"), qp.get("sort", "new")).limit(50_000))).scalars().all()
    await auth.audit(db, ctx, "users.export", summary=f"{len(rows)} ta qator")
    buf = io.StringIO()
    buf.write("\ufeff")  # Excel UTF-8 ni to'g'ri ochishi uchun
    w = csv.writer(buf)
    w.writerow(["id", "ism", "username", "email", "yosh", "ro'yxatdan", "oxirgi faollik", "premium gacha",
                "ball", "daraja", "seriya", "intizom", "do'stlar", "bloklangan"])
    now = utcnow()
    for u in rows:
        w.writerow([
            u.id, u.full_name or "", u.username or "", u.email or "",
            (today().year - u.birth_year) if u.birth_year else "",
            fdt(u.created_at), fdt(u.last_active_at),
            fdt(u.premium_until) if u.premium_until and u.premium_until > now else "",
            u.total_score, u.level, u.streak, u.discipline_score, u.referral_count, "ha" if u.banned_at else "",
        ])
    resp = StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="intizom-users-{today()}.csv"'})
    secure_headers(resp)
    return resp


async def _user_or_404(db: AsyncSession, user_id: int) -> User:
    u = await db.get(User, user_id)
    if u is None:
        raise NotFound("Foydalanuvchi topilmadi (o'chirilgan bo'lishi mumkin).")
    return u


@router.get("/users/{user_id}")
async def user_detail(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    u = await _user_or_404(db, user_id)
    now = utcnow()

    async def count(q) -> int:
        return int(await db.scalar(q) or 0)

    plan_counts = dict(
        (await db.execute(select(Plan.status, func.count(Plan.id)).where(Plan.user_id == u.id).group_by(Plan.status))).all()
    )
    data = {
        "identities": (await db.execute(select(AuthIdentity).where(AuthIdentity.user_id == u.id))).scalars().all(),
        "sessions": await count(select(func.count(Session.id)).where(
            Session.user_id == u.id, Session.revoked_at.is_(None), Session.expires_at > now)),
        "plans": {k: int(v) for k, v in plan_counts.items()},
        "plans_total": sum(int(v) for v in plan_counts.values()),
        "habits_active": await count(select(func.count(Habit.id)).where(Habit.user_id == u.id, Habit.archived.is_(False))),
        "habits_archived": await count(select(func.count(Habit.id)).where(Habit.user_id == u.id, Habit.archived.is_(True))),
        "habit_logs": await count(select(func.count(HabitLog.id)).where(HabitLog.user_id == u.id)),
        "goals_total": await count(select(func.count(Goal.id)).where(Goal.user_id == u.id)),
        "goals_done": await count(select(func.count(Goal.id)).where(Goal.user_id == u.id, Goal.completed.is_(True))),
        "subs": (await db.execute(select(Subscription).where(Subscription.user_id == u.id).order_by(Subscription.created_at.desc()).limit(30))).scalars().all(),
        "orders": (await db.execute(select(PaymentOrder).where(PaymentOrder.user_id == u.id).order_by(PaymentOrder.id.desc()).limit(20))).scalars().all(),
        "redemptions": (await db.execute(
            select(PromoRedemption, Promocode.code).join(Promocode, Promocode.id == PromoRedemption.promo_id)
            .where(PromoRedemption.user_id == u.id).order_by(PromoRedemption.created_at.desc())
        )).all(),
        "groups": (await db.execute(
            select(Group, GroupMember).join(GroupMember, GroupMember.group_id == Group.id).where(GroupMember.user_id == u.id)
        )).all(),
        "referrer": await db.get(User, u.referred_by_id) if u.referred_by_id else None,
        "invitees": (await db.execute(select(User).where(User.referred_by_id == u.id).order_by(User.created_at.desc()).limit(20))).scalars().all(),
        "recent_plans": (await db.execute(select(Plan).where(Plan.user_id == u.id).order_by(Plan.plan_date.desc(), Plan.id.desc()).limit(10))).scalars().all(),
        "messages": (await db.execute(select(Announcement).where(Announcement.target_user_id == u.id).order_by(Announcement.created_at.desc()).limit(10))).scalars().all(),
        "is_premium": premium.is_premium(u),
        "days_left": premium.days_left(u),
        "rank": gamification.rank_for_level(u.level or 1),
        "age": (today().year - u.birth_year) if u.birth_year else None,
        "ai_today": u.ai_msgs_count if u.ai_msgs_date == today() else 0,
        "plans_catalog": await pricing.list_plans(db, active_only=False),
    }
    return render(request, "user_detail.html", ctx, u=u, d=data, SOURCE_LABELS=SOURCE_LABELS)


def _back(user_id: int) -> str:
    return f"/admin/users/{user_id}"


@router.post("/users/{user_id}/premium/grant")
async def grant_premium(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    u = await _user_or_404(db, user_id)
    plan_key = f.str("plan", 16) or None
    days = f.int("days")
    plan = await pricing.get_plan(db, plan_key, active_only=False) if plan_key else None
    if plan is not None and not days:
        days = plan.days
    if not days or not (1 <= days <= 3650):
        return redirect(_back(user_id), "Kunlar sonini (1…3650) kiriting yoki tarif tanlang.", "err")
    note = f.str("note", 200)
    await premium.grant_days(db, u, days, source="admin", plan_key=plan.key if plan else None,
                             reference=f"admin:{secrets.token_hex(8)}")
    await auth.audit(db, ctx, "user.premium_grant", target_type="user", target_id=u.id,
                     summary=f"+{days} kun{f' ({plan.title})' if plan else ''}{f' — {note}' if note else ''}")
    return redirect(_back(user_id), f"{days} kun Premium qo'shildi.")


@router.post("/users/{user_id}/premium/revoke")
async def revoke_premium(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    u = await _user_or_404(db, user_id)
    if not premium.is_premium(u):
        return redirect(_back(user_id), "Foydalanuvchida faol Premium yo'q.", "warn")
    left = premium.days_left(u)
    now = utcnow()
    u.premium_until = now
    # Kelajakdagi obuna yozuvlarini ham hozir tugatamiz — tarix saqlanadi.
    await db.execute(update(Subscription).where(Subscription.user_id == u.id, Subscription.expires_at > now).values(expires_at=now))
    await auth.audit(db, ctx, "user.premium_revoke", target_type="user", target_id=u.id,
                     summary=f"{left} kun qolgan edi. {f.str('note', 200)}".strip(), commit=False)
    await db.commit()
    return redirect(_back(user_id), "Premium bekor qilindi.", "warn")


@router.post("/users/{user_id}/ban")
async def ban(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    u = await _user_or_404(db, user_id)
    reason = f.str("reason", 200)
    if not reason:
        return redirect(_back(user_id), "Bloklash sababini yozing.", "err")
    u.banned_at = utcnow()
    u.ban_reason = reason
    await db.execute(update(Session).where(Session.user_id == u.id, Session.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await auth.audit(db, ctx, "user.ban", target_type="user", target_id=u.id, summary=reason, commit=False)
    await db.commit()
    return redirect(_back(user_id), "Foydalanuvchi bloklandi va barcha qurilmalardan chiqarildi.", "warn")


@router.post("/users/{user_id}/unban")
async def unban(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    u = await _user_or_404(db, user_id)
    u.banned_at = None
    u.ban_reason = None
    await auth.audit(db, ctx, "user.unban", target_type="user", target_id=u.id, commit=False)
    await db.commit()
    return redirect(_back(user_id), "Blok olib tashlandi.")


@router.post("/users/{user_id}/sessions/revoke")
async def revoke_sessions(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db)
    u = await _user_or_404(db, user_id)
    res = await db.execute(update(Session).where(Session.user_id == u.id, Session.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await auth.audit(db, ctx, "user.logout_all", target_type="user", target_id=u.id, summary=f"{res.rowcount} ta sessiya", commit=False)
    await db.commit()
    return redirect(_back(user_id), f"{res.rowcount} ta qurilmadan chiqarildi.")


@router.post("/users/{user_id}/ai-reset")
async def ai_reset(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db)
    u = await _user_or_404(db, user_id)
    u.ai_msgs_count = 0
    await auth.audit(db, ctx, "user.ai_reset", target_type="user", target_id=u.id, commit=False)
    await db.commit()
    return redirect(_back(user_id), "Bugungi AI xabarlar limiti yangilandi.")


@router.post("/users/{user_id}/freeze")
async def give_freeze(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    u = await _user_or_404(db, user_id)
    u.streak_freezes = min(5, (u.streak_freezes or 0) + 1)
    await auth.audit(db, ctx, "user.freeze_add", target_type="user", target_id=u.id, summary=f"jami {u.streak_freezes}", commit=False)
    await db.commit()
    return redirect(_back(user_id), "Seriya muzlatgichi qo'shildi.")


@router.post("/users/{user_id}/edit")
async def edit_user(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    u = await _user_or_404(db, user_id)
    changes = []
    name = f.str("full_name", 80)
    if name and name != (u.full_name or ""):
        cleaned = clean_name(name)
        if not cleaned:
            return redirect(_back(user_id), "Ism 2–60 belgi bo'lsin, < > belgilarisiz.", "err")
        changes.append(f"ism: {u.full_name} → {cleaned}")
        u.full_name = cleaned
    uname = normalize_username(f.str("username", 32))
    if uname and uname != (u.username or ""):
        problem = username_problem(uname)
        if problem:
            return redirect(_back(user_id), "Username: 3–20 belgi, lotin harfi bilan boshlansin, faqat a-z 0-9 _.", "err")
        if await username_taken(db, uname, except_user_id=u.id):
            return redirect(_back(user_id), "Bu username band.", "err")
        changes.append(f"username: {u.username} → {uname}")
        u.username = uname
        u.username_changed_at = utcnow()
    if not changes:
        return redirect(_back(user_id), "O'zgarish yo'q.", "warn")
    await auth.audit(db, ctx, "user.edit", target_type="user", target_id=u.id, summary="; ".join(changes), commit=False)
    await db.commit()
    return redirect(_back(user_id), "Profil yangilandi.")


@router.post("/users/{user_id}/message")
async def message_user(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db)
    u = await _user_or_404(db, user_id)
    title, body = f.str("title", 120), f.raw("body", 2000).strip()
    if not title or not body:
        return redirect(_back(user_id), "Sarlavha va matnni yozing.", "err")
    db.add(Announcement(title=title, body=body, audience="user", target_user_id=u.id, created_by=ctx.admin.username,
                        ends_at=utcnow() + timedelta(days=30)))
    await auth.audit(db, ctx, "user.message", target_type="user", target_id=u.id, summary=title, commit=False)
    await db.commit()
    return redirect(_back(user_id), "Xabar yuborildi — foydalanuvchi ilovani ochganda ko'radi.")


@router.post("/users/{user_id}/delete")
async def delete_user(user_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "owner")
    u = await _user_or_404(db, user_id)
    if f.str("confirm", 20) != str(u.id):
        return redirect(_back(user_id), "Tasdiqlash uchun foydalanuvchi ID raqamini to'g'ri yozing.", "err")
    label = f"#{u.id} @{u.username or '-'} {u.full_name or ''}"
    await account.delete_account(db, u)
    await auth.audit(db, ctx, "user.delete", target_type="user", target_id=user_id, summary=label)
    return redirect("/admin/users", "Hisob va barcha ma'lumotlari o'chirildi.", "warn")


# ─────────────────────────────────────────────────────────────
#  Premium foydalanuvchilar
# ─────────────────────────────────────────────────────────────
@router.get("/premium")
async def premium_users(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    now = utcnow()
    src = request.query_params.get("source", "")
    stmt = select(User).where(User.premium_until > now)
    if src:
        latest = (
            select(Subscription.user_id, Subscription.source)
            .where(Subscription.expires_at > now)
            .distinct(Subscription.user_id)
            .order_by(Subscription.user_id, Subscription.expires_at.desc())
            .subquery()
        )
        stmt = stmt.join(latest, latest.c.user_id == User.id).where(latest.c.source == src)
    order = request.query_params.get("sort", "soon")
    stmt = stmt.order_by(User.premium_until.asc() if order == "soon" else User.premium_until.desc())
    page = await paginate(db, stmt, request, {"source": src, "sort": order})
    ids = [u.id for u in page.items]
    sources: dict[int, str] = {}
    if ids:
        rows = (await db.execute(
            select(Subscription.user_id, Subscription.source).where(Subscription.user_id.in_(ids), Subscription.expires_at > now)
            .distinct(Subscription.user_id).order_by(Subscription.user_id, Subscription.expires_at.desc())
        )).all()
        sources = {uid: s for uid, s in rows}
    return render(request, "premium.html", ctx, page=page, sources=sources, by_source=await premium_by_source(db),
                  source=src, sort=order, SOURCE_LABELS=SOURCE_LABELS, premium=premium)
