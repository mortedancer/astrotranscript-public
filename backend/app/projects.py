from __future__ import annotations

import errno
import json
import logging
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

INBOX_ID = "inbox"
ARCHIVE_NAME = ".archive"
RESERVED_IDS = frozenset({"inbox", "archive"})
NAME_MAX = 80
SLUG_MAX = 60

_SLUG_BAD = re.compile(r"[^\w\-]+", re.UNICODE)
_SLUG_SEP = re.compile(r"[_-]+")


class ProjectError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class NotFound(ProjectError):
    def __init__(self, detail: str):
        super().__init__(404, detail)


class Forbidden(ProjectError):
    def __init__(self, detail: str):
        super().__init__(403, detail)


class Conflict(ProjectError):
    def __init__(self, detail: str):
        super().__init__(409, detail)


class BadRequest(ProjectError):
    def __init__(self, detail: str):
        super().__init__(400, detail)


class InsufficientStorage(ProjectError):
    def __init__(self, detail: str):
        super().__init__(507, detail)


def _fmt_bytes(n: int) -> str:
    x = float(max(0, int(n)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024.0 or unit == "TB":
            return f"{int(x)} B" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024.0
    return f"{int(n)} B"


def is_no_space(exc: BaseException) -> bool:
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "errno", None) in {errno.ENOSPC, getattr(errno, "EDQUOT", None)}:
        return True
    if getattr(exc, "winerror", None) == 112:
        return True
    msg = str(exc).lower()
    return "no space" in msg or "not enough space" in msg or "disk full" in msg


def disk_space_detail(path: Path, need: int, what: str) -> str:
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        free = 0
    drive = str(Path(path).anchor or path)
    return (
        f"{drive} is full: {_fmt_bytes(free)} free, need {_fmt_bytes(need)} for {what}. "
        "Free space or set ASTRO_DATA_ROOT to another disk. "
        "Add path stores a link (no WAV copy); drag-drop copies into the data root."
    )


def require_disk_space(path: Path, need: int, *, what: str) -> None:
    need = max(0, int(need))
    try:
        free = shutil.disk_usage(path).free
    except OSError as exc:
        if is_no_space(exc):
            raise InsufficientStorage(disk_space_detail(path, need, what)) from exc
        raise
    if free < need:
        raise InsufficientStorage(disk_space_detail(path, need, what))


def _upload_nbytes(data: Any) -> int:
    if isinstance(data, (bytes, bytearray, memoryview)):
        return len(data)
    seek = getattr(data, "seek", None)
    tell = getattr(data, "tell", None)
    if not callable(seek) or not callable(tell):
        return 0
    try:
        pos = tell()
        seek(0, 2)
        n = int(tell())
        seek(pos)
        return n
    except OSError:
        return 0


def validate_name(name: str) -> str:
    text = (name or "").strip()
    if not text or len(text) > NAME_MAX:
        raise BadRequest("name must be 1–80 characters")
    return text


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFC", (name or "").strip())
    text = _SLUG_BAD.sub("-", text)
    text = _SLUG_SEP.sub("-", text)
    text = text.strip("-")
    out = []
    for ch in text:
        if "A" <= ch <= "Z" or "a" <= ch <= "z":
            out.append(ch.lower())
        else:
            out.append(ch.casefold() if ch.isalpha() else ch)
    text = "".join(out)
    if not text:
        text = "project"
    return text[:SLUG_MAX]


def _folder_taken(root: Path, slug: str) -> bool:
    if slug.casefold() in {s.casefold() for s in RESERVED_IDS}:
        return True
    if (root / slug).exists():
        return True
    if (root / ARCHIVE_NAME / slug).exists():
        return True
    return False


def unique_slug(root: Path, name: str) -> str:
    base = slugify(name)
    if not _folder_taken(root, base):
        return base
    n = 2
    while _folder_taken(root, f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_inbox_name(name: str) -> bool:
    return name.casefold() == INBOX_ID


def _is_archive_name(name: str) -> bool:
    return name.casefold() == ARCHIVE_NAME


def _new_meta(pid: str, name: str, archived: bool = False) -> dict[str, Any]:
    return {"id": pid, "name": name, "created_at": now_iso(), "archived": archived}


def ensure_layout(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    inbox = root / INBOX_ID
    inbox.mkdir(exist_ok=True)
    meta_path = inbox / "project.json"
    if not meta_path.exists():
        _write_json(meta_path, _new_meta(INBOX_ID, "Inbox", False))
    (root / ARCHIVE_NAME).mkdir(exist_ok=True)
    for child in list(root.iterdir()):
        if not child.is_dir():
            continue
        if _is_inbox_name(child.name) or _is_archive_name(child.name) or child.name.startswith("."):
            continue
        if (child / "project.json").exists():
            continue
        if not (child / "speech_map.json").exists():
            continue
        dest = inbox / child.name
        if dest.exists():
            log.warning("skip migrate %s: %s already exists", child, dest)
            continue
        shutil.move(str(child), str(dest))
    return root


def project_dir(root: Path, pid: str) -> Path:
    ensure_layout(root)
    active = root / pid
    if (active / "project.json").exists():
        return active
    archived = root / ARCHIVE_NAME / pid
    if (archived / "project.json").exists():
        return archived
    raise NotFound(f"project not found: {pid}")


def _load_meta(folder: Path) -> dict[str, Any]:
    meta = _read_json(folder / "project.json")
    meta["archived"] = folder.parent.name == ARCHIVE_NAME
    meta["id"] = folder.name if folder.name.casefold() != INBOX_ID else INBOX_ID
    return meta


def is_recording_dir(d: Path) -> bool:
    if not d.is_dir():
        return False
    if d.name.lower() in {"cuts", ".archive"}:
        return False
    if (d / "speech_map.json").exists() or (d / "recording.json").exists():
        return True
    try:
        return any(p.is_file() and p.suffix.lower() == ".wav" for p in d.iterdir())
    except OSError:
        return False


def _recording_dirs(folder: Path) -> list[Path]:
    out = []
    for child in sorted(folder.iterdir()):
        if is_recording_dir(child):
            out.append(child)
    return out


def _summary_from_folder(folder: Path) -> dict[str, Any]:
    meta = _load_meta(folder)
    recs = _recording_dirs(folder)
    statuses: dict[str, int] = {}
    duration = 0.0
    speech = 0.0
    bytes_ = 0
    for rec in recs:
        sm = {}
        try:
            sm = _read_json(rec / "speech_map.json")
        except Exception:
            sm = {}
        duration += float(sm.get("duration_sec") or 0.0)
        speech += float(sm.get("speech_sec") or 0.0)
        for p in rec.rglob("*"):
            if p.is_file():
                try:
                    bytes_ += p.stat().st_size
                except OSError:
                    pass
        st = "pending"
        rec_meta_path = rec / "recording.json"
        if rec_meta_path.exists():
            try:
                st = str(_read_json(rec_meta_path).get("status") or st)
            except Exception:
                st = "pending"
        if st == "pending":
            if (rec / "transcript.txt").exists() and (rec / "speech_map.json").exists():
                st = "completed"
            elif (rec / "speech_map.json").exists() and (rec / "clips").exists():
                st = "processing"
            elif (rec / "speech_map.json").exists():
                st = "vad"
        statuses[st] = statuses.get(st, 0) + 1
    return {
        "id": meta["id"],
        "name": meta.get("name") or meta["id"],
        "created_at": meta.get("created_at"),
        "archived": bool(meta.get("archived")),
        "protected": meta["id"] == INBOX_ID,
        "recordings": len(recs),
        "duration_sec": duration,
        "speech_sec": speech,
        "bytes": bytes_,
        "statuses": statuses,
        "cuts_count": len(list((folder / "cuts").glob("*.json"))) if (folder / "cuts").is_dir() else 0,
    }


def list_projects(root: Path, *, archived: bool = False) -> list[dict[str, Any]]:
    ensure_layout(root)
    items = []
    for child in root.iterdir():
        if child.is_dir() and (child / "project.json").exists():
            items.append(_summary_from_folder(child))
    if archived:
        arch = root / ARCHIVE_NAME
        if arch.exists():
            for child in arch.iterdir():
                if child.is_dir() and (child / "project.json").exists():
                    items.append(_summary_from_folder(child))
    inbox = [p for p in items if p["id"] == INBOX_ID]
    rest = [p for p in items if p["id"] != INBOX_ID]
    rest.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return inbox + rest


def create_project(root: Path, name: str) -> dict[str, Any]:
    ensure_layout(root)
    name = validate_name(name)
    pid = unique_slug(root, name)
    folder = root / pid
    folder.mkdir()
    _write_json(folder / "project.json", _new_meta(pid, name, False))
    return _summary_from_folder(folder)


def get_project(root: Path, pid: str) -> dict[str, Any]:
    folder = project_dir(root, pid)
    data = _summary_from_folder(folder)
    data["items"] = []  # catalog fills this in a later task
    return data


def rename_project(root: Path, pid: str, name: str) -> dict[str, Any]:
    name = validate_name(name)
    folder = project_dir(root, pid)
    meta = _load_meta(folder)
    meta["name"] = name
    meta["archived"] = folder.parent.name == ARCHIVE_NAME
    _write_json(folder / "project.json", {k: meta[k] for k in ("id", "name", "created_at", "archived")})
    return _summary_from_folder(folder)


def recording_dir(root: Path, pid: str, stem: str) -> Path:
    d = project_dir(root, pid) / stem
    if not is_recording_dir(d):
        raise NotFound(f"recording not found: {stem}")
    return d


def find_stem(root: Path, stem: str) -> Optional[str]:
    ensure_layout(root)
    for p in list_projects(root, archived=True):
        if is_recording_dir(project_dir(root, p["id"]) / stem):
            return p["id"]
    return None


def _require_active(root: Path, pid: str) -> Path:
    folder = project_dir(root, pid)
    if folder.parent.name == ARCHIVE_NAME:
        raise Forbidden("Project is archived")
    return folder


def _require_not_inbox(pid: str) -> None:
    if pid.casefold() == INBOX_ID:
        raise Forbidden("Inbox cannot be archived or deleted")


AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}


def _import_wav(root: Path, pid: str, src: Path) -> dict[str, Any]:
    folder = _require_active(root, pid)
    stem = src.stem
    if not stem:
        raise BadRequest("wav filename has no stem")
    owner = find_stem(root, stem)
    if owner:
        raise Conflict(f"stem already exists in project {owner}: {stem}")
    dest = folder / stem
    if dest.exists():
        raise Conflict(f"stem already exists in project {pid}: {stem}")
    try:
        dest.mkdir()
    except OSError as exc:
        if is_no_space(exc):
            raise InsufficientStorage(disk_space_detail(folder, 65536, "recording metadata")) from exc
        raise
    duration = 0.0
    try:
        import wave

        with wave.open(str(src), "rb") as w:
            sr = w.getframerate() or 1
            duration = w.getnframes() / float(sr)
    except Exception:
        duration = 0.0
    _write_json(
        dest / "recording.json",
        {
            "id": stem,
            "source": str(src.resolve()),
            "created_at": now_iso(),
            "status": "pending",
            "error": None,
            "stage": "",
            "log": [],
            "duration_sec": duration,
        },
    )
    return {"id": stem, "project_id": pid, "name": stem, "status": "pending"}


def _write_upload(dest: Path, data: Any) -> None:
    if isinstance(data, (bytes, bytearray, memoryview)):
        if not data:
            raise BadRequest("empty file")
        dest.write_bytes(bytes(data))
        return
    read = getattr(data, "read", None)
    if not callable(read):
        raise BadRequest("empty file")
    with dest.open("wb") as out:
        shutil.copyfileobj(data, out, length=1024 * 1024)
    if dest.stat().st_size <= 0:
        raise BadRequest("empty file")


def ingest_uploaded_wav(root: Path, pid: str, filename: str, data: Any) -> dict[str, Any]:
    ensure_layout(root)
    if data is None:
        raise BadRequest("empty file")
    name = Path(filename or "recording.wav").name
    suf = Path(name).suffix.lower()
    if suf not in AUDIO_EXTS:
        raise BadRequest("drop WAV or MP3 (also m4a/ogg/flac)")
    folder = _require_active(root, pid)
    base = Path(name).stem or "recording"
    stem = base
    n = 2
    while find_stem(root, stem) or (folder / stem).exists():
        stem = f"{base}-{n}"
        n += 1
    dest = folder / stem
    nbytes = _upload_nbytes(data)
    if nbytes:
        require_disk_space(folder, nbytes + 65536, what=f"upload {name}")
    try:
        dest.mkdir()
    except OSError as exc:
        if is_no_space(exc):
            raise InsufficientStorage(disk_space_detail(folder, nbytes + 65536, f"upload {name}")) from exc
        raise
    wav_path = dest / f"original{suf}"
    try:
        _write_upload(wav_path, data)
    except OSError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        if is_no_space(exc):
            raise InsufficientStorage(disk_space_detail(folder, nbytes + 65536, f"upload {name}")) from exc
        raise
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    duration = 0.0
    try:
        from .process import probe_duration

        duration = probe_duration(wav_path)
    except Exception:
        duration = 0.0
    _write_json(
        dest / "recording.json",
        {
            "id": stem,
            "source": str(wav_path.resolve()),
            "created_at": now_iso(),
            "status": "pending",
            "error": None,
            "stage": "",
            "log": [],
            "duration_sec": duration,
        },
    )
    return {"id": stem, "project_id": pid, "name": stem, "status": "pending"}


def import_recording(root: Path, pid: str, src: Path) -> dict[str, Any]:
    ensure_layout(root)
    src = Path(src)
    if not src.exists():
        raise NotFound(f"path not found: {src}")
    if src.is_file() and src.suffix.lower() in AUDIO_EXTS:
        return _import_wav(root, pid, src)
    folder = _require_active(root, pid)
    if not src.is_dir() or not (src / "speech_map.json").exists():
        raise BadRequest("path must be an audio file (wav/mp3/…) or a processed folder (speech_map.json)")
    stem = src.name
    owner = find_stem(root, stem)
    if owner:
        raise Conflict(f"stem already exists in project {owner}: {stem}")
    dest = folder / stem
    if dest.exists():
        raise Conflict(f"stem already exists in project {pid}: {stem}")
    try:
        shutil.move(str(src), str(dest))
    except OSError as exc:
        raise Conflict(str(exc)) from exc
    return {"id": stem, "project_id": pid, "name": stem}


def move_recording(root: Path, pid: str, stem: str, to_id: str) -> dict[str, Any]:
    ensure_layout(root)
    src_folder = _require_active(root, pid)
    dest_folder = _require_active(root, to_id)
    src = src_folder / stem
    if not is_recording_dir(src):
        raise NotFound(f"recording not found: {stem}")
    dest = dest_folder / stem
    if dest.exists():
        raise Conflict(f"stem already exists in project {to_id}: {stem}")
    try:
        shutil.move(str(src), str(dest))
    except OSError as exc:
        raise Conflict(str(exc)) from exc
    return {"id": stem, "project_id": to_id}


def is_processed_recording(folder: Path) -> bool:
    if (folder / "speech_map.json").exists():
        return True
    if (folder / "transcript.txt").exists():
        return True
    clips = folder / "clips"
    if clips.is_dir() and any(clips.glob("*.wav")):
        return True
    meta_path = folder / "recording.json"
    if meta_path.exists():
        try:
            st = str(_read_json(meta_path).get("status") or "")
        except Exception:
            st = ""
        if st in {"completed", "vad", "processing"}:
            return True
    return False


def recording_links(root: Path, stem: str) -> list[dict[str, Any]]:
    ensure_layout(root)
    hits = []
    for p in list_projects(root, archived=True):
        folder = project_dir(root, p["id"]) / stem
        if not is_recording_dir(folder):
            continue
        hits.append(
            {
                "project_id": p["id"],
                "project_name": p["name"],
                "archived": bool(p.get("archived")),
                "processed": is_processed_recording(folder),
            }
        )
    return hits


def delete_recording(root: Path, pid: str, stem: str, *, confirm: bool = False) -> None:
    ensure_layout(root)
    folder = recording_dir(root, pid, stem)
    links = recording_links(root, stem)
    processed_in = [h for h in links if h["processed"]]
    if processed_in and not confirm:
        names = ", ".join(f"{h['project_name']} ({h['project_id']})" for h in processed_in)
        raise Conflict(
            f"recording {stem} is already processed in {names}; confirm to permanently delete"
        )
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        raise Conflict(str(exc)) from exc


def archive_project(root: Path, pid: str) -> dict[str, Any]:
    ensure_layout(root)
    _require_not_inbox(pid)
    folder = project_dir(root, pid)
    if folder.parent.name == ARCHIVE_NAME:
        return _summary_from_folder(folder)
    dest = root / ARCHIVE_NAME / folder.name
    if dest.exists():
        raise Conflict(f"archive destination exists: {dest.name}")
    try:
        shutil.move(str(folder), str(dest))
    except OSError as exc:
        raise Conflict(str(exc)) from exc
    meta = _load_meta(dest)
    meta["archived"] = True
    _write_json(dest / "project.json", {k: meta[k] for k in ("id", "name", "created_at", "archived")})
    return _summary_from_folder(dest)


def restore_project(root: Path, pid: str) -> dict[str, Any]:
    ensure_layout(root)
    folder = project_dir(root, pid)
    if folder.parent.name != ARCHIVE_NAME:
        return _summary_from_folder(folder)
    dest = root / folder.name
    if dest.exists():
        raise Conflict(f"active folder exists: {folder.name}")
    try:
        shutil.move(str(folder), str(dest))
    except OSError as exc:
        raise Conflict(str(exc)) from exc
    meta = _load_meta(dest)
    meta["archived"] = False
    _write_json(dest / "project.json", {k: meta[k] for k in ("id", "name", "created_at", "archived")})
    return _summary_from_folder(dest)


def delete_project(root: Path, pid: str) -> None:
    ensure_layout(root)
    _require_not_inbox(pid)
    folder = _require_active(root, pid)
    inbox = _require_active(root, INBOX_ID)
    recs = _recording_dirs(folder)
    for rec in recs:
        dest = inbox / rec.name
        if dest.exists():
            raise Conflict(f"stem already exists in project {INBOX_ID}: {rec.name}")
    for rec in recs:
        dest = inbox / rec.name
        try:
            shutil.move(str(rec), str(dest))
        except OSError as exc:
            raise Conflict(str(exc)) from exc
    meta_data = _load_meta(folder)
    meta = folder / "project.json"
    try:
        meta.unlink()
        folder.rmdir()
    except OSError as exc:
        if not meta.exists():
            _write_json(meta, {k: meta_data[k] for k in ("id", "name", "created_at", "archived")})
        raise Conflict(str(exc)) from exc
