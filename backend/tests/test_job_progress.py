import json
from pathlib import Path

from backend.app import catalog, jobs
from backend.app.pipeline import DEFAULT_PIPELINE
from backend.app.process import Span, process_file
from backend.app.projects import create_project


def test_set_progress_records_operation_and_overall_pct(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    prog = jobs.set_progress(folder, "asr", done=35, total=87, detail="01:58:58–01:58:59")
    assert prog["op"] == "asr"
    assert prog["done"] == 35
    assert prog["total"] == 87
    assert prog["pct"] == round(100 * 35 / 87, 1)
    assert "Transcription 35/87" in prog["label"]
    assert "01:58:58" in prog["label"]
    assert prog["overall_pct"] >= 48
    assert prog["overall_pct"] < 95
    meta = jobs.read_meta(folder)
    assert meta["stage"] == "asr"
    assert meta["progress"]["done"] == 35


def test_process_file_updates_asr_progress(tmp_path, monkeypatch):
    from backend.app import process as proc

    src = tmp_path / "rec.wav"
    src.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    pcm = out / "pcm16.wav"
    pcm.write_bytes(b"x")
    spans = [Span(0, 1), Span(2, 3), Span(4, 5)]
    (out / "speech_map.json").write_text(
        json.dumps(
            {
                "source": str(src),
                "work": str(pcm),
                "duration_sec": 5.0,
                "spans": [{"start": s.start, "end": s.end} for s in spans],
                "speech_sec": 3.0,
                "preprocess": "pcm",
            }
        ),
        encoding="utf-8",
    )
    seen: list[dict] = []

    monkeypatch.setattr(proc, "ensure_pcm16", lambda s, d, log=None: pcm)
    monkeypatch.setattr(proc, "adaptive_preprocess", lambda s, d, pipeline, log=None: pcm)
    monkeypatch.setattr(proc, "wav_info", lambda p: (16000, 1, 5.0))
    monkeypatch.setattr(proc, "detect_speech", lambda *a, **k: spans)
    monkeypatch.setattr(proc, "_whisper", lambda pipeline: object())
    monkeypatch.setattr(proc, "extract_clean", lambda *a, **k: (k.get("dest") or a[2]).write_bytes(b"c"))
    monkeypatch.setattr(
        proc,
        "transcribe_span",
        lambda model, wav, pipeline: [(0.0, 0.3, "hi")],
    )
    monkeypatch.setattr(proc, "concat_wavs", lambda parts, dest: dest.write_bytes(b"ok"))

    orig = jobs.set_progress

    def wrap(folder, op, **kw):
        p = orig(folder, op, **kw)
        seen.append(dict(p))
        return p

    monkeypatch.setattr(jobs, "set_progress", wrap)

    process_file(src, out, DEFAULT_PIPELINE)
    asr = [p for p in seen if p["op"] == "asr"]
    assert asr, f"no asr progress in {seen!r}"
    assert asr[-1]["done"] == 3
    assert asr[-1]["total"] == 3
    meta = jobs.read_meta(out)
    assert meta["progress"]["op"] in {"asr", "concat", "mp3", "done"}
    assert meta["progress"]["overall_pct"] >= 48


def test_catalog_exposes_job_progress(tmp_path):
    create_project(tmp_path, "Kids")
    folder = tmp_path / "kids" / "2026.08.18.163513"
    folder.mkdir()
    (folder / "speech_map.json").write_text(
        json.dumps({"duration_sec": 10, "speech_sec": 1, "spans": []}), encoding="utf-8"
    )
    jobs.write_meta(
        folder,
        {
            "id": "2026.08.18.163513",
            "status": "processing",
            "stage": "vad",
            "error": None,
            "log": [],
        },
    )
    jobs.set_progress(folder, "vad", done=120, total=300, detail="00:02:00")
    items = catalog.list_recordings("kids", tmp_path)
    prog = items[0]["job"]["progress"]
    assert prog["op"] == "vad"
    assert prog["done"] == 120
    assert prog["total"] == 300
    assert "Voice activity" in prog["label"]


def test_infer_progress_from_vad_log():
    meta = {
        "status": "processing",
        "stage": "vad",
        "duration_sec": 29129.6,
        "log": [
            "start x.WAV",
            "  00:50:00.00 spans+=0 total=21 thr=0.50",
            "  00:55:00.00 spans+=0 total=21 thr=0.42",
        ],
    }
    prog = jobs.infer_progress(meta)
    assert prog["op"] == "vad"
    assert prog["done"] == 3300
    assert prog["total"] == 29129
    assert "00:55:00" in prog["label"]


def test_infer_progress_from_asr_log():
    meta = {
        "status": "processing",
        "stage": "asr",
        "log": ["clean+asr 10/87 01:51:24.08–01:51:30.96 (6.9s)", "clean+asr 35/87 01:58:58.16–01:58:59.57 (1.4s)"],
    }
    prog = jobs.infer_progress(meta)
    assert prog["op"] == "asr"
    assert prog["done"] == 35
    assert prog["total"] == 87

