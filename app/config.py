"""Ilova backend sozlamalari — hammasi muhit o'zgaruvchilaridan (Railway Variables).

Maxfiy qiymatlar kodda saqlanmaydi. To'liq ro'yxat: `.env.example`.
"""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = _str(name, "")
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    return [p.strip() for p in _str(name).split(",") if p.strip()]


def _database_url() -> str:
    """Railway `DATABASE_URL` (postgres://...) ni asyncpg drayveriga moslaydi."""
    url = _str("DATABASE_URL")
    if not url:
        host = _str("PGHOST", "localhost")
        port = _str("PGPORT", "5432")
        user = _str("PGUSER", "postgres")
        password = _str("PGPASSWORD", "")
        db = _str("PGDATABASE", "intizom_app")
        url = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


ENV = _str("APP_ENV", "production")
IS_PRODUCTION = ENV == "production"

DATABASE_URL = _database_url()
DB_POOL_SIZE = _int("DB_POOL_SIZE", 10)

# Server ochiq manzili (to'lovdan qaytish sahifasi, taklif havolasi, avatarlar).
PUBLIC_BASE_URL = _str("PUBLIC_BASE_URL").rstrip("/") or (
    f"https://{_str('RAILWAY_PUBLIC_DOMAIN')}" if _str("RAILWAY_PUBLIC_DOMAIN") else ""
)

# Ilovaning barcha "bugun" hisob-kitoblari shu vaqt mintaqasida (ilova ham Toshkent).
TIMEZONE = ZoneInfo(_str("APP_TIMEZONE", "Asia/Tashkent"))

# Maxfiy kalit — Apple refresh tokenni shifrlash va boshqa ichki imzolar uchun.
SECRET_KEY = _str("SECRET_KEY")

# ── Kirish (Sign in) ─────────────────────────────────────────────────────────
# Google Cloud'dagi OAuth client ID'lar (vergul bilan): Web (serverClientId),
# iOS va Android. ID token `aud` maydoni shulardan biri bo'lishi shart.
GOOGLE_CLIENT_IDS = _list("GOOGLE_CLIENT_IDS")
# Apple: ilova bundle ID (aud). Masalan: uz.intizomai.intizomAi
APPLE_BUNDLE_IDS = _list("APPLE_BUNDLE_IDS") or ["uz.intizomai.intizomAi"]
# Apple token bekor qilish (hisob o'chirilganda) uchun — ixtiyoriy, lekin tavsiya etiladi.
APPLE_TEAM_ID = _str("APPLE_TEAM_ID")
APPLE_KEY_ID = _str("APPLE_KEY_ID")
APPLE_PRIVATE_KEY = _str("APPLE_PRIVATE_KEY").replace("\\n", "\n")

SESSION_TTL_DAYS = _int("SESSION_TTL_DAYS", 180)

# ── Bepul tarif limitlari ───────────────────────────────────────────────────
FREE_DAILY_PLAN_LIMIT = _int("FREE_DAILY_PLAN_LIMIT", 5)
FREE_HABIT_LIMIT = _int("FREE_HABIT_LIMIT", 3)
FREE_GOAL_LIMIT = _int("FREE_GOAL_LIMIT", 3)
FREE_AI_DAILY_LIMIT = _int("FREE_AI_DAILY_LIMIT", 3)
# Guruh yaratish faqat Premium uchunmi.
GROUP_CREATE_PREMIUM_ONLY = _bool("GROUP_CREATE_PREMIUM_ONLY", True)
# Guruhga qo'shilish (taklifni qabul qilish) faqat Premium uchunmi.
GROUP_JOIN_PREMIUM_ONLY = _bool("GROUP_JOIN_PREMIUM_ONLY", True)
# iOS'da Premium sotuvi. false (standart): iPhone ilovasida narx/obuna/promokod
# ko'rinmaydi va bepul limitlar qo'llanmaydi (hammasi cheksiz). true: iOS ham
# Android kabi ishlaydi. Admin panel → Sozlamalar'dan ham o'zgartirsa bo'ladi.
APPLE_PREMIUM = _bool("APPLE_PREMIUM", False)

# ── Guruhlar ────────────────────────────────────────────────────────────────
MAX_GROUPS_PER_OWNER = _int("MAX_GROUPS_PER_OWNER", 10)
MAX_GROUPS_PER_USER = _int("MAX_GROUPS_PER_USER", 30)
MAX_MEMBERS_PER_GROUP = _int("MAX_MEMBERS_PER_GROUP", 50)
MAX_INVITES_PER_DAY = _int("MAX_INVITES_PER_DAY", 40)
INVITE_TTL_DAYS = _int("INVITE_TTL_DAYS", 14)
DECLINED_INVITE_COOLDOWN_DAYS = _int("DECLINED_INVITE_COOLDOWN_DAYS", 7)

# ── Onboarding ──────────────────────────────────────────────────────────────
# Maxfiylik siyosatiga mos: ilova 13 yoshdan kichiklar uchun emas (COPPA / GDPR-K).
MIN_AGE = _int("MIN_AGE", 13)
MAX_AGE = _int("MAX_AGE", 100)
USERNAME_CHANGE_COOLDOWN_DAYS = _int("USERNAME_CHANGE_COOLDOWN_DAYS", 7)

# ── Gamifikatsiya ───────────────────────────────────────────────────────────
PLAN_DONE_SCORE = 5
HABIT_DONE_SCORE = _int("HABIT_DONE_SCORE", 5)
MAX_STREAK_FREEZES = 2

# ── Referral ────────────────────────────────────────────────────────────────
REFERRAL_THRESHOLD = _int("REFERRAL_THRESHOLD", 5)
REFERRAL_REWARD_DAYS = _int("REFERRAL_REWARD_DAYS", 7)
REFERRAL_INVITEE_REWARD_DAYS = _int("REFERRAL_INVITEE_REWARD_DAYS", 3)
# Taklif "faol" hisoblanishi uchun yangi foydalanuvchi necha xil kunda
# reja/odat bajargan bo'lishi kerak (soxta hisoblar bilan bonus yig'ishga qarshi).
REFERRAL_ACTIVATION_DAYS = _int("REFERRAL_ACTIVATION_DAYS", 3)

# ── AI murabbiy (OpenAI) ────────────────────────────────────────────────────
OPENAI_API_KEY = _str("OPENAI_API_KEY")
OPENAI_MODEL = _str("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = _str("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# ── To'lov: bot backend (Paylov shartnomasi ulangan Railway) ────────────────
# Ilova backend to'lovni o'zi qilmaydi: botning /partner/v1 API'siga murojaat qiladi.
BILLING_BASE_URL = _str("BILLING_BASE_URL").rstrip("/")
BILLING_PARTNER_KEY = _str("BILLING_PARTNER_KEY")
BILLING_PARTNER_SECRET = _str("BILLING_PARTNER_SECRET")
BILLING_ENABLED = bool(BILLING_BASE_URL and BILLING_PARTNER_KEY and BILLING_PARTNER_SECRET)

# ── Havolalar ───────────────────────────────────────────────────────────────
APP_STORE_URL = _str("APP_STORE_URL")
PLAY_STORE_URL = _str("PLAY_STORE_URL", "https://play.google.com/store/apps/details?id=uz.intizomai.intizom_ai")
APP_SCHEME = _str("APP_SCHEME", "intizomai")
SUPPORT_URL = _str("SUPPORT_URL", "https://t.me/IntizomAI_Admin_bot")

# ── Xavfsizlik ──────────────────────────────────────────────────────────────
MAX_BODY_BYTES = _int("MAX_BODY_BYTES", 1_500_000)
RATE_LIMIT_USER = _int("RATE_LIMIT_USER", 240)   # tasdiqlangan foydalanuvchi / daqiqa
RATE_LIMIT_IP = _int("RATE_LIMIT_IP", 600)       # anonim (CGNAT ortida ko'p odam) / daqiqa
RATE_LIMIT_AUTH = _int("RATE_LIMIT_AUTH", 30)    # kirish urinishlari / IP / daqiqa
RATE_LIMIT_WINDOW = 60

# ── Admin panel (/admin) ────────────────────────────────────────────────────
# Birinchi kirish uchun: adminlar jadvali bo'sh bo'lsa shu login "Egasi" bo'lib yaratiladi.
ADMIN_USERNAME = _str("ADMIN_USERNAME")
ADMIN_PASSWORD = _str("ADMIN_PASSWORD")
# Parolni unutsangiz: true qiling, deploy qiling, kiring, keyin o'chiring.
ADMIN_RESET_PASSWORD = _bool("ADMIN_RESET_PASSWORD", False)

# Fon vazifalari (kun yakuni, tozalash). Bir nechta replika bo'lsa faqat bittasida yoqing.
RUN_JOBS = _bool("RUN_JOBS", True)


def validate() -> list[str]:
    """Ishga tushishda ogohlantirishlar ro'yxati (server baribir ishlaydi)."""
    warnings = []
    if not SECRET_KEY:
        warnings.append("SECRET_KEY sozlanmagan — Apple refresh token saqlanmaydi.")
    if not GOOGLE_CLIENT_IDS:
        warnings.append("GOOGLE_CLIENT_IDS sozlanmagan — Google orqali kirish ishlamaydi.")
    if not BILLING_ENABLED:
        warnings.append("BILLING_* sozlanmagan — obuna sotib olish o'chiq.")
    if not OPENAI_API_KEY:
        warnings.append("OPENAI_API_KEY sozlanmagan — AI murabbiy javob bermaydi.")
    if not PUBLIC_BASE_URL:
        warnings.append("PUBLIC_BASE_URL aniqlanmadi — to'lovdan qaytish sahifasi ishlamaydi.")
    return warnings
