from __future__ import annotations

import logging
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import catalog, cuts, jobs, match as speaker_match, projects as project_store, speakers as speaker_store
from .projects import ProjectError

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        jobs.reclaim_orphans(catalog.data_root())
    except Exception:
        log.exception("reclaim_orphans failed")
    yield
    try:
        jobs.interrupt_current()
    except Exception:
        log.exception("interrupt_current failed")


app = FastAPI(title="AstroTranscript", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _root():
    return catalog.data_root()


def _ok_or_raise(fn):
    try:
        return fn()
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    except OSError as exc:
        if project_store.is_no_space(exc):
            raise HTTPException(507, project_store.disk_space_detail(_root(), 1, "write")) from exc
        raise


@app.get("/api/health")
def health():
    root = catalog.data_root()
    return {"ok": True, "data_root": str(root), "exists": root.exists()}


@app.get("/api/speakers")
def list_speakers():
    return {"speakers": _ok_or_raise(lambda: speaker_store.list_speakers(_root()))}


@app.post("/api/speakers", status_code=201)
def create_speaker(payload: dict | None = None):
    return _ok_or_raise(lambda: speaker_store.create_speaker(_root(), (payload or {}).get("name") or ""))


@app.get("/api/speakers/{sid}")
def get_speaker(sid: str):
    return _ok_or_raise(lambda: speaker_store.get_speaker(_root(), sid))


@app.patch("/api/speakers/{sid}")
def patch_speaker(sid: str, payload: dict | None = None):
    return _ok_or_raise(lambda: speaker_store.patch_speaker(_root(), sid, payload or {}))


@app.delete("/api/speakers/{sid}")
def delete_speaker(sid: str):
    _ok_or_raise(lambda: speaker_store.delete_speaker(_root(), sid))
    return {"ok": True}


@app.post("/api/speakers/{sid}/samples", status_code=201)
def add_speaker_sample_path(sid: str, payload: dict | None = None):
    path = (payload or {}).get("path") or ""
    return _ok_or_raise(lambda: speaker_store.add_sample_path(_root(), sid, Path(path)))


@app.post("/api/speakers/{sid}/samples/upload", status_code=201)
async def upload_speaker_sample(sid: str, file: UploadFile = File(...)):
    data = await file.read()
    return _ok_or_raise(lambda: speaker_store.add_sample_bytes(_root(), sid, file.filename or "sample.wav", data))


@app.get("/api/speakers/{sid}/samples/{sample_id}")
def get_speaker_sample(sid: str, sample_id: str):
    path = _ok_or_raise(lambda: speaker_store.sample_path(_root(), sid, sample_id))
    suf = path.suffix.lower()
    media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".flac": "audio/flac"}.get(
        suf, "application/octet-stream"
    )
    return FileResponse(path, media_type=media, filename=path.name)


@app.delete("/api/speakers/{sid}/samples/{sample_id}")
def delete_speaker_sample(sid: str, sample_id: str):
    _ok_or_raise(lambda: speaker_store.delete_sample(_root(), sid, sample_id))
    return {"ok": True}


@app.get("/api/projects")
def projects(archived: int = 0):
    root = _root()
    items = _ok_or_raise(lambda: project_store.list_projects(root, archived=bool(archived)))
    return {"projects": items, "root": str(root)}


@app.post("/api/projects", status_code=201)
def create_project(payload: dict | None = None):
    return _ok_or_raise(lambda: project_store.create_project(_root(), (payload or {}).get("name") or ""))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    def work():
        data = project_store.get_project(_root(), pid)
        data["items"] = catalog.list_recordings(pid, _root())
        return data
    return _ok_or_raise(work)


@app.get("/api/projects/{pid}/search")
def search_project(pid: str, q: str = ""):
    return _ok_or_raise(lambda: catalog.search_transcripts(pid, q, _root()))


def _mp3_slice_response(src: Path, start: float, end: float, filename: str) -> FileResponse:
    from .process import slice_to_mp3

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    path = Path(tmp.name)
    try:
        slice_to_mp3(src, start, end, path)
    except FileNotFoundError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(404, str(exc) or "audio source missing") from exc
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(500, str(exc)) from exc
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=filename,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@app.get("/api/projects/{pid}/recordings/{stem}/lines/{n}/export.mp3")
def export_line_mp3(pid: str, stem: str, n: int):
    win = _ok_or_raise(lambda: catalog.line_audio_slice(pid, stem, n, _root()))
    return _mp3_slice_response(win["source"], win["start"], win["end"], win["filename"])


@app.get("/api/projects/{pid}/recordings/{stem}/lines/{n}/match.mp3")
def export_match_mp3(pid: str, stem: str, n: int, q: str = ""):
    win = _ok_or_raise(lambda: catalog.line_audio_slice(pid, stem, n, _root(), query=q))
    return _mp3_slice_response(win["source"], win["start"], win["end"], win["filename"])


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, payload: dict | None = None):
    return _ok_or_raise(lambda: project_store.rename_project(_root(), pid, (payload or {}).get("name") or ""))


@app.post("/api/projects/{pid}/archive")
def archive_project(pid: str):
    return _ok_or_raise(lambda: project_store.archive_project(_root(), pid))


@app.post("/api/projects/{pid}/restore")
def restore_project(pid: str):
    return _ok_or_raise(lambda: project_store.restore_project(_root(), pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _ok_or_raise(lambda: project_store.delete_project(_root(), pid))
    return {"ok": True}


@app.get("/api/projects/{pid}/cuts")
def list_cuts(pid: str):
    return {"cuts": _ok_or_raise(lambda: cuts.list_cuts(_root(), pid))}


@app.post("/api/projects/{pid}/cuts", status_code=201)
def create_cut(pid: str, payload: dict | None = None):
    body = payload or {}
    return _ok_or_raise(
        lambda: cuts.render_loop_cut(
            _root(),
            pid,
            stem=str(body.get("recording_id") or ""),
            start=float(body.get("start") or 0),
            end=float(body.get("end") or 0),
            source=str(body.get("source") or "original"),
            fx=body.get("fx") if isinstance(body.get("fx"), dict) else None,
            name=str(body.get("name") or ""),
        )
    )


@app.get("/api/projects/{pid}/cuts/{cid}/download")
def download_cut(pid: str, cid: str):
    def work():
        meta = cuts.get_cut(_root(), pid, cid)
        path = cuts.cut_mp3_path(_root(), pid, cid)
        return path, cuts.cut_download_name(meta)

    path, fname = _ok_or_raise(work)
    return FileResponse(path, media_type="audio/mpeg", filename=fname)


@app.post("/api/projects/{pid}/cuts/{cid}/remove")
def remove_cut(pid: str, cid: str):
    _ok_or_raise(lambda: cuts.delete_cut(_root(), pid, cid))
    return {"ok": True}


@app.post("/api/projects/{pid}/recordings", status_code=201)
def import_recording(pid: str, payload: dict | None = None):
    path = (payload or {}).get("path") or ""
    rec = _ok_or_raise(lambda: project_store.import_recording(_root(), pid, Path(path)))
    return catalog.project_summary(project_store.recording_dir(_root(), pid, rec["id"]))


@app.post("/api/projects/{pid}/recordings/upload", status_code=201)
async def upload_recording(pid: str, file: UploadFile = File(...)):
    rec = _ok_or_raise(
        lambda: project_store.ingest_uploaded_wav(_root(), pid, file.filename or "recording.wav", file.file)
    )
    return catalog.project_summary(project_store.recording_dir(_root(), pid, rec["id"]))


@app.post("/api/projects/{pid}/recordings/{stem}/remove")
def remove_recording(pid: str, stem: str, confirm: int = 0):
    _ok_or_raise(lambda: project_store.delete_recording(_root(), pid, stem, confirm=bool(confirm)))
    return {"ok": True}


@app.delete("/api/projects/{pid}/recordings/{stem}")
def delete_recording(pid: str, stem: str, confirm: int = 0):
    _ok_or_raise(lambda: project_store.delete_recording(_root(), pid, stem, confirm=bool(confirm)))
    return {"ok": True}


@app.post("/api/projects/{pid}/recordings/{stem}/move")
def move_recording(pid: str, stem: str, payload: dict | None = None):
    to_id = (payload or {}).get("to") or ""
    if not to_id:
        raise HTTPException(400, "to is required")
    return _ok_or_raise(lambda: project_store.move_recording(_root(), pid, stem, to_id))


@app.post("/api/projects/{pid}/recordings/{stem}/process")
def process_recording(pid: str, stem: str):
    from . import jobs

    return _ok_or_raise(lambda: jobs.enqueue(_root(), pid, stem))


@app.get("/api/projects/{pid}/recordings/{stem}/job")
def recording_job(pid: str, stem: str):
    from . import jobs

    return _ok_or_raise(lambda: jobs.snapshot(_root(), pid, stem))


@app.post("/api/projects/{pid}/recordings/{stem}/speakers/match")
def match_recording_speakers(pid: str, stem: str):
    return _ok_or_raise(lambda: speaker_match.match_recording(_root(), pid, stem))


@app.patch("/api/projects/{pid}/recordings/{stem}/speakers")
def assign_recording_speakers(pid: str, stem: str, payload: dict | None = None):
    body = payload or {}
    return _ok_or_raise(
        lambda: speaker_match.assign_speakers(
            _root(),
            pid,
            stem,
            n=int(body["n"]) if body.get("n") is not None else None,
            cluster=str(body["cluster"]) if body.get("cluster") else None,
            speaker_id=body.get("speaker_id"),
            follow_fx=body.get("follow_fx"),
            fx=body.get("fx") if isinstance(body.get("fx"), dict) else None,
            thresh=body.get("thresh") if isinstance(body.get("thresh"), dict) else None,
        )
    )


@app.post("/api/projects/{pid}/recordings/{stem}/speakers/harvest")
def harvest_recording_speakers(pid: str, stem: str, payload: dict | None = None):
    body = payload or {}
    ns = body.get("ns")
    nums = None
    if isinstance(ns, list):
        nums = [int(x) for x in ns]
    return _ok_or_raise(
        lambda: speaker_match.harvest_samples(
            _root(),
            pid,
            stem,
            speaker_id=body.get("speaker_id"),
            ns=nums,
        )
    )


@app.put("/api/projects/{pid}/recordings/{stem}/clock")
def set_recording_clock(pid: str, stem: str, payload: dict | None = None):
    body = payload or {}
    return _ok_or_raise(
        lambda: catalog.set_clock_anchor(_root(), pid, stem, body.get("rel_sec"), body.get("at"))
    )


@app.delete("/api/projects/{pid}/recordings/{stem}/clock")
def clear_recording_clock(pid: str, stem: str):
    return _ok_or_raise(lambda: catalog.clear_clock_anchor(_root(), pid, stem))


@app.put("/api/projects/{pid}/recordings/{stem}/markers")
def set_recording_markers(pid: str, stem: str, payload: dict | None = None):
    body = payload or {}
    if "markers" not in body:
        raise HTTPException(400, "нужен список меток")
    return _ok_or_raise(lambda: catalog.set_markers(_root(), pid, stem, body.get("markers")))


@app.put("/api/projects/{pid}/recordings/{stem}/clock-shift")
def apply_recording_clock_shift(pid: str, stem: str, payload: dict | None = None):
    body = payload or {}
    if "shift_sec" not in body:
        raise HTTPException(400, "shift_sec is required")
    return _ok_or_raise(lambda: catalog.apply_clock_shift(_root(), pid, stem, body.get("shift_sec")))


@app.patch("/api/projects/{pid}/recordings/{stem}")
def patch_recording(pid: str, stem: str, payload: dict | None = None):
    body = payload or {}
    if "title" not in body and "comment" not in body:
        raise HTTPException(400, "title or comment is required")
    kwargs = {}
    if "title" in body:
        kwargs["title"] = body.get("title")
    if "comment" in body:
        kwargs["comment"] = body.get("comment")
    return _ok_or_raise(lambda: catalog.update_recording_notes(_root(), pid, stem, **kwargs))


@app.get("/api/projects/{pid}/recordings/{stem}")
def recording(pid: str, stem: str):
    try:
        return catalog.load_project(stem, pid)
    except FileNotFoundError:
        raise HTTPException(404, f"recording not found: {stem}")
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@app.get("/api/projects/{pid}/recordings/{stem}/transcript.txt")
def transcript_txt(pid: str, stem: str):
    try:
        body = catalog.transcript_text(stem, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, stem)
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return PlainTextResponse(body or "(empty)\n")


@app.get("/api/projects/{pid}/recordings/{stem}/export.srt")
def export_srt(pid: str, stem: str):
    try:
        body = catalog.export_absolute_srt(stem, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, stem)
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return PlainTextResponse(body, media_type="application/x-subrip")


def _ranged_wav(path: Path, request: Request, filename: str) -> Response:
    size = path.stat().st_size
    start, end = 0, size - 1
    status = 200
    header = request.headers.get("range") or ""
    if header.lower().startswith("bytes="):
        spec = header.split("=", 1)[1].split(",")[0].strip()
        a, _, b = spec.partition("-")
        if a:
            start = max(0, int(a))
        if b:
            end = min(size - 1, int(b))
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = end - start + 1

    def chunks():
        with path.open("rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                buf = fh.read(min(256 * 1024, left))
                if not buf:
                    break
                left -= len(buf)
                yield buf

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f'inline; filename="{filename}"',
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        return Response(status_code=status, media_type="audio/wav", headers=headers)
    return StreamingResponse(chunks(), status_code=status, media_type="audio/wav", headers=headers)


@app.api_route("/api/projects/{pid}/recordings/{stem}/clips/{name}", methods=["GET", "HEAD"])
def clip(pid: str, stem: str, name: str, request: Request):
    try:
        path = catalog.clip_path(stem, name, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, name)
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return _ranged_wav(path, request, name)


@app.api_route("/api/projects/{pid}/recordings/{stem}/cleaned", methods=["GET", "HEAD"])
def cleaned(pid: str, stem: str, request: Request):
    try:
        path = catalog.cleaned_path(stem, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, stem)
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return _ranged_wav(path, request, f"{stem}.cleaned.wav")


@app.api_route("/api/projects/{pid}/recordings/{stem}/original", methods=["GET", "HEAD"])
def original(pid: str, stem: str, request: Request):
    try:
        path = catalog.playable_source_path(stem, project_id=pid) or catalog.source_path(stem, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, stem)
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    if path is None:
        raise HTTPException(404, "original source missing")
    return _ranged_wav(path, request, path.name)


@app.get("/api/projects/{pid}/recordings/{stem}/export.mp3")
def export_mp3(pid: str, stem: str):
    try:
        path = catalog.cleaned_mp3_path(stem, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, "cleaned speech missing — process the recording first")
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return FileResponse(path, media_type="audio/mpeg", filename=f"{stem}.mp3")


@app.get("/api/projects/{pid}/recordings/{stem}/wave/{kind}")
def wave_peaks(pid: str, stem: str, kind: str, bins: int = 1600):
    if kind not in {"original", "cleaned"}:
        raise HTTPException(400, "kind must be original|cleaned")
    try:
        peaks = catalog.cached_peaks(stem, kind, bins=bins, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, kind)
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return {"kind": kind, "peaks": peaks}


@app.get("/api/projects/{pid}/recordings/{stem}/peaks/{name}")
def peaks(pid: str, stem: str, name: str, bins: int = 96):
    try:
        path = catalog.clip_path(stem, name, project_id=pid)
    except FileNotFoundError:
        raise HTTPException(404, name)
    except ProjectError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    bins = max(16, min(int(bins), 256))
    try:
        with wave.open(str(path), "rb") as w:
            n = w.getnframes()
            sw = w.getsampwidth()
            ch = w.getnchannels()
            raw = w.readframes(n)
        if sw != 2 or not raw:
            return {"peaks": [0.0] * bins}
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        if x.size < bins:
            pad = np.zeros(bins, dtype=np.float32)
            pad[: x.size] = np.abs(x)
            vals = pad.tolist()
        else:
            step = x.size / bins
            vals = []
            for i in range(bins):
                a = int(i * step)
                b = int((i + 1) * step)
                sl = x[a:b]
                vals.append(float(np.max(np.abs(sl))) if sl.size else 0.0)
        return {"peaks": vals, "samples": int(x.size)}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/index")
def rebuild_index():
    path = catalog.write_index()
    return {"path": str(path)}


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/")
def index():
    html = FRONTEND / "index.html"
    if not html.exists():
        return JSONResponse({"error": "frontend missing"}, status_code=500)
    return FileResponse(html)


if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")
