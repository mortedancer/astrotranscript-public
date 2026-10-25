import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRO_DATA_ROOT", str(tmp_path))
    return TestClient(main.app), tmp_path


def _silence_wav(path: Path, seconds: float = 0.2, sr: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(seconds * sr))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
    return path


def test_create_list_get_speaker(tmp_path):
    from backend.app.speakers import create_speaker, list_speakers, get_speaker

    sp = create_speaker(tmp_path, "Мама")
    assert sp["id"]
    assert sp["name"] == "Мама"
    assert (tmp_path / ".speakers" / sp["id"] / "speaker.json").is_file()
    items = list_speakers(tmp_path)
    assert len(items) == 1
    assert items[0]["name"] == "Мама"
    got = get_speaker(tmp_path, sp["id"])
    assert got["samples"] == []


def test_add_sample_from_path_and_upload(tmp_path):
    from backend.app.speakers import create_speaker, add_sample_path, add_sample_bytes, get_speaker

    sp = create_speaker(tmp_path, "Papa")
    wav = _silence_wav(tmp_path / "clip.wav")
    sample = add_sample_path(tmp_path, sp["id"], wav)
    assert sample["duration_sec"] > 0
    dest = tmp_path / ".speakers" / sp["id"] / "samples" / f"{sample['id']}.wav"
    assert dest.is_file()
    data = wav.read_bytes()
    up = add_sample_bytes(tmp_path, sp["id"], "voice.WAV", data)
    got = get_speaker(tmp_path, sp["id"])
    assert len(got["samples"]) == 2
    assert up["id"] != sample["id"]


def test_rename_and_delete_speaker(tmp_path):
    from backend.app.speakers import create_speaker, rename_speaker, delete_speaker, list_speakers

    sp = create_speaker(tmp_path, "Kid")
    renamed = rename_speaker(tmp_path, sp["id"], "Ребёнок")
    assert renamed["name"] == "Ребёнок"
    assert renamed["id"] == sp["id"]
    delete_speaker(tmp_path, sp["id"])
    assert list_speakers(tmp_path) == []
    assert not (tmp_path / ".speakers" / sp["id"]).exists()


def test_speakers_http_roundtrip(client):
    api, root = client
    r = api.post("/api/speakers", json={"name": "Мама"})
    assert r.status_code == 201
    sid = r.json()["id"]
    listed = api.get("/api/speakers")
    assert listed.status_code == 200
    assert listed.json()["speakers"][0]["name"] == "Мама"
    wav = _silence_wav(root / "a.wav")
    add = api.post(f"/api/speakers/{sid}/samples", json={"path": str(wav)})
    assert add.status_code == 201
    detail = api.get(f"/api/speakers/{sid}")
    assert len(detail.json()["samples"]) == 1
    audio = api.get(f"/api/speakers/{sid}/samples/{detail.json()['samples'][0]['id']}")
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    gone = api.delete(f"/api/speakers/{sid}")
    assert gone.status_code == 200
    assert api.get(f"/api/speakers/{sid}").status_code == 404
