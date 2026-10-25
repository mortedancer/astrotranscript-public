import json

import pytest
from fastapi.testclient import TestClient

from backend.app import catalog, jobs, projects
from backend.app.main import app
from backend.app.projects import BadRequest


def _timed_tx(lines: list[tuple[str, str, str]]) -> str:
    body = ["=== С таймкодами исходника ==="]
    for start, end, text in lines:
        body.append(f"[{start}–{end}] {text}")
    body.append("=== Сплошной текст ===")
    return "\n".join(body) + "\n"


def _put_recording(root, pid: str, stem: str, lines: list[tuple[str, str, str]], duration: float = 120.0) -> None:
    d = root / pid / stem
    d.mkdir(parents=True, exist_ok=True)
    (d / "speech_map.json").write_text(
        json.dumps({"duration_sec": duration, "speech_sec": 8.0, "spans": [{"start": 0.0, "end": 4.0}]}),
        encoding="utf-8",
    )
    (d / "transcript.txt").write_text(_timed_tx(lines), encoding="utf-8")
    (d / "recording.json").write_text(
        json.dumps({"id": stem, "source": "", "status": "completed", "error": None, "stage": "done", "log": []}),
        encoding="utf-8",
    )


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
            ("00:41:17.76", "00:41:19.58", "Ну, вкусная мороженая."),
        ],
        duration=3600,
    )
    return tmp_path


def test_anchor_shifts_every_absolute_stamp(root):
    proj = catalog.set_clock_anchor(
        root,
        "kids",
        "2026.08.18.163513",
        "00:41:17.76",
        "2026-08-18T18:00:00",
    )
    assert proj["clock_source"] == "anchor"
    assert proj["clock_anchor"]["at"] == "2026-08-18T18:00:00.000"
    assert proj["clock_anchor"]["rel_label"] == "00:41:17.760"
    by_n = {s["n"]: s for s in proj["segments_detail"]}
    assert by_n[2]["start_label"] == "2026-08-18 18:00:00.000"
    assert by_n[1]["start_label"] == "2026-08-18 17:18:42.240"
    assert proj["recording_start_label"] == "2026-08-18 17:18:42"
    assert proj["filename_start_label"] == "2026-08-18 16:35:13"
    assert proj["clock_shift_label"] == "+43:29"
    span = proj["spans_detail"][0]
    assert span["start_label"] == "2026-08-18 17:18:42.240"

    hits = catalog.search_transcripts("kids", "морож", root)
    assert hits["recordings"][0]["lines"][0]["start_label"] == "2026-08-18 18:00:00.000"
    srt = catalog.export_absolute_srt("2026.08.18.163513", "kids", root)
    assert "2026-08-18 18:00:00.000" in srt


def test_clear_anchor_restores_filename_clock(root):
    catalog.set_clock_anchor(root, "kids", "2026.08.18.163513", 0, "2026-08-18T12:00:00")
    proj = catalog.clear_clock_anchor(root, "kids", "2026.08.18.163513")
    assert proj["clock_source"] == "filename"
    assert proj["clock_anchor"] is None
    assert proj["segments_detail"][0]["start_label"] == "2026-08-18 16:35:13.000"
    meta = json.loads((root / "kids" / "2026.08.18.163513" / "recording.json").read_text(encoding="utf-8"))
    assert "clock_anchor" not in meta


def test_anchor_survives_job_progress_rewrite(root):
    catalog.set_clock_anchor(root, "kids", "2026.08.18.163513", 10, "2026-08-18T17:00:00")
    folder = root / "kids" / "2026.08.18.163513"
    jobs.set_progress(folder, "asr", done=1, total=4)
    meta = jobs.read_meta(folder)
    assert meta["clock_anchor"]["at"] == "2026-08-18T17:00:00.000"
    assert meta["stage"] == "asr"
    proj = catalog.load_project("2026.08.18.163513", "kids", root)
    assert proj["clock_source"] == "anchor"


def test_anchor_works_when_filename_has_no_stamp(root):
    _put_recording(
        root,
        "kids",
        "interview",
        [("00:00:02.00", "00:00:03.00", "проверка")],
        duration=30,
    )
    proj = catalog.set_clock_anchor(root, "kids", "interview", 2, "2024-01-02T03:04:05")
    assert proj["clock_source"] == "anchor"
    assert proj["filename_start"] is None
    assert proj["segments_detail"][0]["start_label"] == "2024-01-02 03:04:05.000"
    assert proj["recording_start_label"] == "2024-01-02 03:04:03"


def test_anchor_rejects_point_past_the_end(root):
    with pytest.raises(BadRequest):
        catalog.set_clock_anchor(root, "kids", "2026.08.18.163513", 4000, "2026-08-18T18:00:00")


def test_apply_clock_shift_copies_offset_onto_another_file(root):
    src = catalog.set_clock_anchor(
        root,
        "kids",
        "2026.08.18.163513",
        "00:41:17.76",
        "2026-08-18T18:00:00",
    )
    shift = src["clock_shift_sec"]
    assert abs(shift - 2609.24) < 0.001
    _put_recording(
        root,
        "kids",
        "2026.08.19.100000",
        [("00:00:00.00", "00:00:01.00", "другой файл")],
        duration=60,
    )
    dst = catalog.apply_clock_shift(root, "kids", "2026.08.19.100000", shift)
    assert dst["clock_source"] == "anchor"
    assert abs(dst["clock_shift_sec"] - shift) < 0.001
    assert dst["recording_start_label"] == "2026-08-19 10:43:29"
    assert dst["filename_start_label"] == "2026-08-19 10:00:00"
    assert dst["segments_detail"][0]["start_label"] == "2026-08-19 10:43:29.240"
    cleared = catalog.apply_clock_shift(root, "kids", "2026.08.19.100000", 0)
    assert cleared["clock_source"] == "filename"
    assert cleared["recording_start_label"] == "2026-08-19 10:00:00"


def test_apply_clock_shift_needs_filename_stamp(root):
    _put_recording(root, "kids", "interview", [("00:00:01.00", "00:00:02.00", "x")], duration=10)
    with pytest.raises(BadRequest):
        catalog.apply_clock_shift(root, "kids", "interview", 90)


def test_clock_routes(root):
    api = TestClient(app)
    bad = api.put(
        "/api/projects/kids/recordings/2026.08.18.163513/clock",
        json={"rel_sec": 0, "at": "noon"},
    )
    assert bad.status_code == 400
    missing = api.put(
        "/api/projects/kids/recordings/missing/clock",
        json={"rel_sec": 0, "at": "2026-08-18T18:00:00"},
    )
    assert missing.status_code == 404
    ok = api.put(
        "/api/projects/kids/recordings/2026.08.18.163513/clock",
        json={"rel_sec": "00:00:10", "at": "2026-08-18T16:40:00"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["clock_source"] == "anchor"
    assert body["segments_detail"][0]["start_label"] == "2026-08-18 16:39:50.000"
    cleared = api.delete("/api/projects/kids/recordings/2026.08.18.163513/clock")
    assert cleared.status_code == 200
    assert cleared.json()["clock_source"] == "filename"
    shifted = api.put(
        "/api/projects/kids/recordings/2026.08.18.163513/clock-shift",
        json={"shift_sec": 125.5},
    )
    assert shifted.status_code == 200
    assert shifted.json()["recording_start_label"] == "2026-08-18 16:37:18"
    assert abs(shifted.json()["clock_shift_sec"] - 125.5) < 0.001
    bare = api.put("/api/projects/kids/recordings/2026.08.18.163513/clock-shift", json={})
    assert bare.status_code == 400
