from __future__ import annotations

import re
from datetime import date, datetime

from app import config

_HM = re.compile(r"^(\d{1,2}):(\d{1,2})$")


def now_local() -> datetime:
    return datetime.now(config.TIMEZONE)


def today() -> date:
    return now_local().date()


def normalize_hm(value: str | None) -> str | None:
    """"7:5" / "07:05" → "07:05". Bo'sh → None. Noto'g'ri → ValueError."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _HM.match(s)
    if not m:
        raise ValueError("bad time")
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("bad time")
    return f"{hh:02d}:{mm:02d}"


def time_reached(hm: str | None) -> bool:
    """Bugun "HH:MM" vaqti keldimi."""
    if not hm or len(hm) != 5:
        return False
    return now_local().strftime("%H:%M") >= hm


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError("bad date") from None
