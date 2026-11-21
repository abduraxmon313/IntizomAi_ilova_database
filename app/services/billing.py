"""Obuna sotib olish — bot backend (Paylov shartnomasi va IP'lar ulangan Railway) orqali.

Oqim:
  1. Ilova → POST /payments/checkout → bu server buyurtma yaratadi va botning
     `POST /partner/v1/orders` API'siga imzolangan so'rov yuboradi.
  2. Bot Paylov'da checkout yaratadi va `checkout_url` qaytaradi → ilova uni ochadi.
  3. Paylov to'lov natijasini BOTGA yuboradi (webhook). Bot buyurtmani "paid" qiladi va
     bu serverning `POST /internal/billing/callback` manziliga imzolangan xabar yuboradi.
  4. Bu server premiumni faollashtiradi. Callback kechiksa, ilova
     GET /payments/{id} ni so'raganda server botdan holatni o'zi tekshiradi.

Ikki tomon ham bir xil HMAC sxemasidan foydalanadi (security.sign).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.i18n import t
from app.models import PaymentOrder, PricingPlan, Promocode, User, utcnow
from app.security import sign
from app.services import premium, pricing, promos, runtime_settings
from app.services.pricing import price_label

log = logging.getLogger(__name__)

PROVIDER_TITLES = {"payme": "Payme", "click": "Click", "uzum": "Uzum Bank", "paylov": "Paylov"}


class BillingError(Exception):
    def __init__(self, message: str, status: int = 0, detail: str | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.code = code


async def _request(method: str, path: str, payload: dict | None = None) -> dict:
    if not config.BILLING_ENABLED:
        raise BillingError("billing disabled")
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ts = str(int(time.time() * 1000))
    headers = {
        "X-Partner-Key": config.BILLING_PARTNER_KEY,
        "X-Timestamp": ts,
        "X-Signature": sign(config.BILLING_PARTNER_SECRET, method, path, ts, body),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.request(method, config.BILLING_BASE_URL + path, content=body or None, headers=headers)
    except httpx.HTTPError as e:
        raise BillingError(f"network: {e}") from e
    data: dict = {}
    try:
        data = r.json() if r.content else {}
    except ValueError:
        pass
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        code = data.get("code") if isinstance(data, dict) else None
        raise BillingError(
            f"billing {r.status_code}",
            r.status_code,
            detail if isinstance(detail, str) else None,
            code if isinstance(code, str) else None,
        )
    return data if isinstance(data, dict) else {}


# ── To'lov usullari va holati botdan (5 daqiqa kesh). Tarif va promokodlar — ilova bazasida. ──
_remote_cache: tuple[float, dict] | None = None


async def remote_status(force: bool = False) -> dict:
    """{providers, payments_enabled, reachable, error}. Bot ishlamasa — oxirgi ma'lum holat."""
    global _remote_cache
    if not config.BILLING_ENABLED:
        return {"providers": [], "payments_enabled": False, "reachable": False, "error": "BILLING_* sozlanmagan"}
    if not force and _remote_cache and time.monotonic() - _remote_cache[0] < 300:
        return _remote_cache[1]
    try:
        data = await _request("GET", "/partner/v1/plans")
    except BillingError as e:
        log.warning("Bot to'lov holatini olishda xato: %s", e)
        if _remote_cache:
            return {**_remote_cache[1], "reachable": False, "error": str(e)}
        return {"providers": [], "payments_enabled": False, "reachable": False, "error": str(e)}
    result = {
        "providers": [
            {"key": k, "title": PROVIDER_TITLES.get(k, k.title())} for k in (data.get("providers") or ["paylov"])
        ],
        "payments_enabled": bool(data.get("payments_enabled", True)),
        "reachable": True,
        "error": None,
        # Faqat admin paneldagi "botdan import" uchun.
        "bot_plans": data.get("plans") or [],
    }
    _remote_cache = (time.monotonic(), result)
    return result


def _monthly(amount: int, days: int) -> int:
    """Oyiga narx: butun oylar soniga bo'linadi (365 kun = 12 oy), 10 so'mga pastga yaxlitlanadi.
    Masalan 179 900 so'm / 12 oy = 14 990 so'm."""
    months = max(1, round(days / 30.4))
    return amount // months // 10 * 10


def _plan_json(plan: PricingPlan, promo: Promocode | None) -> dict:
    q = promos.quote(plan, promo)
    discounted = q.amount < plan.price
    orig = plan.price if discounted else (plan.original_price if plan.original_price and plan.original_price > plan.price else None)
    tag = f"{q.discount_percent}% chegirma" if discounted else (plan.tag or None)
    return {
        "key": plan.key,
        "title": plan.title,
        "days": plan.days,
        "price": q.amount,
        "price_label": price_label(q.amount),
        "monthly_label": price_label(_monthly(q.amount, plan.days)),
        "emoji": plan.emoji,
        "tag": tag,
        "original_price": orig,
        "original_price_label": price_label(orig) if orig else None,
        "bonus_days": q.bonus_days,
    }


async def catalog(db: AsyncSession, user: User, promo_code: str | None = None) -> dict:
    """{plans, providers, promo, payments_enabled} — ilovadagi Premium oynasi uchun."""
    promo_info, promo = None, None
    if promo_code and promo_code.strip():
        try:
            promo = await promos.validate_for_user(db, user, promo_code)
            promo_info = promos.promo_public(promo)
        except promos.PromoError as e:
            promo_info = {"code": promo_code.strip()[:64], "valid": False, "reason_code": e.key, "reason": t(e.key)}
    plans = await pricing.list_plans(db)
    remote = await remote_status()
    return {
        "plans": [_plan_json(p, promo) for p in plans],
        "providers": [{"key": x["key"], "title": x["title"]} for x in remote["providers"]],
        "promo": promo_info,
        "payments_enabled": bool(remote["payments_enabled"] and runtime_settings.get("SALES_ENABLED") and plans),
    }


async def create_remote_order(order: PaymentOrder, user: User, title: str, return_url: str | None) -> dict:
    return await _request(
        "POST",
        "/partner/v1/orders",
        {
            "client_ref": order.public_id,
            "app_user_id": user.id,
            "app_user_label": f"@{user.username}" if user.username else f"id{user.id}",
            "plan_key": order.plan_key,
            "title": title,
            "provider": order.provider,
            # Narx va kunlarni ilova belgilaydi (bot faqat Paylov ko'prigi).
            "amount_tiyin": int(order.amount or 0),
            "days": int(order.days or 0),
            "bonus_days": int(order.bonus_days or 0),
            "return_url": return_url,
        },
    )


async def fetch_bot_promocodes() -> list[dict]:
    data = await _request("GET", "/partner/v1/promocodes")
    return data.get("promocodes") or []


async def fetch_remote_order(external_id: str) -> dict:
    from urllib.parse import quote

    return await _request("GET", "/partner/v1/orders/" + quote(external_id, safe=""))


async def mark_paid(db: AsyncSession, order: PaymentOrder, *, days: int | None, paid_at: datetime | None) -> bool:
    """Buyurtmani to'langan deb belgilaydi va premiumni ochadi. Idempotent."""
    order = await db.scalar(select(PaymentOrder).where(PaymentOrder.id == order.id).with_for_update())
    if order is None or order.status == "paid":
        return False
    user = await db.get(User, order.user_id)
    if user is None:
        return False
    grant = int(order.days or days or 0)
    order.status = "paid"
    order.paid_at = paid_at or utcnow()
    await db.flush()
    sub = await premium.grant_days(
        db, user, grant,
        source="paylov", plan_key=order.plan_key, amount=int(order.amount or 0),
        reference=f"pay:{order.external_id or order.public_id}",
    )
    if sub is None:
        # Premium allaqachon shu to'lov bilan berilgan — faqat buyurtma holatini saqlaymiz.
        await db.commit()
    await promos.record_paid(db, order)
    log.info("To'lov faollashdi: order=%s user=%s days=%s", order.public_id, user.id, grant)
    return True


async def mark_cancelled(db: AsyncSession, order: PaymentOrder) -> None:
    if order.status in ("pending", "creating"):
        order.status = "cancelled"
        await db.commit()


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def sync_from_remote(db: AsyncSession, order: PaymentOrder) -> None:
    """Callback kelmagan bo'lsa — bot'dan holatni so'rab moslashtiradi."""
    if not order.external_id or order.status not in ("pending",):
        return
    order.last_checked_at = utcnow()
    await db.commit()
    try:
        data = await fetch_remote_order(order.external_id)
    except BillingError as e:
        log.info("Buyurtma holatini olishda xato %s: %s", order.public_id, e)
        return
    status = data.get("status")
    if status == "paid":
        await mark_paid(db, order, days=data.get("days"), paid_at=parse_ts(data.get("paid_at")))
    elif status == "cancelled":
        await mark_cancelled(db, order)
