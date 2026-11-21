"""E'lonlar: admin panelda yoziladi, ilovada "Bugun" sahifasining tepasida ko'rinadi.

Botdagi "xabar yuborish" o'rnini bosadi: hammaga, faqat bepul/Premium
foydalanuvchilarga yoki bitta foydalanuvchiga. Ilova push-bildirishnoma
yubormaydi — foydalanuvchi ilovani ochganda ko'radi.
"""
from __future__ import annotations

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Announcement, User, utcnow
from app.services import premium

AUDIENCES = {
    "all": "Hammaga",
    "free": "Bepul foydalanuvchilarga",
    "premium": "Premium foydalanuvchilarga",
    "user": "Bitta foydalanuvchiga",
}


def serialize(a: Announcement) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "body": a.body,
        "button_label": a.button_label,
        "button_url": a.button_url,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def for_user(db: AsyncSession, user: User, limit: int = 5) -> list[Announcement]:
    now = utcnow()
    audiences = ["all", "premium" if premium.is_premium(user) else "free"]
    rows = await db.execute(
        select(Announcement)
        .where(
            Announcement.is_active.is_(True),
            or_(Announcement.starts_at.is_(None), Announcement.starts_at <= now),
            or_(Announcement.ends_at.is_(None), Announcement.ends_at > now),
            or_(
                Announcement.audience.in_(audiences),
                (Announcement.audience == "user") & (Announcement.target_user_id == user.id),
            ),
        )
        .order_by(Announcement.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def mark_seen(db: AsyncSession, announcement_id: int) -> None:
    await db.execute(
        update(Announcement).where(Announcement.id == announcement_id).values(views=Announcement.views + 1)
    )
    await db.commit()
