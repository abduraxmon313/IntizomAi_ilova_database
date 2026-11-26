"""Premium holati, bepul limitlar va premium muddatini uzaytirish."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import current_platform
from app.services import runtime_settings as rs
from app.models import Goal, Habit, Plan, Subscription, User, utcnow
from app.security import as_utc
from app.services.clock import today

log = logging.getLogger(__name__)

PLAN_TITLES = {"1m": "1 oy", "3m": "3 oy", "12m": "12 oy"}


def is_premium(user: User) -> bool:
    until = as_utc(user.premium_until)
    return until is not None and until > utcnow()


def purchases_visible() -> bool:
    """Joriy so'rov yuborgan ilovada Premium sotuvi (narx, obuna, promokod) ko'rsatiladimi.

    iOS'da APPLE_PREMIUM o'chiq bo'lsa — yo'q. Android va noma'lum platformada — ha.
    """
    return not (current_platform.get() == "ios" and not rs.get("APPLE_PREMIUM"))


def unlimited(user: User) -> bool:
    """Limit qo'llanmaydi: Premium foydalanuvchi yoki sotuv yopiq iOS ilovasi."""
    return is_premium(user) or not purchases_visible()


def days_left(user: User) -> int:
    until = as_utc(user.premium_until)
    if until is None:
        return 0
    delta = until - utcnow()
    if delta.total_seconds() <= 0:
        return 0
    return delta.days + (1 if delta.seconds > 0 else 0)


async def grant_days(
    db: AsyncSession,
    user: User,
    days: int,
    *,
    source: str,
    plan_key: str | None = None,
    amount: int = 0,
    reference: str | None = None,
) -> Subscription | None:
    """Premiumni `days` kunga uzaytiradi (amaldagi muddat ustiga qo'shiladi).

    `reference` noyob — bir xil to'lov/promo ikki marta faollashmaydi. Agar
    allaqachon bor bo'lsa None qaytaradi (idempotent). Commit chaqiruvchida emas,
    shu yerda bajariladi.
    """
    if days <= 0:
        return None
    if reference:
        exists = await db.scalar(select(Subscription.id).where(Subscription.reference == reference))
        if exists:
            return None
    # Qatorni qulflaymiz: bir vaqtda kelgan ikki bonus muddatni "yo'qotib" qo'ymasin.
    await db.refresh(user, with_for_update=True)
    now = utcnow()
    current = as_utc(user.premium_until)
    start = current if current and current > now else now
    expires = start + timedelta(days=days)
    sub = Subscription(
        user_id=user.id,
        source=source,
        plan_key=plan_key,
        days=days,
        amount=amount,
        reference=reference,
        starts_at=start,
        expires_at=expires,
    )
    db.add(sub)
    user.premium_until = expires
    try:
        await db.commit()
    except IntegrityError:
        # Parallel callback bir xil reference bilan — birinchisi yutadi.
        await db.rollback()
        return None
    log.info("Premium +%sd user=%s source=%s until=%s", days, user.id, source, expires)
    return sub


async def current_plan_key(db: AsyncSession, user: User) -> str | None:
    if not is_premium(user):
        return None
    return await db.scalar(
        select(Subscription.plan_key)
        .where(Subscription.user_id == user.id, Subscription.plan_key.is_not(None))
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )


# ─────────────────────────────────────────────────────────────
#  Bepul limitlar
# ─────────────────────────────────────────────────────────────
@dataclass
class Limit:
    allowed: bool
    used: int
    limit: int  # -1 = cheksiz

    @property
    def remaining(self) -> int:
        return -1 if self.limit < 0 else max(0, self.limit - self.used)


UNLIMITED = Limit(True, 0, -1)


async def plan_limit(db: AsyncSession, user: User, on_date: date, adding: int = 1) -> Limit:
    if unlimited(user):
        return UNLIMITED
    used = await db.scalar(
        select(func.count(Plan.id)).where(Plan.user_id == user.id, Plan.plan_date == on_date)
    ) or 0
    lim = rs.get("FREE_DAILY_PLAN_LIMIT")
    return Limit(used + adding <= lim, used, lim)


async def habit_limit(db: AsyncSession, user: User, adding: int = 1) -> Limit:
    if unlimited(user):
        return UNLIMITED
    used = await db.scalar(
        select(func.count(Habit.id)).where(Habit.user_id == user.id, Habit.archived.is_(False))
    ) or 0
    lim = rs.get("FREE_HABIT_LIMIT")
    return Limit(used + adding <= lim, used, lim)


async def goal_limit(db: AsyncSession, user: User, adding: int = 1) -> Limit:
    if unlimited(user):
        return UNLIMITED
    used = await db.scalar(select(func.count(Goal.id)).where(Goal.user_id == user.id)) or 0
    lim = rs.get("FREE_GOAL_LIMIT")
    return Limit(used + adding <= lim, used, lim)


def ai_limit_peek(user: User) -> Limit:
    if unlimited(user):
        return UNLIMITED
    used = user.ai_msgs_count if user.ai_msgs_date == today() else 0
    lim = rs.get("FREE_AI_DAILY_LIMIT")
    return Limit(used < lim, used, lim)


def ai_consume(user: User) -> None:
    if unlimited(user):
        return
    d = today()
    if user.ai_msgs_date != d:
        user.ai_msgs_date = d
        user.ai_msgs_count = 0
    user.ai_msgs_count += 1


def ai_refund(user: User) -> None:
    if unlimited(user):
        return
    if user.ai_msgs_date == today() and user.ai_msgs_count > 0:
        user.ai_msgs_count -= 1
