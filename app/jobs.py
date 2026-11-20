"""Fon vazifalari (bitta asyncio sikli, har 5 daqiqada).

  • Kun yakuni: o'tgan kunlar uchun seriyani muzlatish/uzish (server o'chib qolsa ham
    o'tkazib yuborilgan kunlar ketma-ket yakunlanadi — `app_state.settled_through`).
  • Oy boshida faol foydalanuvchilarga 1 ta seriya muzlatgichi (maks. 2).
  • Dushanba: haftalik reyting nolga.
  • Intizom balini kuniga bir marta qayta hisoblash (faol foydalanuvchilar).
  • Eskirgan takliflar, muddati o'tgan sessiyalar.
  • Callback kelmay qolgan to'lovlarni bot backend'dan tekshirish.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import delete, or_, select, update

from app import config
from app.db import SessionLocal
from app.models import AppState, PaymentOrder, Session, User, utcnow
from app.services import billing, gamification, groups
from app.services.clock import today

log = logging.getLogger(__name__)
INTERVAL = 300


async def _get_state(db, key: str) -> str | None:
    row = await db.get(AppState, key)
    return row.value if row else None


async def _set_state(db, key: str, value: str) -> None:
    row = await db.get(AppState, key)
    if row is None:
        db.add(AppState(key=key, value=value))
    else:
        row.value = value
    await db.commit()


async def settle_days() -> None:
    async with SessionLocal() as db:
        yesterday = today() - timedelta(days=1)
        raw = await _get_state(db, "settled_through")
        last = date.fromisoformat(raw) if raw else yesterday - timedelta(days=1)
        # Juda uzoq to'xtab qolgan bo'lsa ham 14 kundan ortiq orqaga qaytmaymiz.
        day = max(last + timedelta(days=1), yesterday - timedelta(days=13))
        while day <= yesterday:
            n = await gamification.settle_day(db, day)
            if day.weekday() == 6:  # yakshanba yakunlandi → yangi hafta
                monday = day + timedelta(days=1)
                # Yangi haftada allaqachon ishlaganlar ballini recompute o'zi to'g'ri hisoblagan.
                await db.execute(
                    update(User)
                    .where(or_(User.last_completed_date.is_(None), User.last_completed_date < monday))
                    .values(weekly_xp=0)
                )
                await db.commit()
            await _set_state(db, "settled_through", day.isoformat())
            log.info("Kun yakunlandi %s: %s ta seriya yangilandi", day, n)
            day += timedelta(days=1)


async def grant_monthly_freezes() -> None:
    month = today().strftime("%Y-%m")
    async with SessionLocal() as db:
        if await _get_state(db, "freezes_month") == month:
            return
        active_since = utcnow() - timedelta(days=30)
        users = (
            await db.execute(
                select(User).where(User.last_active_at > active_since, User.freeze_granted_month.is_distinct_from(month))
            )
        ).scalars()
        for u in users:
            u.streak_freezes = min(config.MAX_STREAK_FREEZES, (u.streak_freezes or 0) + 1)
            u.freeze_granted_month = month
        await db.commit()
        await _set_state(db, "freezes_month", month)


async def refresh_discipline() -> None:
    d = today().isoformat()
    async with SessionLocal() as db:
        if await _get_state(db, "discipline_day") == d:
            return
        since = utcnow() - timedelta(days=30)
        ids = list((await db.execute(select(User.id).where(User.last_active_at > since))).scalars())
        for uid in ids:
            u = await db.get(User, uid)
            if u is not None:
                u.discipline_score = await gamification.discipline_score(db, u)
                await db.commit()
        await _set_state(db, "discipline_day", d)


async def cleanup() -> None:
    async with SessionLocal() as db:
        await groups.expire_old_invites(db)
        await db.execute(delete(Session).where(Session.expires_at < utcnow() - timedelta(days=7)))
        await db.commit()


async def sync_pending_payments() -> None:
    if not config.BILLING_ENABLED:
        return
    async with SessionLocal() as db:
        now = utcnow()
        orders = (
            await db.execute(
                select(PaymentOrder).where(
                    PaymentOrder.status == "pending",
                    PaymentOrder.created_at < now - timedelta(minutes=2),
                    PaymentOrder.created_at > now - timedelta(days=2),
                ).limit(50)
            )
        ).scalars().all()
        for o in orders:
            await billing.sync_from_remote(db, o)
        # 2 kundan eski kutilayotgan buyurtmalar — bekor.
        await db.execute(
            update(PaymentOrder)
            .where(PaymentOrder.status.in_(("pending", "creating")), PaymentOrder.created_at < now - timedelta(days=2))
            .values(status="cancelled")
        )
        await db.commit()


async def run_once() -> None:
    for job in (settle_days, grant_monthly_freezes, refresh_discipline, cleanup, sync_pending_payments):
        try:
            await job()
        except Exception:
            log.exception("Fon vazifasi xato: %s", job.__name__)


async def loop() -> None:
    await asyncio.sleep(5)
    while True:
        await run_once()
        await asyncio.sleep(INTERVAL)
