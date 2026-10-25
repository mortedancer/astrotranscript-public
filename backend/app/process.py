"""VAD, denoise, and ASR for a single dictaphone WAV."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .pipeline import DEFAULT_PIPELINE, PipelineConfig, VADConfig

HALLU = (
    "субтитры сделал",
    "субтитры подогнал",
    "субтитры создавал",
    "dimatorzok",
    "продолжение следует",
    "подпишись",
    "subscribe",
    "редактор субтитров",
    "симон",
    "динамичная музыка",
)

LogFn = Callable[[str], None]


@dataclass
class Span:
    start: float
    end: float

    @property
    def dur(self) -> float:
        return max(0.0, self.end - self.start)


def ffmpeg_bin() -> str:
    env = os.environ.get("ASTRO_FFMPEG")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError("ffmpeg not found; install ffmpeg or set ASTRO_FFMPEG")


def ts_txt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def ts_srt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def is_halluc(text: str) -> bool:
    low = text.lower()
    if any(s in low for s in HALLU):
        return True
    words = [w for w in low.replace(",", " ").replace(".", " ").split() if w]
    return len(words) >= 6 and len(set(words)) <= 2


def is_pcm16_wav(path: Path) -> bool:
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            return w.getsampwidth() == 2 and w.getnchannels() >= 1
    except Exception:
        return False


def probe_duration(path: Path) -> float:
    if is_pcm16_wav(path):
        import wave

        with wave.open(str(path), "rb") as w:
            sr = w.getframerate() or 1
            return w.getnframes() / float(sr)
    exe = ffmpeg_bin()
    probe = str(Path(exe).with_name(Path(exe).name.replace("ffmpeg", "ffprobe")))
    cmd = [probe if Path(probe).is_file() else exe]
    if cmd[0].endswith("ffprobe") or cmd[0].endswith("ffprobe.EXE"):
        out = subprocess.check_output(
            [cmd[0], "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
            stderr=subprocess.STDOUT,
            text=True,
        )
        return float(out.strip() or 0)
    # ffmpeg -i prints Duration: HH:MM:SS.xx
    proc = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True)
    blob = (proc.stderr or "") + (proc.stdout or "")
    import re

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", blob)
    if not m:
        raise RuntimeError(f"cannot probe duration of {path.name}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def ensure_pcm16(src: Path, dest: Path, log: LogFn | None = None) -> Path:
    log = log or (lambda _m: None)
    if is_pcm16_wav(src):
        return src
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime and is_pcm16_wav(dest):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"decode {src.name} → PCM 16 kHz mono (compressed WAV / format ≠ 1)")
    ffmpeg("-i", str(src), "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", str(dest))
    if not is_pcm16_wav(dest):
        raise RuntimeError(f"ffmpeg did not produce PCM from {src.name}")
    return dest


def wav_info(path: Path) -> tuple[int, int, float]:
    import wave

    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        return sr, ch, n / float(sr)


def read_mono_f32(path: Path, start: float, dur: float, sr: int) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != sr:
            raise RuntimeError("sample rate mismatch")
        sw = w.getsampwidth()
        ch = w.getnchannels()
        start_f = int(start * sr)
        n = int(dur * sr)
        w.setpos(min(start_f, w.getnframes()))
        raw = w.readframes(n)
    if sw != 2:
        raise RuntimeError(f"unsupported width {sw}")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x


def adapt_window(x: np.ndarray, sr: int) -> tuple[np.ndarray, dict]:
    """Per-window noise floor + gain so long files don't keep a global threshold."""
    if x.size < sr * 0.2:
        return x, {"gain": 1.0, "noise": 0.0, "threshold_delta": 0.0}
    hop = max(1, int(0.02 * sr))
    n = x.size // hop
    if n < 4:
        return x, {"gain": 1.0, "noise": 0.0, "threshold_delta": 0.0}
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    noise = float(np.percentile(rms, 15))
    target = 0.018
    gain = float(np.clip(target / max(noise, 1e-6), 0.2, 14.0))
    y = np.clip(x * gain, -1.0, 1.0).astype(np.float32)
    delta = 0.0
    if gain > 4.0:
        delta = -0.08
    elif gain < 0.45:
        delta = 0.08
    return y, {"gain": gain, "noise": noise, "threshold_delta": delta}


def adaptive_af(enh) -> str:
    parts = [f"highpass=f={getattr(enh, 'highpass_hz', 90)}", f"lowpass=f={getattr(enh, 'lowpass_hz', 7500)}"]
    if getattr(enh, "enabled", True):
        nr = max(4, int(getattr(enh, "nr", 8)))
        nf = int(getattr(enh, "nf", -28))
        parts.append(f"afftdn=nr={nr}:nf={nf}:tn=1")
        if getattr(enh, "agc", True):
            parts.append("dynaudnorm=f=200:g=12:p=0.95:m=10")
        if getattr(enh, "limiter", True):
            parts.append("alimiter=limit=0.89:level=disabled")
    else:
        parts.append("dynaudnorm=f=200:g=10:p=0.95:m=8")
    return ",".join(parts)


def adaptive_preprocess(
    src: Path,
    dest: Path,
    pipeline: PipelineConfig,
    log: LogFn | None = None,
    on_frac=None,
) -> Path:
    """Streamed full-file cleanup. Works on multi-hour recordings (no full RAM load)."""
    log = log or (lambda _m: None)
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime and is_pcm16_wav(dest):
        log("adaptive preprocess cache hit")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    af = adaptive_af(pipeline.enhance)
    log(f"adaptive preprocess (streamed, long-file safe): {af}")
    dur = 0.0
    try:
        if is_pcm16_wav(src):
            dur = wav_info(src)[2]
    except Exception:
        dur = 0.0
    ffmpeg_with_progress(
        dur,
        on_frac,
        "-i",
        str(src),
        "-af",
        af,
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(dest),
    )
    if not is_pcm16_wav(dest):
        raise RuntimeError("adaptive preprocess did not produce PCM")
    return dest


def merge_spans(spans: list[Span], gap: float, pad: float, min_keep: float, total: float) -> list[Span]:
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: s.start)
    out = [Span(max(0.0, spans[0].start - pad), min(total, spans[0].end + pad))]
    for s in spans[1:]:
        a, b = max(0.0, s.start - pad), min(total, s.end + pad)
        if a <= out[-1].end + gap:
            out[-1].end = max(out[-1].end, b)
        else:
            out.append(Span(a, b))
    return [s for s in out if s.dur >= min_keep]


VAD_NAME = "vad_progress.json"


def load_vad_progress(out_dir: Path) -> dict:
    path = out_dir / VAD_NAME
    if not path.exists():
        return {"t": 0.0, "raw": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"t": 0.0, "raw": []}
    if not isinstance(data, dict):
        return {"t": 0.0, "raw": []}
    raw = []
    for row in data.get("raw") or []:
        try:
            raw.append({"start": float(row["start"]), "end": float(row["end"])})
        except (KeyError, TypeError, ValueError):
            continue
    return {"t": float(data.get("t") or 0.0), "raw": raw}


def save_vad_progress(out_dir: Path, t: float, raw: list[Span]) -> None:
    path = out_dir / VAD_NAME
    path.write_text(
        json.dumps(
            {"t": t, "raw": [{"start": s.start, "end": s.end} for s in raw]},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def detect_speech(
    path: Path,
    vad: VADConfig,
    log: LogFn,
    on_window=None,
    *,
    start_t: float = 0.0,
    raw_spans: list[Span] | None = None,
    on_checkpoint=None,
) -> list[Span]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    sr, _ch, total = wav_info(path)
    base_thr = float(vad.threshold)
    raw: list[Span] = list(raw_spans or [])
    t = max(0.0, float(start_t or 0.0))
    win_s = max(30, int(vad.window_s or 300))
    if t > 0:
        log(f"VAD resume {path.name} from {ts_txt(t)} / {total/60:.1f} min adaptive={bool(vad.adaptive)}")
    else:
        log(f"VAD {path.name} {total/60:.1f} min @ {sr} Hz adaptive={bool(vad.adaptive)}")
    while t < total - 0.05:
        win = min(win_s, total - t)
        audio = read_mono_f32(path, t, win, sr)
        if audio.size < sr * 0.3:
            break
        thr = base_thr
        if vad.adaptive:
            audio, info = adapt_window(audio, sr)
            thr = float(np.clip(base_thr + info["threshold_delta"], 0.22, 0.78))
        opts = VadOptions(
            threshold=thr,
            min_speech_duration_ms=int(vad.min_speech_ms),
            min_silence_duration_ms=int(vad.min_silence_ms),
            speech_pad_ms=int(vad.speech_pad_ms),
            max_speech_duration_s=float(vad.max_speech_s),
        )
        hits = get_speech_timestamps(audio, opts, sampling_rate=sr)
        for h in hits:
            raw.append(Span(t + h["start"] / float(sr), t + h["end"] / float(sr)))
        t += win
        log(f"  {ts_txt(min(t, total))} spans+={len(hits)} total={len(raw)} thr={thr:.2f}")
        if on_window:
            on_window(min(t, total), total)
        if on_checkpoint:
            on_checkpoint(min(t, total), raw)
    pad = vad.speech_pad_ms / 1000.0
    merged = merge_spans(raw, gap=float(vad.merge_gap_s), pad=pad, min_keep=float(vad.min_keep_s), total=total)
    speech = sum(s.dur for s in merged)
    log(f"speech {speech/60:.1f} min in {len(merged)} spans ({100 * speech / total if total else 0:.1f}%)")
    return merged


def ffmpeg(*args: str) -> None:
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def ffmpeg_with_progress(duration: float, on_frac, *args: str) -> None:
    if not duration or duration <= 0 or on_frac is None:
        ffmpeg(*args)
        return
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        *args,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    last_bucket = -1
    for raw in proc.stdout:
        line = raw.strip()
        if line.startswith("out_time=") and not line.startswith("out_time_"):
            val = line.split("=", 1)[1].strip()
            if not val or val == "N/A":
                continue
            try:
                h, m, s = val.split(":")
                sec = int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                continue
            frac = min(1.0, max(0.0, sec / duration))
            bucket = int(frac * 40)
            if bucket != last_bucket:
                last_bucket = bucket
                on_frac(frac)
        elif line == "progress=end":
            break
    err = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    if code:
        raise subprocess.CalledProcessError(code, cmd, stderr=err)
    on_frac(1.0)


def extract_clean(src: Path, span: Span, dest: Path, pipeline: PipelineConfig, *, preprocessed: bool = False) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["-ss", f"{span.start:.3f}", "-t", f"{span.dur:.3f}", "-i", str(src)]
    if not preprocessed:
        args += ["-af", adaptive_af(pipeline.enhance)]
    args += ["-ar", "16000", "-ac", "1", str(dest)]
    ffmpeg(*args)


def concat_wavs(parts: list[Path], dest: Path) -> None:
    lst = dest.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(dest))


def slice_to_mp3(src: Path, start: float, end: float, dest: Path) -> Path:
    """Cut [start, end] seconds from src into a 64 kbps mono MP3."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, float(start))
    end = max(start + 0.05, float(end))
    dur = end - start
    if dest.exists():
        dest.unlink()
    try:
        ffmpeg(
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{dur:.3f}",
            "-i",
            str(src),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(dest),
        )
    except subprocess.CalledProcessError:
        if dest.exists():
            dest.unlink()
        ffmpeg(
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{dur:.3f}",
            "-i",
            str(src),
            "-codec:a",
            "mp3",
            "-b:a",
            "64k",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(dest),
        )
    if not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError("mp3 encode failed")
    return dest


def encode_speech_mp3(src: Path, dest: Path, log: LogFn | None = None) -> Path:
    """64 kbps mono MP3 of cleaned speech — share file, not a working copy."""
    log = log or (lambda _m: None)
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime and dest.stat().st_size > 0:
        return dest
    log(f"mp3 {src.name} → {dest.name} (64 kbps mono)")
    try:
        ffmpeg(
            "-i",
            str(src),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(dest),
        )
    except subprocess.CalledProcessError:
        if dest.exists():
            dest.unlink()
        ffmpeg("-i", str(src), "-codec:a", "mp3", "-b:a", "64k", "-ac", "1", "-ar", "16000", str(dest))
    if not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError("mp3 encode failed")
    return dest


_model = None


def _whisper(pipeline: PipelineConfig):
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel

    asr = pipeline.asr
    _model = WhisperModel(
        os.environ.get("ASTRO_WHISPER_MODEL") or asr.model,
        device=os.environ.get("ASTRO_WHISPER_DEVICE") or asr.device,
        compute_type=asr.compute_type,
    )
    return _model


def transcribe_span(model, wav: Path, pipeline: PipelineConfig) -> list[tuple[float, float, str]]:
    asr = pipeline.asr
    segs, _ = model.transcribe(
        str(wav),
        language=asr.language,
        task="transcribe",
        vad_filter=asr.vad_filter,
        vad_parameters=dict(min_silence_duration_ms=350),
        beam_size=asr.beam_size,
        condition_on_previous_text=False,
        no_speech_threshold=0.55,
        compression_ratio_threshold=2.35,
    )
    out: list[tuple[float, float, str]] = []
    for s in segs:
        text = (s.text or "").strip()
        if not text or is_halluc(text):
            continue
        out.append((float(s.start), float(s.end), text))
    return out


PROGRESS_NAME = "asr_progress.json"


def clip_wav_name(i: int, span: Span) -> str:
    return f"{i:04d}_{int(span.start):05d}-{int(span.end):05d}.wav"


def load_progress(out_dir: Path) -> dict:
    path = out_dir / PROGRESS_NAME
    if not path.exists():
        return {"completed": [], "segments": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"completed": [], "segments": []}
    if not isinstance(data, dict):
        return {"completed": [], "segments": []}
    completed = [int(x) for x in (data.get("completed") or [])]
    segments = list(data.get("segments") or [])
    return {"completed": completed, "segments": segments}


def save_progress(out_dir: Path, progress: dict) -> None:
    path = out_dir / PROGRESS_NAME
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _report(out_dir: Path, op: str, *, done: int | None = None, total: int | None = None, detail: str = "") -> None:
    from . import jobs

    jobs.set_progress(out_dir, op, done=done, total=total, detail=detail)


def _set_recording_stage(out_dir: Path, stage: str) -> None:
    _report(out_dir, stage)


def _spans_from_map(data: dict) -> list[Span]:
    out: list[Span] = []
    for row in data.get("spans") or []:
        try:
            out.append(Span(float(row["start"]), float(row["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def write_transcript_files(
    src: Path,
    out_dir: Path,
    total: float,
    spans: list[Span],
    segments: list[dict],
) -> None:
    clips_dir = out_dir / "clips"
    lines_txt: list[str] = []
    lines_srt: list[str] = []
    plain: list[str] = []
    for n, seg in enumerate(segments, 1):
        a = float(seg["start_rel"])
        b = float(seg["end_rel"])
        text = str(seg.get("text") or "")
        lines_txt.append(f"[{ts_txt(a)}–{ts_txt(b)}] {text}")
        lines_srt.append(f"{n}\n{ts_srt(a)} --> {ts_srt(b)}\n{text}\n")
        plain.append(text)
    header = (
        f"Источник: {src}\n"
        f"Длительность файла: {total/60:.1f} мин\n"
        f"Речи (VAD): {sum(s.dur for s in spans)/60:.1f} мин, кусков {len(spans)}\n"
        f"Очищенные клипы: {clips_dir}\n"
        f"Склейка речи: {out_dir / 'cleaned_speech.wav'}\n\n"
        "=== С таймкодами исходника ===\n"
    )
    (out_dir / "transcript.txt").write_text(
        header + "\n".join(lines_txt) + "\n\n=== Сплошной текст ===\n" + "\n".join(plain) + "\n",
        encoding="utf-8",
    )
    (out_dir / "transcript.srt").write_text("\n".join(lines_srt) + ("\n" if lines_srt else ""), encoding="utf-8")


def process_file(src: Path, out_dir: Path, pipeline: PipelineConfig = DEFAULT_PIPELINE, log: LogFn | None = None) -> dict:
    log = log or (lambda _m: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "speech_map.json"
    existing_map = None
    if map_path.exists():
        try:
            loaded = json.loads(map_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("spans"):
                existing_map = loaded
        except Exception:
            existing_map = None

    src_bytes = src.stat().st_size if src.is_file() else 0
    need = src_bytes
    if pipeline.vad.adaptive or pipeline.enhance.enabled:
        need += src_bytes
    from .projects import require_disk_space

    require_disk_space(out_dir, int(need * 1.05) + 8 * 1024 * 1024, what="pcm16 decode/adapt")

    if existing_map:
        log("resume: using existing speech_map.json")
        work = Path(str(existing_map.get("work") or ""))
        pcm = work if work.is_file() else ensure_pcm16(src, out_dir / "pcm16.wav", log)
        adapted = str(existing_map.get("preprocess") or "") == "adaptive"
        spans = _spans_from_map(existing_map)
        total = float(existing_map.get("duration_sec") or 0.0)
        if total <= 0:
            _sr, _ch, total = wav_info(pcm)
    else:
        _report(out_dir, "decode")
        pcm = ensure_pcm16(src, out_dir / "pcm16.wav", log)
        adapted = False
        if pipeline.vad.adaptive or pipeline.enhance.enabled:
            _report(out_dir, "enhance", done=0, total=1)

            def _enh_frac(frac: float) -> None:
                _report(out_dir, "enhance", done=int(frac * 100), total=100)

            pcm = adaptive_preprocess(pcm, out_dir / "pcm16_adapt.wav", pipeline, log, on_frac=_enh_frac)
            adapted = True
        _sr, _ch, total = wav_info(pcm)
        _report(out_dir, "vad", done=0, total=max(1, int(total)))
        vad_ckpt = load_vad_progress(out_dir)
        raw0 = [Span(float(r["start"]), float(r["end"])) for r in vad_ckpt.get("raw") or []]

        def _vad_win(t: float, tot: float) -> None:
            _report(out_dir, "vad", done=int(t), total=max(1, int(tot)), detail=ts_txt(t))

        def _vad_save(t: float, raw: list[Span]) -> None:
            save_vad_progress(out_dir, t, raw)

        spans = (
            detect_speech(
                pcm,
                pipeline.vad,
                log,
                on_window=_vad_win,
                start_t=float(vad_ckpt.get("t") or 0.0),
                raw_spans=raw0,
                on_checkpoint=_vad_save,
            )
            if pipeline.vad.enabled
            else [Span(0.0, total)]
        )
        vad_path = out_dir / VAD_NAME
        if vad_path.exists():
            vad_path.unlink()
        map_path.write_text(
            json.dumps(
                {
                    "source": str(src),
                    "work": str(pcm),
                    "duration_sec": total,
                    "spans": [asdict(s) for s in spans],
                    "speech_sec": sum(s.dur for s in spans),
                    "preprocess": "adaptive" if adapted else "pcm",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    clips_dir = out_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    progress = load_progress(out_dir)
    done = {int(i) for i in progress["completed"]}
    segments: list[dict] = list(progress["segments"])
    cleaned_parts: list[Path] = []
    nspans = max(1, len(spans))
    _report(out_dir, "asr", done=len(done), total=nspans)
    model = None
    for i, span in enumerate(spans, 1):
        clip = clips_dir / clip_wav_name(i, span)
        if clip.exists() and clip.stat().st_size > 0:
            cleaned_parts.append(clip)
        if i in done:
            continue
        _report(
            out_dir,
            "asr",
            done=i - 1,
            total=nspans,
            detail=f"{ts_txt(span.start)}–{ts_txt(span.end)}",
        )
        log(f"clean+asr {i}/{len(spans)} {ts_txt(span.start)}–{ts_txt(span.end)} ({span.dur:.1f}s)")
        if not (clip.exists() and clip.stat().st_size > 0):
            try:
                extract_clean(pcm, span, clip, pipeline, preprocessed=adapted)
            except subprocess.CalledProcessError as exc:
                log(f"EXTRACT FAIL {exc}")
                continue
            cleaned_parts.append(clip)
        if model is None:
            model = _whisper(pipeline)
        try:
            segs = transcribe_span(model, clip, pipeline)
        except Exception as exc:
            log(f"ASR FAIL {exc}")
            continue
        for a, b, text in segs:
            abs0, abs1 = span.start + a, span.start + b
            row = f"[{ts_txt(abs0)}–{ts_txt(abs1)}] {text}"
            log(row)
            segments.append({"span_i": i, "start_rel": abs0, "end_rel": abs1, "text": text})
        done.add(i)
        progress = {"completed": sorted(done), "segments": segments}
        save_progress(out_dir, progress)
        write_transcript_files(src, out_dir, total, spans, segments)
        _report(
            out_dir,
            "asr",
            done=i,
            total=nspans,
            detail=f"{ts_txt(span.start)}–{ts_txt(span.end)}",
        )

    if cleaned_parts:
        _report(out_dir, "concat", done=0, total=1)
        try:
            concat_wavs(cleaned_parts, out_dir / "cleaned_speech.wav")
        except subprocess.CalledProcessError as exc:
            log(f"concat fail {exc}")
        _report(out_dir, "concat", done=1, total=1)
    cleaned = out_dir / "cleaned_speech.wav"
    if cleaned.exists() and cleaned.stat().st_size > 0:
        try:
            _report(out_dir, "mp3", done=0, total=1)
            encode_speech_mp3(cleaned, out_dir / "cleaned_speech.mp3", log)
            _report(out_dir, "mp3", done=1, total=1)
        except Exception as exc:
            log(f"mp3 export fail {exc}")
    write_transcript_files(src, out_dir, total, spans, segments)
    return {"spans": len(spans), "lines": len(segments), "out": str(out_dir), "resumed": bool(existing_map)}
