"""Adminlar (faqat Egasi) va audit jurnali."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.web import NotFound, guard, paginate, post_guard, redirect, render
from app.db import get_session
from app.models import AdminAudit, AdminSession, AdminUser, utcnow

router = APIRouter()


def _temp_password() -> str:
    return secrets.token_urlsafe(9) + "7a"


@router.get("/admins")
async def admins(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "owner")
    rows = (await db.execute(select(AdminUser).order_by(AdminUser.created_at))).scalars().all()
    active = dict((await db.execute(
        select(AdminSession.admin_id, func.count(AdminSession.id))
        .where(AdminSession.revoked_at.is_(None), AdminSession.expires_at > utcnow()).group_by(AdminSession.admin_id)
    )).all())
    new_pw = request.cookies.get("iz_newpw")
    resp = render(request, "admins.html", ctx, admins=rows, active=active, ROLES=auth.ROLES,
                  new_password=new_pw.split(":", 1) if new_pw and ":" in new_pw else None)
    if new_pw:
        resp.delete_cookie("iz_newpw", path="/admin/admins")  # parol faqat bir marta ko'rsatiladi
    return resp


@router.post("/admins/new")
async def admin_create(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "owner")
    username = f.str("username", 40).lower()
    role = f.str("role", 12)
    if not username or not username.replace("_", "").replace(".", "").isalnum() or len(username) < 3:
        return redirect("/admin/admins", "Login: kamida 3 belgi, lotin harfi, raqam, _ yoki .", "err")
    if role not in auth.ROLES:
        return redirect("/admin/admins", "Rolni tanlang.", "err")
    if await db.scalar(select(AdminUser.id).where(AdminUser.username == username)):
        return redirect("/admin/admins", "Bunday login bor.", "err")
    pw = _temp_password()
    db.add(AdminUser(username=username, full_name=f.str("full_name", 80) or None, password_hash=auth.hash_password(pw),
                     role=role, must_change_password=True))
    await auth.audit(db, ctx, "admin.create", target_type="admin", target_id=username, summary=auth.ROLE_LABELS[role], commit=False)
    await db.commit()
    resp = redirect("/admin/admins", f"{username} qo'shildi. Vaqtinchalik parolni unga xavfsiz yo'l bilan yuboring.")
    resp.set_cookie("iz_newpw", f"{username}:{pw}", max_age=120, httponly=True, samesite="strict", path="/admin/admins")
    return resp


async def _target(db: AsyncSession, ctx: auth.Ctx, admin_id: int) -> AdminUser:
    a = await db.get(AdminUser, admin_id)
    if a is None:
        raise NotFound("Admin topilmadi.")
    return a


async def _owners_left(db: AsyncSession, except_id: int) -> int:
    return int(await db.scalar(select(func.count(AdminUser.id)).where(
        AdminUser.role == "owner", AdminUser.is_active.is_(True), AdminUser.id != except_id)) or 0)


@router.post("/admins/{admin_id}/role")
async def admin_role(admin_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "owner")
    a = await _target(db, ctx, admin_id)
    role = f.str("role", 12)
    if role not in auth.ROLES:
        return redirect("/admin/admins", "Noto'g'ri rol.", "err")
    if a.role == "owner" and role != "owner" and not await _owners_left(db, a.id):
        return redirect("/admin/admins", "Kamida bitta faol Egasi qolishi kerak.", "err")
    old = a.role
    a.role = role
    await auth.audit(db, ctx, "admin.role", target_type="admin", target_id=a.username, summary=f"{old} → {role}", commit=False)
    await db.commit()
    return redirect("/admin/admins", f"{a.username}: rol o'zgardi.")


@router.post("/admins/{admin_id}/toggle")
async def admin_toggle(admin_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "owner")
    a = await _target(db, ctx, admin_id)
    if a.id == ctx.admin.id:
        return redirect("/admin/admins", "O'zingizni o'chira olmaysiz.", "err")
    if a.is_active and a.role == "owner" and not await _owners_left(db, a.id):
        return redirect("/admin/admins", "Kamida bitta faol Egasi qolishi kerak.", "err")
    a.is_active = not a.is_active
    if not a.is_active:
        await auth.revoke_admin_sessions(db, a.id)
    await auth.audit(db, ctx, "admin.toggle", target_type="admin", target_id=a.username,
                     summary="yoqildi" if a.is_active else "o'chirildi", commit=False)
    await db.commit()
    return redirect("/admin/admins", f"{a.username} " + ("yoqildi." if a.is_active else "o'chirildi va tizimdan chiqarildi."))


@router.post("/admins/{admin_id}/reset")
async def admin_reset(admin_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "owner")
    a = await _target(db, ctx, admin_id)
    if a.id == ctx.admin.id:
        return redirect("/admin/account", "O'z parolingizni «Hisobim» sahifasida o'zgartiring.", "warn")
    pw = _temp_password()
    a.password_hash = auth.hash_password(pw)
    a.must_change_password = True
    a.totp_secret = None
    a.failed_logins = 0
    a.locked_until = None
    await auth.revoke_admin_sessions(db, a.id)
    await auth.audit(db, ctx, "admin.reset", target_type="admin", target_id=a.username, summary="parol va 2FA tiklandi", commit=False)
    await db.commit()
    resp = redirect("/admin/admins", f"{a.username} uchun yangi vaqtinchalik parol yaratildi.")
    resp.set_cookie("iz_newpw", f"{a.username}:{pw}", max_age=120, httponly=True, samesite="strict", path="/admin/admins")
    return resp


@router.post("/admins/{admin_id}/delete")
async def admin_delete(admin_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "owner")
    a = await _target(db, ctx, admin_id)
    if a.id == ctx.admin.id:
        return redirect("/admin/admins", "O'zingizni o'chira olmaysiz.", "err")
    if a.role == "owner" and not await _owners_left(db, a.id):
        return redirect("/admin/admins", "Kamida bitta faol Egasi qolishi kerak.", "err")
    name = a.username
    await db.delete(a)
    await auth.audit(db, ctx, "admin.delete", target_type="admin", target_id=name, commit=False)
    await db.commit()
    return redirect("/admin/admins", f"{name} o'chirildi.", "warn")


# ─────────────────────────────────────────────────────────────
#  Audit jurnali
# ─────────────────────────────────────────────────────────────
@router.get("/audit")
async def audit_log(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    q = request.query_params.get("q", "")[:80].strip()
    stmt = select(AdminAudit)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(
            func.lower(AdminAudit.action).like(like), func.lower(AdminAudit.admin_username).like(like),
            AdminAudit.target_id == q, func.lower(AdminAudit.summary).like(like),
        ))
    page = await paginate(db, stmt.order_by(AdminAudit.created_at.desc()), request, {"q": q}, per_page=50)
    return render(request, "audit.html", ctx, page=page, q=q)
