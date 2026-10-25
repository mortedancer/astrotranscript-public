"""Background VAD / denoise / ASR jobs for recordings."""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Optional

from . import projects as project_store
from .pipeline import DEFAULT_PIPELINE, PipelineConfig
from .projects import Conflict, NotFound, _read_json, _write_json

log = logging.getLogger(__name__)

Processor = Callable[[Path, Path, PipelineConfig, Callable[[str], None]], Any]

_q: Queue = Queue()
_lock = threading.Lock()
_current: Optional[dict[str, Any]] = None
_worker: Optional[threading.Thread] = None
_processor: Optional[Processor] = None


def set_processor(fn: Optional[Processor]) -> None:
    global _processor
    _processor = fn


def _rec_meta_path(folder: Path) -> Path:
    return folder / "recording.json"


def read_meta(folder: Path) -> dict[str, Any]:
    path = _rec_meta_path(folder)
    if path.exists():
        try:
            data = _read_json(path)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "id": folder.name,
        "source": "",
        "status": "pending",
        "error": None,
        "stage": "",
        "log": [],
    }


def write_meta(folder: Path, meta: dict[str, Any]) -> dict[str, Any]:
    _write_json(_rec_meta_path(folder), meta)
    return meta


_OP_LABEL = {
    "queued": "Queued",
    "decode": "Decode to PCM",
    "enhance": "Noise reduction / AGC",
    "vad": "Voice activity",
    "asr": "Transcription",
    "concat": "Concat speech clips",
    "mp3": "Encode speech MP3",
    "done": "Done",
}

# (overall start %, span %) so the bar moves across the whole pipeline
_OP_OVERALL = {
    "queued": (0.0, 0.0),
    "decode": (0.0, 8.0),
    "enhance": (8.0, 20.0),
    "vad": (28.0, 20.0),
    "asr": (48.0, 47.0),
    "concat": (95.0, 3.0),
    "mp3": (98.0, 2.0),
    "done": (100.0, 0.0),
}


def make_progress(
    op: str,
    *,
    done: Optional[int] = None,
    total: Optional[int] = None,
    detail: str = "",
) -> dict[str, Any]:
    start, span = _OP_OVERALL.get(op, (0.0, 0.0))
    frac = 0.0
    op_pct = None
    if total and total > 0 and done is not None:
        frac = min(1.0, max(0.0, float(done) / float(total)))
        op_pct = round(100.0 * frac, 1)
    overall = 100.0 if op == "done" else round(start + span * frac, 1)
    base = _OP_LABEL.get(op, op)
    if done is not None and total:
        label = f"{base} {int(done)}/{int(total)}"
    else:
        label = base
    if detail:
        label = f"{label} · {detail}"
    return {
        "op": op,
        "label": label,
        "done": done,
        "total": total,
        "pct": op_pct,
        "overall_pct": overall,
        "detail": detail or "",
    }


_ASR_LOG = re.compile(r"clean\+asr\s+(\d+)/(\d+)")
_VAD_LOG = re.compile(r"^\s+(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)\s+spans\+=")


def infer_progress(meta: dict[str, Any]) -> Optional[dict[str, Any]]:
    raw = meta.get("progress")
    if isinstance(raw, dict) and raw.get("op"):
        return raw
    dur = float(meta.get("duration_sec") or 0.0)
    for line in reversed(list(meta.get("log") or [])):
        m = _ASR_LOG.search(str(line))
        if m:
            return make_progress("asr", done=int(m.group(1)), total=int(m.group(2)))
        m = _VAD_LOG.match(str(line))
        if m:
            sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            total = max(1, int(dur) or int(sec) or 1)
            clock = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
            return make_progress("vad", done=int(sec), total=total, detail=clock)
        if str(line).startswith("adaptive preprocess"):
            return make_progress("enhance")
        if str(line).startswith("decode "):
            return make_progress("decode")
        if str(line).startswith("queued "):
            return make_progress("queued")
    stage = str(meta.get("stage") or "")
    if stage in _OP_LABEL:
        return make_progress(stage)
    return None


def set_progress(
    folder: Path,
    op: str,
    *,
    done: Optional[int] = None,
    total: Optional[int] = None,
    detail: str = "",
) -> dict[str, Any]:
    progress = make_progress(op, done=done, total=total, detail=detail)
    with _lock:
        meta = read_meta(folder)
        if op != "done":
            meta["stage"] = op
        else:
            meta["stage"] = "done"
        meta["progress"] = progress
        write_meta(folder, meta)
    return progress


def _append_log(folder: Path, line: str) -> dict[str, Any]:
    with _lock:
        meta = read_meta(folder)
        lines = list(meta.get("log") or [])
        lines.append(line)
        meta["log"] = lines[-200:]
        return write_meta(folder, meta)


def snapshot(root: Path, pid: str, stem: str) -> dict[str, Any]:
    folder = project_store.recording_dir(root, pid, stem)
    meta = read_meta(folder)
    with _lock:
        cur = dict(_current) if _current else None
    meta["running"] = bool(cur and cur.get("project_id") == pid and cur.get("stem") == stem)
    meta["progress"] = infer_progress(meta)
    return meta


def enqueue(root: Path, pid: str, stem: str, pipeline: Optional[PipelineConfig] = None) -> dict[str, Any]:
    reclaim_orphans(root)
    folder = project_store.recording_dir(root, pid, stem)
    meta = read_meta(folder)
    status = str(meta.get("status") or "pending")
    with _lock:
        cur = dict(_current) if _current else None
    if cur and cur.get("project_id") == pid and cur.get("stem") == stem:
        raise Conflict(f"recording already processing: {stem}")
    if status in {"queued", "vad", "processing"}:
        _mark_interrupted(folder, meta)
        meta = read_meta(folder)
    if not meta.get("source"):
        sm = folder / "speech_map.json"
        if sm.exists():
            try:
                meta["source"] = _read_json(sm).get("source") or ""
            except Exception:
                pass
    if not meta.get("source") or not Path(str(meta["source"])).is_file():
        raise NotFound(f"source wav missing for {stem}")
    meta["status"] = "queued"
    meta["error"] = None
    meta["stage"] = "queued"
    meta["progress"] = make_progress("queued")
    meta["log"] = list(meta.get("log") or []) + [f"queued {stem}"]
    write_meta(folder, meta)
    _q.put((str(root), pid, stem, pipeline or DEFAULT_PIPELINE))
    _ensure_worker()
    return meta


def wait_idle(timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            busy = _current is not None
        if _q.empty() and not busy:
            return
        time.sleep(0.05)
    raise TimeoutError("processing job did not finish")


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="astro-jobs", daemon=True)
        _worker.start()


def _loop() -> None:
    global _current
    while True:
        try:
            root_s, pid, stem, pipeline = _q.get(timeout=0.5)
        except Empty:
            continue
        root = Path(root_s)
        with _lock:
            _current = {"project_id": pid, "stem": stem, "status": "processing", "root": str(root)}
        try:
            _run_one(root, pid, stem, pipeline)
        except Exception:
            log.exception("job crashed %s/%s", pid, stem)
        finally:
            with _lock:
                _current = None
            _q.task_done()


def _run_one(root: Path, pid: str, stem: str, pipeline: PipelineConfig) -> None:
    folder = project_store.recording_dir(root, pid, stem)
    meta = read_meta(folder)
    src = Path(str(meta.get("source") or ""))
    if not src.is_file():
        meta["status"] = "error"
        meta["error"] = f"source wav missing: {src}"
        meta["stage"] = "error"
        write_meta(folder, meta)
        return

    def log_line(msg: str) -> None:
        _append_log(folder, msg)

    meta["status"] = "processing"
    meta["stage"] = "decode"
    meta["error"] = None
    meta["progress"] = make_progress("decode")
    write_meta(folder, meta)
    log_line(f"start {src.name}")
    fn = _processor
    if fn is None:
        from .process import process_file

        fn = process_file
    try:
        fn(src, folder, pipeline, log_line)
        meta = read_meta(folder)
        meta["status"] = "completed"
        meta["stage"] = "done"
        meta["error"] = None
        meta["progress"] = make_progress("done", done=1, total=1)
        write_meta(folder, meta)
        log_line("completed")
    except Exception as exc:
        meta = read_meta(folder)
        meta["status"] = "error"
        meta["stage"] = "error"
        meta["error"] = str(exc)
        write_meta(folder, meta)
        log_line(f"error: {exc}")


_STAGE_TAIL = {
    "asr": "during ASR",
    "vad": "during VAD",
    "enhance": "during preprocess",
    "queued": "while queued",
    "processing": "during processing",
}


def _mark_interrupted(folder: Path, meta: dict[str, Any]) -> None:
    stage = str(meta.get("stage") or "processing")
    tail = _STAGE_TAIL.get(stage, "during processing")
    meta["status"] = "error"
    meta["stage"] = "error"
    meta["error"] = f"interrupted: API process exited {tail}"
    lines = list(meta.get("log") or [])
    lines.append("interrupted: API process exited")
    meta["log"] = lines[-200:]
    write_meta(folder, meta)


def reclaim_orphans(root: Path) -> int:
    """Mark jobs left `queued`/`processing` after the API process died."""
    n = 0
    with _lock:
        cur = dict(_current) if _current else None
    for proj in project_store.list_projects(root, archived=True):
        folder = project_store.project_dir(root, proj["id"])
        for child in folder.iterdir():
            if not project_store.is_recording_dir(child):
                continue
            meta = read_meta(child)
            if str(meta.get("status") or "") not in {"queued", "vad", "processing"}:
                continue
            if cur and cur.get("project_id") == proj["id"] and cur.get("stem") == child.name:
                continue
            _mark_interrupted(child, meta)
            n += 1
    return n


def interrupt_current() -> None:
    with _lock:
        cur = dict(_current) if _current else None
    if not cur:
        return
    try:
        folder = project_store.recording_dir(Path(cur["root"]), cur["project_id"], cur["stem"])
    except Exception:
        return
    _mark_interrupted(folder, read_meta(folder))
