"""Brauzerda ochiladigan sahifalar: maxfiylik siyosati, to'lovdan qaytish, taklif havolasi."""
from __future__ import annotations

import html
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.db import get_session
from app.models import User
from app.services.users import display_name, normalize_username

router = APIRouter(include_in_schema=False)
STATIC = Path(__file__).resolve().parent.parent / "static"

_BASE_CSS = """
:root{color-scheme:light dark}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:520px;margin:0 auto;
padding:48px 22px;line-height:1.55;color:#141414;background:#fff;text-align:center}
@media (prefers-color-scheme:dark){body{color:#ececec;background:#0f1115}.muted{color:#9aa0a6}}
h1{font-size:1.45rem;margin:.6em 0 .3em}.muted{color:#5f6368;font-size:.95rem}
.btn{display:block;margin:14px auto 0;max-width:320px;padding:14px 18px;border-radius:14px;background:#111;color:#fff;
text-decoration:none;font-weight:600}.btn.alt{background:transparent;color:inherit;border:1px solid #8884}
.logo{font-size:44px}
"""


def _page(title: str, body: str, extra_head: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{_BASE_CSS}</style>{extra_head}</head><body>{body}</body></html>""",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/health")
async def health(db: AsyncSession = Depends(get_session)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/privacy")
async def privacy():
    return HTMLResponse(
        (STATIC / "privacy.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/pay/return")
async def pay_return(o: str = ""):
    """Paylov to'lovdan keyin shu yerga qaytaradi. Sahifa ilovani ochishni taklif qiladi;
    premium holatini ilova o'zi serverdan tekshiradi (bu sahifa hech narsani tasdiqlamaydi)."""
    order = "".join(ch for ch in o[:40] if ch.isalnum())
    deep = f"{config.APP_SCHEME}://payment?order={order}"
    body = f"""
<div class="logo">✅</div>
<h1>To'lov oynasi yopildi</h1>
<p class="muted">IntizomAI ilovasiga qayting — to'lov holati avtomatik tekshiriladi va Premium bir necha soniyada ochiladi.</p>
<a class="btn" href="{deep}">Ilovaga qaytish</a>
<p class="muted" style="margin-top:28px">Вернитесь в приложение IntizomAI — статус оплаты проверится автоматически.</p>
<script>setTimeout(function(){{location.href={deep!r};}},400);</script>
"""
    return _page("IntizomAI — to'lov", body)


@router.get("/i/{username}")
async def invite_landing(username: str, db: AsyncSession = Depends(get_session)):
    """Do'st taklif havolasi: ilovani yuklab olish va ro'yxatdan o'tishda username'ni kiritish."""
    u = normalize_username(username)[:20]
    user = await db.scalar(select(User).where(User.username == u, User.onboarded.is_(True)))
    who = html.escape(display_name(user)) if user else "Do'stingiz"
    handle = html.escape(u)
    links = ""
    if config.APP_STORE_URL:
        links += f'<a class="btn" href="{html.escape(config.APP_STORE_URL)}">App Store</a>'
    if config.PLAY_STORE_URL:
        links += f'<a class="btn alt" href="{html.escape(config.PLAY_STORE_URL)}">Google Play</a>'
    body = f"""
<div class="logo">🌱</div>
<h1>{who} sizni IntizomAI'ga taklif qildi</h1>
<p class="muted">Rejalar, odatlar va AI murabbiy — bitta ilovada. Ro'yxatdan o'tishda
«Kim taklif qildi?» maydoniga <b>@{handle}</b> deb yozing — ikkalangiz ham Premium kunlariga ega bo'lasiz.</p>
{links}
"""
    return _page("IntizomAI — taklif", body)
