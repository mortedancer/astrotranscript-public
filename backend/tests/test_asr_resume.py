import json
from pathlib import Path

from backend.app.pipeline import DEFAULT_PIPELINE
from backend.app.process import Span, process_file
from backend.app.projects import create_project, ensure_layout
from backend.app import jobs


def _map(spans: list[Span], src: Path, pcm: Path) -> dict:
    return {
        "source": str(src),
        "work": str(pcm),
        "duration_sec": 5.0,
        "spans": [{"start": s.start, "end": s.end} for s in spans],
        "speech_sec": sum(s.dur for s in spans),
        "preprocess": "pcm",
    }


def test_process_resumes_remaining_spans(tmp_path, monkeypatch):
    from backend.app import process as proc

    src = tmp_path / "rec.wav"
    src.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    pcm = out / "pcm16.wav"
    pcm.write_bytes(b"x")
    spans = [Span(0, 1), Span(2, 3), Span(4, 5)]
    (out / "speech_map.json").write_text(json.dumps(_map(spans, src, pcm)), encoding="utf-8")
    clips = out / "clips"
    clips.mkdir()
    (clips / "0001_00000-00001.wav").write_bytes(b"clip")
    progress = {
        "completed": [1],
        "segments": [{"span_i": 1, "start_rel": 0.0, "end_rel": 0.5, "text": "first"}],
    }
    (out / "asr_progress.json").write_text(json.dumps(progress), encoding="utf-8")

    transcribed: list[str] = []
    extracted: list[str] = []

    monkeypatch.setattr(proc, "ensure_pcm16", lambda s, d, log=None: pcm)
    monkeypatch.setattr(proc, "adaptive_preprocess", lambda s, d, pipeline, log=None: pcm)
    monkeypatch.setattr(proc, "wav_info", lambda p: (16000, 1, 5.0))
    monkeypatch.setattr(proc, "detect_speech", lambda *a, **k: (_ for _ in ()).throw(AssertionError("VAD should be skipped")))
    monkeypatch.setattr(proc, "_whisper", lambda pipeline: object())

    def fake_extract(src_p, span, dest, pipeline, preprocessed=False):
        extracted.append(dest.name)
        dest.write_bytes(b"clip")

    def fake_transcribe(model, wav, pipeline):
        transcribed.append(Path(wav).name)
        return [(0.0, 0.4, f"from-{Path(wav).name[:4]}")]

    monkeypatch.setattr(proc, "extract_clean", fake_extract)
    monkeypatch.setattr(proc, "transcribe_span", fake_transcribe)
    monkeypatch.setattr(proc, "concat_wavs", lambda parts, dest: dest.write_bytes(b"ok"))

    result = process_file(src, out, DEFAULT_PIPELINE)
    assert result["lines"] == 3
    assert transcribed == ["0002_00002-00003.wav", "0003_00004-00005.wav"]
    assert "0001_00000-00001.wav" not in extracted
    text = (out / "transcript.txt").read_text(encoding="utf-8")
    assert "=== С таймкодами исходника ===" in text
    assert "first" in text
    assert "from-0002" in text
    assert "from-0003" in text
    saved = json.loads((out / "asr_progress.json").read_text(encoding="utf-8"))
    assert saved["completed"] == [1, 2, 3]


def test_process_file_writes_cleaned_mp3(tmp_path, monkeypatch):
    from backend.app import process as proc

    src = tmp_path / "rec.wav"
    src.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    pcm = out / "pcm16.wav"
    pcm.write_bytes(b"x")
    spans = [Span(0, 1)]
    (out / "speech_map.json").write_text(
        json.dumps(
            {
                "source": str(src),
                "work": str(pcm),
                "duration_sec": 1.0,
                "spans": [{"start": 0, "end": 1}],
                "speech_sec": 1.0,
                "preprocess": "pcm",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(proc, "ensure_pcm16", lambda s, d, log=None: pcm)
    monkeypatch.setattr(proc, "adaptive_preprocess", lambda *a, **k: pcm)
    monkeypatch.setattr(proc, "wav_info", lambda p: (16000, 1, 1.0))
    monkeypatch.setattr(proc, "detect_speech", lambda *a, **k: spans)
    monkeypatch.setattr(proc, "_whisper", lambda pipeline: object())
    monkeypatch.setattr(proc, "extract_clean", lambda src_p, span, dest, pipeline, preprocessed=False: dest.write_bytes(b"c"))
    monkeypatch.setattr(proc, "transcribe_span", lambda *a, **k: [(0.0, 0.4, "hi")])
    monkeypatch.setattr(proc, "concat_wavs", lambda parts, dest: dest.write_bytes(b"RIFF"))

    encoded = []

    def fake_mp3(wav, dest, log=None):
        assert wav.name == "cleaned_speech.wav"
        dest.write_bytes(b"ID3ok")
        encoded.append(dest.name)
        return dest

    monkeypatch.setattr(proc, "encode_speech_mp3", fake_mp3)
    process_file(src, out, DEFAULT_PIPELINE)
    assert encoded == ["cleaned_speech.mp3"]
    assert (out / "cleaned_speech.mp3").read_bytes() == b"ID3ok"


def test_reclaim_orphans_marks_stale_asr_job(tmp_path):
    create_project(tmp_path, "Kids")
    folder = tmp_path / "kids" / "2026.08.18.163513"
    folder.mkdir()
    (folder / "speech_map.json").write_text("{}", encoding="utf-8")
    jobs.write_meta(
        folder,
        {
            "id": "2026.08.18.163513",
            "source": "x.wav",
            "status": "processing",
            "stage": "asr",
            "error": None,
            "log": ["clean+asr 35/87"],
        },
    )
    n = jobs.reclaim_orphans(tmp_path)
    assert n == 1
    meta = jobs.read_meta(folder)
    assert meta["status"] == "error"
    assert meta["error"] == "interrupted: API process exited during ASR"
    assert "interrupted: API process exited" in meta["log"][-1]


def test_reclaim_orphans_skips_job_running_in_this_process(tmp_path):
    create_project(tmp_path, "Kids")
    folder = tmp_path / "kids" / "2026.08.18.163513"
    folder.mkdir()
    (folder / "speech_map.json").write_text("{}", encoding="utf-8")
    jobs.write_meta(
        folder,
        {
            "id": "2026.08.18.163513",
            "source": "x.wav",
            "status": "processing",
            "stage": "asr",
            "error": None,
            "log": [],
        },
    )
    jobs._current = {"project_id": "kids", "stem": "2026.08.18.163513", "status": "processing"}
    try:
        assert jobs.reclaim_orphans(tmp_path) == 0
        assert jobs.read_meta(folder)["status"] == "processing"
    finally:
        jobs._current = None


def test_detect_speech_resumes_from_checkpoint(monkeypatch):
    import numpy as np
    from backend.app.pipeline import VADConfig
    from backend.app.process import Span, detect_speech
    import faster_whisper.vad as vadmod

    starts = []
    monkeypatch.setattr("backend.app.process.wav_info", lambda p: (16000, 1, 600.0))
    monkeypatch.setattr(
        "backend.app.process.read_mono_f32",
        lambda path, start, dur, sr: (starts.append(start) or np.zeros(int(dur * sr), np.float32)),
    )
    monkeypatch.setattr(
        vadmod,
        "get_speech_timestamps",
        lambda audio, opts, sampling_rate=16000: [{"start": 0, "end": 1600}],
    )
    vad = VADConfig(adaptive=False, window_s=300, min_keep_s=0.01)
    spans = detect_speech(
        Path("dummy.wav"),
        vad,
        lambda _m: None,
        start_t=300.0,
        raw_spans=[Span(10.0, 11.0)],
    )
    assert starts == [300.0]
    assert any(s.start < 11.0 and s.end > 10.0 for s in spans)


def test_enqueue_stale_processing_is_resumable(tmp_path):
    create_project(tmp_path, "Kids")
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"RIFF")
    folder = tmp_path / "kids" / "rec1"
    folder.mkdir()
    (folder / "speech_map.json").write_text("{}", encoding="utf-8")
    jobs.write_meta(
        folder,
        {"id": "rec1", "source": str(wav), "status": "processing", "stage": "asr", "error": None, "log": []},
    )
    called = []

    def fake(src, out_dir, pipeline, log):
        called.append(out_dir.name)
        return {"lines": 0}

    jobs.set_processor(fake)
    try:
        meta = jobs.enqueue(tmp_path, "kids", "rec1")
        assert meta["status"] == "queued"
        jobs.wait_idle(timeout=8)
        assert called == ["rec1"], f"worker did not run, meta={jobs.read_meta(folder)}"
        assert jobs.read_meta(folder)["status"] == "completed"
    finally:
        jobs.set_processor(None)
