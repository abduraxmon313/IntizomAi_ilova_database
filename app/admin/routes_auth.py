"""Kirish, chiqish va o'z hisobim (parol, 2FA, sessiyalar)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.admin import auth
from app.admin.web import guard, post_guard, redirect, render
from app.db import get_session
from app.models import AdminSession, AdminUser, utcnow
from app.security import as_utc, client_ip, rate_limited

router = APIRouter()


def _safe_next(raw: str | None) -> str:
    n = (raw or "").strip()
    return n if n.startswith("/admin") and not n.startswith("//") and "\\" not in n else "/admin"


@router.get("/login")
async def login_page(request: Request, db: AsyncSession = Depends(get_session)):
    try:
        await auth.resolve(request, db)
        return redirect("/admin")
    except auth.LoginRequired:
        pass
    configured = bool(await db.scalar(select(func.count(AdminUser.id))))
    if not configured:
        # Ishga tushishda yaratilmagan bo'lsa (masalan, o'zgaruvchi keyin qo'shilgan) — hozir urinamiz.
        await auth.bootstrap(db)
        configured = bool(await db.scalar(select(func.count(AdminUser.id))))
    return render(request, "login.html", configured=configured, next=_safe_next(request.query_params.get("next")),
                  boot=auth.bootstrap_status())


@router.post("/login")
async def login(request: Request, db: AsyncSession = Depends(get_session)):
    form = auth.Form(await request.form())
    username = form.str("username", 40).lower()
    password = form.raw("password", 200)
    code = form.str("code", 12)
    nxt = _safe_next(form.str("next", 300))
    ip = client_ip(request)

    def fail(msg: str, status: int = 401):
        return render(request, "login.html", configured=True, next=nxt, error=msg, username=username, http_code=status)

    if rate_limited(f"admin-login:{ip}", 10, 600) or rate_limited(f"admin-login-u:{username}", 20, 3600):
        return fail("Juda ko'p urinish. 10 daqiqadan keyin qayta urinib ko'ring.", 429)

    admin = await db.scalar(select(AdminUser).where(AdminUser.username == username))
    now = utcnow()
    if admin is not None and admin.locked_until and as_utc(admin.locked_until) > now:
        return fail("Hisob vaqtincha qulflangan (ko'p noto'g'ri urinish). Keyinroq urinib ko'ring.", 423)
    if admin is None or not admin.is_active or not auth.verify_password(password, admin.password_hash):
        if admin is not None:
            admin.failed_logins = (admin.failed_logins or 0) + 1
            if admin.failed_logins >= auth.MAX_FAILED:
                admin.locked_until = now + timedelta(minutes=auth.LOCK_MINUTES)
                admin.failed_logins = 0
            await auth.audit(db, None, "login.failed", target_type="admin", target_id=username, ip=ip[:64])
        else:
            # Vaqt bo'yicha farq qilmasin (login bor-yo'qligini bilib bo'lmasin).
            auth.verify_password(password, auth.hash_password("x"))
        return fail("Login yoki parol noto'g'ri.")

    secret = auth.admin_totp_secret(admin)
    if secret:
        if not code:
            return render(request, "login.html", configured=True, next=nxt, username=username, need_code=True,
                          error="Autentifikator ilovasidagi 6 xonali kodni kiriting.", http_code=401)
        if not auth.verify_totp(secret, code):
            admin.failed_logins = (admin.failed_logins or 0) + 1
            await db.commit()
            return render(request, "login.html", configured=True, next=nxt, username=username, need_code=True,
                          error="2FA kodi noto'g'ri.", http_code=401)

    token = await auth.create_session(db, admin, request)
    await auth.audit(db, None, "login.ok", target_type="admin", target_id=admin.username, ip=ip[:64])
    resp = redirect("/admin/account" if admin.must_change_password else nxt)
    auth.set_cookie(resp, token)
    return resp


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_session)):
    try:
        ctx, _ = await post_guard(request, db)
        ctx.session.revoked_at = utcnow()
        await db.commit()
    except auth.LoginRequired:
        pass
    resp = redirect("/admin/login", "Tizimdan chiqdingiz.")
    auth.clear_cookie(resp)
    return resp


# ─────────────────────────────────────────────────────────────
#  O'z hisobim
# ─────────────────────────────────────────────────────────────
@router.get("/account")
async def account(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    sessions = (
        await db.execute(
            select(AdminSession)
            .where(AdminSession.admin_id == ctx.admin.id, AdminSession.revoked_at.is_(None), AdminSession.expires_at > utcnow())
            .order_by(AdminSession.last_seen_at.desc())
        )
    ).scalars().all()
    pending_secret = request.cookies.get("iz_totp_setup") if not ctx.admin.totp_secret else None
    return render(
        request, "account.html", ctx,
        sessions=sessions,
        has_2fa=bool(ctx.admin.totp_secret),
        can_2fa=bool(config.SECRET_KEY),
        pending_secret=pending_secret,
        pending_uri=auth.totp_uri(pending_secret, ctx.admin.username) if pending_secret else None,
    )


@router.post("/account/password")
async def change_password(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db)
    current, new, again = f.raw("current", 200), f.raw("new", 200), f.raw("again", 200)
    if not auth.verify_password(current, ctx.admin.password_hash):
        return redirect("/admin/account", "Joriy parol noto'g'ri.", "err")
    if new != again:
        return redirect("/admin/account", "Yangi parollar bir xil emas.", "err")
    problem = auth.password_problem(new, ctx.admin.username)
    if problem:
        return redirect("/admin/account", problem, "err")
    ctx.admin.password_hash = auth.hash_password(new)
    ctx.admin.must_change_password = False
    await auth.revoke_admin_sessions(db, ctx.admin.id, except_id=ctx.session.id)
    await auth.audit(db, ctx, "account.password", target_type="admin", target_id=ctx.admin.username)
    return redirect("/admin", "Parol yangilandi. Boshqa qurilmalardagi sessiyalar yopildi.")


@router.post("/account/2fa/start")
async def start_2fa(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db)
    if not config.SECRET_KEY:
        return redirect("/admin/account", "2FA uchun serverda SECRET_KEY sozlanishi kerak.", "err")
    resp = redirect("/admin/account")
    resp.set_cookie("iz_totp_setup", auth.new_totp_secret(), max_age=600, httponly=True, samesite="strict",
                    secure=config.IS_PRODUCTION, path="/admin/account")
    return resp


@router.post("/account/2fa/confirm")
async def confirm_2fa(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db)
    secret = request.cookies.get("iz_totp_setup") or ""
    if not secret or not auth.verify_totp(secret, f.str("code", 12)):
        return redirect("/admin/account", "Kod noto'g'ri yoki vaqti o'tgan. Qaytadan urinib ko'ring.", "err")
    sealed = auth.seal_totp(secret)
    if not sealed:
        return redirect("/admin/account", "2FA kalitini saqlab bo'lmadi (SECRET_KEY).", "err")
    ctx.admin.totp_secret = sealed
    await auth.audit(db, ctx, "account.2fa_on", target_type="admin", target_id=ctx.admin.username)
    resp = redirect("/admin/account", "Ikki bosqichli kirish yoqildi.")
    resp.delete_cookie("iz_totp_setup", path="/admin/account")
    return resp


@router.post("/account/2fa/disable")
async def disable_2fa(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db)
    if not auth.verify_password(f.raw("current", 200), ctx.admin.password_hash):
        return redirect("/admin/account", "Parol noto'g'ri.", "err")
    ctx.admin.totp_secret = None
    await auth.audit(db, ctx, "account.2fa_off", target_type="admin", target_id=ctx.admin.username)
    return redirect("/admin/account", "Ikki bosqichli kirish o'chirildi.", "warn")


@router.post("/account/sessions/{session_id}/revoke")
async def revoke_session(session_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db)
    s = await db.get(AdminSession, session_id)
    if s is None or s.admin_id != ctx.admin.id:
        return redirect("/admin/account", "Sessiya topilmadi.", "err")
    s.revoked_at = utcnow()
    await db.commit()
    return redirect("/admin/account", "Sessiya yopildi.")

