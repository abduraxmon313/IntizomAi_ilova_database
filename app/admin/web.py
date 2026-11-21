"""Admin panel: shablonlar, flash xabarlar, ruxsat tekshiruvi, sahifalash, formatlash."""
from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.admin import auth
from app.security import as_utc
from app.models import utcnow

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
FLASH = "iz_flash"

CSP = (
    "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


class Forbidden(Exception):
    status = 403

    def __init__(self, message: str = "Bu bo'lim uchun ruxsatingiz yo'q."):
        self.message = message


class NotFound(Forbidden):
    def __init__(self, message: str = "Topilmadi."):
        super().__init__(message)
        self.status = 404


class MustChangePassword(Exception):
    """Admin yaratgan vaqtinchalik parol bilan kirilgan — avval yangi parol o'rnatiladi."""


# ─────────────────────────────────────────────────────────────
#  Formatlash (shablon filtrlari)
# ─────────────────────────────────────────────────────────────
def num(v) -> str:
    try:
        return f"{int(v):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def som(v) -> str:
    return "—" if v is None else f"{num(v)} so'm"


def tiyin(v) -> str:
    return "—" if v is None else som(int(v) // 100)


def local(dt: datetime | None) -> datetime | None:
    dt = as_utc(dt)
    return dt.astimezone(config.TIMEZONE) if dt else None


def fdt(dt: datetime | None) -> str:
    d = local(dt)
    return d.strftime("%d.%m.%Y %H:%M") if d else "—"


def fdate(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, datetime):
        v = local(v).date()
    return v.strftime("%d.%m.%Y") if isinstance(v, date) else str(v)


def ymd(dt: datetime | None) -> str:
    d = local(dt)
    return d.strftime("%Y-%m-%d") if d else ""


def dtlocal(dt: datetime | None) -> str:
    d = local(dt)
    return d.strftime("%Y-%m-%dT%H:%M") if d else ""


def ago(dt: datetime | None) -> str:
    dt = as_utc(dt)
    if dt is None:
        return "—"
    s = int((utcnow() - dt).total_seconds())
    if s < 0:
        s = -s
        for unit, n in (("kun", 86400), ("soat", 3600), ("daqiqa", 60)):
            if s >= n:
                return f"{s // n} {unit}dan keyin"
        return "hozir"
    for unit, n in (("kun", 86400), ("soat", 3600), ("daqiqa", 60)):
        if s >= n:
            return f"{s // n} {unit} oldin"
    return "hozirgina"


templates.env.filters.update(num=num, som=som, tiyin=tiyin, fdt=fdt, fdate=fdate, ago=ago, ymd=ymd, dtlocal=dtlocal)
templates.env.globals.update(ROLE_LABELS=auth.ROLE_LABELS)


# ─────────────────────────────────────────────────────────────
#  Flash
# ─────────────────────────────────────────────────────────────
def flash(response, message: str, kind: str = "ok") -> None:
    raw = base64.urlsafe_b64encode(json.dumps([kind, message[:300]]).encode()).decode()
    response.set_cookie(FLASH, raw, max_age=60, httponly=True, samesite="strict", secure=config.IS_PRODUCTION, path="/admin")


def _take_flash(request: Request):
    raw = request.cookies.get(FLASH)
    if not raw:
        return None
    try:
        kind, msg = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return {"kind": kind if kind in ("ok", "err", "warn") else "ok", "msg": str(msg)}
    except (ValueError, TypeError):
        return None


def redirect(url: str, message: str | None = None, kind: str = "ok") -> RedirectResponse:
    r = RedirectResponse(url, status_code=303)
    if message:
        flash(r, message, kind)
    return r


# ─────────────────────────────────────────────────────────────
#  Ruxsat
# ─────────────────────────────────────────────────────────────
async def guard(request: Request, db: AsyncSession, role: str = "support") -> auth.Ctx:
    ctx = await auth.resolve(request, db)
    if ctx.admin.must_change_password and not request.url.path.startswith("/admin/account"):
        raise MustChangePassword()
    if not ctx.can(role):
        raise Forbidden()
    request.state.admin_ctx = ctx
    return ctx


async def post_guard(request: Request, db: AsyncSession, role: str = "support") -> tuple[auth.Ctx, auth.Form]:
    ctx = await guard(request, db, role)
    form = await auth.check_csrf(request, ctx)
    return ctx, form


def render(request: Request, name: str, ctx: auth.Ctx | None = None, http_code: int = 200, **data) -> HTMLResponse:
    fl = _take_flash(request)
    resp = templates.TemplateResponse(
        request,
        name,
        {"ctx": ctx, "csrf": ctx.csrf if ctx else "", "flash": fl, "path": request.url.path, **data},
        status_code=http_code,
    )
    if fl:
        resp.delete_cookie(FLASH, path="/admin")
    secure_headers(resp)
    return resp


def secure_headers(resp) -> None:
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"


# ─────────────────────────────────────────────────────────────
#  Sahifalash
# ─────────────────────────────────────────────────────────────
@dataclass
class Page:
    items: list
    page: int
    pages: int
    total: int
    per_page: int
    base: str
    params: dict

    def url(self, page: int) -> str:
        q = {k: v for k, v in self.params.items() if v not in (None, "")}
        q["page"] = page
        return f"{self.base}?{urlencode(q)}"


async def paginate(db: AsyncSession, query, request: Request, params: dict, per_page: int = 25) -> Page:
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    total = int(await db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    rows = (await db.execute(query.limit(per_page).offset((page - 1) * per_page))).all()
    items = [r[0] if len(r) == 1 else r for r in rows]
    return Page(items, page, pages, total, per_page, request.url.path, params)
