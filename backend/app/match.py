"""Match recording spans/lines to the global speaker library."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from .projects import _read_json, _write_json, recording_dir
from .speakers import get_speaker, list_speakers, sample_path

MAP_NAME = "speakers.json"
HIGH = 0.62
MARGIN = 0.07
LOW = 0.40
CAND_N = 4


def classify_scores(
    scores: dict[str, float],
    high: float = HIGH,
    margin: float = MARGIN,
    low: float = LOW,
) -> dict[str, Any]:
    ranked = sorted(((sid, float(sc)) for sid, sc in scores.items()), key=lambda x: -x[1])
    if not ranked:
        return {"speaker_id": None, "conf": 0.0, "status": "unmatched", "candidates": []}
    top_id, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    cands = [{"id": sid, "score": round(sc, 4)} for sid, sc in ranked[:CAND_N]]
    if top >= high and (top - second) >= margin:
        return {"speaker_id": top_id, "conf": round(top, 4), "status": "auto", "candidates": cands}
    if top >= low:
        return {"speaker_id": top_id, "conf": round(top, 4), "status": "low", "candidates": cands}
    return {"speaker_id": None, "conf": round(top, 4), "status": "unmatched", "candidates": cands}


def clamp_thresh(high: float, margin: float, low: float) -> dict[str, float]:
    high = min(0.95, max(0.35, float(high)))
    low = min(high, max(0.10, float(low)))
    margin = min(0.30, max(0.01, float(margin)))
    return {"high": round(high, 3), "margin": round(margin, 3), "low": round(low, 3)}


def thresh_of(mapping: dict[str, Any]) -> dict[str, float]:
    t = mapping.get("thresh") or {}
    return clamp_thresh(t.get("high", HIGH), t.get("margin", MARGIN), t.get("low", LOW))


def tally_lines(lines: dict[str, Any]) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}
    auto = low = unmatched = manual = 0
    for row in (lines or {}).values():
        if not isinstance(row, dict):
            continue
        st = str(row.get("status") or "")
        sid = row.get("speaker_id")
        if st == "manual" and sid:
            manual += 1
        elif st == "auto" and sid:
            auto += 1
        elif st == "low":
            low += 1
        else:
            unmatched += 1
        if sid:
            slot = by.setdefault(
                sid, {"id": sid, "name": row.get("name") or sid, "auto": 0, "low": 0, "manual": 0, "total": 0}
            )
            if st == "manual":
                slot["manual"] += 1
            elif st == "auto":
                slot["auto"] += 1
            elif st == "low":
                slot["low"] += 1
            slot["total"] = slot["auto"] + slot["low"] + slot["manual"]
    return {
        "auto": auto,
        "low": low,
        "unmatched": unmatched,
        "manual": manual,
        "associated": auto + manual,
        "assigned": auto + manual,
        "by_speaker": list(by.values()),
        "lines": len(lines or {}),
    }


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def embed_pcm(x: np.ndarray, sr: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if x.size < 256:
        x = np.pad(x, (0, 256 - int(x.size)))
    x = x - float(np.mean(x))
    peak = float(np.max(np.abs(x))) + 1e-9
    x = x / peak
    n_fft = 512
    hop = 160
    window = np.hanning(n_fft).astype(np.float32)
    frames = []
    last = max(n_fft, x.size)
    for i in range(0, last - n_fft + 1, hop):
        chunk = x[i : i + n_fft]
        if chunk.size < n_fft:
            chunk = np.pad(chunk, (0, n_fft - chunk.size))
        spec = np.fft.rfft(chunk * window)
        frames.append(np.abs(spec))
    mag = np.mean(np.stack(frames, axis=0), axis=0) if frames else np.abs(np.fft.rfft(x[:n_fft] * window))
    logm = np.log1p(mag)
    bins = 40
    idx = np.linspace(0, logm.size, bins + 1).astype(int)
    band = np.array(
        [float(np.mean(logm[idx[i] : idx[i + 1]])) if idx[i + 1] > idx[i] else 0.0 for i in range(bins)],
        dtype=np.float32,
    )
    zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0))
    rms = float(np.sqrt(np.mean(x * x) + 1e-12))
    freqs = np.linspace(0.0, sr / 2.0, mag.size)
    centroid = float(np.sum(freqs * mag) / (np.sum(mag) + 1e-9))
    feat = np.concatenate([band, np.array([zcr * 10.0, rms, centroid / 4000.0], dtype=np.float32)])
    nrm = float(np.linalg.norm(feat)) + 1e-9
    return feat / nrm


def embed_file(path: Path) -> np.ndarray:
    from .process import is_pcm16_wav, read_mono_f32, wav_info

    if is_pcm16_wav(path):
        sr, _ch, dur = wav_info(path)
        take = min(dur, 8.0)
        x = read_mono_f32(path, 0.0, take, sr)
        return embed_pcm(x, sr)
    try:
        import librosa

        y, sr = librosa.load(str(path), sr=16000, mono=True, duration=8.0)
        return embed_pcm(np.asarray(y, dtype=np.float32), int(sr) or 16000)
    except Exception:
        import wave

        with wave.open(str(path), "rb") as w:
            sr = w.getframerate() or 16000
            raw = w.readframes(min(w.getnframes(), sr * 8))
            sw = w.getsampwidth()
            ch = w.getnchannels()
        if sw != 2:
            raise
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        return embed_pcm(x, sr)


def _mean_unit(embs: list[np.ndarray]) -> np.ndarray:
    m = np.mean(np.stack(embs, axis=0), axis=0)
    nrm = float(np.linalg.norm(m)) + 1e-9
    return m / nrm


def library_profiles(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for sp in list_speakers(root):
        embs = []
        for sm in sp.get("samples") or []:
            try:
                embs.append(embed_file(sample_path(root, sp["id"], sm["id"])))
            except Exception:
                continue
        if not embs:
            continue
        out[sp["id"]] = {"name": sp["name"], "emb": _mean_unit(embs)}
    return out


def read_map(folder: Path) -> dict[str, Any]:
    p = folder / MAP_NAME
    if not p.exists():
        return {"version": 1, "follow_fx": True, "fx_overrides": {}, "lines": {}}
    try:
        data = _read_json(p)
    except Exception:
        return {"version": 1, "follow_fx": True, "fx_overrides": {}, "lines": {}}
    if not isinstance(data, dict):
        return {"version": 1, "follow_fx": True, "fx_overrides": {}, "lines": {}}
    data.setdefault("follow_fx", True)
    data.setdefault("fx_overrides", {})
    data.setdefault("lines", {})
    data["thresh"] = thresh_of(data)
    return data


def write_map(folder: Path, data: dict[str, Any]) -> None:
    slim = {
        "version": 1,
        "follow_fx": bool(data.get("follow_fx", True)),
        "fx_overrides": data.get("fx_overrides") or {},
        "thresh": thresh_of(data),
        "lines": data.get("lines") or {},
    }
    _write_json(folder / MAP_NAME, slim)


def apply_map_to_lines(lines: list[dict[str, Any]], mapping: dict[str, Any]) -> None:
    rows = mapping.get("lines") or {}
    for seg in lines:
        row = rows.get(str(seg.get("n")))
        if not row:
            continue
        seg["speaker_id"] = row.get("speaker_id")
        seg["speaker"] = row.get("name") or row.get("speaker_id") or seg.get("speaker") or "SPEECH"
        seg["speaker_conf"] = row.get("conf")
        seg["speaker_status"] = row.get("status")
        seg["speaker_candidates"] = row.get("candidates") or []
        seg["speaker_cluster"] = row.get("cluster")
        seg["speaker_scores"] = row.get("scores") or {}
        seg["span_i"] = row.get("span_i")


def apply_thresholds(mapping: dict[str, Any]) -> dict[str, Any]:
    th = thresh_of(mapping)
    mapping["thresh"] = th
    names = {}
    lines = mapping.get("lines") or {}
    for row in lines.values():
        if not isinstance(row, dict):
            continue
        if row.get("status") == "manual" and row.get("speaker_id"):
            continue
        scores = row.get("scores") or {}
        hit = classify_scores(scores, high=th["high"], margin=th["margin"], low=th["low"])
        sid = hit["speaker_id"]
        name = row.get("name")
        for c in hit["candidates"]:
            if row.get("candidates"):
                prev = next((x for x in row["candidates"] if x.get("id") == c["id"] and x.get("name")), None)
                if prev:
                    c["name"] = prev["name"]
            names[c["id"]] = c.get("name") or names.get(c["id"]) or c["id"]
        row["speaker_id"] = sid
        row["name"] = names.get(sid) or (name if sid else None)
        row["conf"] = hit["conf"]
        row["status"] = hit["status"]
        row["candidates"] = hit["candidates"]
    by_c: dict[str, list[str]] = {}
    for key, row in lines.items():
        if isinstance(row, dict):
            by_c.setdefault(str(row.get("cluster") or "?"), []).append(key)
    for keys in by_c.values():
        votes: dict[str, int] = {}
        for key in keys:
            row = lines[key]
            if row.get("status") == "auto" and row.get("speaker_id"):
                votes[row["speaker_id"]] = votes.get(row["speaker_id"], 0) + 1
        if not votes:
            continue
        winner = max(votes, key=votes.get)
        for key in keys:
            row = lines[key]
            if row.get("status") == "manual":
                continue
            if row.get("status") == "low" and (
                row.get("speaker_id") == winner
                or any(c.get("id") == winner for c in row.get("candidates") or [])
            ):
                row["speaker_id"] = winner
                row["name"] = names.get(winner) or row.get("name") or winner
                row["status"] = "auto"
    mapping["lines"] = lines
    return mapping


def _clip_files(folder: Path) -> dict[int, Path]:
    clips = folder / "clips"
    out: dict[int, Path] = {}
    if not clips.is_dir():
        return out
    for p in clips.glob("*.wav"):
        name = p.name
        if len(name) >= 4 and name[:4].isdigit():
            out[int(name[:4])] = p
    return out


def _cluster_ids(embs: list[np.ndarray]) -> list[str]:
    n = len(embs)
    if n == 0:
        return []
    if n == 1:
        return ["A"]
    try:
        from sklearn.cluster import AgglomerativeClustering

        x = np.stack(embs, axis=0)
        try:
            model = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=0.42,
                metric="cosine",
                linkage="average",
            )
        except TypeError:
            model = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=0.42,
                affinity="cosine",
                linkage="average",
            )
        labels = model.fit_predict(x)
    except Exception:
        labels = np.zeros(n, dtype=int)
    letters = []
    for i in range(n):
        letters.append(chr(ord("A") + int(labels[i]) % 26))
    return letters


def match_recording(root: Path, pid: str, stem: str) -> dict[str, Any]:
    from .catalog import parse_transcript_lines

    folder = recording_dir(root, pid, stem)
    profiles = library_profiles(root)
    clips = _clip_files(folder)
    tx = folder / "transcript.txt"
    parsed = parse_transcript_lines(tx.read_text(encoding="utf-8")) if tx.exists() else []
    sm = {}
    mp = folder / "speech_map.json"
    if mp.exists():
        try:
            sm = _read_json(mp)
        except Exception:
            sm = {}
    spans = sm.get("spans") or []
    units: list[dict[str, Any]] = []
    if parsed:
        for n, row in enumerate(parsed, 1):
            hit_i = None
            a, b = row["start_rel"], row["end_rel"]
            for i, sp in enumerate(spans, 1):
                try:
                    sa, sb = float(sp["start"]), float(sp["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if sa - 0.08 <= a <= sb + 0.08:
                    hit_i = i
                    break
            units.append({"n": n, "span_i": hit_i, "start": a, "end": b, "text": row.get("text") or ""})
    else:
        for i, sp in enumerate(spans, 1):
            units.append(
                {
                    "n": i,
                    "span_i": i,
                    "start": float(sp.get("start") or 0),
                    "end": float(sp.get("end") or 0),
                    "text": "",
                }
            )

    embs: list[Optional[np.ndarray]] = []
    for u in units:
        path = clips.get(int(u["span_i"] or 0)) or clips.get(int(u["n"]))
        if path is None or not path.is_file():
            embs.append(None)
            continue
        try:
            embs.append(embed_file(path))
        except Exception:
            embs.append(None)

    present = [e for e in embs if e is not None]
    cluster_of = ["?"] * len(units)
    if present:
        clustered = _cluster_ids(present)
        j = 0
        for i, e in enumerate(embs):
            if e is None:
                continue
            cluster_of[i] = clustered[j]
            j += 1

    names = {sp["id"]: sp["name"] for sp in list_speakers(root)}
    mapping = read_map(folder)
    lines: dict[str, Any] = dict(mapping.get("lines") or {})
    for i, u in enumerate(units):
        key = str(u["n"])
        prev = lines.get(key) or {}
        emb = embs[i]
        scores: dict[str, float] = {}
        if emb is not None:
            for sid, prof in profiles.items():
                scores[sid] = cosine(emb, prof["emb"])
        if prev.get("status") == "manual" and prev.get("speaker_id"):
            prev["cluster"] = cluster_of[i]
            prev["span_i"] = u.get("span_i")
            if scores:
                prev["scores"] = {k: round(v, 4) for k, v in scores.items()}
            lines[key] = prev
            continue
        th = thresh_of(mapping)
        hit = classify_scores(scores, high=th["high"], margin=th["margin"], low=th["low"])
        for c in hit["candidates"]:
            c["name"] = names.get(c["id"], c["id"])
        sid = hit["speaker_id"]
        row = {
            "speaker_id": sid,
            "name": names.get(sid, sid) if sid else None,
            "conf": hit["conf"],
            "status": hit["status"],
            "candidates": hit["candidates"],
            "cluster": cluster_of[i],
            "span_i": u.get("span_i"),
            "scores": {k: round(v, 4) for k, v in scores.items()},
        }
        lines[key] = row

    mapping["lines"] = lines
    apply_thresholds(mapping)
    write_map(folder, mapping)
    stats = tally_lines(mapping["lines"])
    stats["speakers"] = len(profiles)
    stats["follow_fx"] = bool(mapping.get("follow_fx", True))
    stats["thresh"] = thresh_of(mapping)
    return stats


def assign_speakers(
    root: Path,
    pid: str,
    stem: str,
    *,
    n: Optional[int] = None,
    cluster: Optional[str] = None,
    speaker_id: Optional[str] = None,
    follow_fx: Optional[bool] = None,
    fx: Optional[dict[str, Any]] = None,
    thresh: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    folder = recording_dir(root, pid, stem)
    mapping = read_map(folder)
    names = {sp["id"]: sp["name"] for sp in list_speakers(root)}
    if follow_fx is not None:
        mapping["follow_fx"] = bool(follow_fx)
    if thresh:
        mapping["thresh"] = clamp_thresh(
            thresh.get("high", HIGH), thresh.get("margin", MARGIN), thresh.get("low", LOW)
        )
        apply_thresholds(mapping)
    if fx is not None and speaker_id:
        ov = dict(mapping.get("fx_overrides") or {})
        ov[speaker_id] = fx
        mapping["fx_overrides"] = ov
    sid = speaker_id
    name = None
    if sid:
        try:
            name = get_speaker(root, sid)["name"]
        except Exception:
            name = names.get(sid, sid)
    keys: list[str] = []
    if n is not None:
        keys.append(str(int(n)))
    if cluster:
        for key, row in (mapping.get("lines") or {}).items():
            if str(row.get("cluster") or "") == str(cluster):
                keys.append(key)
    lines = dict(mapping.get("lines") or {})
    for key in keys:
        row = dict(lines.get(key) or {})
        if sid:
            row["speaker_id"] = sid
            row["name"] = name
            row["status"] = "manual"
            row["conf"] = 1.0
        else:
            row["speaker_id"] = None
            row["name"] = None
            row["status"] = "unmatched"
        lines[key] = row
    mapping["lines"] = lines
    write_map(folder, mapping)
    stats = tally_lines(mapping["lines"])
    stats["follow_fx"] = bool(mapping.get("follow_fx", True))
    stats["thresh"] = thresh_of(mapping)
    return stats


def set_thresholds(root: Path, pid: str, stem: str, *, high: float, margin: float, low: float) -> dict[str, Any]:
    folder = recording_dir(root, pid, stem)
    mapping = read_map(folder)
    mapping["thresh"] = clamp_thresh(high, margin, low)
    apply_thresholds(mapping)
    write_map(folder, mapping)
    stats = tally_lines(mapping["lines"])
    stats["thresh"] = thresh_of(mapping)
    stats["follow_fx"] = bool(mapping.get("follow_fx", True))
    return stats


def harvest_samples(
    root: Path,
    pid: str,
    stem: str,
    *,
    speaker_id: Optional[str] = None,
    ns: Optional[list[int]] = None,
) -> dict[str, Any]:
    from .speakers import add_sample_path

    folder = recording_dir(root, pid, stem)
    mapping = read_map(folder)
    clips = _clip_files(folder)
    lines = mapping.get("lines") or {}
    picked: list[tuple[str, dict[str, Any]]] = []
    if ns:
        for n in ns:
            row = lines.get(str(int(n)))
            if isinstance(row, dict) and row.get("speaker_id"):
                picked.append((str(int(n)), row))
    else:
        for key, row in lines.items():
            if not isinstance(row, dict) or not row.get("speaker_id"):
                continue
            if speaker_id and row.get("speaker_id") != speaker_id:
                continue
            if row.get("status") != "manual" and not speaker_id:
                continue
            picked.append((key, row))
    added = 0
    skipped = 0
    errors: list[str] = []
    for key, row in picked:
        sid = str(row.get("speaker_id"))
        span_i = row.get("span_i")
        clip = clips.get(int(span_i or 0)) or clips.get(int(key))
        if clip is None or not clip.is_file():
            skipped += 1
            errors.append(f"#{key} no clip")
            continue
        try:
            add_sample_path(root, sid, clip)
            added += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"#{key} {exc}")
    return {"added": added, "skipped": skipped, "errors": errors[:12], "picked": len(picked)}
