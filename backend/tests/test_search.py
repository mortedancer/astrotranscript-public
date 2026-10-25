import json

import pytest
from fastapi.testclient import TestClient

from backend.app import catalog, projects
from backend.app.main import app


def _timed_tx(lines: list[tuple[str, str, str]]) -> str:
    body = ["=== С таймкодами исходника ==="]
    for start, end, text in lines:
        body.append(f"[{start}–{end}] {text}")
    body.append("=== Сплошной текст ===")
    return "\n".join(body) + "\n"


def _put_recording(root, pid: str, stem: str, lines: list[tuple[str, str, str]]) -> None:
    d = root / pid / stem
    d.mkdir(parents=True, exist_ok=True)
    (d / "speech_map.json").write_text(
        json.dumps({"duration_sec": 120.0, "speech_sec": 8.0, "spans": []}),
        encoding="utf-8",
    )
    (d / "transcript.txt").write_text(_timed_tx(lines), encoding="utf-8")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRO_DATA_ROOT", str(tmp_path))
    projects.create_project(tmp_path, "Kids")
    _put_recording(
        tmp_path,
        "kids",
        "2026.08.18.163513",
        [
            ("00:00:00.00", "00:00:01.20", "Ну, не холодно."),
            ("00:00:02.00", "00:00:04.00", "Вы что играли в мультфильм?"),
            ("00:41:17.76", "00:41:19.58", "Ну, вкусная мороженая."),
        ],
    )
    _put_recording(
        tmp_path,
        "kids",
        "2026.08.19.090000",
        [
            ("00:00:01.00", "00:00:02.50", "Мороженое потом купим."),
            ("00:00:03.00", "00:00:04.00", "Идём домой."),
        ],
    )
    return tmp_path


def test_search_finds_casefold_cyrillic_across_recordings(root):
    data = catalog.search_transcripts("kids", "морож", root)
    assert data["query"] == "морож"
    assert data["total"] == 2
    stems = [r["id"] for r in data["recordings"]]
    assert stems == ["2026.08.18.163513", "2026.08.19.090000"]
    first = data["recordings"][0]["lines"][0]
    assert first["n"] == 3
    assert "мороженая" in first["text"]
    assert first["start_rel"] == pytest.approx(41 * 60 + 17.76)
    assert first["clock"].startswith("17:16:30")


def test_search_empty_query_returns_no_hits(root):
    data = catalog.search_transcripts("kids", "   ", root)
    assert data["total"] == 0
    assert data["recordings"] == []


def test_search_unknown_project_raises(root):
    with pytest.raises(projects.NotFound):
        catalog.search_transcripts("nope", "холод", root)


def test_search_respects_per_recording_limit(root):
    extra = [(f"00:00:{i:02d}.00", f"00:00:{i:02d}.50", f"слово {i}") for i in range(12)]
    _put_recording(root, "kids", "2026.08.20.120000", extra)
    data = catalog.search_transcripts("kids", "слово", root, per_recording=3, limit=10)
    rec = next(r for r in data["recordings"] if r["id"] == "2026.08.20.120000")
    assert rec["hits"] == 12
    assert len(rec["lines"]) == 3
    assert data["truncated"] is True


def test_search_route_returns_hits(root):
    api = TestClient(app)
    r = api.get("/api/projects/kids/search", params={"q": "мультфильм"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["recordings"][0]["id"] == "2026.08.18.163513"
    assert body["recordings"][0]["lines"][0]["n"] == 2


def test_search_route_missing_project_404(root):
    api = TestClient(app)
    r = api.get("/api/projects/ghost/search", params={"q": "x"})
    assert r.status_code == 404


def test_match_time_span_proportional_first_hit():
    a, b = catalog.match_time_span("aaaaXXXXaaaa", "XXXX", 10.0, 22.0, pad=0.0, min_dur=0.0)
    assert a == pytest.approx(14.0)
    assert b == pytest.approx(18.0)


def test_match_time_span_casefold_cyrillic():
    text = "Ну, Блядь, да."
    a, b = catalog.match_time_span(text, "блядь", 0.0, float(len(text)), pad=0.0, min_dur=0.0)
    at = text.casefold().find("блядь")
    assert a == pytest.approx(float(at))
    assert b == pytest.approx(float(at + len("блядь")))


def test_match_time_span_pads_and_enforces_min_duration():
    a, b = catalog.match_time_span("abXcd", "X", 0.0, 1.0, pad=0.0, min_dur=0.5)
    assert a == pytest.approx(0.25)
    assert b == pytest.approx(0.75)
    a2, b2 = catalog.match_time_span("word", "word", 1.0, 1.2, pad=0.12, min_dur=0.28)
    assert a2 == pytest.approx(0.88)
    assert b2 == pytest.approx(1.32)


def test_match_time_span_missing_or_blank_raises():
    with pytest.raises(ValueError):
        catalog.match_time_span("hello", "xyz", 0.0, 1.0)
    with pytest.raises(ValueError):
        catalog.match_time_span("hello", "   ", 0.0, 1.0)


def test_recording_line_by_n(root):
    row = catalog.recording_line("kids", "2026.08.18.163513", 2, root)
    assert row["n"] == 2
    assert "мультфильм" in row["text"]
    assert row["end_rel"] > row["start_rel"]


def test_recording_line_missing(root):
    with pytest.raises(projects.NotFound):
        catalog.recording_line("kids", "2026.08.18.163513", 99, root)


def test_search_hit_includes_match_span(root):
    data = catalog.search_transcripts("kids", "морож", root)
    line = data["recordings"][0]["lines"][0]
    assert line["match_end_rel"] > line["match_start_rel"]
    line_dur = line["end_rel"] - line["start_rel"]
    match_dur = line["match_end_rel"] - line["match_start_rel"]
    assert match_dur <= line_dur + 0.3


def test_line_export_route_404_without_audio(root):
    api = TestClient(app)
    r = api.get("/api/projects/kids/recordings/2026.08.18.163513/lines/2/export.mp3")
    assert r.status_code == 404
    detail = str(r.json().get("detail") or "").lower()
    assert "audio" in detail or "source" in detail


def test_match_export_requires_query(root):
    api = TestClient(app)
    r = api.get("/api/projects/kids/recordings/2026.08.18.163513/lines/2/match.mp3")
    assert r.status_code == 400


def test_match_export_query_not_in_line(root):
    api = TestClient(app)
    r = api.get(
        "/api/projects/kids/recordings/2026.08.18.163513/lines/2/match.mp3",
        params={"q": "zzzzz"},
    )
    assert r.status_code == 400


def _silence_wav(path, seconds=2.0, sr=16000):
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(seconds * sr))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
    return path


def test_line_and_match_export_mp3(root):
    from backend.app.process import ffmpeg_bin

    try:
        ffmpeg_bin()
    except FileNotFoundError:
        pytest.skip("ffmpeg missing")

    stem = "2026.08.21.120000"
    d = root / "kids" / stem
    _put_recording(
        root,
        "kids",
        stem,
        [("00:00:00.20", "00:00:01.80", "Ну, вкусная мороженая, да.")],
    )
    _silence_wav(d / "original.wav", seconds=2.0)
    api = TestClient(app)
    line = api.get(f"/api/projects/kids/recordings/{stem}/lines/1/export.mp3")
    assert line.status_code == 200, line.text
    assert line.headers["content-type"].startswith("audio/mpeg")
    assert "export" in (line.headers.get("content-disposition") or "").lower() or "n1" in (
        line.headers.get("content-disposition") or ""
    )
    assert len(line.content) > 200

    match = api.get(
        f"/api/projects/kids/recordings/{stem}/lines/1/match.mp3",
        params={"q": "мороженая"},
    )
    assert match.status_code == 200, match.text
    assert match.headers["content-type"].startswith("audio/mpeg")
    assert len(match.content) > 100
    assert len(match.content) <= len(line.content)
