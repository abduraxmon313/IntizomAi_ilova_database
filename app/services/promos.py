"""Promokodlar (ilova bazasida): tekshirish, narxga qo'llash, ishlatilganini yozish."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthIdentity, PaymentOrder, PricingPlan, Promocode, PromoRedemption, User, utcnow
from app.security import as_utc
from app.services import pricing
from app.services.referrals import identity_hash

log = logging.getLogger(__name__)

KINDS = {
    "free": "Bepul Premium",
    "bonus": "Bonus kunlar (sotib olganda)",
    "discount": "Chegirma",
}
CODE_RE = re.compile(r"^[a-z0-9_-]{3,40}$")


class PromoError(Exception):
    """`key` — app/i18n.py kaliti."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def normalize(code: str | None) -> str:
    return (code or "").strip().lower()[:64]


def plan_keys(p: Promocode) -> set[str]:
    return {k for k in (p.plan_keys or "").split(",") if k}


def applies_to(p: Promocode, plan_key: str) -> bool:
    keys = plan_keys(p)
    return not keys or plan_key in keys


def status(p: Promocode) -> str:
    """Admin panel uchun: active | inactive | scheduled | expired | exhausted."""
    now = utcnow()
    if not p.is_active:
        return "inactive"
    if p.starts_at and as_utc(p.starts_at) > now:
        return "scheduled"
    if p.expires_at and as_utc(p.expires_at) <= now:
        return "expired"
    if p.max_uses and p.used_count >= p.max_uses:
        return "exhausted"
    return "active"


_STATUS_ERR = {
    "inactive": "promo_inactive",
    "scheduled": "promo_inactive",
    "expired": "promo_expired",
    "exhausted": "promo_exhausted",
}


async def identity_ref(db: AsyncSession, user: User) -> str:
    """Hisob o'chirib qayta ochilsa ham bir xil — promokodni ikki marta olib bo'lmaydi."""
    ident = await db.scalar(
        select(AuthIdentity).where(AuthIdentity.user_id == user.id).order_by(AuthIdentity.id).limit(1)
    )
    return identity_hash(ident.provider, ident.subject) if ident else f"user:{user.id}"


async def find(db: AsyncSession, code: str) -> Promocode | None:
    norm = normalize(code)
    if not norm:
        return None
    return await db.scalar(select(Promocode).where(Promocode.code == norm))


async def validate_for_user(db: AsyncSession, user: User, code: str) -> Promocode:
    p = await find(db, code)
    if p is None:
        raise PromoError("promo_not_found")
    st = status(p)
    if st != "active":
        raise PromoError(_STATUS_ERR[st])
    ref = await identity_ref(db, user)
    used = await db.scalar(
        select(PromoRedemption.id).where(PromoRedemption.promo_id == p.id, PromoRedemption.identity_ref == ref)
    )
    if used:
        raise PromoError("promo_already_used")
    return p


@dataclass
class Quote:
    amount: int  # so'm, to'lanadigan
    original: int  # so'm, chegirmasiz
    days: int  # jami kunlar (tarif + bonus)
    bonus_days: int
    discount_percent: int


def quote(plan: PricingPlan, promo: Promocode | None) -> Quote:
    amount, bonus, pct = plan.price, 0, 0
    if promo is not None and promo.kind in ("bonus", "discount") and applies_to(promo, plan.key):
        bonus = max(0, promo.days or 0)
        if promo.kind == "discount":
            pct = max(0, min(99, promo.discount_percent or 0))
            amount = pricing.discounted(plan.price, pct)
    return Quote(amount=amount, original=plan.price, days=plan.days + bonus, bonus_days=bonus, discount_percent=pct)


def promo_public(p: Promocode) -> dict:
    """Ilovaga yuboriladigan promokod ma'lumoti (eski format bilan mos)."""
    return {
        "code": p.code.upper(),
        "valid": True,
        "is_free": p.kind == "free",
        "discount_percent": p.discount_percent if p.kind == "discount" else 0,
        "bonus_days": p.days or 0,
        "plan_keys": sorted(plan_keys(p)) or None,
    }


async def redeem_free(db: AsyncSession, user: User, code: str) -> int:
    """Bepul promokod → Premium kunlari. Qaytaradi: qo'shilgan kunlar."""
    from app.services import premium  # aylana importdan qochish

    p = await validate_for_user(db, user, code)
    if p.kind != "free":
        raise PromoError("promo_not_free")
    if (p.days or 0) <= 0:
        raise PromoError("promo_invalid")
    ref = await identity_ref(db, user)
    # Avval limitni atomar band qilamiz: bir vaqtda kelgan so'rovlar limitdan oshmasin.
    res = await db.execute(
        update(Promocode)
        .where(Promocode.id == p.id, (Promocode.max_uses == 0) | (Promocode.used_count < Promocode.max_uses))
        .values(used_count=Promocode.used_count + 1)
    )
    if res.rowcount == 0:
        await db.rollback()
        raise PromoError("promo_exhausted")
    ins = await db.execute(
        pg_insert(PromoRedemption)
        .values(promo_id=p.id, user_id=user.id, identity_ref=ref, days=p.days)
        .on_conflict_do_nothing(constraint="uq_promo_redemption_identity")
        .returning(PromoRedemption.id)
    )
    if ins.scalar() is None:
        await db.rollback()
        raise PromoError("promo_already_used")
    await db.commit()
    await premium.grant_days(db, user, p.days, source="promo", reference=f"promo:{p.code}:{ref}")
    return p.days


async def record_paid(db: AsyncSession, order: PaymentOrder) -> None:
    """To'langan buyurtmadagi promokodni hisobga yozadi. Idempotent; xato to'lovni buzmaydi."""
    if not order.promo_id:
        return
    try:
        user = await db.get(User, order.user_id)
        if user is None:
            return
        ref = await identity_ref(db, user)
        ins = await db.execute(
            pg_insert(PromoRedemption)
            .values(
                promo_id=order.promo_id, user_id=user.id, identity_ref=ref, order_id=order.id,
                days=order.bonus_days or 0, amount=int(order.amount or 0),
            )
            .on_conflict_do_nothing(constraint="uq_promo_redemption_identity")
            .returning(PromoRedemption.id)
        )
        if ins.scalar() is not None:
            await db.execute(
                update(Promocode).where(Promocode.id == order.promo_id).values(used_count=Promocode.used_count + 1)
            )
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception("Promokod hisobini yozib bo'lmadi: order=%s", order.public_id)


async def usage_counts(db: AsyncSession, promo_ids: list[int]) -> dict[int, int]:
    if not promo_ids:
        return {}
    rows = await db.execute(
        select(PromoRedemption.promo_id, func.count(PromoRedemption.id))
        .where(PromoRedemption.promo_id.in_(promo_ids))
        .group_by(PromoRedemption.promo_id)
    )
    return {pid: int(n) for pid, n in rows.all()}
