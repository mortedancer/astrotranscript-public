"""Project-level loop cuts: MP3 slots with the current listen FX."""
from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import catalog
from .process import encode_speech_mp3, ffmpeg
from .projects import (
    BadRequest,
    NotFound,
    _read_json,
    _require_active,
    _write_json,
    now_iso,
    recording_dir,
)

EQ_HZ = [80, 125, 200, 315, 500, 800, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000]
CUT_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{4,80}$")
_FX_KEYS = (
    "on",
    "eq",
    "lfHz",
    "lfDb",
    "hfHz",
    "hfDb",
    "thresh",
    "ratio",
    "attack",
    "release",
    "knee",
    "makeup",
    "deecho",
    "deechoAmt",
)


def _db_to_lin(db: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, 10.0 ** (float(db) / 20.0)))


def _fx_snapshot(fx: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not fx:
        return None
    out: dict[str, Any] = {}
    for k in _FX_KEYS:
        if k in fx:
            v = fx[k]
            out[k] = list(v) if k == "eq" and isinstance(v, list) else v
    return out


def cuts_dir(root: Path, pid: str) -> Path:
    d = _require_active(root, pid) / "cuts"
    d.mkdir(exist_ok=True)
    return d


def fx_filter_af(fx: Optional[dict[str, Any]]) -> str:
    if not fx or fx.get("on") is False:
        return "anull"
    parts = [
        f"highpass=f={max(20, float(fx.get('lfHz') or 90))}",
        f"lowshelf=f=140:t=q:width=0.7:g={min(0.0, float(fx.get('lfDb') or 0))}",
    ]
    eq = fx.get("eq") or []
    for i, hz in enumerate(EQ_HZ):
        try:
            g = float(eq[i])
        except (TypeError, ValueError, IndexError):
            g = 0.0
        if abs(g) < 0.05:
            continue
        parts.append(f"equalizer=f={hz}:t=q:width=1.35:g={g}")
    parts.append(f"highshelf=f=7500:t=q:width=0.7:g={min(0.0, float(fx.get('hfDb') or 0))}")
    parts.append(f"lowpass=f={max(2000, float(fx.get('hfHz') or 9000))}")
    thresh = _db_to_lin(float(fx.get("thresh") or -22), 0.000976563, 1.0)
    ratio = min(20.0, max(1.0, float(fx.get("ratio") or 3)))
    attack = min(2000.0, max(0.01, float(fx.get("attack") or 12)))
    release = min(9000.0, max(0.01, float(fx.get("release") or 180)))
    knee = min(8.0, max(1.0, float(fx.get("knee") or 10)))
    makeup = _db_to_lin(max(0.0, float(fx.get("makeup") or 0)), 1.0, 64.0)
    comp = (
        "acompressor="
        f"threshold={thresh:.6g}:ratio={ratio:.4g}:attack={attack:.4g}:release={release:.4g}:knee={knee:.4g}:makeup={makeup:.6g}"
    )
    pre = ",".join(parts)
    amt_raw = float(fx.get("deechoAmt") or 0)
    amt = amt_raw / 100.0 if amt_raw > 1.0 else amt_raw
    amt = min(1.0, max(0.0, amt))
    if fx.get("deecho") and amt > 0.02:
        gains = [round(-g * amt, 4) for g in (0.34, 0.24, 0.16, 0.11, 0.07)]
        delays = (23, 39, 61, 87, 119)
        taps = ";".join(
            f"[t{i}]adelay={d}|{d},volume={gains[i]}[a{i}]" for i, d in enumerate(delays)
        )
        return (
            f"{pre},asplit=2[dry][wet];"
            "[wet]highpass=f=480,lowpass=f=4300,asplit=5[t0][t1][t2][t3][t4];"
            f"{taps};"
            f"[dry][a0][a1][a2][a3][a4]amix=inputs=6:normalize=0:dropout_transition=0,{comp}"
        )
    return f"{pre},{comp}"


def _new_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def list_cuts(root: Path, pid: str) -> list[dict[str, Any]]:
    folder = cuts_dir(root, pid)
    items = []
    for p in sorted(folder.glob("*.json"), reverse=True):
        try:
            meta = _read_json(p)
        except Exception:
            continue
        if not isinstance(meta, dict) or not meta.get("id"):
            continue
        mp3 = folder / f"{meta['id']}.mp3"
        meta["bytes"] = mp3.stat().st_size if mp3.is_file() else 0
        meta["url"] = f"/api/projects/{pid}/cuts/{meta['id']}/download"
        items.append(meta)
    return items


def get_cut(root: Path, pid: str, cid: str) -> dict[str, Any]:
    if not CUT_ID_RE.match(cid):
        raise BadRequest("bad cut id")
    folder = cuts_dir(root, pid)
    meta_p = folder / f"{cid}.json"
    mp3 = folder / f"{cid}.mp3"
    if not meta_p.exists() or not mp3.is_file():
        raise NotFound(f"cut not found: {cid}")
    meta = _read_json(meta_p)
    meta["bytes"] = mp3.stat().st_size
    meta["url"] = f"/api/projects/{pid}/cuts/{cid}/download"
    return meta


def cut_mp3_path(root: Path, pid: str, cid: str) -> Path:
    get_cut(root, pid, cid)
    return cuts_dir(root, pid) / f"{cid}.mp3"


def cut_download_name(meta: dict[str, Any]) -> str:
    raw = str(meta.get("name") or meta.get("id") or "cut")
    safe = re.sub(r"[^\w.\-]+", "_", raw).strip("._")[:80] or str(meta.get("id") or "cut")
    if not safe.lower().endswith(".mp3"):
        safe += ".mp3"
    return safe


def delete_cut(root: Path, pid: str, cid: str) -> None:
    if not CUT_ID_RE.match(cid):
        raise BadRequest("bad cut id")
    folder = cuts_dir(root, pid)
    meta_p = folder / f"{cid}.json"
    mp3 = folder / f"{cid}.mp3"
    if not meta_p.exists() and not mp3.exists():
        raise NotFound(f"cut not found: {cid}")
    for p in (meta_p, mp3):
        if p.exists():
            p.unlink()


def _source_for_cut(root: Path, pid: str, stem: str, source: str) -> Path:
    rec = recording_dir(root, pid, stem)
    if source == "cleaned":
        p = rec / "cleaned_speech.wav"
        if p.is_file():
            return p
    for name in ("pcm16_adapt.wav", "pcm16.wav"):
        p = rec / name
        if p.is_file():
            return p
    src = catalog.source_path(stem, pid, root)
    if src is None:
        raise NotFound(f"audio source missing for {stem}")
    return src


def render_loop_cut(
    root: Path,
    pid: str,
    *,
    stem: str,
    start: float,
    end: float,
    source: str = "original",
    fx: Optional[dict[str, Any]] = None,
    name: str = "",
) -> dict[str, Any]:
    start = float(start)
    end = float(end)
    stem = (stem or "").strip()
    if not stem:
        raise BadRequest("recording_id is required")
    if end - start < 0.12:
        raise BadRequest("loop is too short")
    if end - start > 15 * 60:
        raise BadRequest("loop longer than 15 minutes")
    src = _source_for_cut(root, pid, stem, source)
    cid = _new_id()
    folder = cuts_dir(root, pid)
    wav_tmp = folder / f"{cid}.wav"
    mp3 = folder / f"{cid}.mp3"
    af = fx_filter_af(fx)
    dur = end - start
    try:
        ffmpeg(
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{dur:.3f}",
            "-i",
            str(src),
            "-af",
            af,
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav_tmp),
        )
        encode_speech_mp3(wav_tmp, mp3)
    except (subprocess.CalledProcessError, RuntimeError, FileNotFoundError) as exc:
        if mp3.exists():
            mp3.unlink()
        raise BadRequest(f"cut encode failed: {exc}") from exc
    finally:
        if wav_tmp.exists():
            wav_tmp.unlink()
    if not mp3.is_file() or mp3.stat().st_size <= 0:
        raise BadRequest("mp3 encode failed")
    label = (name or "").strip() or f"{stem} {start:.1f}–{end:.1f}s"
    applied = bool(fx and fx.get("on") is not False)
    meta = {
        "id": cid,
        "name": label[:120],
        "recording_id": stem,
        "start_rel": start,
        "end_rel": end,
        "duration_sec": dur,
        "source": source,
        "created_at": now_iso(),
        "fx": applied,
        "fx_settings": _fx_snapshot(fx) if applied else None,
    }
    _write_json(folder / f"{cid}.json", meta)
    meta["bytes"] = mp3.stat().st_size
    meta["url"] = f"/api/projects/{pid}/cuts/{cid}/download"
    return meta
