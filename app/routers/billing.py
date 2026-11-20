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
from app.models import AuthIdentity, PaymentOrder, User, utcnow
from app.security import limit_action, onboarded_user, verify_signature
from app.services import billing, premium
from app.services.referrals import identity_hash

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


def _status(user: User, plan_key: str | None) -> dict:
    active = premium.is_premium(user)
    until = user.premium_until
    return {
        "is_premium": active,
        "premium_until": until.astimezone(config.TIMEZONE).date().isoformat() if until and active else None,
        "days_left": premium.days_left(user),
        "plan": plan_key,
        "plan_title": premium.PLAN_TITLES.get(plan_key or ""),
    }


@router.get("/subscription")
async def subscription(
    promo_code: str | None = None, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)
):
    if promo_code:
        limit_action(f"promo-check:{user.id}", 20, 600)
    cat = await billing.catalog(promo_code)
    return {
        **_status(user, await premium.current_plan_key(db, user)),
        **cat,
        "free_limits": {
            "plans_per_day": config.FREE_DAILY_PLAN_LIMIT,
            "habits": config.FREE_HABIT_LIMIT,
            "goals": config.FREE_GOAL_LIMIT,
            "ai_per_day": config.FREE_AI_DAILY_LIMIT,
        },
    }


def _order_json(o: PaymentOrder, user: User | None = None) -> dict:
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
        out["subscription"] = _status(user, o.plan_key if o.status == "paid" else None)
    return out


@router.post("/payments/checkout")
async def checkout(body: CheckoutIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    if not config.BILLING_ENABLED:
        fail(503, "billing_disabled")
    limit_action(f"checkout:{user.id}", 10, 600)
    cat = await billing.catalog()
    if body.plan not in {p["key"] for p in cat["plans"]}:
        fail(422, "plan_unknown")
    provider = body.provider.strip().lower()
    if cat["providers"] and provider not in {p["key"] for p in cat["providers"]}:
        fail(422, "provider_unknown")
    promo = (body.promo_code or "").strip() or None

    # Ikki marta bosilsa — yaqindagi xuddi shunday kutilayotgan buyurtmani qaytaramiz.
    recent = await db.scalar(
        select(PaymentOrder)
        .where(
            PaymentOrder.user_id == user.id,
            PaymentOrder.status == "pending",
            PaymentOrder.plan_key == body.plan,
            PaymentOrder.provider == provider,
            PaymentOrder.promo_code.is_(None) if promo is None else PaymentOrder.promo_code == promo,
            PaymentOrder.created_at > utcnow() - timedelta(minutes=15),
            PaymentOrder.checkout_url.is_not(None),
        )
        .order_by(PaymentOrder.id.desc())
        .limit(1)
    )
    if recent is not None:
        return _order_json(recent)

    order = PaymentOrder(
        public_id="ap" + secrets.token_hex(12),
        user_id=user.id,
        plan_key=body.plan,
        provider=provider,
        promo_code=promo,
        status="creating",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    return_url = f"{config.PUBLIC_BASE_URL}/pay/return?o={order.public_id}" if config.PUBLIC_BASE_URL else None
    try:
        data = await billing.create_remote_order(order, user, return_url)
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
    order.external_id = str(data["external_id"])
    order.checkout_url = str(data["checkout_url"])
    order.amount = int(data.get("amount") or 0)
    order.days = int(data.get("days") or 0) or None
    order.status = "pending"
    await db.commit()
    return _order_json(order)


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
    return _order_json(order, user)


@router.post("/promo/redeem")
async def redeem_promo(body: PromoIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    """Bepul ("-" turidagi) promokod — to'lovsiz Premium kunlari. Chegirma promokodi checkout'da qo'llanadi."""
    if not config.BILLING_ENABLED:
        fail(503, "billing_disabled")
    limit_action(f"promo:{user.id}", 10, 3600)
    ident = await db.scalar(
        select(AuthIdentity).where(AuthIdentity.user_id == user.id).order_by(AuthIdentity.id).limit(1)
    )
    # Hisob o'chirib qayta ochilsa ham bir xil — bir promokodni ikki marta olib bo'lmaydi.
    customer_ref = identity_hash(ident.provider, ident.subject) if ident else f"user:{user.id}"
    try:
        data = await billing.redeem_remote_promo(body.code, user, customer_ref)
    except billing.BillingError as e:
        _remote_fail(e)
    days = int(data.get("days") or 0)
    if days <= 0:
        fail(422, "promo_invalid")
    code = str(data.get("code") or body.code).lower()
    await premium.grant_days(db, user, days, source="promo", reference=f"promo:{code}:{customer_ref}")
    await db.refresh(user)
    return {"days": days, "subscription": _status(user, await premium.current_plan_key(db, user))}


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
