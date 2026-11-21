"""Bosh sahifa (dashboard) ko'rsatkichlari — hammasi ilova bazasidan."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import Date, cast, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import Goal, Group, Habit, HabitLog, PaymentOrder, Plan, Subscription, User, utcnow
from app.services.clock import today
from app.services.gamification import RANKS, rank_for_level

SOURCE_LABELS = {
    "paylov": "Sotib olgan",
    "promo": "Bepul promokod",
    "referral": "Do'st taklifi (mukofot)",
    "referral_invitee": "Taklif qilingan (bonus)",
    "admin": "Admin bergan",
}


def _local_date(col):
    return cast(func.timezone(config.TIMEZONE.key, col), Date)


async def _count(db: AsyncSession, *where) -> int:
    return int(await db.scalar(select(func.count(User.id)).where(*where)) or 0)


def _series(rows, start: date, days: int) -> list[dict]:
    m = {d: int(v or 0) for d, v in rows}
    return [{"d": start + timedelta(days=i), "v": m.get(start + timedelta(days=i), 0)} for i in range(days)]


async def premium_by_source(db: AsyncSession) -> list[tuple[str, str, int]]:
    now = utcnow()
    rows = (
        await db.execute(
            select(Subscription.user_id, Subscription.source)
            .join(User, User.id == Subscription.user_id)
            .where(User.premium_until > now, Subscription.expires_at > now)
            .distinct(Subscription.user_id)
            .order_by(Subscription.user_id, Subscription.expires_at.desc())
        )
    ).all()
    c = Counter(src for _, src in rows)
    return [(k, SOURCE_LABELS.get(k, k), n) for k, n in c.most_common()]


async def dashboard(db: AsyncSession) -> dict:
    now = utcnow()
    d0 = today()
    day = timedelta(days=1)
    start30 = d0 - timedelta(days=29)

    users_total = await _count(db)
    onboarded = await _count(db, User.onboarded.is_(True))
    premium_now = await _count(db, User.premium_until > now)
    banned = await _count(db, User.banned_at.is_not(None))
    new = {
        n: await _count(db, User.created_at > now - timedelta(days=n)) for n in (1, 7, 30)
    }
    active = {
        n: await _count(db, User.last_active_at > now - timedelta(days=n)) for n in (1, 7, 30)
    }

    paid = PaymentOrder.status == "paid"

    async def revenue(since: datetime | None) -> tuple[int, int]:
        q = select(func.coalesce(func.sum(PaymentOrder.amount), 0), func.count(PaymentOrder.id)).where(paid)
        if since is not None:
            q = q.where(PaymentOrder.paid_at > since)
        s, n = (await db.execute(q)).one()
        return int(s or 0) // 100, int(n or 0)

    rev = {"1": await revenue(now - day), "7": await revenue(now - 7 * day), "30": await revenue(now - 30 * day), "all": await revenue(None)}
    pending = int(await db.scalar(select(func.count(PaymentOrder.id)).where(PaymentOrder.status == "pending")) or 0)

    # Kunlik grafiklar (30 kun)
    signups = (
        await db.execute(
            select(_local_date(User.created_at).label("d"), func.count(User.id))
            .where(User.created_at > now - timedelta(days=31))
            .group_by("d")
        )
    ).all()
    revenue_rows = (
        await db.execute(
            select(_local_date(PaymentOrder.paid_at).label("d"), func.sum(PaymentOrder.amount) / 100)
            .where(paid, PaymentOrder.paid_at > now - timedelta(days=31))
            .group_by("d")
        )
    ).all()
    acts = union_all(
        select(Plan.user_id.label("u"), Plan.plan_date.label("d")).where(Plan.status == "done", Plan.plan_date >= start30),
        select(HabitLog.user_id.label("u"), HabitLog.log_date.label("d")).where(HabitLog.log_date >= start30),
    ).subquery()
    dau_rows = (await db.execute(select(acts.c.d, func.count(func.distinct(acts.c.u))).group_by(acts.c.d))).all()

    # Kontent
    plans_total = int(await db.scalar(select(func.count(Plan.id))) or 0)
    plans_done = int(await db.scalar(select(func.count(Plan.id)).where(Plan.status == "done")) or 0)
    plans_today = int(await db.scalar(select(func.count(Plan.id)).where(Plan.plan_date == d0)) or 0)
    habits_active = int(await db.scalar(select(func.count(Habit.id)).where(Habit.archived.is_(False))) or 0)
    habit_logs_today = int(await db.scalar(select(func.count(HabitLog.id)).where(HabitLog.log_date == d0)) or 0)
    goals_total = int(await db.scalar(select(func.count(Goal.id))) or 0)
    groups_total = int(await db.scalar(select(func.count(Group.id))) or 0)
    ai_today = int(await db.scalar(select(func.coalesce(func.sum(User.ai_msgs_count), 0)).where(User.ai_msgs_date == d0)) or 0)

    # Unvonlar bo'yicha (daraja → unvon)
    lvl_rows = (await db.execute(select(User.level, func.count(User.id)).where(User.onboarded.is_(True)).group_by(User.level))).all()
    ranks = Counter()
    for lvl, n in lvl_rows:
        title, _ = rank_for_level(lvl or 1)
        ranks[title] += int(n)
    rank_list = [(e, t, ranks.get(t, 0)) for _, t, e in RANKS]

    top_score = (
        await db.execute(select(User).where(User.onboarded.is_(True)).order_by(User.total_score.desc()).limit(5))
    ).scalars().all()
    top_streak = (
        await db.execute(select(User).where(User.onboarded.is_(True), User.streak > 0).order_by(User.streak.desc()).limit(5))
    ).scalars().all()
    expiring = (
        await db.execute(
            select(User)
            .where(User.premium_until > now, User.premium_until < now + timedelta(days=3))
            .order_by(User.premium_until)
            .limit(8)
        )
    ).scalars().all()

    return {
        "users_total": users_total,
        "onboarded": onboarded,
        "not_onboarded": users_total - onboarded,
        "premium_now": premium_now,
        "conversion": round(premium_now * 100 / onboarded, 1) if onboarded else 0,
        "banned": banned,
        "new": new,
        "active": active,
        "revenue": rev,
        "pending": pending,
        "signups": _series(signups, start30, 30),
        "revenue_series": _series(revenue_rows, start30, 30),
        "dau": _series(dau_rows, start30, 30),
        "plans_total": plans_total,
        "plans_done": plans_done,
        "plans_today": plans_today,
        "habits_active": habits_active,
        "habit_logs_today": habit_logs_today,
        "goals_total": goals_total,
        "groups_total": groups_total,
        "ai_today": ai_today,
        "ranks": rank_list,
        "by_source": await premium_by_source(db),
        "top_score": top_score,
        "top_streak": top_streak,
        "expiring": expiring,
    }
