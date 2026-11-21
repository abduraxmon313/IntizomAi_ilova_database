"""To'lovlar, promokodlar va tariflar."""
from __future__ import annotations

import csv
import io
import secrets
import string
from datetime import datetime, time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.admin import auth
from app.admin.web import NotFound, Page, fdt, guard, paginate, post_guard, redirect, render, secure_headers
from app.db import get_session
from app.models import PaymentOrder, PricingPlan, Promocode, PromoRedemption, User, utcnow
from app.services import billing, pricing, promos
from app.services.clock import today

router = APIRouter()

ORDER_STATUSES = {
    "pending": "Kutilmoqda",
    "paid": "To'langan",
    "cancelled": "Bekor qilingan",
    "failed": "Xato",
    "creating": "Yaratilmoqda",
}


# ─────────────────────────────────────────────────────────────
#  To'lovlar
# ─────────────────────────────────────────────────────────────
def _orders_query(q: str, status: str):
    stmt = select(PaymentOrder, User).join(User, User.id == PaymentOrder.user_id, isouter=True)
    if status in ORDER_STATUSES:
        stmt = stmt.where(PaymentOrder.status == status)
    q = q.strip()
    if q:
        like = f"%{q.lower()}%"
        conds = [
            PaymentOrder.public_id == q,
            PaymentOrder.external_id == q,
            func.lower(PaymentOrder.promo_code).like(like),
            User.username.like(f"%{q.lower().lstrip('@')}%"),
            func.lower(User.full_name).like(like),
        ]
        if q.lstrip("#").isdigit():
            conds.append(PaymentOrder.user_id == int(q.lstrip("#")))
        stmt = stmt.where(or_(*conds))
    return stmt.order_by(PaymentOrder.id.desc())


@router.get("/payments")
async def payments(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    q, status = request.query_params.get("q", "")[:80], request.query_params.get("status", "")
    page = await paginate(db, _orders_query(q, status), request, {"q": q, "status": status})
    summary = dict((await db.execute(select(PaymentOrder.status, func.count(PaymentOrder.id)).group_by(PaymentOrder.status))).all())
    by_plan = (await db.execute(
        select(PaymentOrder.plan_key, func.count(PaymentOrder.id), func.coalesce(func.sum(PaymentOrder.amount), 0))
        .where(PaymentOrder.status == "paid").group_by(PaymentOrder.plan_key)
    )).all()
    return render(request, "payments.html", ctx, page=page, q=q, status=status, STATUSES=ORDER_STATUSES,
                  summary=summary, by_plan=by_plan)


@router.get("/payments.csv")
async def payments_csv(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    rows = (await db.execute(_orders_query(request.query_params.get("q", "")[:80], request.query_params.get("status", "")).limit(50_000))).all()
    await auth.audit(db, ctx, "payments.export", summary=f"{len(rows)} ta qator")
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["order_id", "external_id", "holat", "foydalanuvchi_id", "username", "tarif", "kun", "summa_som",
                "chegirmasiz_som", "promokod", "to'lov usuli", "yaratilgan", "to'langan"])
    for o, u in rows:
        w.writerow([o.public_id, o.external_id or "", o.status, o.user_id, (u.username if u else "") or "",
                    o.plan_key, o.days or "", (o.amount or 0) // 100, (o.original_amount or o.amount or 0) // 100,
                    o.promo_code or "", o.provider, fdt(o.created_at), fdt(o.paid_at)])
    resp = StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="intizom-payments-{today()}.csv"'})
    secure_headers(resp)
    return resp


async def _order(db: AsyncSession, order_id: int) -> PaymentOrder:
    o = await db.get(PaymentOrder, order_id)
    if o is None:
        raise NotFound("To'lov topilmadi.")
    return o


@router.get("/payments/{order_id}")
async def payment_detail(order_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    o = await _order(db, order_id)
    return render(request, "payment_detail.html", ctx, o=o, u=await db.get(User, o.user_id),
                  plan=await pricing.get_plan(db, o.plan_key, active_only=False), STATUSES=ORDER_STATUSES)


@router.post("/payments/{order_id}/sync")
async def payment_sync(order_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db)
    o = await _order(db, order_id)
    if o.status != "pending" or not o.external_id:
        return redirect(f"/admin/payments/{order_id}", "Faqat kutilayotgan (bot'da yaratilgan) to'lovni tekshirish mumkin.", "warn")
    before = o.status
    await billing.sync_from_remote(db, o)
    await db.refresh(o)
    await auth.audit(db, ctx, "payment.sync", target_type="payment", target_id=o.public_id, summary=f"{before} → {o.status}")
    return redirect(f"/admin/payments/{order_id}", f"Bot'dagi holat tekshirildi: {ORDER_STATUSES.get(o.status, o.status)}.")


@router.post("/payments/{order_id}/mark-paid")
async def payment_mark_paid(order_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    o = await _order(db, order_id)
    reason = f.str("reason", 200)
    if o.status == "paid":
        return redirect(f"/admin/payments/{order_id}", "Bu to'lov allaqachon faollashgan.", "warn")
    if not reason:
        return redirect(f"/admin/payments/{order_id}", "Sababini yozing (masalan: Paylov kabinetida to'lov ko'rindi).", "err")
    if not o.days:
        plan = await pricing.get_plan(db, o.plan_key, active_only=False)
        o.days = plan.days if plan else None
    if not o.days:
        return redirect(f"/admin/payments/{order_id}", "Buyurtmada kunlar soni yo'q.", "err")
    await billing.mark_paid(db, o, days=o.days, paid_at=None)
    await auth.audit(db, ctx, "payment.mark_paid", target_type="payment", target_id=o.public_id, summary=reason)
    return redirect(f"/admin/payments/{order_id}", "To'lov qo'lda faollashtirildi va Premium berildi.")


@router.post("/payments/{order_id}/cancel")
async def payment_cancel(order_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    o = await _order(db, order_id)
    if o.status not in ("pending", "creating"):
        return redirect(f"/admin/payments/{order_id}", "Faqat kutilayotgan to'lovni bekor qilish mumkin.", "warn")
    await billing.mark_cancelled(db, o)
    await auth.audit(db, ctx, "payment.cancel", target_type="payment", target_id=o.public_id)
    return redirect(f"/admin/payments/{order_id}", "To'lov bekor qilindi.", "warn")


# ─────────────────────────────────────────────────────────────
#  Promokodlar
# ─────────────────────────────────────────────────────────────
PROMO_STATUSES = {
    "active": "Faol",
    "scheduled": "Rejalashtirilgan",
    "inactive": "O'chirilgan",
    "expired": "Muddati tugagan",
    "exhausted": "Limit tugagan",
}


def _parse_day(raw: str, end: bool) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Sana formati noto'g'ri.") from None
    return datetime.combine(d, time(23, 59, 59) if end else time(0, 0), tzinfo=config.TIMEZONE)


def _promo_fields(f: auth.Form, plan_keys_all: set[str]) -> tuple[dict, list[str]]:
    errors: list[str] = []
    kind = f.str("kind", 12)
    if kind not in promos.KINDS:
        errors.append("Promokod turini tanlang.")
    days = (f.int("days_free", 0) if kind == "free" else f.int("days_bonus", 0)) or 0
    pct = f.int("discount_percent", 0) or 0
    max_uses = f.int("max_uses", 0) or 0
    if kind == "free" and not (1 <= days <= 3650):
        errors.append("Bepul promokod uchun Premium kunlari 1…3650 bo'lsin.")
    if kind == "bonus" and not (1 <= days <= 3650):
        errors.append("Bonus kunlar 1…3650 bo'lsin.")
    if kind == "discount" and not (1 <= pct <= 99):
        errors.append("Chegirma 1…99% bo'lsin. 100% uchun «Bepul Premium» turini tanlang.")
    if kind == "discount" and not (0 <= days <= 3650):
        errors.append("Qo'shimcha kunlar 0…3650 bo'lsin.")
    if not (0 <= max_uses <= 1_000_000):
        errors.append("Ishlatish limiti 0 (cheksiz) … 1 000 000.")
    keys = [k for k in f.list("plan_keys") if k in plan_keys_all]
    try:
        starts = _parse_day(f.str("starts_at", 10), end=False)
        expires = _parse_day(f.str("expires_at", 10), end=True)
    except ValueError as e:
        errors.append(str(e))
        starts = expires = None
    if starts and expires and expires <= starts:
        errors.append("Tugash sanasi boshlanishidan keyin bo'lsin.")
    return {
        "kind": kind,
        "days": days,
        "discount_percent": pct if kind == "discount" else 0,
        "plan_keys": ",".join(sorted(keys)) or None if kind != "free" else None,
        "max_uses": max_uses,
        "starts_at": starts,
        "expires_at": expires,
        "note": f.str("note", 200) or None,
    }, errors


def _random_code(prefix: str, n: int = 6) -> str:
    alphabet = string.ascii_lowercase.replace("l", "").replace("o", "") + "23456789"
    return f"{prefix}{''.join(secrets.choice(alphabet) for _ in range(n))}"


def _promos_query(q: str, kind: str):
    stmt = select(Promocode)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(Promocode.code.like(like), func.lower(Promocode.note).like(like)))
    if kind in promos.KINDS:
        stmt = stmt.where(Promocode.kind == kind)
    return stmt.order_by(Promocode.created_at.desc(), Promocode.id.desc())


@router.get("/promos")
async def promo_list(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    q, kind, st = request.query_params.get("q", "")[:64], request.query_params.get("kind", ""), request.query_params.get("st", "")
    stmt = _promos_query(q, kind)
    if st in PROMO_STATUSES:
        # Holat vaqtga bog'liq — ro'yxatni Python'da filtrlaymiz (promokodlar soni kichik).
        rows = (await db.execute(stmt.limit(5000))).scalars().all()
        items = [p for p in rows if promos.status(p) == st]
        page = Page(items[:200], 1, 1, len(items), 200, request.url.path, {"q": q, "kind": kind, "st": st})
    else:
        page = await paginate(db, stmt, request, {"q": q, "kind": kind, "st": st}, per_page=30)
    revenue = dict((await db.execute(
        select(PaymentOrder.promo_id, func.coalesce(func.sum(PaymentOrder.amount), 0))
        .where(PaymentOrder.status == "paid", PaymentOrder.promo_id.in_([p.id for p in page.items] or [0]))
        .group_by(PaymentOrder.promo_id)
    )).all())
    return render(request, "promos.html", ctx, page=page, q=q, kind=kind, st=st, KINDS=promos.KINDS,
                  STATUSES=PROMO_STATUSES, status=promos.status, plan_keys=promos.plan_keys, revenue=revenue,
                  billing_enabled=config.BILLING_ENABLED)


@router.get("/promos.csv")
async def promo_csv(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    rows = (await db.execute(_promos_query(request.query_params.get("q", "")[:64], request.query_params.get("kind", "")).limit(20_000))).scalars().all()
    await auth.audit(db, ctx, "promos.export", summary=f"{len(rows)} ta")
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["kod", "turi", "kun", "chegirma_%", "tariflar", "ishlatilgan", "limit", "holat", "boshlanish", "tugash", "izoh"])
    for p in rows:
        w.writerow([p.code.upper(), promos.KINDS[p.kind], p.days, p.discount_percent, p.plan_keys or "hammasi",
                    p.used_count, p.max_uses or "cheksiz", PROMO_STATUSES[promos.status(p)], fdt(p.starts_at), fdt(p.expires_at), p.note or ""])
    resp = StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="intizom-promos-{today()}.csv"'})
    secure_headers(resp)
    return resp


@router.get("/promos/new")
async def promo_new(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    return render(request, "promo_form.html", ctx, p=None, plans=await pricing.list_plans(db, active_only=False),
                  KINDS=promos.KINDS, selected=set(), suggestion=_random_code("", 8).upper())


@router.post("/promos/new")
async def promo_create(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    plans = await pricing.list_plans(db, active_only=False)
    fields, errors = _promo_fields(f, {p.key for p in plans})
    count = f.int("count", 1) or 1
    code = promos.normalize(f.str("code", 40))
    prefix = promos.normalize(f.str("prefix", 20))
    if count > 1:
        if not (2 <= count <= 1000):
            errors.append("Bir yo'la 2…1000 ta kod yaratish mumkin.")
        if prefix and not promos.CODE_RE.match(prefix + "aaa"):
            errors.append("Prefiks: lotin harfi, raqam, - va _.")
    elif not promos.CODE_RE.match(code):
        errors.append("Kod 3–40 belgi: lotin harfi, raqam, - va _.")
    elif await promos.find(db, code):
        errors.append("Bunday promokod allaqachon bor.")
    if errors:
        return render(request, "promo_form.html", ctx, p=None, plans=plans, KINDS=promos.KINDS, errors=errors,
                      form=f, selected=set(f.list("plan_keys")), suggestion=code.upper(), http_code=422)

    created: list[str] = []
    if count == 1:
        db.add(Promocode(code=code, created_by=ctx.admin.username, **fields))
        created.append(code)
    else:
        # Har biri bir martalik (max_uses=1) — sovg'a/aksiya uchun noyob kodlar.
        fields = {**fields, "max_uses": 1, "note": fields["note"] or f"To'plam {utcnow():%d.%m.%Y %H:%M}"}
        existing = set((await db.execute(select(Promocode.code).where(Promocode.code.like(f"{prefix}%")))).scalars())
        while len(created) < count:
            c = _random_code(prefix, 7)
            if c in existing or c in created:
                continue
            created.append(c)
            db.add(Promocode(code=c, created_by=ctx.admin.username, **fields))
    await auth.audit(db, ctx, "promo.create", target_type="promo", target_id=created[0] if count == 1 else prefix or "batch",
                     summary=f"{promos.KINDS[fields['kind']]}: {len(created)} ta" + (f" ({created[0]})" if count == 1 else ""), commit=False)
    await db.commit()
    if count == 1:
        p = await promos.find(db, created[0])
        return redirect(f"/admin/promos/{p.id}", f"Promokod {created[0].upper()} yaratildi.")
    return redirect("/admin/promos?q=" + quote(prefix or fields["note"]), f"{len(created)} ta bir martalik promokod yaratildi.")


async def _promo(db: AsyncSession, promo_id: int) -> Promocode:
    p = await db.get(Promocode, promo_id)
    if p is None:
        raise NotFound("Promokod topilmadi.")
    return p


@router.get("/promos/{promo_id}")
async def promo_detail(promo_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    p = await _promo(db, promo_id)
    redemptions = (await db.execute(
        select(PromoRedemption, User).join(User, User.id == PromoRedemption.user_id, isouter=True)
        .where(PromoRedemption.promo_id == p.id).order_by(PromoRedemption.created_at.desc()).limit(200)
    )).all()
    orders = (await db.execute(
        select(PaymentOrder.status, func.count(PaymentOrder.id), func.coalesce(func.sum(PaymentOrder.amount), 0),
               func.coalesce(func.sum(PaymentOrder.original_amount), 0))
        .where(PaymentOrder.promo_id == p.id).group_by(PaymentOrder.status)
    )).all()
    return render(request, "promo_detail.html", ctx, p=p, redemptions=redemptions, orders=orders,
                  plans=await pricing.list_plans(db, active_only=False), KINDS=promos.KINDS,
                  status=promos.status(p), STATUSES=PROMO_STATUSES, selected=promos.plan_keys(p), STATUS_LABELS=ORDER_STATUSES)


@router.post("/promos/{promo_id}")
async def promo_update(promo_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    p = await _promo(db, promo_id)
    plans = await pricing.list_plans(db, active_only=False)
    fields, errors = _promo_fields(f, {x.key for x in plans})
    if fields["max_uses"] and fields["max_uses"] < p.used_count:
        errors.append(f"Limit {p.used_count} dan kam bo'lmasin (shuncha marta ishlatilgan).")
    if errors:
        return redirect(f"/admin/promos/{promo_id}", " ".join(errors), "err")
    for k, v in fields.items():
        setattr(p, k, v)
    p.is_active = f.bool("is_active")
    await auth.audit(db, ctx, "promo.update", target_type="promo", target_id=p.code, commit=False)
    await db.commit()
    return redirect(f"/admin/promos/{promo_id}", "Promokod yangilandi.")


@router.post("/promos/{promo_id}/toggle")
async def promo_toggle(promo_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    p = await _promo(db, promo_id)
    p.is_active = not p.is_active
    await auth.audit(db, ctx, "promo.on" if p.is_active else "promo.off", target_type="promo", target_id=p.code, commit=False)
    await db.commit()
    back = request.headers.get("referer") or "/admin/promos"
    return redirect(back if "/admin/promos" in back else "/admin/promos",
                    f"{p.code.upper()} " + ("yoqildi." if p.is_active else "faolsizlantirildi."))


@router.post("/promos/{promo_id}/delete")
async def promo_delete(promo_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    p = await _promo(db, promo_id)
    used = await db.scalar(select(func.count(PromoRedemption.id)).where(PromoRedemption.promo_id == p.id))
    if used:
        return redirect(f"/admin/promos/{promo_id}", "Bu promokod ishlatilgan — tarix saqlanishi uchun o'chirib bo'lmaydi. O'rniga uni o'chiring (faolsizlantiring).", "warn")
    code = p.code
    await db.delete(p)
    await auth.audit(db, ctx, "promo.delete", target_type="promo", target_id=code, commit=False)
    await db.commit()
    return redirect("/admin/promos", f"{code.upper()} o'chirildi.", "warn")


@router.post("/promos-import-bot")
async def promo_import(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    try:
        rows = await billing.fetch_bot_promocodes()
    except billing.BillingError as e:
        return redirect("/admin/promos", f"Bot'dan olib bo'lmadi: {e}. Bot yangilanganini tekshiring.", "err")
    have = set((await db.execute(select(Promocode.code))).scalars())
    added = 0
    for r in rows:
        code = promos.normalize(r.get("code"))
        if not code or code in have or not promos.CODE_RE.match(code):
            continue
        pct = int(r.get("discount_percent") or 0)
        days = int(r.get("bonus_days") or 0)
        if r.get("is_free"):
            kind = "free"
        elif pct > 0:
            kind = "discount"
        else:
            kind = "bonus"
        if kind in ("free", "bonus") and days <= 0:
            continue
        expires = billing.parse_ts(r.get("expires_at"))
        db.add(Promocode(
            code=code, kind=kind, days=days, discount_percent=min(99, pct) if kind == "discount" else 0,
            # Bot'da chegirma faqat 1 va 3 oylikka qo'llanardi — shuni saqlaymiz.
            plan_keys="1m,3m" if kind == "discount" else None,
            max_uses=int(r.get("max_uses") or 0), is_active=bool(r.get("is_active", True)),
            expires_at=expires, note="Bot'dan import", created_by=ctx.admin.username,
        ))
        have.add(code)
        added += 1
    await auth.audit(db, ctx, "promo.import_bot", summary=f"{added} ta qo'shildi, bot'da {len(rows)} ta", commit=False)
    await db.commit()
    return redirect("/admin/promos", f"Bot'dan {added} ta promokod ko'chirildi (bot'da jami {len(rows)} ta; mavjudlari o'tkazib yuborildi).")


# ─────────────────────────────────────────────────────────────
#  Tariflar
# ─────────────────────────────────────────────────────────────
@router.get("/plans")
async def plans_page(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    plans = await pricing.list_plans(db, active_only=False)
    sales = dict((await db.execute(
        select(PaymentOrder.plan_key, func.count(PaymentOrder.id)).where(PaymentOrder.status == "paid").group_by(PaymentOrder.plan_key)
    )).all())
    base = min((p for p in plans if p.is_active), key=lambda p: p.days, default=None)
    return render(request, "plans.html", ctx, plans=plans, sales=sales, base=base)


def _plan_form(f: auth.Form) -> tuple[dict, list[str]]:
    title = " ".join(f.str("title", 64).split())
    days = f.int("days", 0) or 0
    price = f.int("price", 0) or 0
    orig = f.int("original_price")
    errors = pricing.validate_fields(title, days, price, orig)
    tag = f.str("tag", 64) or None
    return {
        "title": title, "days": days, "price": price, "original_price": orig or None, "tag": tag,
        "emoji": f.str("emoji", 8) or None, "sort_order": f.int("sort_order", 0) or 0, "is_active": f.bool("is_active"),
    }, errors


@router.post("/plans/new")
async def plan_create(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    key = f.str("key", 16).lower()
    fields, errors = _plan_form(f)
    if not pricing.KEY_RE.match(key):
        errors.append("Kalit: 1–16 belgi, a-z 0-9 _ (masalan 6m).")
    elif await db.get(PricingPlan, key):
        errors.append("Bunday kalitli tarif bor.")
    if errors:
        return redirect("/admin/plans", " ".join(errors), "err")
    db.add(PricingPlan(key=key, **fields))
    await auth.audit(db, ctx, "plan.create", target_type="plan", target_id=key, summary=f"{fields['title']} — {fields['price']} so'm", commit=False)
    await db.commit()
    return redirect("/admin/plans", f"«{fields['title']}» tarifi qo'shildi.")


@router.post("/plans/{key}")
async def plan_update(key: str, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    p = await db.get(PricingPlan, key)
    if p is None:
        raise NotFound("Tarif topilmadi.")
    fields, errors = _plan_form(f)
    if errors:
        return redirect("/admin/plans", f"{p.title}: " + " ".join(errors), "err")
    if not fields["is_active"] and p.is_active:
        others = await db.scalar(select(func.count(PricingPlan.key)).where(PricingPlan.is_active.is_(True), PricingPlan.key != key))
        if not others:
            return redirect("/admin/plans", "Kamida bitta tarif faol qolishi kerak. Sotuvni to'xtatish uchun Sozlamalardan foydalaning.", "err")
    diff = [f"{k}: {getattr(p, k)} → {v}" for k, v in fields.items() if getattr(p, k) != v]
    for k, v in fields.items():
        setattr(p, k, v)
    await auth.audit(db, ctx, "plan.update", target_type="plan", target_id=key, summary="; ".join(diff) or "o'zgarishsiz", commit=False)
    await db.commit()
    return redirect("/admin/plans", f"«{p.title}» saqlandi. Kutilayotgan to'lovlar eski narxda qoladi.")


@router.post("/plans/{key}/delete")
async def plan_delete(key: str, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "owner")
    p = await db.get(PricingPlan, key)
    if p is None:
        raise NotFound("Tarif topilmadi.")
    used = await db.scalar(select(func.count(PaymentOrder.id)).where(PaymentOrder.plan_key == key))
    if used:
        return redirect("/admin/plans", "Bu tarif bilan to'lovlar bor — o'chirish o'rniga uni nofaol qiling.", "warn")
    await db.delete(p)
    await auth.audit(db, ctx, "plan.delete", target_type="plan", target_id=key, commit=False)
    await db.commit()
    return redirect("/admin/plans", "Tarif o'chirildi.", "warn")


@router.post("/plans-import-bot")
async def plans_import(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    remote = await billing.remote_status(force=True)
    if not remote["reachable"]:
        return redirect("/admin/plans", f"Bot bilan aloqa yo'q: {remote['error']}", "err")
    n = 0
    for bp in remote.get("bot_plans") or []:
        key = str(bp.get("key") or "")
        p = await db.get(PricingPlan, key)
        price = int(bp.get("price") or 0)
        if p is None or not (pricing.MIN_PRICE <= price <= pricing.MAX_PRICE):
            continue
        p.price = price
        p.title = (bp.get("title") or p.title)[:64]
        p.days = int(bp.get("days") or p.days)
        p.tag = (bp.get("tag") or None)
        p.original_price = None
        n += 1
    await auth.audit(db, ctx, "plan.import_bot", summary=f"{n} ta tarif yangilandi", commit=False)
    await db.commit()
    return redirect("/admin/plans", f"Bot'dagi narxlardan {n} ta tarif yangilandi.")

