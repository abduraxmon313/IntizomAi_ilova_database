"""XP, daraja, seriya (streak), intizom bali, yutuqlar.

Botdagidan farqi: hamma narsa HAQIQIY manbadan (bajarilgan reja/odatlar)
deterministik qayta hisoblanadi. Shuning uchun belgilash ↔ bekor qilishda
"drift" bo'lmaydi: bugungi yagona ishni bekor qilsangiz, seriya ham orqaga qaytadi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import Achievement, Habit, HabitLog, Plan, StreakFreezeDay, User
from app.services import habits as habit_rules
from app.services.clock import now_local, time_reached, today

STREAK_WINDOW_DAYS = 1000


# ─────────────────────────────────────────────────────────────
#  XP egri chizig'i va unvonlar
# ─────────────────────────────────────────────────────────────
def xp_for_level(level: int) -> int:
    return 0 if level <= 1 else 50 * (level - 1) * level


def level_for_xp(xp: int) -> int:
    lvl = 1
    while lvl < 200 and xp >= xp_for_level(lvl + 1):
        lvl += 1
    return lvl


def xp_progress(xp: int) -> tuple[int, int, int, int]:
    lvl = level_for_xp(xp)
    base, nxt = xp_for_level(lvl), xp_for_level(lvl + 1)
    needed = max(1, nxt - base)
    in_lvl = xp - base
    return lvl, in_lvl, needed, max(0, min(100, in_lvl * 100 // needed))


RANKS = [
    (1, "Boshlovchi", "🌱"),
    (3, "O'rganuvchi", "🔰"),
    (5, "Faol", "⚡"),
    (8, "Izchil", "🔥"),
    (12, "Intizomli", "💎"),
    (18, "Ustoz", "🏆"),
    (25, "Afsona", "👑"),
    (35, "Mifik", "🌌"),
]


def rank_for_level(level: int) -> tuple[str, str]:
    title, emoji = RANKS[0][1], RANKS[0][2]
    for min_lvl, t, e in RANKS:
        if level >= min_lvl:
            title, emoji = t, e
    return title, emoji


@dataclass(frozen=True)
class AchDef:
    code: str
    title: str  # o'zbekcha — ilova tarjima katalogi kaliti sifatida ishlatadi
    icon: str
    rarity: str


ACHIEVEMENTS = [
    AchDef("first_step", "Birinchi qadam", "🌱", "common"),
    AchDef("streak_3", "Olov yondi", "🔥", "common"),
    AchDef("streak_7", "Bir hafta", "⚡", "rare"),
    AchDef("streak_14", "Ikki hafta", "💪", "rare"),
    AchDef("streak_30", "Bir oy", "💎", "epic"),
    AchDef("streak_100", "Yuz kun", "🌌", "legendary"),
    AchDef("level_5", "5-daraja", "⭐", "common"),
    AchDef("level_10", "10-daraja", "🌟", "rare"),
    AchDef("level_25", "25-daraja", "👑", "epic"),
    AchDef("perfect_day", "Mukammal kun", "✨", "rare"),
    AchDef("early_bird", "Erta qush", "🌅", "common"),
    AchDef("night_owl", "Tungi ishchi", "🌙", "common"),
    AchDef("100_done", "100 ta bajarildi", "🎯", "rare"),
    AchDef("500_done", "500 ta bajarildi", "🏆", "epic"),
    AchDef("comeback", "Qaytdim", "🔄", "rare"),
]
ACH = {a.code: a for a in ACHIEVEMENTS}


@dataclass
class Reward:
    leveled_up: bool = False
    level: int = 1
    streak: int = 0
    perfect_day: bool = False
    unlocked: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
#  Faollik kunlari
# ─────────────────────────────────────────────────────────────
async def _active_days(db: AsyncSession, user_id: int, since: date) -> set[date]:
    plan_days = await db.execute(
        select(Plan.plan_date)
        .where(Plan.user_id == user_id, Plan.status == "done", Plan.plan_date >= since)
        .distinct()
    )
    habit_days = await db.execute(
        select(HabitLog.log_date).where(HabitLog.user_id == user_id, HabitLog.log_date >= since).distinct()
    )
    return {d for (d,) in plan_days.all()} | {d for (d,) in habit_days.all()}


async def _frozen_days(db: AsyncSession, user_id: int, since: date) -> set[date]:
    rows = await db.execute(
        select(StreakFreezeDay.day).where(StreakFreezeDay.user_id == user_id, StreakFreezeDay.day >= since)
    )
    return {d for (d,) in rows.all()}


def _streak_from(days: set[date], d0: date) -> int:
    start = d0 if d0 in days else d0 - timedelta(days=1)
    if start not in days:
        return 0
    n = 0
    d = start
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


async def _done_points(db: AsyncSession, user_id: int, since: date | None = None) -> int:
    pq = select(func.coalesce(func.sum(Plan.score_value), 0)).where(Plan.user_id == user_id, Plan.status == "done")
    hq = select(func.count(HabitLog.id)).where(HabitLog.user_id == user_id)
    if since is not None:
        pq = pq.where(Plan.plan_date >= since, Plan.plan_date <= today())
        hq = hq.where(HabitLog.log_date >= since, HabitLog.log_date <= today())
    plans = await db.scalar(pq) or 0
    logs = await db.scalar(hq) or 0
    return int(plans) + int(logs) * config.HABIT_DONE_SCORE


async def _done_count(db: AsyncSession, user_id: int) -> int:
    plans = await db.scalar(select(func.count(Plan.id)).where(Plan.user_id == user_id, Plan.status == "done")) or 0
    logs = await db.scalar(select(func.count(HabitLog.id)).where(HabitLog.user_id == user_id)) or 0
    return int(plans) + int(logs)


# ─────────────────────────────────────────────────────────────
#  Intizom bali (0..100) — botdagi formula
# ─────────────────────────────────────────────────────────────
async def discipline_score(db: AsyncSession, user: User) -> int:
    d0 = today()
    window_start = d0 - timedelta(days=29)
    last7 = d0 - timedelta(days=6)
    total = done = last7_done = 0

    plans = (
        await db.execute(
            select(Plan.plan_date, Plan.status, Plan.scheduled_time).where(
                Plan.user_id == user.id, Plan.plan_date >= window_start, Plan.plan_date <= d0
            )
        )
    ).all()
    for pdate, status, stime in plans:
        if status == "done":
            total += 1
            done += 1
            last7_done += pdate >= last7
        elif pdate < d0 or (pdate == d0 and time_reached(stime)):
            total += 1

    habits = list((await db.execute(select(Habit).where(Habit.user_id == user.id))).scalars())
    if habits:
        logs = await habit_rules.log_dates(db, [h.id for h in habits], window_start)
        for h in habits:
            hl = logs.get(h.id, set())
            for i in range(30):
                d = window_start + timedelta(days=i)
                if d in hl:
                    total += 1
                    done += 1
                    last7_done += d >= last7
                    continue
                if h.archived or not habit_rules.is_due_on(h, d):
                    continue
                if d < d0 or (d == d0 and time_reached(h.reminder_time)):
                    total += 1

    completion = done / total if total else 0.0
    streak_norm = min(1.0, (user.streak or 0) / 30.0)
    intensity = min(1.0, last7_done / 21.0)
    if user.last_completed_date is None:
        inactivity = 0.0
    else:
        inactivity = max(0.0, 1.0 - (d0 - user.last_completed_date).days / 7.0)
    score = completion * 50 + streak_norm * 25 + intensity * 15 + inactivity * 10
    return int(round(max(0, min(100, score))))


# ─────────────────────────────────────────────────────────────
#  Bugungi holat (reja + bugun rejalashtirilgan odatlar)
# ─────────────────────────────────────────────────────────────
async def today_progress(db: AsyncSession, user_id: int) -> tuple[int, int]:
    d0 = today()
    rows = (await db.execute(select(Plan.status).where(Plan.user_id == user_id, Plan.plan_date == d0))).all()
    total = len(rows)
    done = sum(1 for (s,) in rows if s == "done")
    habits = await habit_rules.list_active(db, user_id)
    due = [h for h in habits if habit_rules.is_due_on(h, d0)]
    total += len(due)
    if due:
        done += await db.scalar(
            select(func.count(HabitLog.id)).where(
                HabitLog.habit_id.in_([h.id for h in due]), HabitLog.log_date == d0
            )
        ) or 0
    return int(done), int(total)


# ─────────────────────────────────────────────────────────────
#  Asosiy: o'zgarishdan keyin qayta hisoblash
# ─────────────────────────────────────────────────────────────
async def recompute(db: AsyncSession, user: User, *, completed_now: bool = False) -> Reward:
    """Reja/odat holati o'zgargach chaqiriladi. Commit shu yerda."""
    prev_level = user.level or 1
    prev_last = user.last_completed_date
    d0 = today()

    total = await _done_points(db, user.id)
    user.xp = user.total_score = total
    user.level = level_for_xp(total)
    monday = d0 - timedelta(days=d0.weekday())
    user.weekly_xp = await _done_points(db, user.id, since=monday)

    since = d0 - timedelta(days=STREAK_WINDOW_DAYS)
    active = await _active_days(db, user.id, since)
    frozen = await _frozen_days(db, user.id, since)
    user.streak = _streak_from(active | frozen, d0)
    user.longest_streak = max(user.longest_streak or 0, user.streak)
    past_active = [d for d in active if d <= d0]
    user.last_completed_date = max(past_active) if past_active else None
    user.discipline_score = await discipline_score(db, user)

    reward = Reward(level=user.level, streak=user.streak, leveled_up=user.level > prev_level)

    if completed_now:
        done, all_ = await today_progress(db, user.id)
        if all_ >= 1 and done >= all_ and user.last_perfect_day != d0:
            user.last_perfect_day = d0
            user.perfect_days = (user.perfect_days or 0) + 1
            reward.perfect_day = True
        reward.unlocked = await _unlock(db, user, reward.perfect_day, prev_last, d0)

    await db.commit()
    return reward


async def _unlock(db: AsyncSession, user: User, perfect: bool, prev_last: date | None, d0: date) -> list[str]:
    have = set((await db.execute(select(Achievement.code).where(Achievement.user_id == user.id))).scalars())
    want: list[str] = []

    def need(code: str, cond: bool) -> None:
        if cond and code not in have:
            want.append(code)

    s = user.streak or 0
    need("first_step", True)
    for n in (3, 7, 14, 30, 100):
        need(f"streak_{n}", s >= n)
    for n in (5, 10, 25):
        need(f"level_{n}", user.level >= n)
    need("perfect_day", perfect or (user.perfect_days or 0) > 0)
    hour = now_local().hour
    need("early_bird", hour < 7)
    need("night_owl", hour >= 22)
    if "100_done" not in have or "500_done" not in have:
        cnt = await _done_count(db, user.id)
        need("100_done", cnt >= 100)
        need("500_done", cnt >= 500)
    need("comeback", prev_last is not None and (d0 - prev_last).days >= 7)

    if want:
        await db.execute(
            pg_insert(Achievement)
            .values([{"user_id": user.id, "code": c} for c in want])
            .on_conflict_do_nothing(index_elements=["user_id", "code"])
        )
    return want


# ─────────────────────────────────────────────────────────────
#  Ilova uchun statistika (GET /stats)
# ─────────────────────────────────────────────────────────────
async def snapshot(db: AsyncSession, user: User, is_premium: bool) -> dict:
    lvl, in_lvl, needed, pct = xp_progress(user.xp or 0)
    rank_title, rank_emoji = rank_for_level(lvl)
    done, total = await today_progress(db, user.id)
    achs = (
        await db.execute(
            select(Achievement.code, Achievement.unlocked_at)
            .where(Achievement.user_id == user.id)
            .order_by(Achievement.unlocked_at.desc())
        )
    ).all()
    return {
        "level": lvl,
        "xp": user.xp or 0,
        "total_score": user.total_score or 0,
        "weekly_xp": user.weekly_xp or 0,
        "xp_in_level": in_lvl,
        "xp_needed": needed,
        "xp_percent": pct,
        "streak": user.streak or 0,
        "longest_streak": user.longest_streak or 0,
        "streak_at_risk": user.last_completed_date != today(),
        "streak_freezes": user.streak_freezes or 0,
        "discipline_score": user.discipline_score if user.discipline_score is not None else 50,
        "rank_title": rank_title,
        "rank_emoji": rank_emoji,
        "is_premium": is_premium,
        "perfect_days": user.perfect_days or 0,
        "today_done": done,
        "today_total": total,
        "today_completion_pct": done * 100 // total if total else 0,
        "achievements": [
            {
                "code": code,
                "title": ACH[code].title,
                "icon": ACH[code].icon,
                "rarity": ACH[code].rarity,
                "unlocked_at": at.isoformat() if at else None,
            }
            for code, at in achs
            if code in ACH
        ],
    }


# ─────────────────────────────────────────────────────────────
#  Kun yakuni (fon vazifasi): muzlatgich yoki seriyani uzish
# ─────────────────────────────────────────────────────────────
async def settle_day(db: AsyncSession, day: date) -> int:
    """`day` kuni hech narsa bajarmagan, seriyasi bor foydalanuvchilar uchun:
    muzlatgich bo'lsa — kun "muzlatiladi", aks holda seriya 0. Idempotent."""
    users = list(
        (
            await db.execute(
                select(User).where(
                    User.streak > 0,
                    and_(User.last_completed_date.is_not(None), User.last_completed_date < day),
                )
            )
        ).scalars()
    )
    changed = 0
    for u in users:
        already = await db.scalar(
            select(StreakFreezeDay.id).where(StreakFreezeDay.user_id == u.id, StreakFreezeDay.day == day)
        )
        if already:
            continue
        # Seriya kechagi kungacha (day-1) davom etgan bo'lsagina muzlatish ma'noli.
        prev_covered = u.last_completed_date == day - timedelta(days=1) or bool(
            await db.scalar(
                select(StreakFreezeDay.id).where(
                    StreakFreezeDay.user_id == u.id, StreakFreezeDay.day == day - timedelta(days=1)
                )
            )
        )
        if prev_covered and (u.streak_freezes or 0) > 0:
            u.streak_freezes -= 1
            db.add(StreakFreezeDay(user_id=u.id, day=day))
        else:
            u.streak = 0
        changed += 1
    await db.commit()
    return changed
