"""IntizomAI admin paneli: /admin (server tomonida chiziladigan HTML, JavaScript minimal).

Bo'limlar: bosh sahifa, foydalanuvchilar, Premium, to'lovlar, promokodlar, tariflar,
e'lonlar, guruhlar, sozlamalar, adminlar, audit jurnali, tizim holati.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.admin import auth, web
from app.admin.routes_admins import router as admins_router
from app.admin.routes_auth import router as auth_router
from app.admin.routes_billing import router as billing_router
from app.admin.routes_content import router as content_router
from app.admin.routes_main import dashboard, router as main_router
from app.admin.routes_users import router as users_router

router = APIRouter(prefix="/admin", include_in_schema=False)
for r in (auth_router, main_router, users_router, billing_router, content_router, admins_router):
    router.include_router(r)
router.add_api_route("", dashboard, methods=["GET"], include_in_schema=False)


def error_page(request: Request, status: int, message: str):
    ctx = getattr(request.state, "admin_ctx", None)
    return web.render(request, "error.html", ctx, http_code=status, status_code=status, message=message)


def install(app: FastAPI) -> None:
    app.mount("/admin/static", StaticFiles(directory=str(web.BASE / "static")), name="admin-static")
    app.include_router(router)

    @app.exception_handler(auth.LoginRequired)
    async def _login_required(request: Request, exc: auth.LoginRequired):
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        resp = web.redirect(f"/admin/login?next={quote(nxt)}" if request.method == "GET" else "/admin/login",
                            "Sessiya tugagan. Qaytadan kiring." if request.cookies.get(auth.COOKIE) else None, "warn")
        auth.clear_cookie(resp)
        return resp

    @app.exception_handler(web.MustChangePassword)
    async def _must_change(request: Request, exc: web.MustChangePassword):
        return web.redirect("/admin/account", "Davom etishdan oldin vaqtinchalik parolni o'zgartiring.", "warn")

    @app.exception_handler(web.Forbidden)
    async def _forbidden(request: Request, exc: web.Forbidden):
        return error_page(request, exc.status, exc.message)
