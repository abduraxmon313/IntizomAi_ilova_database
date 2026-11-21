"""Admin paneldan o'zgartiriladigan sozlamalar.

Qiymat `app_settings` jadvalida bo'lsa — o'sha, aks holda `app/config.py`dagi
(muhit o'zgaruvchisi) qiymat. Jarayon xotirasida keshlanadi: ishga tushishda,
har fon siklida (5 daqiqa) va admin saqlaganda darhol yangilanadi.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import AppSetting


@dataclass(frozen=True)
class Spec:
    key: str
    kind: str  # int | bool
    label: str
    help: str
    group: str
    min: int = 0
    max: int = 100_000
    default: int | bool | None = None  # config'da bo'lmagan kalitlar uchun


SPECS: list[Spec] = [
    Spec("SALES_ENABLED", "bool", "Sotuv yoqilgan", "O'chirilsa ilovada to'lov tugmasi yopiladi (promokodlar ishlayveradi).", "Sotuv", default=True),
    Spec("FREE_DAILY_PLAN_LIMIT", "int", "Kunlik rejalar", "Bepul foydalanuvchi bir kunga nechta reja qo'sha oladi.", "Bepul tarif", 0, 1000),
    Spec("FREE_HABIT_LIMIT", "int", "Odatlar", "Bepul foydalanuvchining faol odatlari soni.", "Bepul tarif", 0, 1000),
    Spec("FREE_GOAL_LIMIT", "int", "Maqsadlar", "Bepul foydalanuvchining maqsadlari soni.", "Bepul tarif", 0, 1000),
    Spec("FREE_AI_DAILY_LIMIT", "int", "AI xabarlar (kuniga)", "Bepul foydalanuvchi AI murabbiyga kuniga nechta xabar yozadi.", "Bepul tarif", 0, 1000),
    Spec("GROUP_CREATE_PREMIUM_ONLY", "bool", "Guruh yaratish faqat Premium", "Taklifni qabul qilish baribir bepul.", "Bepul tarif"),
    Spec("MAX_GROUPS_PER_OWNER", "int", "Bitta egadagi guruhlar", "Bir foydalanuvchi nechta guruh yarata oladi.", "Guruhlar", 1, 1000),
    Spec("MAX_GROUPS_PER_USER", "int", "Bitta a'zodagi guruhlar", "Bir foydalanuvchi nechta guruhda bo'la oladi.", "Guruhlar", 1, 1000),
    Spec("MAX_MEMBERS_PER_GROUP", "int", "Guruhdagi a'zolar", "Bitta guruhdagi maksimal a'zolar soni.", "Guruhlar", 2, 10_000),
    Spec("REFERRAL_THRESHOLD", "int", "Necha faol do'st uchun", "Taklif qilgan foydalanuvchi har shuncha faol do'st uchun mukofot oladi.", "Do'st taklif qilish", 1, 1000),
    Spec("REFERRAL_REWARD_DAYS", "int", "Taklif qilganga (kun)", "Har bosqich uchun beriladigan Premium kunlari.", "Do'st taklif qilish", 0, 3650),
    Spec("REFERRAL_INVITEE_REWARD_DAYS", "int", "Taklif qilinganga (kun)", "Yangi foydalanuvchi faollashganda oladigan Premium kunlari.", "Do'st taklif qilish", 0, 3650),
    Spec("REFERRAL_ACTIVATION_DAYS", "int", "Faollashish (kun)", "Taklif \"faol\" hisoblanishi uchun nechta turli kunda ish bajarilishi kerak.", "Do'st taklif qilish", 1, 60),
]
SPEC = {s.key: s for s in SPECS}

_cache: dict[str, str] = {}


def default(key: str):
    s = SPEC[key]
    return getattr(config, key, s.default)


def _parse(spec: Spec, raw: str):
    if spec.kind == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    try:
        v = int(raw)
    except ValueError:
        return default(spec.key)
    return max(spec.min, min(spec.max, v))


def get(key: str):
    raw = _cache.get(key)
    if raw is None:
        return default(key)
    return _parse(SPEC[key], raw)


def is_overridden(key: str) -> bool:
    return key in _cache


async def load(db: AsyncSession) -> None:
    rows = (await db.execute(select(AppSetting).where(AppSetting.key.in_(list(SPEC))))).scalars()
    _cache.clear()
    _cache.update({r.key: r.value for r in rows})


def validate(key: str, raw: str | None) -> str:
    """Formadan kelgan qiymat → saqlanadigan matn. Xato bo'lsa ValueError."""
    spec = SPEC[key]
    if spec.kind == "bool":
        return "true" if (raw or "").lower() in ("1", "true", "on", "yes") else "false"
    try:
        v = int((raw or "").strip())
    except ValueError:
        raise ValueError(f"{spec.label}: butun son kiriting") from None
    if not (spec.min <= v <= spec.max):
        raise ValueError(f"{spec.label}: {spec.min}…{spec.max} oralig'ida bo'lsin")
    return str(v)


async def save(db: AsyncSession, values: dict[str, str], by: str) -> list[str]:
    """Faqat o'zgarganlarini yozadi; standart qiymatga teng bo'lsa — yozuvni o'chiradi."""
    changed = []
    for key, val in values.items():
        spec = SPEC[key]
        row = await db.get(AppSetting, key)
        is_default = _parse(spec, val) == default(key)
        if is_default:
            if row is not None:
                await db.delete(row)
                changed.append(key)
            continue
        if row is None:
            db.add(AppSetting(key=key, value=val, updated_by=by))
            changed.append(key)
        elif row.value != val:
            row.value = val
            row.updated_by = by
            changed.append(key)
    await db.commit()
    await load(db)
    return changed
