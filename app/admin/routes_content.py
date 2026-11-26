"""E'lonlar, sozlamalar va guruhlar."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import config
from app.admin import auth
from app.admin.web import NotFound, guard, paginate, post_guard, redirect, render
from app.db import get_session
from app.models import Announcement, Group, GroupInvite, GroupMember, Report, User, utcnow
from app.services import announcements as ann
from app.services import runtime_settings as rs

router = APIRouter()


# ─────────────────────────────────────────────────────────────
#  E'lonlar
# ─────────────────────────────────────────────────────────────
def _parse_dt(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=config.TIMEZONE)
    except ValueError:
        raise ValueError("Sana/vaqt formati noto'g'ri.") from None


async def _audience_size(db: AsyncSession, a: Announcement) -> int:
    now = utcnow()
    q = select(func.count(User.id)).where(User.onboarded.is_(True), User.banned_at.is_(None))
    if a.audience == "premium":
        q = q.where(User.premium_until > now)
    elif a.audience == "free":
        q = q.where(or_(User.premium_until.is_(None), User.premium_until <= now))
    elif a.audience == "user":
        return 1
    return int(await db.scalar(q) or 0)


@router.get("/announcements")
async def announcements_list(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    stmt = select(Announcement).where(Announcement.audience != "user").order_by(Announcement.created_at.desc())
    if request.query_params.get("personal") == "1":
        stmt = select(Announcement).where(Announcement.audience == "user").order_by(Announcement.created_at.desc())
    page = await paginate(db, stmt, request, {"personal": request.query_params.get("personal", "")})
    sizes = {a.id: await _audience_size(db, a) for a in page.items}
    now = utcnow()
    return render(request, "announcements.html", ctx, page=page, sizes=sizes, AUD=ann.AUDIENCES, now=now,
                  personal=request.query_params.get("personal") == "1")


def _ann_fields(f: auth.Form) -> tuple[dict, list[str]]:
    errors = []
    title, body = f.str("title", 120), f.raw("body", 2000).strip()
    if not title:
        errors.append("Sarlavha yozing.")
    if not body:
        errors.append("Matn yozing.")
    audience = f.str("audience", 12)
    if audience not in ("all", "free", "premium"):
        errors.append("Kimga ko'rsatilishini tanlang.")
    label, url = f.str("button_label", 40) or None, f.str("button_url", 500) or None
    if url and not url.startswith(("https://", "intizomai://")):
        errors.append("Tugma havolasi https:// bilan boshlansin.")
    if bool(label) != bool(url):
        errors.append("Tugma uchun ham yozuv, ham havola kerak (yoki ikkalasi bo'sh).")
    try:
        starts, ends = _parse_dt(f.str("starts_at", 16)), _parse_dt(f.str("ends_at", 16))
    except ValueError as e:
        errors.append(str(e))
        starts = ends = None
    if starts and ends and ends <= starts:
        errors.append("Tugash vaqti boshlanishidan keyin bo'lsin.")
    return {"title": title, "body": body, "audience": audience, "button_label": label, "button_url": url,
            "starts_at": starts, "ends_at": ends, "is_active": f.bool("is_active")}, errors


@router.get("/announcements/new")
async def announcement_new(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    return render(request, "announcement_form.html", ctx, a=None, AUD=ann.AUDIENCES)


@router.post("/announcements/new")
async def announcement_create(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    fields, errors = _ann_fields(f)
    if errors:
        return render(request, "announcement_form.html", ctx, a=None, AUD=ann.AUDIENCES, errors=errors, form=f, http_code=422)
    a = Announcement(**fields, created_by=ctx.admin.username)
    db.add(a)
    await db.flush()
    await auth.audit(db, ctx, "announcement.create", target_type="announcement", target_id=a.id,
                     summary=f"{ann.AUDIENCES[a.audience]}: {a.title}", commit=False)
    await db.commit()
    return redirect("/admin/announcements", "E'lon saqlandi. Foydalanuvchilar ilovani ochganda ko'radi.")


async def _ann(db: AsyncSession, ann_id: int) -> Announcement:
    a = await db.get(Announcement, ann_id)
    if a is None:
        raise NotFound("E'lon topilmadi.")
    return a


@router.get("/announcements/{ann_id}")
async def announcement_edit(ann_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db, "admin")
    a = await _ann(db, ann_id)
    return render(request, "announcement_form.html", ctx, a=a, AUD=ann.AUDIENCES, size=await _audience_size(db, a))


@router.post("/announcements/{ann_id}")
async def announcement_update(ann_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    a = await _ann(db, ann_id)
    if a.audience == "user":
        return redirect("/admin/announcements?personal=1", "Shaxsiy xabarni tahrirlab bo'lmaydi — o'chirib, qaytadan yuboring.", "warn")
    fields, errors = _ann_fields(f)
    if errors:
        return render(request, "announcement_form.html", ctx, a=a, AUD=ann.AUDIENCES, errors=errors, form=f, http_code=422)
    for k, v in fields.items():
        setattr(a, k, v)
    await auth.audit(db, ctx, "announcement.update", target_type="announcement", target_id=a.id, summary=a.title, commit=False)
    await db.commit()
    return redirect("/admin/announcements", "E'lon yangilandi.")


@router.post("/announcements/{ann_id}/toggle")
async def announcement_toggle(ann_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    a = await _ann(db, ann_id)
    a.is_active = not a.is_active
    await auth.audit(db, ctx, "announcement.toggle", target_type="announcement", target_id=a.id,
                     summary="yoqildi" if a.is_active else "to'xtatildi", commit=False)
    await db.commit()
    return redirect("/admin/announcements" + ("?personal=1" if a.audience == "user" else ""),
                    "E'lon " + ("yoqildi." if a.is_active else "to'xtatildi."))


@router.post("/announcements/{ann_id}/delete")
async def announcement_delete(ann_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, _ = await post_guard(request, db, "admin")
    a = await _ann(db, ann_id)
    personal = a.audience == "user"
    await db.delete(a)
    await auth.audit(db, ctx, "announcement.delete", target_type="announcement", target_id=ann_id, summary=a.title, commit=False)
    await db.commit()
    return redirect("/admin/announcements" + ("?personal=1" if personal else ""), "E'lon o'chirildi.", "warn")


# ─────────────────────────────────────────────────────────────
#  Sozlamalar
# ─────────────────────────────────────────────────────────────
@router.get("/settings")
async def settings_page(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    await rs.load(db)
    groups: dict[str, list] = {}
    for s in rs.SPECS:
        groups.setdefault(s.group, []).append((s, rs.get(s.key), rs.default(s.key), rs.is_overridden(s.key)))
    return render(request, "settings.html", ctx, groups=groups)


@router.post("/settings")
async def settings_save(request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    values, errors = {}, []
    for s in rs.SPECS:
        try:
            values[s.key] = rs.validate(s.key, f.str(s.key, 20) if s.kind == "int" else ("true" if f.bool(s.key) else "false"))
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return redirect("/admin/settings", " ".join(errors), "err")
    changed = await rs.save(db, values, ctx.admin.username)
    if changed:
        await auth.audit(db, ctx, "settings.update", summary=", ".join(f"{k}={values[k]}" for k in changed))
    return redirect("/admin/settings", f"{len(changed)} ta sozlama o'zgardi." if changed else "O'zgarish yo'q.")


# ─────────────────────────────────────────────────────────────
#  Guruhlar
# ─────────────────────────────────────────────────────────────
@router.get("/groups")
async def groups_list(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    q = request.query_params.get("q", "")[:80]
    members = select(GroupMember.group_id, func.count(GroupMember.id).label("n")).group_by(GroupMember.group_id).subquery()
    stmt = (
        select(Group, User, func.coalesce(members.c.n, 0))
        .join(User, User.id == Group.owner_user_id, isouter=True)
        .join(members, members.c.group_id == Group.id, isouter=True)
    )
    if q.strip():
        like = f"%{q.strip().lower()}%"
        conds = [func.lower(Group.name).like(like), User.username.like(f"%{q.strip().lower().lstrip('@')}%")]
        if q.isdigit():
            conds.append(Group.id == int(q))
        stmt = stmt.where(or_(*conds))
    page = await paginate(db, stmt.order_by(Group.created_at.desc()), request, {"q": q})
    return render(request, "groups.html", ctx, page=page, q=q)


@router.get("/groups/{group_id}")
async def group_detail(group_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    g = await db.get(Group, group_id)
    if g is None:
        raise NotFound("Guruh topilmadi.")
    members = (await db.execute(
        select(GroupMember, User).join(User, User.id == GroupMember.user_id).where(GroupMember.group_id == g.id)
        .order_by(GroupMember.role.desc(), GroupMember.joined_at)
    )).all()
    invites = (await db.execute(
        select(GroupInvite, User).join(User, User.id == GroupInvite.invitee_user_id)
        .where(GroupInvite.group_id == g.id).order_by(GroupInvite.created_at.desc()).limit(30)
    )).all()
    return render(request, "group_detail.html", ctx, g=g, owner=await db.get(User, g.owner_user_id), members=members, invites=invites)


@router.post("/groups/{group_id}/delete")
async def group_delete(group_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db, "admin")
    g = await db.get(Group, group_id)
    if g is None:
        raise NotFound("Guruh topilmadi.")
    reason = f.str("reason", 200)
    if not reason:
        return redirect(f"/admin/groups/{group_id}", "O'chirish sababini yozing.", "err")
    name = g.name
    await db.delete(g)
    await auth.audit(db, ctx, "group.delete", target_type="group", target_id=group_id, summary=f"{name}: {reason}", commit=False)
    await db.commit()
    return redirect("/admin/groups", f"«{name}» guruhi o'chirildi.", "warn")


# ─────────────────────────────────────────────────────────────
#  Shikoyatlar (App Store Guideline 1.2: 24 soat ichida ko'rib chiqish)
# ─────────────────────────────────────────────────────────────
REPORT_REASONS = {"spam": "Spam", "abuse": "Haqorat / tahdid", "inappropriate": "Nomaqbul kontent", "other": "Boshqa"}
REPORT_STATUSES = {"open": "Yangi", "resolved": "Chora ko'rildi", "dismissed": "Rad etildi"}


@router.get("/reports")
async def reports_list(request: Request, db: AsyncSession = Depends(get_session)):
    ctx = await guard(request, db)
    st = request.query_params.get("s", "open")
    if st not in REPORT_STATUSES and st != "all":
        st = "open"
    reporter = aliased(User)
    target = aliased(User)
    stmt = (
        select(Report, reporter, target, Group)
        .join(reporter, reporter.id == Report.reporter_user_id, isouter=True)
        .join(target, target.id == Report.target_user_id, isouter=True)
        .join(Group, Group.id == Report.target_group_id, isouter=True)
    )
    if st != "all":
        stmt = stmt.where(Report.status == st)
    page = await paginate(db, stmt.order_by(Report.created_at.desc()), request, {"s": st})
    open_count = await db.scalar(select(func.count(Report.id)).where(Report.status == "open")) or 0
    return render(request, "reports.html", ctx, page=page, s=st, open_count=open_count,
                  REASONS=REPORT_REASONS, STATUSES=REPORT_STATUSES)


@router.post("/reports/{report_id}/close")
async def report_close(report_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    ctx, f = await post_guard(request, db)
    r = await db.get(Report, report_id)
    if r is None:
        raise NotFound("Shikoyat topilmadi.")
    status = f.str("status", 10)
    if status not in ("resolved", "dismissed"):
        return redirect("/admin/reports", "Holat noto'g'ri.", "err")
    r.status = status
    r.resolved_at = utcnow()
    r.resolution_note = f.str("note", 300) or None
    await auth.audit(db, ctx, f"report.{status}", target_type="report", target_id=r.id,
                     summary=r.resolution_note or REPORT_STATUSES[status], commit=False)
    await db.commit()
    return redirect("/admin/reports", f"Shikoyat #{r.id}: {REPORT_STATUSES[status]}.")
