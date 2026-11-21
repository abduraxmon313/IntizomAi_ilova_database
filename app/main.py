"""IntizomAI mobil ilova backend'i (FastAPI).

Bot backend'idan alohida: o'z ma'lumotlar bazasi, o'z foydalanuvchilari
(Google/Apple orqali). Faqat obuna sotib olishda bot backend'ga murojaat qiladi.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import admin, config, jobs
from app.i18n import current_lang, pick_lang, t
from app.routers import ai, auth, billing, goals, groups, habits, pages, plans, profile, stats
from app.security import rate_key, rate_limited

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("intizom")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    for w in config.validate():
        log.warning("⚠️ %s", w)
    task = asyncio.create_task(jobs.loop()) if config.RUN_JOBS else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _startup() -> None:
    """Standart tariflar, admin paneldagi sozlamalar va birinchi admin (env orqali)."""
    from app.admin import auth as admin_auth
    from app.db import SessionLocal
    from app.services import pricing, runtime_settings

    try:
        async with SessionLocal() as db:
            await pricing.ensure_defaults(db)
            await runtime_settings.load(db)
            await admin_auth.bootstrap(db)
    except Exception:
        log.exception("Ishga tushish vazifalari bajarilmadi (migratsiya qo'llanganmi?)")


app = FastAPI(
    title="IntizomAI App API",
    lifespan=lifespan,
    docs_url=None if config.IS_PRODUCTION else "/docs",
    redoc_url=None,
    openapi_url=None if config.IS_PRODUCTION else "/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=800)


@app.middleware("http")
async def guard(request: Request, call_next):
    current_lang.set(pick_lang(request.headers.get("accept-language")))
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/v1/avatars/"):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > config.MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": t("too_large")})
        key, limit = rate_key(request)
        retry = rate_limited(key, limit)
        if retry:
            return JSONResponse(
                status_code=429, content={"detail": t("too_many")}, headers={"Retry-After": str(retry)}
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if config.IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Ilova `detail`ni matn sifatida ko'rsatadi — ro'yxat emas, tushunarli xabar.
    return JSONResponse(status_code=422, content={"detail": t("invalid_input")})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/admin"):
        from app.admin import error_page

        msg = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Method Not Allowed") else (
            "Sahifa topilmadi." if exc.status_code in (404, 405) else "Xatolik yuz berdi."
        )
        return error_page(request, exc.status_code, msg)
    detail = exc.detail
    if not isinstance(detail, str) or detail in ("Not Found", "Method Not Allowed"):
        detail = t("not_found") if exc.status_code in (404, 405) else t("server_error")
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Kutilmagan xato: %s %s", request.method, request.url.path)
    if request.url.path.startswith("/admin"):
        from app.admin import error_page

        return error_page(request, 500, "Serverda kutilmagan xato. Jurnal (log) yozildi — qayta urinib ko'ring.")
    return JSONResponse(status_code=500, content={"detail": t("server_error")})


API = "/api/v1"
for r in (auth, profile, plans, habits, goals, stats, ai, groups, billing):
    app.include_router(r.router, prefix=API)
app.include_router(pages.router)
admin.install(app)
