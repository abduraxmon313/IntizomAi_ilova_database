"""Hisobni butunlay o'chirish (App Store 5.1.1(v), Google Play talabi)."""
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthIdentity, Group, GroupMember, GroupPermission, User
from app.security import unseal
from app.services import oauth

log = logging.getLogger(__name__)


async def delete_account(db: AsyncSession, user: User) -> None:
    # 1) Egasi bo'lgan guruhlar: eng eski faol a'zoga topshiriladi, a'zo bo'lmasa o'chiriladi.
    owned = list((await db.execute(select(Group).where(Group.owner_user_id == user.id))).scalars())
    for g in owned:
        heir = await db.scalar(
            select(GroupMember)
            .where(GroupMember.group_id == g.id, GroupMember.user_id != user.id)
            .order_by(GroupMember.is_active.desc(), GroupMember.joined_at)
            .limit(1)
        )
        if heir is None:
            await db.delete(g)
            continue
        g.owner_user_id = heir.user_id
        heir.role = "owner"
        heir.is_active = True
    await db.execute(
        delete(GroupPermission).where(
            (GroupPermission.grantor_user_id == user.id) | (GroupPermission.grantee_user_id == user.id)
        )
    )

    # 2) Apple kirishini bekor qilish (sozlangan bo'lsa).
    tokens = (
        await db.execute(
            select(AuthIdentity.apple_refresh_token).where(
                AuthIdentity.user_id == user.id, AuthIdentity.provider == "apple"
            )
        )
    ).scalars()
    for sealed in tokens:
        rt = unseal(sealed)
        if rt:
            await oauth.apple_revoke(rt)

    # 3) Foydalanuvchi va unga bog'liq hamma narsa (FK ON DELETE CASCADE).
    #    referral_claims saqlanadi — bonusni qayta olishga yo'l qo'ymaslik uchun.
    await db.delete(user)
    await db.commit()
    log.info("Hisob o'chirildi: user=%s", user.id)
