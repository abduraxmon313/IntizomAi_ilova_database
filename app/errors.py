from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from app.i18n import t


def fail(status: int, key: str, **kwargs) -> NoReturn:
    """Lokalizatsiya qilingan `{"detail": "..."}` bilan HTTP xato ko'taradi.

    Status kodlari ilova bilan kelishilgan: 401 — qayta kirish, 402 — Premium
    kerak, 403 — ruxsat yo'q, 404 — topilmadi, 409 — amalga hozir ruxsat yo'q,
    422 — noto'g'ri ma'lumot, 429 — juda ko'p so'rov.
    """
    raise HTTPException(status_code=status, detail=t(key, **kwargs))
