"""AI murabbiy: foydalanuvchining real holati (maqsad, reja, odat, kayfiyat) asosida suhbat.

Suhbat tarixi serverda saqlanmaydi — ilova uni o'zi yuboradi. OpenAI'ga to'g'ridan
REST orqali murojaat qilinadi (SDK versiyalariga bog'lanmaslik uchun).
"""
from __future__ import annotations

import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.models import Checkin, Goal, Plan, User
from app.services import habits as habit_rules
from app.services.clock import today
from app.services.users import display_name

log = logging.getLogger(__name__)

LANG_NAME = {"uz": "o'zbek (lotin yozuvida)", "ru": "rus", "en": "ingliz"}
WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]

PERSONA = """Sen — "Intizom AI": tajribali shaxsiy psixolog, motivator va intizom murabbiysisan.

ICHKI BILIM (foydalanuvchiga aytma):
- Senga foydalanuvchining maqsadlari, odatlari, oxirgi 7 kunlik rejalari, seriyasi va kayfiyati beriladi.
- Javobdan oldin ichingda tahlil qil: izchillik, kuchli/zaif tomonlar, kayfiyat va energiya tendensiyasi.

ASOSIY QOIDA — ORTIQCHA GAPIRMA:
- So'ralmaguncha "ma'lumotlaringni ko'rib turibman" kabi hisobot berma, ro'yxat sanama.
- "Salom" desa — iliq salomlash va qisqa tabiiy savol.
- O'zi so'rasa ("rejalarimni tahlil qil") — aniq misol va raqamlar bilan javob ber.
- Faqat berilgan ma'lumotga tayan, hech narsani o'ylab topma.

USLUB:
- Iliq, qo'llab-quvvatlovchi, dono. Uyaltirma, ayblama.
- Qisqa: 2–5 jumla, 1–2 mos emoji, kerak bo'lsa bitta amaliy keyingi qadam.
- Foydalanuvchiga "sen" deb murojaat qil.
- Tibbiy, huquqiy yoki moliyaviy tashxis berma; jiddiy ruhiy inqiroz belgisi bo'lsa — mutaxassisga yoki yaqinlariga murojaat qilishni muloyimlik bilan tavsiya qil."""


async def build_context(db: AsyncSession, user: User) -> str:
    d0 = today()
    week_ago = d0 - timedelta(days=6)
    lines = [
        f"ISM: {display_name(user).split()[0]}",
        f"PROGRESS: daraja {user.level}, {user.xp} XP, seriya {user.streak} kun (rekord {user.longest_streak}), "
        f"intizom bali {user.discipline_score}/100",
        "\n=== MAQSADLAR ===",
    ]
    goals = (await db.execute(select(Goal).where(Goal.user_id == user.id).order_by(Goal.created_at))).scalars().all()
    if goals:
        for g in goals[:30]:
            kind = "Yillik" if g.goal_type == "yearly" else "Oylik"
            mark = "bajarilgan" if g.completed else "jarayonda"
            lines.append(f"  • [{kind} {g.period}] {g.title} — {mark}")
    else:
        lines.append("Hali maqsad yo'q.")

    lines.append("\n=== ODATLAR ===")
    habit_rows = await habit_rules.list_active(db, user.id)
    snaps = await habit_rules.snapshots_for(db, habit_rows)
    if snaps:
        for h in snaps:
            freq = "har kuni" if h["frequency"] == "daily" else "haftada: " + ",".join(
                WEEKDAYS[i][:3] for i in h["weekdays"]
            )
            today_mark = "bugun bajarildi" if h["done_today"] else ("bugun hali yo'q" if h["due_today"] else "bugun rejada yo'q")
            lines.append(f"  • {h['title']} ({freq}) — seriya {h['streak']}, jami {h['total_done']} — {today_mark}")
    else:
        lines.append("Hali odat yo'q.")

    lines.append("\n=== OXIRGI 7 KUN REJALARI ===")
    plans = (
        await db.execute(
            select(Plan)
            .where(Plan.user_id == user.id, Plan.plan_date >= week_ago, Plan.plan_date <= d0 + timedelta(days=1))
            .order_by(Plan.plan_date, Plan.scheduled_time)
        )
    ).scalars().all()
    if plans:
        by_day: dict = {}
        for p in plans:
            by_day.setdefault(p.plan_date, []).append(p)
        for d in sorted(by_day, reverse=True):
            items = by_day[d]
            done = sum(1 for p in items if p.status == "done")
            tag = " (BUGUN)" if d == d0 else (" (ERTAGA)" if d > d0 else "")
            lines.append(f"{d.strftime('%d.%m')} {WEEKDAYS[d.weekday()]}{tag} — {done}/{len(items)}:")
            for p in items[:12]:
                mark = {"done": "bajarildi", "failed": "bajarilmadi"}.get(p.status, "kutilmoqda")
                tm = f" {p.scheduled_time}" if p.scheduled_time else ""
                lines.append(f"  • {p.title}{tm} — {mark}")
    else:
        lines.append("Oxirgi 7 kunda reja yo'q.")

    lines.append("\n=== KAYFIYAT VA ENERGIYA (7 kun) ===")
    checks = (
        await db.execute(
            select(Checkin).where(Checkin.user_id == user.id, Checkin.checkin_date >= week_ago).order_by(Checkin.checkin_date)
        )
    ).scalars().all()
    wrote = False
    for c in checks:
        parts = [p for p in (f"kayfiyat {c.mood}" if c.mood else "", f"energiya {c.energy}/5" if c.energy else "") if p]
        if parts:
            lines.append(f"  {c.checkin_date.strftime('%d.%m')}: " + ", ".join(parts))
            wrote = True
    if not wrote:
        lines.append("Belgilanmagan.")
    return "\n".join(lines)


class CoachUnavailable(Exception):
    pass


async def reply(context_block: str, history: list[dict], lang: str) -> str:
    if not config.OPENAI_API_KEY:
        raise CoachUnavailable("no api key")
    messages = [
        {"role": "system", "content": PERSONA + f"\n\nJAVOB TILI: faqat {LANG_NAME.get(lang, LANG_NAME['uz'])} tilida yoz."},
        {
            "role": "system",
            "content": "FOYDALANUVCHINING REAL HOLATI (faqat shunga tayan, so'ralmasa sanab berma):\n" + context_block,
        },
    ]
    for m in history[-12:]:
        content = (m.get("content") or "").strip()
        if m.get("role") in ("user", "assistant") and content:
            messages.append({"role": m["role"], "content": content[:1500]})
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(
                f"{config.OPENAI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                json={"model": config.OPENAI_MODEL, "messages": messages, "temperature": 0.5, "max_tokens": 500},
            )
        if r.status_code != 200:
            log.error("OpenAI %s: %s", r.status_code, r.text[:300])
            raise CoachUnavailable(f"status {r.status_code}")
        text = (r.json()["choices"][0]["message"]["content"] or "").strip()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.error("OpenAI xato: %s", e)
        raise CoachUnavailable(str(e)) from e
    if not text:
        raise CoachUnavailable("empty")
    return text
