"""Do'st taklif qilish: yangi foydalanuvchi onboarding'da taklif qilganning username'ini kiritadi.

Mukofot faqat taklif qilingan foydalanuvchi haqiqatan ilovadan foydalansa beriladi:
`REFERRAL_ACTIVATION_DAYS` xil kunda reja/odat bajargach. Shunda:
  • taklif qilingan — `REFERRAL_INVITEE_REWARD_DAYS` kun Premium (bir hisob uchun bir marta);
  • taklif qilgan — har `REFERRAL_THRESHOLD` faol do'st uchun `REFERRAL_REWARD_DAYS` kun.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import runtime_settings as rs
from app.models import AuthIdentity, HabitLog, Plan, ReferralClaim, User, utcnow
from app.services import premium
from app.services.clock import today

log = logging.getLogger(__name__)


def identity_hash(provider: str, subject: str) -> str:
    return hashlib.sha256(f"{provider}:{subject}".encode()).hexdigest()


async def _distinct_active_days(db: AsyncSession, user_id: int) -> int:
    since = today() - timedelta(days=60)
    p = select(Plan.plan_date.label("d")).where(Plan.user_id == user_id, Plan.status == "done", Plan.plan_date >= since)
    h = select(HabitLog.log_date.label("d")).where(HabitLog.user_id == user_id, HabitLog.log_date >= since)
    sub = p.union(h).subquery()
    return int(await db.scalar(select(func.count()).select_from(sub)) or 0)


async def maybe_activate(db: AsyncSession, invitee: User) -> None:
    """Reja/odat bajarilganda chaqiriladi. Idempotent; xato asosiy amalni buzmaydi."""
    if invitee.referred_by_id is None or invitee.referral_activated_at is not None:
        return
    if await _distinct_active_days(db, invitee.id) < rs.get("REFERRAL_ACTIVATION_DAYS"):
        return

    invitee.referral_activated_at = utcnow()
    await db.commit()

    # Taklif qilinganga bonus — faqat shu Google/Apple hisob ilgari olmagan bo'lsa.
    identities = (
        await db.execute(select(AuthIdentity.provider, AuthIdentity.subject).where(AuthIdentity.user_id == invitee.id))
    ).all()
    hashes = [identity_hash(p, s) for p, s in identities]
    claimed = bool(hashes) and bool(
        await db.scalar(select(ReferralClaim.identity_hash).where(ReferralClaim.identity_hash.in_(hashes)))
    )
    if not claimed and hashes and rs.get("REFERRAL_INVITEE_REWARD_DAYS") > 0:
        await db.execute(
            pg_insert(ReferralClaim).values([{"identity_hash": h} for h in hashes]).on_conflict_do_nothing()
        )
        await db.commit()
        await premium.grant_days(
            db, invitee, rs.get("REFERRAL_INVITEE_REWARD_DAYS"),
            source="referral_invitee", reference=f"refi:{invitee.id}",
        )

    referrer = await db.get(User, invitee.referred_by_id, with_for_update=True)
    if referrer is None:
        await db.commit()
        return
    referrer.referral_count = (referrer.referral_count or 0) + 1
    await db.commit()
    while referrer.referral_count - (referrer.referral_rewards_given or 0) >= rs.get("REFERRAL_THRESHOLD"):
        batch = (referrer.referral_rewards_given or 0) + rs.get("REFERRAL_THRESHOLD")
        referrer.referral_rewards_given = batch
        await db.commit()
        await premium.grant_days(
            db, referrer, rs.get("REFERRAL_REWARD_DAYS"),
            source="referral", reference=f"ref:{referrer.id}:{batch}",
        )
    log.info("Referral faollashdi invitee=%s referrer=%s count=%s", invitee.id, referrer.id, referrer.referral_count)
