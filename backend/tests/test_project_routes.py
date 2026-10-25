import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRO_DATA_ROOT", str(tmp_path))
    return TestClient(main.app), tmp_path


def test_get_projects_migrates_and_returns_inbox(client):
    api, root = client
    rec = root / "2026.08.18.163513"
    rec.mkdir()
    (rec / "speech_map.json").write_text(json.dumps({"duration_sec": 1, "speech_sec": 0, "spans": []}), encoding="utf-8")
    r = api.get("/api/projects")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["projects"]]
    assert ids[0] == "inbox"
    inbox = next(p for p in r.json()["projects"] if p["id"] == "inbox")
    assert inbox["recordings"] == 1
    assert not (root / "2026.08.18.163513").exists()


def test_create_and_get_project(client):
    api, _ = client
    r = api.post("/api/projects", json={"name": "Kids Aug 2026"})
    assert r.status_code == 201
    assert r.json()["id"] == "kids-aug-2026"
    g = api.get("/api/projects/kids-aug-2026")
    assert g.status_code == 200
    assert g.json()["items"] == []


def test_patch_recording_notes(client):
    import wave
    from io import BytesIO

    api, _root = client
    api.post("/api/projects", json={"name": "Kids"})
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 400)
    up = api.post(
        "/api/projects/kids/recordings/upload",
        files={"file": ("drop.wav", buf.getvalue(), "audio/wav")},
    )
    assert up.status_code == 201
    missing = api.patch("/api/projects/kids/recordings/drop", json={})
    assert missing.status_code == 400
    patched = api.patch(
        "/api/projects/kids/recordings/drop",
        json={"title": "Имя записи", "comment": "комментарий"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["title"] == "Имя записи"
    assert body["comment"] == "комментарий"
    assert body["file_name"] == "drop.wav"
    assert body["id"] == "drop"
    listed = api.get("/api/projects/kids")
    item = listed.json()["items"][0]
    assert item["title"] == "Имя записи"
    assert item["comment"] == "комментарий"
    too_long = api.patch("/api/projects/kids/recordings/drop", json={"comment": "я" * 2001})
    assert too_long.status_code == 400
    cleared = api.patch("/api/projects/kids/recordings/drop", json={"title": "  ", "comment": ""})
    assert cleared.status_code == 200
    assert cleared.json()["title"] == ""
    assert cleared.json()["name"] == "drop"
    assert cleared.json()["comment"] == ""


def test_put_markers(client):
    import wave
    from io import BytesIO

    api, _root = client
    api.post("/api/projects", json={"name": "Kids"})
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 400)
    up = api.post(
        "/api/projects/kids/recordings/upload",
        files={"file": ("drop.wav", buf.getvalue(), "audio/wav")},
    )
    assert up.status_code == 201
    missing = api.put("/api/projects/kids/recordings/drop/markers", json={})
    assert missing.status_code == 400
    bad = api.put("/api/projects/kids/recordings/drop/markers", json={"markers": [{"t": -5}]})
    assert bad.status_code == 400
    put = api.put(
        "/api/projects/kids/recordings/drop/markers",
        json={"markers": [{"t": 0.5, "name": "старт"}]},
    )
    assert put.status_code == 200
    assert put.json()["markers"] == [{"t": 0.5, "name": "старт"}]
    got = api.get("/api/projects/kids/recordings/drop")
    assert got.status_code == 200
    assert got.json()["markers"] == [{"t": 0.5, "name": "старт"}]
    assert got.json()["title"] == ""
    listed = api.get("/api/projects/kids")
    assert "markers" not in listed.json()["items"][0]
    cleared = api.put("/api/projects/kids/recordings/drop/markers", json={"markers": []})
    assert cleared.status_code == 200
    assert cleared.json()["markers"] == []
    again = api.get("/api/projects/kids/recordings/drop")
    assert again.json()["markers"] == []


def test_create_blank_name_400(client):
    api, _ = client
    assert api.post("/api/projects", json={"name": "  "}).status_code == 400


def test_inbox_archive_403(client):
    api, _ = client
    api.get("/api/projects")
    r = api.post("/api/projects/inbox/archive")
    assert r.status_code == 403
    assert r.json()["detail"] == "Inbox cannot be archived or deleted"


def test_import_move_archive_delete_roundtrip(client):
    api, root = client
    api.post("/api/projects", json={"name": "Kids"})
    src = root / "incoming" / "2026.08.18.163513"
    src.mkdir(parents=True)
    (src / "speech_map.json").write_text("{}", encoding="utf-8")
    r = api.post("/api/projects/kids/recordings", json={"path": str(src)})
    assert r.status_code == 201
    m = api.post("/api/projects/kids/recordings/2026.08.18.163513/move", json={"to": "inbox"})
    assert m.status_code == 200
    assert m.json()["project_id"] == "inbox"
    rec = api.get("/api/projects/inbox/recordings/2026.08.18.163513")
    assert rec.status_code == 200
    assert rec.json()["project_id"] == "inbox"
    assert api.get("/api/projects/old-stem").status_code == 404


def test_upload_wav_507_when_disk_full(client, monkeypatch):
    import shutil
    import wave
    from collections import namedtuple
    from io import BytesIO

    api, _root = client
    api.post("/api/projects", json={"name": "Kids"})
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 800)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("backend.app.projects.shutil.disk_usage", lambda _p: Usage(1000, 999, 10))
    r = api.post(
        "/api/projects/kids/recordings/upload",
        files={"file": ("huge.wav", buf.getvalue(), "audio/wav")},
    )
    assert r.status_code == 507
    assert "detail" in r.json()


def test_upload_wav_creates_pending(client):
    import wave
    from io import BytesIO

    api, root = client
    api.post("/api/projects", json={"name": "Kids"})
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 400)
    r = api.post(
        "/api/projects/kids/recordings/upload",
        files={"file": ("drop.wav", buf.getvalue(), "audio/wav")},
    )
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    assert (root / "kids" / "drop" / "original.wav").is_file()
    gone = api.post("/api/projects/kids/recordings/drop/remove")
    assert gone.status_code == 200
    assert not (root / "kids" / "drop").exists()


def test_import_wav_and_process(client, monkeypatch):
    import json
    import wave

    from backend.app import jobs

    api, root = client
    api.post("/api/projects", json={"name": "Kids"})
    wav = root / "incoming" / "2026.08.18.163513.WAV"
    wav.parent.mkdir(parents=True)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 800)

    def fake_process(src, out_dir, pipeline, log):
        log("ok")
        (out_dir / "speech_map.json").write_text(
            json.dumps({"source": str(src), "duration_sec": 0.05, "speech_sec": 0, "spans": []}),
            encoding="utf-8",
        )
        (out_dir / "transcript.txt").write_text("=== С таймкодами исходника ===\n", encoding="utf-8")

    jobs.set_processor(fake_process)
    try:
        r = api.post("/api/projects/kids/recordings", json={"path": str(wav)})
        assert r.status_code == 201
        assert r.json()["status"] == "pending"
        p = api.post("/api/projects/kids/recordings/2026.08.18.163513/process")
        assert p.status_code == 200
        jobs.wait_idle(timeout=5)
        job = api.get("/api/projects/kids/recordings/2026.08.18.163513/job")
        assert job.status_code == 200
        assert job.json()["status"] == "completed"
        rec = api.get("/api/projects/kids/recordings/2026.08.18.163513")
        assert rec.json()["status"] == "completed"
        url = "/api/projects/kids/recordings/2026.08.18.163513/original"
        ranged = api.get(url, headers={"Range": "bytes=0-11"})
        assert ranged.status_code == 206
        assert ranged.content[:4] == b"RIFF"
        head = api.head(url)
        assert head.status_code in (200, 206)
        assert head.headers.get("accept-ranges") == "bytes"
        assert int(head.headers.get("content-length") or 0) > 0
    finally:
        jobs.set_processor(None)


def test_cuts_http_export_download_remove(client):
    import wave

    from backend.app.process import ffmpeg_bin

    try:
        ffmpeg_bin()
    except FileNotFoundError:
        pytest.skip("ffmpeg missing")

    api, root = client
    api.post("/api/projects", json={"name": "Kids"})
    wav = root / "incoming" / "2026.08.18.163513.WAV"
    wav.parent.mkdir(parents=True)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)
    assert api.post("/api/projects/kids/recordings", json={"path": str(wav)}).status_code == 201
    short = api.post(
        "/api/projects/kids/cuts",
        json={"recording_id": "2026.08.18.163513", "start": 0.01, "end": 0.05},
    )
    assert short.status_code == 400
    r = api.post(
        "/api/projects/kids/cuts",
        json={
            "recording_id": "2026.08.18.163513",
            "start": 0.05,
            "end": 0.4,
            "name": "loop-a",
            "fx": {"on": True, "eq": [0] * 16, "thresh": -20, "ratio": 3, "makeup": 2},
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    listed = api.get("/api/projects/kids/cuts")
    assert listed.status_code == 200
    assert listed.json()["cuts"][0]["id"] == cid
    g = api.get("/api/projects/kids")
    assert g.json()["cuts_count"] == 1
    dl = api.get(f"/api/projects/kids/cuts/{cid}/download")
    assert dl.status_code == 200
    assert "audio/mpeg" in (dl.headers.get("content-type") or "")
    assert len(dl.content) > 200
    gone = api.post(f"/api/projects/kids/cuts/{cid}/remove")
    assert gone.status_code == 200
    assert api.get("/api/projects/kids").json()["cuts_count"] == 0
