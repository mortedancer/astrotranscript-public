"""Absolute Earth wall-clock helpers.

'Astro' here means only: bind a relative file offset to the real datetime
when the recorder started. No Julian date, no sidereal time, no observatory.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

STEM_RE = re.compile(
    r"^(?P<y>\d{4})\.(?P<mo>\d{2})\.(?P<d>\d{2})\.(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})$"
)
# Olympus/Sony-style dictaphone: R20250827-115520
DICTAPHONE_RE = re.compile(
    r"^R?(?P<y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})[-_.]?(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})$",
    re.I,
)


def parse_recording_start(stem: str) -> Optional[datetime]:
    """Parse `2026.08.18.163513` or `R20250827-115520` as recorder wall-clock."""
    text = stem.strip()
    m = STEM_RE.match(text) or DICTAPHONE_RE.match(text)
    if not m:
        return None
    g = m.groupdict()
    return datetime(
        int(g["y"]), int(g["mo"]), int(g["d"]),
        int(g["h"]), int(g["mi"]), int(g["s"]),
    )


def _split_seconds(rel_sec: float) -> tuple[int, int]:
    """Whole seconds plus microseconds, matching `relative_to_absolute`."""
    neg = rel_sec < 0
    mag = -rel_sec if neg else rel_sec
    whole = int(mag)
    micros = int(round((mag - whole) * 1_000_000))
    if micros >= 1_000_000:
        whole += 1
        micros -= 1_000_000
    if neg:
        if micros:
            return -whole - 1, 1_000_000 - micros
        return -whole, 0
    return whole, micros


def relative_to_absolute(recording_start: datetime, rel_sec: float) -> datetime:
    whole, micros = _split_seconds(rel_sec)
    return recording_start + timedelta(seconds=whole, microseconds=micros)


def start_from_anchor(at: datetime, rel_sec: float) -> datetime:
    """Recording start such that this timeline point lands on `at`."""
    if rel_sec < 0:
        raise ValueError("timeline point is before the start")
    whole, micros = _split_seconds(rel_sec)
    return at - timedelta(seconds=whole, microseconds=micros)


_WALL_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})"
    r"[T ]"
    r"(?P<h>\d{2}):(?P<mi>\d{2})"
    r"(?::(?P<s>\d{2})(?:\.(?P<frac>\d{1,6}))?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?$"
)
_REL_HMS = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{1,2}(?:\.\d+)?)$"
)


def parse_wall_datetime(text: str) -> datetime:
    """Naive wall clock. A trailing Z or offset is ignored: the digits are the clock face."""
    raw = (text or "").strip()
    m = _WALL_RE.match(raw)
    if not m:
        raise ValueError("date-time must look like YYYY-MM-DD HH:MM[:SS]")
    frac = m.group("frac") or ""
    micros = int(frac.ljust(6, "0")) if frac else 0
    try:
        return datetime(
            int(m.group("y")),
            int(m.group("mo")),
            int(m.group("d")),
            int(m.group("h")),
            int(m.group("mi")),
            int(m.group("s") or 0),
            micros,
        )
    except ValueError as exc:
        raise ValueError("invalid date or time") from exc


def parse_rel_point(value: object) -> float:
    """Seconds, or `hh:mm:ss` / `mm:ss`, for a point on the recording timeline."""
    if isinstance(value, bool) or value is None:
        raise ValueError("timeline point is required")
    if isinstance(value, (int, float)):
        rel = float(value)
    else:
        text = str(value).strip().replace(",", ".")
        if not text:
            raise ValueError("timeline point is required")
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            rel = float(text)
        else:
            m = _REL_HMS.fullmatch(text)
            if not m:
                raise ValueError("timeline point must be seconds or hh:mm:ss")
            minute = int(m.group("m"))
            sec = float(m.group("s"))
            if minute >= 60 or sec >= 60:
                raise ValueError("timeline point must be seconds or hh:mm:ss")
            rel = int(m.group("h") or 0) * 3600 + minute * 60 + sec
    if rel != rel or rel in (float("inf"), float("-inf")):
        raise ValueError("timeline point is not a number")
    if rel < -0.05:
        raise ValueError("timeline point is before the start")
    return 0.0 if rel < 0 else rel


def parse_stored_anchor(raw: object) -> Optional[dict]:
    """Return `{rel_sec, at}` or None when the saved anchor is missing or unreadable."""
    if not isinstance(raw, dict):
        return None
    try:
        rel = float(raw.get("rel_sec"))  # type: ignore[arg-type]
        at = parse_wall_datetime(str(raw.get("at") or ""))
    except (TypeError, ValueError):
        return None
    if rel < 0 or rel != rel or rel == float("inf"):
        return None
    return {"rel_sec": rel, "at": at}


def resolve_clock(stem: str, anchor_raw: object) -> dict:
    """Effective recording start: a user anchor wins over the filename stamp."""
    filename = parse_recording_start(stem)
    anchor = parse_stored_anchor(anchor_raw)
    if anchor:
        return {
            "start": start_from_anchor(anchor["at"], anchor["rel_sec"]),
            "source": "anchor",
            "anchor": anchor,
            "filename_start": filename,
        }
    if filename:
        return {"start": filename, "source": "filename", "anchor": None, "filename_start": filename}
    return {"start": None, "source": None, "anchor": None, "filename_start": None}


def format_rel(sec: float) -> str:
    total_ms = int(round(float(sec) * 1000.0))
    if total_ms < 0:
        total_ms = 0
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def format_signed_hms(seconds: float) -> str:
    sign = "+" if seconds >= 0 else "-"
    total = int(round(abs(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{sign}{h}:{m:02d}:{s:02d}"
    return f"{sign}{m}:{s:02d}"


def format_astro(dt: datetime, millis: bool = True) -> str:
    if millis:
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_clock(dt: datetime, millis: bool = True) -> str:
    if millis:
        return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    return dt.strftime("%H:%M:%S")


def iso_astro(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def parse_hms(h: str | int, m: str | int, s: str | float) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)
