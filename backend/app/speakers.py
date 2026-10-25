"""Global speaker library — voice examples outside any project."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .projects import (
    AUDIO_EXTS,
    BadRequest,
    NotFound,
    _read_json,
    _write_json,
    now_iso,
    slugify,
    unique_slug,
    validate_name,
)

SPEAKERS_DIR = ".speakers"
SAMPLE_MAX = 12 * 1024 * 1024
SAMPLE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{4,80}$")


def speakers_root(root: Path) -> Path:
    d = Path(root) / SPEAKERS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _speaker_dir(root: Path, sid: str) -> Path:
    folder = speakers_root(root) / sid
    if not (folder / "speaker.json").is_file():
        raise NotFound(f"speaker not found: {sid}")
    return folder


def _load(folder: Path) -> dict[str, Any]:
    meta = _read_json(folder / "speaker.json")
    samples_dir = folder / "samples"
    items = []
    for row in meta.get("samples") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        wav = samples_dir / f"{row['id']}{Path(str(row.get('filename') or 'x.wav')).suffix.lower() or '.wav'}"
        if not wav.is_file():
            wav = next(samples_dir.glob(f"{row['id']}.*"), None) if samples_dir.is_dir() else None
        if wav and wav.is_file():
            row = dict(row)
            row["bytes"] = wav.stat().st_size
            row["url"] = f"/api/speakers/{meta['id']}/samples/{row['id']}"
            items.append(row)
    meta["samples"] = items
    meta["samples_count"] = len(items)
    return meta


def _save(folder: Path, meta: dict[str, Any]) -> None:
    slim = {
        "id": meta["id"],
        "name": meta["name"],
        "created_at": meta.get("created_at") or now_iso(),
        "fx": meta.get("fx"),
        "samples": [
            {
                "id": s["id"],
                "filename": s.get("filename") or f"{s['id']}.wav",
                "duration_sec": float(s.get("duration_sec") or 0),
                "created_at": s.get("created_at") or now_iso(),
            }
            for s in (meta.get("samples") or [])
            if isinstance(s, dict) and s.get("id")
        ],
    }
    _write_json(folder / "speaker.json", slim)


def list_speakers(root: Path) -> list[dict[str, Any]]:
    folder = speakers_root(root)
    items = []
    for child in sorted(folder.iterdir(), key=lambda p: p.name):
        if not (child / "speaker.json").is_file():
            continue
        try:
            items.append(_load(child))
        except Exception:
            continue
    items.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
    return items


def create_speaker(root: Path, name: str) -> dict[str, Any]:
    name = validate_name(name)
    sid = unique_slug(speakers_root(root), name)
    if sid == "project":
        sid = unique_slug(speakers_root(root), "speaker")
    folder = speakers_root(root) / sid
    folder.mkdir()
    (folder / "samples").mkdir()
    meta = {"id": sid, "name": name, "created_at": now_iso(), "fx": None, "samples": []}
    _save(folder, meta)
    return _load(folder)


def get_speaker(root: Path, sid: str) -> dict[str, Any]:
    return _load(_speaker_dir(root, sid))


def rename_speaker(root: Path, sid: str, name: str) -> dict[str, Any]:
    folder = _speaker_dir(root, sid)
    meta = _load(folder)
    meta["name"] = validate_name(name)
    _save(folder, meta)
    return _load(folder)


def patch_speaker(root: Path, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    folder = _speaker_dir(root, sid)
    meta = _load(folder)
    if "name" in payload and payload["name"] is not None:
        meta["name"] = validate_name(str(payload["name"]))
    if "fx" in payload:
        fx = payload["fx"]
        meta["fx"] = fx if isinstance(fx, dict) else None
    _save(folder, meta)
    return _load(folder)


def delete_speaker(root: Path, sid: str) -> None:
    folder = _speaker_dir(root, sid)
    shutil.rmtree(folder)


def _duration(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path), "rb") as w:
            sr = w.getframerate() or 1
            return w.getnframes() / float(sr)
    except Exception:
        try:
            from .process import probe_duration

            return float(probe_duration(path))
        except Exception:
            return 0.0


def _new_sample_id() -> str:
    from datetime import datetime, timezone
    import time

    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def _add_file(root: Path, sid: str, src: Path, filename: str) -> dict[str, Any]:
    if not src.is_file():
        raise NotFound(f"path not found: {src}")
    suf = Path(filename or src.name).suffix.lower()
    if suf not in AUDIO_EXTS:
        raise BadRequest("sample must be wav/mp3/m4a/ogg/flac")
    size = src.stat().st_size
    if size <= 0:
        raise BadRequest("empty sample")
    if size > SAMPLE_MAX:
        raise BadRequest(f"sample too large ({size} bytes); use a short clip under 12 MB")
    folder = _speaker_dir(root, sid)
    meta = _load(folder)
    sample_id = _new_sample_id()
    samples_dir = folder / "samples"
    samples_dir.mkdir(exist_ok=True)
    dest = samples_dir / f"{sample_id}{suf}"
    shutil.copy2(src, dest)
    row = {
        "id": sample_id,
        "filename": Path(filename or src.name).name,
        "duration_sec": _duration(dest),
        "created_at": now_iso(),
    }
    meta["samples"] = list(meta.get("samples") or []) + [row]
    _save(folder, meta)
    return _load(folder)["samples"][-1]


def add_sample_path(root: Path, sid: str, src: Path) -> dict[str, Any]:
    src = Path(src)
    if not src.exists():
        raise NotFound(f"path not found: {src}")
    return _add_file(root, sid, src, src.name)


def add_sample_bytes(root: Path, sid: str, filename: str, data: bytes) -> dict[str, Any]:
    if not data:
        raise BadRequest("empty sample")
    if len(data) > SAMPLE_MAX:
        raise BadRequest("sample too large; use a short clip under 12 MB")
    name = Path(filename or "sample.wav").name
    suf = Path(name).suffix.lower()
    if suf not in AUDIO_EXTS:
        raise BadRequest("sample must be wav/mp3/m4a/ogg/flac")
    folder = _speaker_dir(root, sid)
    tmp = folder / "samples" / f"_tmp_{name}"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(data)
    try:
        return _add_file(root, sid, tmp, name)
    finally:
        if tmp.exists():
            tmp.unlink()


def sample_path(root: Path, sid: str, sample_id: str) -> Path:
    if not SAMPLE_ID_RE.match(sample_id):
        raise BadRequest("bad sample id")
    folder = _speaker_dir(root, sid)
    samples_dir = folder / "samples"
    if not samples_dir.is_dir():
        raise NotFound(f"sample not found: {sample_id}")
    hits = list(samples_dir.glob(f"{sample_id}.*"))
    if not hits:
        raise NotFound(f"sample not found: {sample_id}")
    return hits[0]


def delete_sample(root: Path, sid: str, sample_id: str) -> None:
    folder = _speaker_dir(root, sid)
    meta = _load(folder)
    path = sample_path(root, sid, sample_id)
    path.unlink(missing_ok=True)
    meta["samples"] = [s for s in meta.get("samples") or [] if s.get("id") != sample_id]
    _save(folder, meta)
