from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.db import get_session
from app.errors import fail
from app.models import PaymentOrder, User, utcnow
from app.security import limit_action, onboarded_user, verify_signature
from app.services import billing, premium, pricing, promos
from app.services import runtime_settings as rs

log = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])

# Bot qaytaradigan xato kodlari → i18n kalitlari.
REMOTE_ERRORS = {
    "promo_not_found": "promo_not_found",
    "promo_inactive": "promo_inactive",
    "promo_expired": "promo_expired",
    "promo_exhausted": "promo_exhausted",
    "promo_already_used": "promo_already_used",
    "promo_not_free": "promo_not_free",
    "promo_free_at_checkout": "promo_free_at_checkout",
    "plan_unknown": "plan_unknown",
    "provider_unknown": "provider_unknown",
}


class CheckoutIn(BaseModel):
    plan: str = Field(max_length=16)
    provider: str = Field(default="paylov", max_length=24)
    promo_code: str | None = Field(default=None, max_length=64)


class PromoIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)


def _remote_fail(e: billing.BillingError):
    key = REMOTE_ERRORS.get(e.code or "")
    if key and e.status in (400, 404, 409, 422):
        fail(422, key)
    fail(502, "billing_failed")


async def _status(db: AsyncSession, user: User, plan_key: str | None) -> dict:
    active = premium.is_premium(user)
    until = user.premium_until
    plan = await pricing.get_plan(db, plan_key, active_only=False) if plan_key else None
    return {
        "is_premium": active,
        "premium_until": until.astimezone(config.TIMEZONE).date().isoformat() if until and active else None,
        "days_left": premium.days_left(user),
        "plan": plan_key,
        "plan_title": plan.title if plan else premium.PLAN_TITLES.get(plan_key or ""),
    }


@router.get("/subscription")
async def subscription(
    promo_code: str | None = None, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    if promo_code:
        limit_action(f"promo-check:{user.id}", 20, 600)
    visible = premium.purchases_visible()
    if not visible:
        # iOS'da sotuv yopiq: tariflar, to'lov usullari va limitlar qaytarilmaydi.
        return {
            **await _status(db, user, await premium.current_plan_key(db, user)),
            "plans": [],
            "providers": [],
            "promo": None,
            "payments_enabled": False,
            "purchases_visible": False,
            "free_limits": {"plans_per_day": -1, "habits": -1, "goals": -1, "ai_per_day": -1},
        }
    cat = await billing.catalog(db, user, promo_code)
    return {
        **await _status(db, user, await premium.current_plan_key(db, user)),
        **cat,
        "purchases_visible": True,
        "free_limits": {
            "plans_per_day": rs.get("FREE_DAILY_PLAN_LIMIT"),
            "habits": rs.get("FREE_HABIT_LIMIT"),
            "goals": rs.get("FREE_GOAL_LIMIT"),
            "ai_per_day": rs.get("FREE_AI_DAILY_LIMIT"),
        },
    }


async def _order_json(db: AsyncSession, o: PaymentOrder, user: User | None = None) -> dict:
    som = (o.amount or 0) // 100
    out = {
        "order_id": o.public_id,
        "status": o.status,
        "plan_key": o.plan_key,
        "provider": o.provider,
        "days": o.days,
        "amount": som,
        "amount_label": billing.price_label(som) if o.amount else None,
        "checkout_url": o.checkout_url if o.status == "pending" else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
    }
    if user is not None:
        out["subscription"] = await _status(db, user, o.plan_key if o.status == "paid" else None)
    return out


@router.post("/payments/checkout")
async def checkout(body: CheckoutIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    if not premium.purchases_visible():
        fail(403, "purchases_unavailable")
    if not config.BILLING_ENABLED:
        fail(503, "billing_disabled")
    if not rs.get("SALES_ENABLED"):
        fail(503, "sales_paused")
    limit_action(f"checkout:{user.id}", 10, 600)
    plan = await pricing.get_plan(db, body.plan)
    if plan is None:
        fail(422, "plan_unknown")
    remote = await billing.remote_status()
    provider = body.provider.strip().lower()
    if remote["providers"] and provider not in {p["key"] for p in remote["providers"]}:
        fail(422, "provider_unknown")

    promo = None
    code = (body.promo_code or "").strip()
    if code:
        try:
            promo = await promos.validate_for_user(db, user, code)
        except promos.PromoError as e:
            fail(422, e.key)
        if promo.kind == "free":
            fail(422, "promo_free_at_checkout")
        if not promos.applies_to(promo, plan.key):
            fail(422, "promo_plan_mismatch")
    q = promos.quote(plan, promo)
    amount = q.amount * 100

    # Ikki marta bosilsa — yaqindagi xuddi shunday kutilayotgan buyurtmani qaytaramiz.
    recent = await db.scalar(
        select(PaymentOrder)
        .where(
            PaymentOrder.user_id == user.id,
            PaymentOrder.status == "pending",
            PaymentOrder.plan_key == plan.key,
            PaymentOrder.provider == provider,
            PaymentOrder.amount == amount,
            PaymentOrder.days == q.days,
            PaymentOrder.promo_id.is_(None) if promo is None else PaymentOrder.promo_id == promo.id,
            PaymentOrder.created_at > utcnow() - timedelta(minutes=15),
            PaymentOrder.checkout_url.is_not(None),
        )
        .order_by(PaymentOrder.id.desc())
        .limit(1)
    )
    if recent is not None:
        return await _order_json(db, recent)

    order = PaymentOrder(
        public_id="ap" + secrets.token_hex(12),
        user_id=user.id,
        plan_key=plan.key,
        provider=provider,
        promo_code=promo.code if promo else None,
        promo_id=promo.id if promo else None,
        amount=amount,
        original_amount=q.original * 100,
        days=q.days,
        bonus_days=q.bonus_days,
        status="creating",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    return_url = f"{config.PUBLIC_BASE_URL}/pay/return?o={order.public_id}" if config.PUBLIC_BASE_URL else None
    try:
        data = await billing.create_remote_order(order, user, plan.title, return_url)
    except billing.BillingError as e:
        order.status = "failed"
        order.error = (e.detail or str(e))[:300]
        await db.commit()
        log.warning("Checkout xato user=%s: %s", user.id, e)
        _remote_fail(e)

    if not data.get("checkout_url") or not data.get("external_id"):
        order.status = "failed"
        order.error = "no checkout_url"
        await db.commit()
        fail(502, "billing_failed")
    if int(data.get("amount") or 0) != amount or int(data.get("days") or 0) != q.days:
        # Bot eski versiyada (narxni o'zi hisoblayapti) — foydalanuvchi boshqa summa to'lamasin.
        order.status = "failed"
        order.error = f"bot summa/kun mos emas: {data.get('amount')}/{data.get('days')}"[:300]
        await db.commit()
        log.error("Bot partner API ilova narxini qabul qilmadi (bot yangilanmagan?): %s", order.error)
        fail(502, "billing_failed")
    order.external_id = str(data["external_id"])
    order.checkout_url = str(data["checkout_url"])
    order.status = "pending"
    await db.commit()
    return await _order_json(db, order)


@router.get("/payments/{order_id}")
async def payment_status(order_id: str, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    order = await db.scalar(
        select(PaymentOrder).where(PaymentOrder.public_id == order_id[:40], PaymentOrder.user_id == user.id)
    )
    if order is None:
        fail(404, "order_not_found")
    last = order.last_checked_at
    if order.status == "pending" and (last is None or utcnow() - last > timedelta(seconds=5)):
        await billing.sync_from_remote(db, order)
        await db.refresh(order)
    await db.refresh(user)
    return await _order_json(db, order, user)


@router.post("/promo/redeem")
async def redeem_promo(body: PromoIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    """Bepul promokod — to'lovsiz Premium kunlari. Chegirma/bonus promokodi checkout'da qo'llanadi."""
    if not premium.purchases_visible():
        fail(403, "purchases_unavailable")
    limit_action(f"promo:{user.id}", 10, 3600)
    try:
        days = await promos.redeem_free(db, user, body.code)
    except promos.PromoError as e:
        fail(409 if e.key == "promo_already_used" else 422, e.key)
    await db.refresh(user)
    return {"days": days, "subscription": await _status(db, user, await premium.current_plan_key(db, user))}


# ─────────────────────────────────────────────────────────────
#  Bot backend → shu server: to'lov natijasi (imzolangan)
# ─────────────────────────────────────────────────────────────
@router.post("/internal/billing/callback", include_in_schema=False)
async def billing_callback(request: Request, db: AsyncSession = Depends(get_session)):
    raw = await request.body()
    valid = (
        config.BILLING_ENABLED
        and request.headers.get("x-partner-key") == config.BILLING_PARTNER_KEY
        and verify_signature(
            config.BILLING_PARTNER_SECRET,
            "POST",
            request.url.path,
            request.headers.get("x-timestamp", ""),
            raw,
            request.headers.get("x-signature", ""),
        )
    )
    if not valid:
        log.warning("Billing callback imzosi noto'g'ri")
        return JSONResponse(status_code=401, content={"ok": False})
    try:
        p = json.loads(raw or b"{}")
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False})

    order = await db.scalar(select(PaymentOrder).where(PaymentOrder.public_id == str(p.get("client_ref") or "")))
    if order is None or (order.external_id and order.external_id != p.get("external_id")):
        # Noma'lum buyurtma — qayta yuborish foydasiz, 200 qaytaramiz.
        log.warning("Callback: buyurtma topilmadi client_ref=%s", p.get("client_ref"))
        return {"ok": True, "ignored": True}
    if not order.external_id and p.get("external_id"):
        order.external_id = str(p["external_id"])
    if order.amount is None and p.get("amount"):
        order.amount = int(p["amount"])
    if order.days is None and p.get("days"):
        order.days = int(p["days"])

    event = p.get("event")
    if event == "order.paid":
        await billing.mark_paid(db, order, days=p.get("days"), paid_at=billing.parse_ts(p.get("paid_at")))
    elif event == "order.cancelled":
        await billing.mark_cancelled(db, order)
    else:
        await db.commit()
    return {"ok": True}
