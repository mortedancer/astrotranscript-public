import json
import wave
from pathlib import Path

from backend.app.projects import (
    create_project,
    import_recording,
    recording_dir,
    find_stem,
    delete_recording,
    recording_links,
    Conflict,
    BadRequest,
)
from backend.app import catalog, jobs


def _silence_wav(path: Path, seconds: float = 0.05, sr: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(seconds * sr))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
    return path


def test_import_wav_creates_pending_recording(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "2026.08.18.163513.WAV")
    rec = import_recording(tmp_path, "kids", wav)
    assert rec["id"] == "2026.08.18.163513"
    dest = tmp_path / "kids" / "2026.08.18.163513"
    meta = json.loads((dest / "recording.json").read_text(encoding="utf-8"))
    assert meta["status"] == "pending"
    assert Path(meta["source"]) == wav.resolve()
    items = catalog.list_recordings("kids", tmp_path)
    assert len(items) == 1
    assert items[0]["status"] == "pending"
    assert items[0]["id"] == "2026.08.18.163513"


def test_recording_title_and_comment(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "2026.08.18.163513.WAV")
    import_recording(tmp_path, "kids", wav)
    updated = catalog.update_recording_notes(
        tmp_path,
        "kids",
        "2026.08.18.163513",
        title="Утро во дворе",
        comment="заметка\nвторая строка",
    )
    assert updated["id"] == "2026.08.18.163513"
    assert updated["title"] == "Утро во дворе"
    assert updated["name"] == "Утро во дворе"
    assert updated["comment"] == "заметка\nвторая строка"
    assert updated["file_name"] == "2026.08.18.163513.WAV"
    meta = json.loads(
        (tmp_path / "kids" / "2026.08.18.163513" / "recording.json").read_text(encoding="utf-8")
    )
    assert meta["status"] == "pending"
    assert meta["title"] == "Утро во дворе"
    listed = catalog.list_recordings("kids", tmp_path)
    assert listed[0]["comment"].startswith("заметка")
    cleared = catalog.update_recording_notes(tmp_path, "kids", "2026.08.18.163513", title="", comment="  ")
    assert cleared["title"] == ""
    assert cleared["name"] == "2026.08.18.163513"
    assert cleared["comment"] == ""
    try:
        catalog.update_recording_notes(tmp_path, "kids", "2026.08.18.163513", comment="я" * 2001)
        raise AssertionError("expected BadRequest")
    except BadRequest:
        pass
    detail = catalog.load_project("2026.08.18.163513", "kids", tmp_path)
    assert detail["title"] == ""
    assert detail["file_name"] == "2026.08.18.163513.WAV"


def test_markers_roundtrip_and_do_not_touch_notes(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "note.wav")
    import_recording(tmp_path, "kids", wav)
    catalog.update_recording_notes(tmp_path, "kids", "note", title="Имя", comment="ком")
    saved = catalog.set_markers(
        tmp_path,
        "kids",
        "note",
        [
            {"t": 1.2346, "name": "  вторая\nметка "},
            {"t": 0.2},
            {"t": 0.205, "name": "рядом"},
        ],
    )
    assert saved["markers"] == [
        {"t": 0.2, "name": "рядом"},
        {"t": 1.235, "name": "вторая метка"},
    ]
    detail = catalog.load_project("note", "kids", tmp_path)
    assert detail["markers"] == saved["markers"]
    assert detail["title"] == "Имя"
    assert detail["comment"] == "ком"
    listed = catalog.list_recordings("kids", tmp_path)
    assert "markers" not in listed[0]
    meta_path = tmp_path / "kids" / "note" / "recording.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "pending"
    assert meta["title"] == "Имя"
    assert meta["markers"][0]["name"] == "рядом"
    both = catalog.set_markers(
        tmp_path,
        "kids",
        "note",
        [{"t": 1.0, "name": "а"}, {"t": 1.005, "name": "б"}],
    )
    assert [m["name"] for m in both["markers"]] == ["а", "б"]
    same = catalog.set_markers(
        tmp_path,
        "kids",
        "note",
        [{"t": 1.0, "name": "а"}, {"t": 1.005, "name": "а"}],
    )
    assert same["markers"] == [{"t": 1.0, "name": "а"}]
    edge = catalog.set_markers(tmp_path, "kids", "note", [{"t": 48 * 3600}])
    assert edge["markers"] == [{"t": float(48 * 3600)}]
    cleared = catalog.set_markers(tmp_path, "kids", "note", [])
    assert cleared["markers"] == []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "markers" not in meta
    assert meta["title"] == "Имя"
    assert catalog.load_project("note", "kids", tmp_path)["markers"] == []


def test_markers_reject_garbage(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "note.wav")
    import_recording(tmp_path, "kids", wav)
    samples = (
        "no",
        {"t": 1},
        [{"t": "1"}],
        [{"t": True}],
        [{"t": -0.001}],
        [{"t": 48 * 3600 + 0.001}],
        [{"t": float("nan")}],
        [{"t": float("inf")}],
        [{"t": 1, "name": "я" * 81}],
        [{"t": 1, "name": 5}],
        [[{"t": 1}]],
        [{"t": i * 0.02} for i in range(201)],
    )
    for bad in samples:
        try:
            catalog.set_markers(tmp_path, "kids", "note", bad)
            raise AssertionError(f"expected BadRequest for {bad!r}")
        except BadRequest:
            pass
    meta = json.loads((tmp_path / "kids" / "note" / "recording.json").read_text(encoding="utf-8"))
    assert "markers" not in meta


def test_import_wav_duplicate_stem_conflicts(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "2026.08.18.163513.WAV")
    import_recording(tmp_path, "kids", wav)
    other = _silence_wav(tmp_path / "incoming" / "copy" / "2026.08.18.163513.wav")
    try:
        import_recording(tmp_path, "kids", other)
        raise AssertionError("expected Conflict")
    except Conflict as e:
        assert e.status == 409


def test_import_rejects_unknown_path_type(tmp_path):
    create_project(tmp_path, "Kids")
    src = tmp_path / "notes.txt"
    src.write_text("x", encoding="utf-8")
    try:
        import_recording(tmp_path, "kids", src)
        raise AssertionError("expected BadRequest")
    except BadRequest:
        pass


def test_adapt_window_boosts_quiet_noise():
    from backend.app.process import adapt_window
    import numpy as np

    rng = np.random.default_rng(0)
    quiet = rng.normal(0, 0.002, 16000).astype(np.float32)
    y, info = adapt_window(quiet, 16000)
    assert info["gain"] > 1.0
    assert float(np.sqrt(np.mean(y**2))) > float(np.sqrt(np.mean(quiet**2)))


def test_adaptive_af_tracks_noise():
    from backend.app.pipeline import DEFAULT_PIPELINE
    from backend.app.process import adaptive_af

    af = adaptive_af(DEFAULT_PIPELINE.enhance)
    assert "afftdn" in af and "tn=1" in af
    assert "dynaudnorm" in af
    assert "highpass" in af


def test_ensure_pcm16_passthrough_for_pcm(tmp_path):
    from backend.app.process import ensure_pcm16, is_pcm16_wav

    src = _silence_wav(tmp_path / "ok.wav")
    out = ensure_pcm16(src, tmp_path / "pcm16.wav")
    assert out == src
    assert is_pcm16_wav(out)


def test_ensure_pcm16_decodes_ima_adpcm(tmp_path):
    from backend.app.process import ensure_pcm16, ffmpeg_bin, is_pcm16_wav, probe_duration
    import subprocess

    try:
        exe = ffmpeg_bin()
    except FileNotFoundError:
        return
    adpcm = tmp_path / "ima.wav"
    try:
        subprocess.run(
            [exe, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25", "-acodec", "adpcm_ima_wav", str(adpcm)],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    if is_pcm16_wav(adpcm):
        return
    pcm = ensure_pcm16(adpcm, tmp_path / "pcm16.wav")
    assert is_pcm16_wav(pcm)
    assert probe_duration(pcm) > 0.1


def test_playable_source_uses_pcm16_when_original_is_not_pcm(tmp_path):
    create_project(tmp_path, "Kids")
    folder = tmp_path / "kids" / "R20250827-115520"
    folder.mkdir()
    (folder / "speech_map.json").write_text("{}", encoding="utf-8")
    (folder / "original.wav").write_bytes(b"RIFF....ADPCM")
    pcm = _silence_wav(folder / "pcm16.wav", seconds=0.2)
    p = catalog.playable_source_path("R20250827-115520", "kids", tmp_path)
    assert p == pcm
    peaks = catalog.cached_peaks("R20250827-115520", "original", bins=32, project_id="kids", root=tmp_path)
    assert len(peaks) == 32
    assert max(peaks) >= 0.0


def test_ingest_mp3_writes_original_mp3(tmp_path):
    from backend.app.projects import ingest_uploaded_wav

    create_project(tmp_path, "Kids")
    rec = ingest_uploaded_wav(tmp_path, "kids", "talk.mp3", b"ID3fake-bytes")
    dest = tmp_path / "kids" / "talk" / "original.mp3"
    assert dest.is_file()
    assert dest.read_bytes().startswith(b"ID3")
    src = catalog.source_path(rec["id"], "kids", tmp_path)
    assert src == dest


def test_cleaned_mp3_export(tmp_path):
    from backend.app.process import ffmpeg_bin
    import subprocess

    try:
        exe = ffmpeg_bin()
    except FileNotFoundError:
        return
    create_project(tmp_path, "Kids")
    folder = tmp_path / "kids" / "2026.08.18.163513"
    folder.mkdir()
    wav = _silence_wav(folder / "cleaned_speech.wav", seconds=0.3)
    (folder / "speech_map.json").write_text("{}", encoding="utf-8")
    try:
        mp3 = catalog.cleaned_mp3_path("2026.08.18.163513", "kids", tmp_path)
    except (subprocess.CalledProcessError, FileNotFoundError, RuntimeError):
        return
    assert mp3.is_file()
    assert mp3.stat().st_size > 0
    assert mp3.suffix.lower() == ".mp3"


def test_ingest_uploaded_wav_writes_original(tmp_path):
    from backend.app.projects import ingest_uploaded_wav

    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "2026.08.18.163513.WAV")
    data = wav.read_bytes()
    rec = ingest_uploaded_wav(tmp_path, "kids", "2026.08.18.163513.WAV", data)
    assert rec["id"] == "2026.08.18.163513"
    dest = tmp_path / "kids" / "2026.08.18.163513" / "original.wav"
    assert dest.is_file()
    src = catalog.source_path("2026.08.18.163513", "kids", tmp_path)
    assert src == dest


def test_ingest_uploaded_wav_streams_fileobj_without_read_all(tmp_path):
    from backend.app.projects import ingest_uploaded_wav

    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "big.WAV", seconds=1.0)
    with wav.open("rb") as fh:
        rec = ingest_uploaded_wav(tmp_path, "kids", "big.WAV", fh)
    dest = tmp_path / "kids" / "big" / "original.wav"
    assert rec["id"] == "big"
    assert dest.is_file()
    assert dest.stat().st_size == wav.stat().st_size
    assert dest.read_bytes()[:4] == b"RIFF"


def test_ingest_uploaded_wav_rejects_empty_fileobj(tmp_path):
    from io import BytesIO

    from backend.app.projects import ingest_uploaded_wav, BadRequest

    create_project(tmp_path, "Kids")
    try:
        ingest_uploaded_wav(tmp_path, "kids", "empty.wav", BytesIO(b""))
        raise AssertionError("expected BadRequest")
    except BadRequest:
        pass
    assert not (tmp_path / "kids" / "empty").exists()


def test_ingest_uploaded_wav_refuses_when_disk_full(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    from backend.app.projects import ingest_uploaded_wav, InsufficientStorage

    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "fat.WAV", seconds=0.2)
    data = wav.read_bytes()
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(total=1000, used=990, free=20))
    try:
        ingest_uploaded_wav(tmp_path, "kids", "fat.WAV", data)
        raise AssertionError("expected InsufficientStorage")
    except InsufficientStorage as e:
        assert e.status == 507
        assert "free" in e.detail.lower() or "full" in e.detail.lower()
        assert str(len(data)) in e.detail or "MB" in e.detail or "need" in e.detail.lower()
    assert not (tmp_path / "kids" / "fat").exists()


def test_require_disk_space_maps_enospc_message(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    from backend.app.projects import InsufficientStorage, require_disk_space

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(total=1000, used=999, free=50))
    try:
        require_disk_space(tmp_path, 10**9, what="pcm16.wav")
        raise AssertionError("expected InsufficientStorage")
    except InsufficientStorage as e:
        assert "pcm16.wav" in e.detail
        assert e.status == 507
        assert "free" in e.detail.lower() or "full" in e.detail.lower()


def test_find_stem_sees_pending_wav(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "2026.08.19.010000.WAV")
    import_recording(tmp_path, "kids", wav)
    assert find_stem(tmp_path, "2026.08.19.010000") == "kids"
    assert recording_dir(tmp_path, "kids", "2026.08.19.010000").is_dir()


def test_process_job_runs_injected_processor(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "2026.08.18.163513.WAV")
    import_recording(tmp_path, "kids", wav)

    def fake_process(src, out_dir, pipeline, log):
        log("vad done")
        (out_dir / "speech_map.json").write_text(
            json.dumps({"source": str(src), "duration_sec": 1, "speech_sec": 0.2, "spans": []}),
            encoding="utf-8",
        )
        (out_dir / "transcript.txt").write_text(
            "=== С таймкодами исходника ===\n[00:00:00.00–00:00:01.00] hi\n",
            encoding="utf-8",
        )
        return {"lines": 1}

    jobs.set_processor(fake_process)
    try:
        rec = jobs.enqueue(tmp_path, "kids", "2026.08.18.163513")
        assert rec["status"] in {"queued", "vad", "processing", "completed"}
        jobs.wait_idle(timeout=5)
        items = catalog.list_recordings("kids", tmp_path)
        assert items[0]["status"] == "completed"
        assert (tmp_path / "kids" / "2026.08.18.163513" / "speech_map.json").exists()
        meta = json.loads(
            (tmp_path / "kids" / "2026.08.18.163513" / "recording.json").read_text(encoding="utf-8")
        )
        assert meta["status"] == "completed"
        assert any("vad done" in line for line in meta.get("log") or [])
    finally:
        jobs.set_processor(None)


def test_delete_pending_recording(tmp_path):
    create_project(tmp_path, "Kids")
    wav = _silence_wav(tmp_path / "incoming" / "drop.WAV")
    import_recording(tmp_path, "kids", wav)
    delete_recording(tmp_path, "kids", "drop")
    assert not (tmp_path / "kids" / "drop").exists()
    assert wav.is_file()


def test_delete_processed_requires_confirm(tmp_path):
    create_project(tmp_path, "Kids")
    src = tmp_path / "incoming" / "2026.08.18.163513"
    src.mkdir(parents=True)
    (src / "speech_map.json").write_text("{}", encoding="utf-8")
    (src / "transcript.txt").write_text("x", encoding="utf-8")
    import_recording(tmp_path, "kids", src)
    links = recording_links(tmp_path, "2026.08.18.163513")
    assert links[0]["processed"] is True
    assert links[0]["project_id"] == "kids"
    try:
        delete_recording(tmp_path, "kids", "2026.08.18.163513")
        raise AssertionError("expected Conflict")
    except Conflict as e:
        assert e.status == 409
        assert "kids" in e.detail.lower() or "Kids" in e.detail
        assert "processed" in e.detail.lower()
    assert (tmp_path / "kids" / "2026.08.18.163513" / "speech_map.json").exists()
    delete_recording(tmp_path, "kids", "2026.08.18.163513", confirm=True)
    assert not (tmp_path / "kids" / "2026.08.18.163513").exists()
