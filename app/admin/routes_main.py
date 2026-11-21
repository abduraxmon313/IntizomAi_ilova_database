"""Bosh sahifa va tizim holati."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.admin import stats
from app.admin.web import guard, post_guard, redirect, render
from app.db import get_session
from app.models import AppState, PaymentOrder, Session, User, utcnow
from app.services import billing

router = APIRouter()


@router.get("/")
async def dashboard(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    return render(request, "dashboard.html", ctx, s=await stats.dashboard(db))


@router.get("/system")
async def system(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    remote = await billing.remote_status(force=request.query_params.get("check") == "1")
    db_size = await db.scalar(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
    pg_version = await db.scalar(text("SHOW server_version"))
    states = (await db.execute(select(AppState).order_by(AppState.key))).scalars().all()
    active_sessions = await db.scalar(
        select(func.count(Session.id)).where(Session.revoked_at.is_(None), Session.expires_at > utcnow())
    )
    failed_orders = (
        await db.execute(
            select(PaymentOrder).where(PaymentOrder.status == "failed").order_by(PaymentOrder.id.desc()).limit(10)
        )
    ).scalars().all()
    checks = [
        ("SECRET_KEY", bool(config.SECRET_KEY), "Apple token shifrlash va 2FA uchun"),
        ("GOOGLE_CLIENT_IDS", bool(config.GOOGLE_CLIENT_IDS), "Google orqali kirish"),
        ("APPLE (TEAM/KEY/PRIVATE_KEY)", bool(config.APPLE_TEAM_ID and config.APPLE_KEY_ID and config.APPLE_PRIVATE_KEY), "Hisob o'chirilganda Apple ruxsatini bekor qilish"),
        ("OPENAI_API_KEY", bool(config.OPENAI_API_KEY), "AI murabbiy"),
        ("BILLING_* (bot partner API)", config.BILLING_ENABLED, "Paylov to'lovi bot orqali"),
        ("PUBLIC_BASE_URL", bool(config.PUBLIC_BASE_URL), "To'lovdan qaytish va taklif havolasi"),
        ("RUN_JOBS", config.RUN_JOBS, "Fon vazifalari (kun yakuni, to'lovlarni tekshirish)"),
    ]
    return render(
        request, "system.html", ctx,
        remote=remote, checks=checks, db_size=db_size, pg_version=pg_version, states=states,
        active_sessions=active_sessions, failed_orders=failed_orders,
        env=config.ENV, tz=config.TIMEZONE.key, billing_url=config.BILLING_BASE_URL,
        users=await db.scalar(select(func.count(User.id))),
    )


@router.post("/system/check-bot")
async def check_bot(request: Request, db: AsyncSession = Depends(get_session)):
    await post_guard(request, db, "admin")
    remote = await billing.remote_status(force=True)
    if remote["reachable"]:
        return redirect("/admin/system", "Bot bilan aloqa bor. To'lov: " + ("yoqilgan" if remote["payments_enabled"] else "o'chiq"))
    return redirect("/admin/system", f"Bot bilan aloqa yo'q: {remote['error']}", "err")
