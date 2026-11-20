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
from app.models import PaymentOrder, User, utcnow
from app.security import sign
from app.services import premium

log = logging.getLogger(__name__)

DEFAULT_PLANS = [
    {"key": "1m", "title": "1 oy", "days": 30, "price": 39900, "emoji": "✅", "tag": None},
    {"key": "3m", "title": "3 oy", "days": 90, "price": 79900, "emoji": "⭐", "tag": "33% tejaysiz"},
    {"key": "12m", "title": "12 oy", "days": 365, "price": 179900, "emoji": "💎", "tag": None},
]
PROVIDER_TITLES = {"payme": "Payme", "click": "Click", "uzum": "Uzum Bank", "paylov": "Paylov"}


class BillingError(Exception):
    def __init__(self, message: str, status: int = 0, detail: str | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.code = code


def price_label(som: int) -> str:
    return f"{som:,}".replace(",", " ") + " so'm"


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


# ── Tariflar (5 daqiqa kesh — narxni admin botda o'zgartiradi) ──
_plans_cache: tuple[float, dict] | None = None


def _decorate(plans: list[dict]) -> list[dict]:
    out = []
    for p in plans:
        price = int(p.get("price") or 0)
        orig = p.get("original_price")
        orig = int(orig) if orig and int(orig) != price else None
        out.append(
            {
                "key": p["key"],
                "title": p.get("title") or p["key"],
                "days": int(p.get("days") or 0),
                "price": price,
                "price_label": price_label(price),
                "monthly_label": price_label(price * 30 // max(1, int(p.get("days") or 30)) // 100 * 100),
                "emoji": p.get("emoji"),
                "tag": p.get("tag") or None,
                "original_price": orig,
                "original_price_label": price_label(orig) if orig else None,
            }
        )
    return out


async def catalog(promo_code: str | None = None) -> dict:
    """{plans, providers, promo, payments_enabled}. Bot ishlamasa — default katalog (sotib olish o'chiq)."""
    global _plans_cache
    if not config.BILLING_ENABLED:
        return {"plans": _decorate(DEFAULT_PLANS), "providers": [], "promo": None, "payments_enabled": False}
    if not promo_code and _plans_cache and time.monotonic() - _plans_cache[0] < 300:
        return _plans_cache[1]
    path = "/partner/v1/plans"
    if promo_code:
        from urllib.parse import quote

        path += "?promo_code=" + quote(promo_code.strip()[:64])
    try:
        data = await _request("GET", path)
    except BillingError as e:
        log.warning("Tariflarni olishda xato: %s", e)
        if _plans_cache:
            return _plans_cache[1]
        return {"plans": _decorate(DEFAULT_PLANS), "providers": [], "promo": None, "payments_enabled": False}
    result = {
        "plans": _decorate(data.get("plans") or DEFAULT_PLANS),
        "providers": [
            {"key": k, "title": PROVIDER_TITLES.get(k, k.title())}
            for k in (data.get("providers") or ["paylov"])
        ],
        "promo": data.get("promo"),
        "payments_enabled": bool(data.get("payments_enabled", True)),
    }
    if not promo_code:
        _plans_cache = (time.monotonic(), result)
    return result


async def create_remote_order(order: PaymentOrder, user: User, return_url: str | None) -> dict:
    return await _request(
        "POST",
        "/partner/v1/orders",
        {
            "client_ref": order.public_id,
            "app_user_id": user.id,
            "app_user_label": f"@{user.username}" if user.username else f"id{user.id}",
            "plan_key": order.plan_key,
            "provider": order.provider,
            "promo_code": order.promo_code,
            "return_url": return_url,
        },
    )


async def fetch_remote_order(external_id: str) -> dict:
    from urllib.parse import quote

    return await _request("GET", "/partner/v1/orders/" + quote(external_id, safe=""))


async def redeem_remote_promo(code: str, user: User, customer_ref: str) -> dict:
    return await _request(
        "POST",
        "/partner/v1/promo/redeem",
        {"code": code.strip()[:64], "app_user_id": user.id, "customer_ref": customer_ref},
    )


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
