"""Tariflar (ilova bazasida). Admin panelda narx, nom, kun va belgi o'zgartiriladi."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingPlan

DEFAULT_PLANS = [
    {"key": "1m", "title": "1 oy", "days": 30, "price": 39900, "emoji": "✅", "tag": None, "sort_order": 1},
    {"key": "3m", "title": "3 oy", "days": 90, "price": 79900, "emoji": "⭐", "tag": "33% tejaysiz", "sort_order": 2},
    {"key": "12m", "title": "12 oy", "days": 365, "price": 179900, "emoji": "💎", "tag": None, "sort_order": 3},
]
KEY_RE = re.compile(r"^[a-z0-9_]{1,16}$")
MIN_PRICE = 1_000  # so'm — Paylov uchun ma'noli eng kichik summa
MAX_PRICE = 100_000_000


def price_label(som: int) -> str:
    return f"{som:,}".replace(",", " ") + " so'm"


def discounted(price: int, percent: int) -> int:
    """Chegirmali narx, 100 so'mga pastga yaxlitlanadi (botdagidek), lekin MIN_PRICE dan kam emas."""
    if percent <= 0:
        return price
    v = price * (100 - percent) // 100 // 100 * 100
    return max(MIN_PRICE, v)


async def ensure_defaults(db: AsyncSession) -> None:
    """Jadval bo'sh bo'lsa standart tariflarni yozadi (birinchi ishga tushish)."""
    if await db.scalar(select(PricingPlan.key).limit(1)):
        return
    for p in DEFAULT_PLANS:
        db.add(PricingPlan(**p, is_active=True))
    await db.commit()


async def list_plans(db: AsyncSession, *, active_only: bool = True) -> list[PricingPlan]:
    q = select(PricingPlan).order_by(PricingPlan.sort_order, PricingPlan.days)
    if active_only:
        q = q.where(PricingPlan.is_active.is_(True))
    return list((await db.execute(q)).scalars())


async def get_plan(db: AsyncSession, key: str, *, active_only: bool = True) -> PricingPlan | None:
    p = await db.get(PricingPlan, key)
    if p is None or (active_only and not p.is_active):
        return None
    return p


def validate_fields(title: str, days: int, price: int, original_price: int | None) -> list[str]:
    errors = []
    if not title.strip():
        errors.append("Nom bo'sh bo'lmasin.")
    if not (1 <= days <= 3650):
        errors.append("Kunlar 1…3650 oralig'ida bo'lsin.")
    if not (MIN_PRICE <= price <= MAX_PRICE):
        errors.append(f"Narx {price_label(MIN_PRICE)} … {price_label(MAX_PRICE)} oralig'ida bo'lsin.")
    if original_price is not None and original_price <= price:
        errors.append("Eski narx joriy narxdan katta bo'lishi kerak (yoki bo'sh qoldiring).")
    return errors
