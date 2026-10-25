"""Scan processed recordings and attach wall-clock timestamps."""
from __future__ import annotations

import json
import re
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .astro_time import (
    format_astro,
    format_clock,
    format_rel,
    format_signed_hms,
    iso_astro,
    parse_hms,
    parse_rel_point,
    parse_wall_datetime,
    relative_to_absolute,
    resolve_clock,
)
from . import jobs, projects as project_store
from .pipeline import DEFAULT_PIPELINE

LINE_RE = re.compile(
    r"\[(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)[–—-](\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)\]\s*(.*)$"
)
CLIP_RE = re.compile(r"^(\d{4})_(\d+)-(\d+)\.wav$", re.I)


def data_root(override: Optional[str] = None) -> Path:
    import os

    raw = override or os.environ.get("ASTRO_DATA_ROOT")
    if not raw:
        raw = Path.cwd() / "data"
    return Path(raw)


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def match_time_span(
    text: str,
    query: str,
    start_rel: float,
    end_rel: float,
    *,
    pad: float = 0.12,
    min_dur: float = 0.28,
) -> tuple[float, float]:
    """Map a case-insensitive substring onto the line's time range (first hit)."""
    body = text or ""
    needle = (query or "").strip()
    if not needle:
        raise ValueError("empty query")
    hay = body.casefold()
    n = needle.casefold()
    at = hay.find(n)
    if at < 0:
        raise ValueError("query not in line")
    end_at = at + len(n)
    start_rel = float(start_rel)
    end_rel = float(end_rel)
    dur = max(0.0, end_rel - start_rel)
    nchars = max(1, len(body))
    frac0 = at / nchars
    frac1 = min(1.0, end_at / nchars)
    if dur <= 0:
        a = b = start_rel
    else:
        a = start_rel + dur * frac0
        b = start_rel + dur * frac1
    a -= float(pad)
    b += float(pad)
    need = float(min_dur)
    if b - a < need:
        extra = (need - (b - a)) / 2.0
        a -= extra
        b += extra
    if a < 0:
        b -= a
        a = 0.0
    if b < a:
        b = a
    return a, b


def recording_line(project_id: str, stem: str, n: int, root: Optional[Path] = None) -> dict[str, Any]:
    root = root or data_root()
    d = project_store.recording_dir(root, project_id, stem)
    try:
        n = int(n)
    except (TypeError, ValueError) as exc:
        raise project_store.NotFound(f"line not found: {n}") from exc
    if n < 1:
        raise project_store.NotFound(f"line not found: {n}")
    tx = d / "transcript.txt"
    if not tx.exists():
        raise project_store.NotFound(f"transcript missing: {stem}")
    parsed = parse_transcript_lines(tx.read_text(encoding="utf-8"))
    if n > len(parsed):
        raise project_store.NotFound(f"line not found: {n}")
    row = parsed[n - 1]
    return {
        "n": n,
        "text": row["text"],
        "start_rel": float(row["start_rel"]),
        "end_rel": float(row["end_rel"]),
    }


def timeline_source(stem: str, project_id: str, root: Optional[Path] = None) -> Path:
    """Audio on the original recording timeline (not concatenated cleaned speech)."""
    root = root or data_root()
    d = project_store.recording_dir(root, project_id, stem)
    from .process import is_pcm16_wav

    for name in ("pcm16.wav", "pcm16_adapt.wav"):
        p = d / name
        try:
            if p.is_file() and is_pcm16_wav(p):
                return p
        except OSError:
            continue
    src = playable_source_path(stem, project_id, root) or source_path(stem, project_id, root)
    if src is None or not Path(src).is_file():
        raise project_store.NotFound("audio source missing")
    return Path(src)


def line_download_name(stem: str, n: int, query: Optional[str] = None) -> str:
    stem_s = re.sub(r"[^\w.\-]+", "_", str(stem)).strip("._")[:40] or "rec"
    if query and str(query).strip():
        q = re.sub(r"[^\w\-]+", "_", str(query).strip(), flags=re.UNICODE).strip("_")[:40] or "match"
        return f"{stem_s}_n{n}_{q}.mp3"
    return f"{stem_s}_n{n}.mp3"


def line_audio_slice(
    project_id: str,
    stem: str,
    n: int,
    root: Optional[Path] = None,
    *,
    query: Optional[str] = None,
) -> dict[str, Any]:
    row = recording_line(project_id, stem, n, root)
    if query is not None:
        q = (query or "").strip()
        if not q:
            raise project_store.BadRequest("q is required")
        try:
            a, b = match_time_span(row["text"], q, row["start_rel"], row["end_rel"])
        except ValueError as exc:
            raise project_store.BadRequest(str(exc)) from exc
        kind = "match"
        fname = line_download_name(stem, row["n"], q)
    else:
        a = float(row["start_rel"])
        b = float(row["end_rel"])
        if b - a < 0.12:
            b = a + 0.12
        kind = "line"
        fname = line_download_name(stem, row["n"])
    src = timeline_source(stem, project_id, root)
    return {
        "n": row["n"],
        "text": row["text"],
        "start": a,
        "end": b,
        "source": src,
        "kind": kind,
        "filename": fname,
    }


def parse_transcript_lines(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    in_timed = False
    for raw in text.splitlines():
        if raw.startswith("=== С таймкодами"):
            in_timed = True
            continue
        if raw.startswith("=== Сплошной"):
            break
        if not in_timed:
            continue
        m = LINE_RE.match(raw.strip())
        if not m:
            continue
        start = parse_hms(m.group(1), m.group(2), m.group(3))
        end = parse_hms(m.group(4), m.group(5), m.group(6))
        body = (m.group(7) or "").strip()
        if not body:
            continue
        lines.append({"start_rel": start, "end_rel": end, "text": body})
    return lines


def _recording_meta(d: Path) -> dict[str, Any]:
    p = d / "recording.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _status(d: Path) -> str:
    meta = _recording_meta(d)
    st = str(meta.get("status") or "")
    if st in {"queued", "vad", "processing", "error", "pending", "completed"}:
        if st != "pending":
            return st
    has_map = (d / "speech_map.json").exists()
    has_tx = (d / "transcript.txt").exists()
    clips = list((d / "clips").glob("*.wav")) if (d / "clips").exists() else []
    if has_tx and has_map:
        return "completed"
    if has_map and clips:
        return "processing"
    if has_map:
        return "vad"
    return "pending"


def _load_map(d: Path) -> dict[str, Any]:
    p = d / "speech_map.json"
    if not p.exists():
        return {"duration_sec": 0.0, "speech_sec": 0.0, "spans": [], "source": ""}
    return json.loads(p.read_text(encoding="utf-8"))


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate() or 1
        return w.getnframes() / float(sr)


def _clip_index(clips_dir: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    if not clips_dir.exists():
        return out
    for p in clips_dir.glob("*.wav"):
        m = CLIP_RE.match(p.name)
        if m:
            out[int(m.group(1))] = p.name
    return out


def _clock_public(stem: str, meta: dict[str, Any], duration: float) -> dict[str, Any]:
    clock = resolve_clock(stem, meta.get("clock_anchor"))
    rec = clock["start"]
    filename = clock["filename_start"]
    anchor = clock["anchor"]
    rec_end = relative_to_absolute(rec, duration) if rec and duration else None
    shift = (rec - filename).total_seconds() if rec and filename else None
    anchor_out = None
    if anchor:
        anchor_out = {
            "rel_sec": anchor["rel_sec"],
            "rel_label": format_rel(anchor["rel_sec"]),
            "at": iso_astro(anchor["at"]),
            "at_label": format_astro(anchor["at"]),
        }
    return {
        "recording_start": iso_astro(rec) if rec else None,
        "recording_end": iso_astro(rec_end) if rec_end else None,
        "recording_start_label": format_astro(rec, millis=False) if rec else None,
        "clock_source": clock["source"],
        "clock_anchor": anchor_out,
        "filename_start": iso_astro(filename) if filename else None,
        "filename_start_label": format_astro(filename, millis=False) if filename else None,
        "clock_shift_sec": shift,
        "clock_shift_label": format_signed_hms(shift) if shift is not None and abs(shift) >= 0.5 else None,
    }


TITLE_MAX = 200
COMMENT_MAX = 2000
MARKER_NAME_MAX = 80
MARKER_CAP = 200
MARKER_T_MAX = 48 * 3600


def _file_name(source: str, stem: str) -> str:
    name = Path(str(source or "")).name
    # Uploads are stored as original.wav inside the recording folder.
    if stem and name.lower().startswith("original."):
        return f"{stem}{Path(name).suffix}"
    return name or stem


def _clean_title(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > TITLE_MAX:
        raise project_store.BadRequest(f"title must be at most {TITLE_MAX} characters")
    return text


def _clean_comment(value: object) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > COMMENT_MAX:
        raise project_store.BadRequest(f"comment must be at most {COMMENT_MAX} characters")
    return text.strip()


def _marker_time_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise project_store.BadRequest("время метки должно быть числом")
    t = float(value)
    if t != t or t in (float("inf"), float("-inf")):
        raise project_store.BadRequest("время метки должно быть числом")
    if t < 0 or t > MARKER_T_MAX:
        raise project_store.BadRequest("время метки вне диапазона")
    return int(round(t * 1000.0))


def _marker_name(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise project_store.BadRequest("имя метки должно быть строкой")
    text = value.replace("\r", " ").replace("\n", " ").replace("\x00", "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MARKER_NAME_MAX:
        raise project_store.BadRequest(f"имя метки не длиннее {MARKER_NAME_MAX} символов")
    return text


def _clean_markers(value: object) -> list[dict[str, Any]]:
    """Sort, round to milliseconds, and collapse markers that land on the same instant."""
    if not isinstance(value, list):
        raise project_store.BadRequest("метки должны быть списком")
    if len(value) > MARKER_CAP:
        raise project_store.BadRequest(f"не больше {MARKER_CAP} меток")
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise project_store.BadRequest("метка должна быть объектом")
        ms = _marker_time_ms(item.get("t"))
        name = _marker_name(item.get("name")) if "name" in item else ""
        row: dict[str, Any] = {"_ms": ms, "t": ms / 1000.0}
        if name:
            row["name"] = name
        rows.append(row)
    rows.sort(key=lambda m: (m["_ms"], m.get("name") or ""))
    out: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("name") or ""
        if out and row["_ms"] - out[-1]["_ms"] < 10:
            prev = out[-1]
            prev_name = prev.get("name") or ""
            if prev_name and name and prev_name != name:
                out.append(row)
            elif not prev_name and name:
                prev["name"] = name
            continue
        out.append(row)
    public: list[dict[str, Any]] = []
    for row in out:
        item = {"t": row["t"]}
        if row.get("name"):
            item["name"] = row["name"]
        public.append(item)
    return public


def markers_public(meta: dict[str, Any]) -> list[dict[str, Any]]:
    raw = meta.get("markers")
    if not isinstance(raw, list):
        return []
    try:
        return _clean_markers(raw)
    except project_store.BadRequest:
        kept: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                kept.extend(_clean_markers([item]))
            except project_store.BadRequest:
                continue
        if not kept:
            return []
        return _clean_markers(kept)


def set_markers(root: Path, project_id: str, stem: str, markers: object) -> dict[str, Any]:
    """Replace timeline markers on the recording. An empty list removes the key."""
    cleaned = _clean_markers(markers)
    folder = project_store.recording_dir(root, project_id, stem)
    meta = jobs.read_meta(folder)
    if cleaned:
        meta["markers"] = cleaned
    else:
        meta.pop("markers", None)
    jobs.write_meta(folder, meta)
    return {"markers": cleaned}


def update_recording_notes(
    root: Path,
    project_id: str,
    stem: str,
    *,
    title: object = None,
    comment: object = None,
) -> dict[str, Any]:
    """Store a display title and a comment on the recording. The folder id stays put."""
    folder = project_store.recording_dir(root, project_id, stem)
    meta = jobs.read_meta(folder)
    if title is not None:
        text = _clean_title(title)
        if text:
            meta["title"] = text
        else:
            meta.pop("title", None)
    if comment is not None:
        text = _clean_comment(comment)
        if text:
            meta["comment"] = text
        else:
            meta.pop("comment", None)
    jobs.write_meta(folder, meta)
    return project_summary(folder)


def project_summary(d: Path) -> dict[str, Any]:
    stem = d.name
    sm = _load_map(d)
    status = _status(d)
    rec_meta = _recording_meta(d)
    spans = sm.get("spans") or []
    duration = float(sm.get("duration_sec") or rec_meta.get("duration_sec") or 0.0)
    speech = float(sm.get("speech_sec") or 0.0)
    tx = d / "transcript.txt"
    n_lines = 0
    if tx.exists():
        n_lines = len(parse_transcript_lines(tx.read_text(encoding="utf-8")))
    n_clips = len(list((d / "clips").glob("*.wav"))) if (d / "clips").exists() else 0
    source = rec_meta.get("source") or sm.get("source") or ""
    title = str(rec_meta.get("title") or "").strip()
    comment = str(rec_meta.get("comment") or "")
    return {
        "id": stem,
        "name": title or stem,
        "title": title,
        "comment": comment,
        "file_name": _file_name(source, stem),
        "source": source,
        "status": status,
        **_clock_public(stem, rec_meta, duration),
        "duration_sec": duration,
        "speech_sec": speech,
        "speech_ratio": (speech / duration) if duration else 0.0,
        "spans": len(spans),
        "clips": n_clips,
        "segments": n_lines,
        "has_cleaned": (d / "cleaned_speech.wav").exists(),
        "has_mp3": (d / "cleaned_speech.mp3").exists(),
        "has_transcript": tx.exists(),
        "resumable": (d / "speech_map.json").exists()
        or (d / "asr_progress.json").exists()
        or (d / "vad_progress.json").exists()
        or (d / "pcm16.wav").exists()
        or (d / "pcm16_adapt.wav").exists(),
        "bytes": _dir_size(d),
        "job": {
            "status": status,
            "stage": rec_meta.get("stage") or "",
            "error": rec_meta.get("error"),
            "log": list(rec_meta.get("log") or [])[-30:],
            "source": rec_meta.get("source") or sm.get("source") or "",
            "progress": jobs.infer_progress(rec_meta),
        },
    }


def _recording_duration(d: Path, meta: dict[str, Any]) -> float:
    sm = _load_map(d)
    return float(sm.get("duration_sec") or meta.get("duration_sec") or 0.0)


def set_clock_anchor(
    root: Path,
    project_id: str,
    stem: str,
    rel_value: object,
    at_value: object,
) -> dict[str, Any]:
    """Bind one timeline point to a real wall-clock time and shift every absolute stamp."""
    d = project_store.recording_dir(root, project_id, stem)
    try:
        rel = parse_rel_point(rel_value)
        at = parse_wall_datetime(str(at_value or ""))
    except ValueError as exc:
        raise project_store.BadRequest(str(exc)) from exc
    meta = jobs.read_meta(d)
    duration = _recording_duration(d, meta)
    if duration > 0 and rel > duration + 0.05:
        raise project_store.BadRequest("timeline point is past the end of the recording")
    rel_ms = int(round(rel * 1000.0))
    meta["clock_anchor"] = {"rel_sec": rel_ms / 1000.0, "at": iso_astro(at)}
    jobs.write_meta(d, meta)
    return load_project(stem, project_id, root)


def clear_clock_anchor(root: Path, project_id: str, stem: str) -> dict[str, Any]:
    d = project_store.recording_dir(root, project_id, stem)
    meta = jobs.read_meta(d)
    meta.pop("clock_anchor", None)
    jobs.write_meta(d, meta)
    return load_project(stem, project_id, root)


def apply_clock_shift(root: Path, project_id: str, stem: str, shift_value: object) -> dict[str, Any]:
    """Move this recording's start by the same amount another file was corrected.

    `shift_value` is seconds from the time parsed out of the filename to the
    corrected recording start. Zero clears the calibration.
    """
    from .astro_time import parse_recording_start, relative_to_absolute

    filename = parse_recording_start(stem)
    if filename is None:
        raise project_store.BadRequest("у записи нет времени в имени файла")
    try:
        shift = float(shift_value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise project_store.BadRequest("shift_sec must be a number") from exc
    if shift != shift or shift in (float("inf"), float("-inf")):
        raise project_store.BadRequest("shift_sec must be a number")
    ms = int(round(shift * 1000.0))
    if abs(ms) > 366 * 24 * 3600 * 1000:
        raise project_store.BadRequest("shift_sec is out of range")
    if ms == 0:
        return clear_clock_anchor(root, project_id, stem)
    at = relative_to_absolute(filename, ms / 1000.0)
    return set_clock_anchor(root, project_id, stem, 0, iso_astro(at))


def list_recordings(project_id: str, root: Optional[Path] = None) -> list[dict[str, Any]]:
    root = root or data_root()
    folder = project_store.project_dir(root, project_id)
    items = []
    for d in sorted(folder.iterdir()):
        if project_store.is_recording_dir(d):
            items.append(project_summary(d))
    return items


def load_project(stem: str, project_id: str, root: Optional[Path] = None) -> dict[str, Any]:
    root = root or data_root()
    d = project_store.recording_dir(root, project_id, stem)
    sm = _load_map(d)
    rec = resolve_clock(stem, _recording_meta(d).get("clock_anchor"))["start"]
    spans_raw = sm.get("spans") or []
    clip_by_i = _clip_index(d / "clips")

    spans: list[dict[str, Any]] = []
    for i, sp in enumerate(spans_raw, 1):
        a = float(sp["start"])
        b = float(sp["end"])
        item: dict[str, Any] = {
            "i": i,
            "start_rel": a,
            "end_rel": b,
            "dur": max(0.0, b - a),
            "clip": clip_by_i.get(i),
            "clean_start": 0.0,
            "clean_end": 0.0,
        }
        if rec:
            sa = relative_to_absolute(rec, a)
            sb = relative_to_absolute(rec, b)
            item["start_astro"] = iso_astro(sa)
            item["end_astro"] = iso_astro(sb)
            item["start_label"] = format_astro(sa)
            item["end_label"] = format_astro(sb)
        spans.append(item)

    lines: list[dict[str, Any]] = []
    tx = d / "transcript.txt"
    if tx.exists():
        parsed = parse_transcript_lines(tx.read_text(encoding="utf-8"))
        for n, row in enumerate(parsed, 1):
            a, b = row["start_rel"], row["end_rel"]
            hit = next((s for s in spans if s["start_rel"] - 0.08 <= a <= s["end_rel"] + 0.08), None)
            seg: dict[str, Any] = {
                "n": n,
                "start_rel": a,
                "end_rel": b,
                "dur": max(0.0, b - a),
                "text": row["text"],
                "speaker": "SPEECH",
                "speaker_id": None,
                "speaker_conf": None,
                "speaker_status": None,
                "speaker_candidates": [],
                "speaker_cluster": None,
                "confidence": None,
                "clip": hit["clip"] if hit else None,
                "span_i": hit["i"] if hit else None,
                "clip_offset": max(0.0, a - hit["start_rel"]) if hit else 0.0,
            }
            if rec:
                sa = relative_to_absolute(rec, a)
                sb = relative_to_absolute(rec, b)
                seg["start_astro"] = iso_astro(sa)
                seg["end_astro"] = iso_astro(sb)
                seg["start_label"] = format_astro(sa)
                seg["end_label"] = format_astro(sb)
                seg["clock"] = sa.strftime("%H:%M:%S.") + f"{sa.microsecond // 1000:03d}"
            lines.append(seg)

    clips_dir = d / "clips"
    acc = 0.0
    for item in spans:
        item["clean_start"] = acc
        clip_name = item.get("clip")
        actual = item["dur"]
        if clip_name:
            cp = clips_dir / clip_name
            if cp.exists():
                try:
                    actual = wav_duration(cp)
                except Exception:
                    actual = item["dur"]
        item["clean_dur"] = actual
        acc += actual
        item["clean_end"] = acc

    from .match import apply_map_to_lines, read_map, tally_lines

    smap = read_map(d)
    apply_map_to_lines(lines, smap)

    src = playable_source_path(stem, project_id, root) or source_path(stem, project_id, root)
    summary = project_summary(d)
    summary["spans_detail"] = spans
    summary["segments_detail"] = lines
    summary["pipeline"] = DEFAULT_PIPELINE.as_dict()
    meta = project_store.get_project(root, project_id)
    summary["project_id"] = project_id
    summary["project_name"] = meta["name"]
    summary["cleaned_url"] = (
        f"/api/projects/{project_id}/recordings/{stem}/cleaned" if summary["has_cleaned"] else None
    )
    summary["original_url"] = (
        f"/api/projects/{project_id}/recordings/{stem}/original" if src else None
    )
    summary["has_original"] = src is not None
    summary["clean_duration_sec"] = acc
    summary["follow_fx"] = bool(smap.get("follow_fx", True))
    summary["fx_overrides"] = smap.get("fx_overrides") or {}
    summary["thresh"] = smap.get("thresh") or {}
    stats = tally_lines(smap.get("lines") or {})
    stats["assigned"] = stats["associated"]
    summary["speaker_stats"] = stats
    summary["markers"] = markers_public(_recording_meta(d))
    return summary


def clip_path(stem: str, name: str, project_id: str, root: Optional[Path] = None) -> Path:
    root = root or data_root()
    clips = (project_store.recording_dir(root, project_id, stem) / "clips").resolve()
    p = (clips / Path(name).name).resolve()
    if clips not in p.parents and p.parent != clips:
        raise FileNotFoundError(name)
    if not p.exists():
        raise FileNotFoundError(name)
    return p


def source_path(stem: str, project_id: str, root: Optional[Path] = None) -> Optional[Path]:
    root = root or data_root()
    d = project_store.recording_dir(root, project_id, stem)
    sm = _load_map(d)
    rec_meta = _recording_meta(d)
    raw = rec_meta.get("source") or sm.get("source") or ""
    candidates = []
    if raw:
        candidates.append(Path(raw))
    candidates.append(d / "original.wav")
    for suf in (".mp3", ".m4a", ".aac", ".ogg", ".flac", ".WAV", ".wav"):
        candidates.append(d / f"original{suf.lower() if suf.startswith('.') else suf}")
        candidates.append(d / f"original{suf}")
    candidates.append(root.parent / f"{stem}.WAV")
    candidates.append(root.parent / f"{stem}.wav")
    candidates.append(root.parent / f"{stem}.mp3")
    seen: set[str] = set()
    for p in candidates:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def playable_source_path(stem: str, project_id: str, root: Optional[Path] = None) -> Optional[Path]:
    """PCM the browser can play and `wave` can peak — not IMA ADPCM originals."""
    from .process import is_pcm16_wav

    root = root or data_root()
    d = project_store.recording_dir(root, project_id, stem)
    for name in ("pcm16.wav", "pcm16_adapt.wav"):
        p = d / name
        try:
            if p.is_file() and is_pcm16_wav(p):
                return p
        except OSError:
            continue
    src = source_path(stem, project_id, root)
    if src is not None:
        try:
            if is_pcm16_wav(src):
                return src
        except OSError:
            pass
    return src


def wav_peaks(path: Path, bins: int = 1600) -> list[float]:
    bins = max(32, min(int(bins), 4000))
    try:
        with wave.open(str(path), "rb") as w:
            n = w.getnframes()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            if sw != 2 or n <= 0:
                raise OSError("not pcm16")
            if n >= 8_000_000:
                raise OSError("long file")
            block = max(1, n // bins)
            out: list[float] = []
            for _ in range(bins):
                raw = w.readframes(block)
                if not raw:
                    out.append(0.0)
                    continue
                x = np.frombuffer(raw, dtype=np.int16)
                if ch > 1:
                    usable = (x.size // ch) * ch
                    x = x[:usable].reshape(-1, ch).mean(axis=1)
                out.append(float(np.max(np.abs(x)) / 32768.0) if x.size else 0.0)
            return out
    except Exception:
        return _ffmpeg_peaks(path, bins)


def _ffmpeg_peaks(path: Path, bins: int) -> list[float]:
    """Downsample via ffmpeg so 8-hour PCM / ADPCM originals still get a waveform."""
    import subprocess

    from .process import ffmpeg_bin

    try:
        exe = ffmpeg_bin()
    except FileNotFoundError:
        return [0.0] * bins
    sr = 40
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"],
        capture_output=True,
        check=False,
    )
    raw = proc.stdout or b""
    if len(raw) < 4:
        return [0.0] * bins
    x = np.frombuffer(raw[: (len(raw) // 2) * 2], dtype=np.int16).astype(np.float32)
    if x.size < bins:
        pad = np.zeros(bins, dtype=np.float32)
        pad[: x.size] = np.abs(x) / 32768.0
        return pad.tolist()
    step = x.size / bins
    out: list[float] = []
    for i in range(bins):
        a = int(i * step)
        b = int((i + 1) * step)
        sl = x[a:b]
        out.append(float(np.max(np.abs(sl)) / 32768.0) if sl.size else 0.0)
    return out


def cached_peaks(stem: str, kind: str, bins: int = 1600, project_id: str = "", root: Optional[Path] = None) -> list[float]:
    root = root or data_root()
    d = project_store.recording_dir(root, project_id, stem)
    cache = d / f"peaks_{kind}.json"
    if kind == "original":
        src = playable_source_path(stem, project_id, root)
    elif kind == "cleaned":
        src = d / "cleaned_speech.wav"
        src = src if src.exists() else None
    else:
        raise FileNotFoundError(kind)
    if src is None:
        raise FileNotFoundError(kind)
    if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("bins") == bins and data.get("peaks"):
            return data["peaks"]
    peaks = wav_peaks(src, bins)
    cache.write_text(json.dumps({"bins": bins, "peaks": peaks}, ensure_ascii=False), encoding="utf-8")
    return peaks


def cleaned_path(stem: str, project_id: str, root: Optional[Path] = None) -> Path:
    p = project_store.recording_dir(root or data_root(), project_id, stem) / "cleaned_speech.wav"
    if not p.exists():
        raise FileNotFoundError(stem)
    return p


def cleaned_mp3_path(stem: str, project_id: str, root: Optional[Path] = None) -> Path:
    """Speech-only MP3 for sharing. Built after transcribe, or on first download."""
    from .process import encode_speech_mp3

    wav = cleaned_path(stem, project_id, root)
    return encode_speech_mp3(wav, wav.with_suffix(".mp3"))


def transcript_text(stem: str, project_id: str, root: Optional[Path] = None) -> str:
    p = project_store.recording_dir(root or data_root(), project_id, stem) / "transcript.txt"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def search_transcripts(
    project_id: str,
    query: str,
    root: Optional[Path] = None,
    *,
    limit: int = 80,
    per_recording: int = 8,
) -> dict[str, Any]:
    """Case-insensitive substring search across timed transcript lines in a project."""
    root = root or data_root()
    folder = project_store.project_dir(root, project_id)
    needle = (query or "").strip()
    folded = needle.casefold()
    empty = {"query": needle, "total": 0, "truncated": False, "recordings": []}
    if not folded:
        return empty

    limit = max(1, int(limit))
    per_recording = max(1, int(per_recording))
    remaining = limit
    recordings: list[dict[str, Any]] = []
    total = 0

    for d in sorted(folder.iterdir()):
        if not project_store.is_recording_dir(d):
            continue
        tx = d / "transcript.txt"
        if not tx.exists():
            continue
        try:
            parsed = parse_transcript_lines(tx.read_text(encoding="utf-8"))
        except OSError:
            continue
        rec = resolve_clock(d.name, _recording_meta(d).get("clock_anchor"))["start"]
        matched: list[dict[str, Any]] = []
        hits = 0
        for n, row in enumerate(parsed, 1):
            text = row.get("text") or ""
            if folded not in text.casefold():
                continue
            hits += 1
            if len(matched) >= per_recording or remaining <= 0:
                continue
            a = float(row["start_rel"])
            b = float(row["end_rel"])
            try:
                ma, mb = match_time_span(text, needle, a, b)
            except ValueError:
                ma, mb = a, b
            item: dict[str, Any] = {
                "n": n,
                "text": text,
                "start_rel": a,
                "end_rel": b,
                "match_start_rel": ma,
                "match_end_rel": mb,
                "clock": "",
                "start_label": "",
            }
            if rec:
                sa = relative_to_absolute(rec, a)
                item["clock"] = format_clock(sa)
                item["start_label"] = format_astro(sa)
            matched.append(item)
            remaining -= 1
        if not hits:
            continue
        total += hits
        recordings.append(
            {
                "id": d.name,
                "status": _status(d),
                "hits": hits,
                "lines": matched,
            }
        )

    shown = sum(len(r["lines"]) for r in recordings)
    return {
        "query": needle,
        "total": total,
        "truncated": shown < total,
        "recordings": recordings,
    }


def export_absolute_srt(stem: str, project_id: str, root: Optional[Path] = None) -> str:
    proj = load_project(stem, project_id, root)
    rows = []
    for i, seg in enumerate(proj["segments_detail"], 1):
        a = seg.get("start_label") or ""
        b = seg.get("end_label") or ""
        rows.append(f"{i}\n{a} --> {b}\n{seg['text']}\n")
    return "\n".join(rows)


def write_index(root: Optional[Path] = None) -> Path:
    root = root or data_root()
    projects = []
    for container in project_store.list_projects(root, archived=True):
        pid = container["id"]
        for s in list_recordings(pid, root):
            stem = s["id"]
            try:
                full = load_project(stem, pid, root)
            except Exception:
                full = s
                full["segments_detail"] = []
                full["project_id"] = pid
            projects.append(full)
    lines = [
        "AstroTranscript — индекс обработанных записей",
        "Время в таймкодах = земные часы: имя файла YYYY.MM.DD.HHMMSS + смещение,",
        "либо калибровка по одной точке таймлайна (clock_anchor в recording.json).",
        "",
    ]
    for p in projects:
        rec = p.get("recording_start_label") or "?"
        pid = p.get("project_id")
        stem = p["id"]
        rec_dir = project_store.recording_dir(root, pid, stem)
        lines.append("=" * 72)
        lines.append(f"{p['id']}   status={p['status']}")
        lines.append(f"  старт записи : {rec}")
        if p.get("clock_source") == "anchor":
            anc = p.get("clock_anchor") or {}
            lines.append(f"  калибровка   : {anc.get('rel_label') or '?'} = {anc.get('at_label') or '?'}")
            if p.get("filename_start_label"):
                lines.append(f"  имя файла    : {p['filename_start_label']}")
            if p.get("clock_shift_label"):
                lines.append(f"  сдвиг        : {p['clock_shift_label']}")
        lines.append(f"  длительность : {p['duration_sec']/60:.1f} мин")
        lines.append(f"  речь (VAD)   : {p['speech_sec']/60:.1f} мин  ({100*p['speech_ratio']:.1f}%)  spans={p['spans']}  lines={p['segments']}")
        lines.append(f"  клипы        : {rec_dir / 'clips'}")
        lines.append(f"  склейка      : {rec_dir / 'cleaned_speech.wav'}")
        lines.append(f"  транскрипт   : {rec_dir / 'transcript.txt'}")
        segs = p.get("segments_detail") or []
        if not segs:
            lines.append("  (нет распознанного текста)")
            lines.append("")
            continue
        lines.append("  --- транскрипт с абсолютным временем ---")
        for seg in segs:
            clock = seg.get("start_label") or ""
            lines.append(f"  [{clock}] {seg['text']}")
        lines.append("")
    out = root / "INDEX.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
