"""Ma'lumotlar bazasi modellari (PostgreSQL).

Vaqtlar UTC'da (timezone-aware) saqlanadi; "kun" tushunchasi (plan_date,
log_date, ...) esa `config.TIMEZONE` bo'yicha `date` sifatida saqlanadi.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Onboarding'da to'ldiriladi (Apple ism bermasligi mumkin).
    full_name: Mapped[str | None] = mapped_column(String(80))
    # Kichik harfda saqlanadi — noyoblik registrga bog'liq emas.
    username: Mapped[str | None] = mapped_column(String(20), unique=True)
    username_changed_at: Mapped[datetime | None] = mapped_column(TS)
    # Yosh o'rniga tug'ilgan yil saqlanadi — yosh vaqt o'tishi bilan to'g'ri qoladi.
    birth_year: Mapped[int | None] = mapped_column(Integer)
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    # Provayder (Google) rasmi URL'i yoki None.
    provider_photo_url: Mapped[str | None] = mapped_column(String(1024))
    # Foydalanuvchi yuklagan rasm kaliti (avatars.key).
    avatar_key: Mapped[str | None] = mapped_column(String(40))

    onboarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    onboarded_at: Mapped[datetime | None] = mapped_column(TS)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    leaderboard_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Gamifikatsiya ──
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    weekly_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_completed_date: Mapped[date | None] = mapped_column(Date)
    streak_freezes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    freeze_granted_month: Mapped[str | None] = mapped_column(String(7))
    discipline_score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    perfect_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_perfect_day: Mapped[date | None] = mapped_column(Date)

    # ── Premium ──
    premium_until: Mapped[datetime | None] = mapped_column(TS, index=True)

    # ── AI kunlik limit ──
    ai_msgs_date: Mapped[date | None] = mapped_column(Date)
    ai_msgs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Referral ──
    referred_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    referral_activated_at: Mapped[datetime | None] = mapped_column(TS)
    referral_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    referral_rewards_given: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(TS)

    # ── Moderatsiya (admin panel) ──
    banned_at: Mapped[datetime | None] = mapped_column(TS)
    ban_reason: Mapped[str | None] = mapped_column(String(200))


class AuthIdentity(Base):
    """Kirish usuli: Google yoki Apple hisobi. Bitta foydalanuvchida ikkalasi bo'lishi mumkin."""

    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_identity_provider_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(16))  # google | apple
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Apple refresh token (shifrlangan) — hisob o'chirilganda Apple'da bekor qilish uchun.
    apple_refresh_token: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)


class Session(Base):
    """Qurilma sessiyasi. Token o'zi saqlanmaydi — faqat SHA-256 xeshi."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    platform: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class Avatar(Base):
    __tablename__ = "avatars"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content_type: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (Index("ix_plans_user_date", "user_id", "plan_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(String(1000))
    scheduled_time: Mapped[str | None] = mapped_column(String(5))
    plan_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    score_value: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class Habit(Base):
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500))
    icon: Mapped[str] = mapped_column(String(16), default="✅", nullable=False)
    reminder_time: Mapped[str | None] = mapped_column(String(5))
    frequency: Mapped[str] = mapped_column(String(10), default="daily", nullable=False)
    weekdays: Mapped[str | None] = mapped_column(String(20))  # "0,2,4" (0=Dushanba)
    duration_type: Mapped[str] = mapped_column(String(12), default="permanent", nullable=False)
    target_days: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # O'chirilgan odat arxivlanadi — tarix va ballar saqlanadi.
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Arxivlangan kun (TIMEZONE bo'yicha). Shu kungacha o'tkazib yuborilgan kunlar
    # intizom balida qoladi — odatni o'chirib, "qarzni" yo'qotib bo'lmaydi.
    archived_on: Mapped[date | None] = mapped_column(Date)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class HabitLog(Base):
    __tablename__ = "habit_logs"
    __table_args__ = (
        UniqueConstraint("habit_id", "log_date", name="uq_habit_log_day"),
        Index("ix_habit_logs_user_date", "user_id", "log_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    log_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(String(1000))
    goal_type: Mapped[str] = mapped_column(String(10))  # yearly | monthly
    period: Mapped[str] = mapped_column(String(7))  # "2026" | "2026-05"
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class StreakFreezeDay(Base):
    """Seriya muzlatgichi ishlatilgan kun — o'sha kun "faol" deb hisoblanadi."""

    __tablename__ = "streak_freeze_days"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_streak_freeze_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)


class Achievement(Base):
    __tablename__ = "achievements"
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_achievement_user_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    unlocked_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class Checkin(Base):
    __tablename__ = "checkins"
    __table_args__ = (UniqueConstraint("user_id", "checkin_date", name="uq_checkin_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    checkin_date: Mapped[date] = mapped_column(Date)
    mood: Mapped[str | None] = mapped_column(String(16))
    energy: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class Subscription(Base):
    """Premium tarixi: har bir to'lov / bonus alohida yozuv."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # paylov | promo | referral | referral_invitee | admin
    source: Mapped[str] = mapped_column(String(24))
    plan_key: Mapped[str | None] = mapped_column(String(16))
    days: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # tiyin
    # Takroriy faollashtirishdan himoya: to'lov external_id, promo kodi va h.k.
    reference: Mapped[str | None] = mapped_column(String(160), unique=True)
    starts_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class PaymentOrder(Base):
    """Ilova tomonidagi to'lov buyurtmasi. Paylov bilan bot backend gaplashadi."""

    __tablename__ = "payment_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_key: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(24))
    promo_code: Mapped[str | None] = mapped_column(String(64))
    promo_id: Mapped[int | None] = mapped_column(ForeignKey("promocodes.id", ondelete="SET NULL"))
    amount: Mapped[int | None] = mapped_column(BigInteger)  # tiyin
    # Chegirmadan oldingi narx (tiyin) — admin panelda chegirma summasini ko'rsatish uchun.
    original_amount: Mapped[int | None] = mapped_column(BigInteger)
    bonus_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    days: Mapped[int | None] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    # creating | pending | paid | cancelled | failed
    status: Mapped[str] = mapped_column(String(12), default="creating", nullable=False)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(TS)
    last_checked_at: Mapped[datetime | None] = mapped_column(TS)


class ReferralClaim(Base):
    """Bonus olgan hisob identifikatori xeshi. Hisob o'chirib qayta ochilsa ham saqlanadi
    — taklif bonusini qayta-qayta olishning oldini oladi."""

    __tablename__ = "referral_claims"

    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member", nullable=False)  # owner | member
    # Ega a'zoni "pauza" qilsa — uning ma'lumotlari boshqalarga ko'rinmaydi.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class GroupPermission(Base):
    """grantor → grantee: grantee grantor uchun reja/odat yarata oladi."""

    __tablename__ = "group_permissions"
    __table_args__ = (
        UniqueConstraint("group_id", "grantor_user_id", "grantee_user_id", name="uq_group_permission"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    grantor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    grantee_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class GroupInvite(Base):
    """Username orqali yuborilgan taklif. Taklif qilingan foydalanuvchi qabul qilsagina a'zo bo'ladi."""

    __tablename__ = "group_invites"
    __table_args__ = (Index("ix_group_invites_invitee_status", "invitee_user_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    inviter_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    invitee_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # pending | accepted | declined | cancelled | expired
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS)
    responded_at: Mapped[datetime | None] = mapped_column(TS)


class UserBlock(Base):
    """blocker endi blocked'dan guruh taklifi olmaydi."""

    __tablename__ = "user_blocks"
    __table_args__ = (UniqueConstraint("blocker_user_id", "blocked_user_id", name="uq_user_block"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blocker_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    blocked_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class AppState(Base):
    """Kichik kalit-qiymat ombori (masalan, oxirgi yakunlangan kun)."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow, nullable=False)


# ─────────────────────────────────────────────────────────────
#  Tariflar va promokodlar (admin paneldan boshqariladi)
# ─────────────────────────────────────────────────────────────
class PricingPlan(Base):
    """Sotuvdagi tarif. Narx so'mda. Bot faqat Paylov to'lovini o'tkazadi."""

    __tablename__ = "pricing_plans"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    days: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    # Chizilgan "eski narx" (aksiya ko'rinishi uchun), ixtiyoriy.
    original_price: Mapped[int | None] = mapped_column(Integer)
    tag: Mapped[str | None] = mapped_column(String(64))
    emoji: Mapped[str | None] = mapped_column(String(8))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow, nullable=False)


class Promocode(Base):
    """kind:
    free     — to'lovsiz `days` kun Premium;
    bonus    — tarif sotib olinganda `days` kun qo'shimcha;
    discount — tarif narxidan `discount_percent`% chegirma (ixtiyoriy `days` bonus bilan).
    """

    __tablename__ = "promocodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kichik harfda saqlanadi — kiritishda registr ahamiyatsiz.
    code: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(12))
    days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Qaysi tariflarga amal qiladi ("1m,3m"); bo'sh — hammasiga.
    plan_keys: Mapped[str | None] = mapped_column(String(160))
    max_uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 — cheksiz
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class PromoRedemption(Base):
    """Promokod ishlatilishi. Bitta hisob (identity) — bitta promokodni bir marta."""

    __tablename__ = "promo_redemptions"
    __table_args__ = (UniqueConstraint("promo_id", "identity_ref", name="uq_promo_redemption_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promocodes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    identity_ref: Mapped[str] = mapped_column(String(80))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("payment_orders.id", ondelete="SET NULL"))
    days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # tiyin
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ─────────────────────────────────────────────────────────────
#  E'lonlar (ilovada ko'rinadigan xabarlar — botdagi "xabar yuborish" o'rniga)
# ─────────────────────────────────────────────────────────────
class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    button_label: Mapped[str | None] = mapped_column(String(40))
    button_url: Mapped[str | None] = mapped_column(String(500))
    # all | free | premium | user
    audience: Mapped[str] = mapped_column(String(12), default="all", nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(TS)
    ends_at: Mapped[datetime | None] = mapped_column(TS)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class AppSetting(Base):
    """Admin paneldan o'zgartiriladigan sozlamalar (bo'lmasa — muhit o'zgaruvchisidagi qiymat)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow, nullable=False)


# ─────────────────────────────────────────────────────────────
#  Admin panel: hisoblar, sessiyalar, audit jurnali
# ─────────────────────────────────────────────────────────────
class AdminUser(Base):
    """role: owner — hammasi (adminlarni boshqarish ham); admin — kundalik boshqaruv;
    support — ko'rish va foydalanuvchiga yordam (premium berish yo'q)."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(12), default="admin", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Ikki bosqichli kirish (TOTP) — shifrlangan maxfiy kalit.
    totp_secret: Mapped[str | None] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(TS)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class AdminAudit(Base):
    __tablename__ = "admin_audit"
    __table_args__ = (Index("ix_admin_audit_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    admin_username: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(48))
    target_type: Mapped[str | None] = mapped_column(String(24))
    target_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(String(500))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
