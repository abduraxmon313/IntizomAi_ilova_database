from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import fail
from app.i18n import current_lang
from app.models import User
from app.security import limit_action, onboarded_user
from app.services import coach, premium

router = APIRouter(tags=["ai"])


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=40)


@router.post("/ai/chat")
async def chat(body: ChatIn, user: User = Depends(onboarded_user), db: AsyncSession = Depends(get_session)):
    last = body.messages[-1]
    if last.role != "user" or not last.content.strip():
        fail(422, "ai_empty")
    # Narx himoyasi: Premium ham cheksiz, lekin daqiqasiga ko'p emas.
    limit_action(f"ai:{user.id}", 20, 600)

    await db.refresh(user, with_for_update=True)  # parallel so'rovlar limitni chetlab o'tmasin
    lim = premium.ai_limit_peek(user)
    if not lim.allowed:
        fail(402, "ai_limit", limit=lim.limit)
    premium.ai_consume(user)
    await db.commit()

    try:
        ctx = await coach.build_context(db, user)
        text = await coach.reply(ctx, [m.model_dump() for m in body.messages], current_lang.get())
    except coach.CoachUnavailable:
        # AI ishlamasa — bepul xabar sarflanmaydi.
        premium.ai_refund(user)
        await db.commit()
        fail(503, "ai_unavailable")

    after = premium.ai_limit_peek(user)
    return {
        "reply": text,
        "is_premium": premium.is_premium(user),
        "remaining": after.remaining,
        "limit": after.limit,
    }
