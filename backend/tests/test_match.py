import json
import math
import struct
import wave
from pathlib import Path

from backend.app.projects import create_project, import_recording
from backend.app.speakers import create_speaker, add_sample_path


def _sine_wav(path: Path, hz: float, seconds: float = 0.6, sr: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = b"".join(
            struct.pack("<h", int(16000 * math.sin(2 * math.pi * hz * i / sr))) for i in range(n)
        )
        w.writeframes(frames)
    return path


def test_classify_scores_high_low_unmatched():
    from backend.app.match import classify_scores

    auto = classify_scores({"mama": 0.91, "papa": 0.50})
    assert auto["status"] == "auto"
    assert auto["speaker_id"] == "mama"
    low = classify_scores({"mama": 0.58, "papa": 0.54})
    assert low["status"] == "low"
    assert [c["id"] for c in low["candidates"]][:2] == ["mama", "papa"]
    none = classify_scores({"mama": 0.2, "papa": 0.1})
    assert none["status"] == "unmatched"
    assert none["speaker_id"] is None


def test_match_recording_assigns_high_conf(tmp_path):
    from backend.app.match import match_recording, read_map

    create_project(tmp_path, "Kids")
    mama = create_speaker(tmp_path, "Mama")
    papa = create_speaker(tmp_path, "Papa")
    add_sample_path(tmp_path, mama["id"], _sine_wav(tmp_path / "mama.wav", 180))
    add_sample_path(tmp_path, papa["id"], _sine_wav(tmp_path / "papa.wav", 900))

    rec = tmp_path / "incoming" / "2026.08.18.163513"
    rec.mkdir(parents=True)
    (rec / "speech_map.json").write_text(
        json.dumps({"duration_sec": 2, "speech_sec": 1.2, "spans": [{"start": 0.0, "end": 0.6}, {"start": 0.7, "end": 1.3}]}),
        encoding="utf-8",
    )
    clips = rec / "clips"
    clips.mkdir()
    _sine_wav(clips / "0001_00000-00000.wav", 180)
    _sine_wav(clips / "0002_00000-00001.wav", 900)
    (rec / "transcript.txt").write_text(
        "=== С таймкодами исходника ===\n[00:00:00.00–00:00:00.60] hello mama\n[00:00:00.70–00:00:01.30] hello papa\n",
        encoding="utf-8",
    )
    import_recording(tmp_path, "kids", rec)

    out = match_recording(tmp_path, "kids", "2026.08.18.163513")
    assert out["assigned"] >= 1
    mapping = read_map(tmp_path / "kids" / "2026.08.18.163513")
    line1 = mapping["lines"]["1"]
    line2 = mapping["lines"]["2"]
    assert line1["speaker_id"] == mama["id"]
    assert line2["speaker_id"] == papa["id"]
    assert line1["status"] in {"auto", "low"}
    assert line2["status"] in {"auto", "low"}


def test_classify_respects_custom_thresholds():
    from backend.app.match import classify_scores

    mid = classify_scores({"mama": 0.58, "papa": 0.40}, high=0.50, margin=0.05, low=0.30)
    assert mid["status"] == "auto"
    assert mid["speaker_id"] == "mama"
    tight = classify_scores({"mama": 0.58, "papa": 0.40}, high=0.80, margin=0.05, low=0.55)
    assert tight["status"] == "low"


def test_apply_thresholds_preview_counts(tmp_path):
    from backend.app.match import match_recording, set_thresholds, read_map

    create_project(tmp_path, "Kids")
    mama = create_speaker(tmp_path, "Mama")
    papa = create_speaker(tmp_path, "Papa")
    add_sample_path(tmp_path, mama["id"], _sine_wav(tmp_path / "mama.wav", 180))
    add_sample_path(tmp_path, papa["id"], _sine_wav(tmp_path / "papa.wav", 900))
    rec = tmp_path / "incoming" / "2026.08.18.163513"
    rec.mkdir(parents=True)
    (rec / "speech_map.json").write_text(
        json.dumps({"duration_sec": 2, "speech_sec": 1.2, "spans": [{"start": 0.0, "end": 0.6}, {"start": 0.7, "end": 1.3}]}),
        encoding="utf-8",
    )
    clips = rec / "clips"
    clips.mkdir()
    _sine_wav(clips / "0001_00000-00000.wav", 180)
    _sine_wav(clips / "0002_00000-00001.wav", 900)
    (rec / "transcript.txt").write_text(
        "=== С таймкодами исходника ===\n[00:00:00.00–00:00:00.60] hello mama\n[00:00:00.70–00:00:01.30] hello papa\n",
        encoding="utf-8",
    )
    import_recording(tmp_path, "kids", rec)
    match_recording(tmp_path, "kids", "2026.08.18.163513")
    loose = set_thresholds(tmp_path, "kids", "2026.08.18.163513", high=0.2, margin=0.01, low=0.1)
    assert loose["auto"] + loose["manual"] == 2
    by = {s["id"]: s["total"] for s in loose["by_speaker"]}
    assert by[mama["id"]] >= 1
    assert by[papa["id"]] >= 1
    tight = set_thresholds(tmp_path, "kids", "2026.08.18.163513", high=0.95, margin=0.25, low=0.90)
    assert tight["thresh"]["high"] == 0.95
    mapping = read_map(tmp_path / "kids" / "2026.08.18.163513")
    assert mapping["thresh"]["high"] == 0.95
    assert mapping["thresh"]["low"] == 0.90


def test_harvest_manual_lines_into_library(tmp_path):
    from backend.app.match import assign_speakers, harvest_samples, match_recording
    from backend.app.speakers import get_speaker

    create_project(tmp_path, "Kids")
    mama = create_speaker(tmp_path, "Mama")
    add_sample_path(tmp_path, mama["id"], _sine_wav(tmp_path / "seed.wav", 180))
    rec = tmp_path / "incoming" / "2026.08.18.163513"
    rec.mkdir(parents=True)
    (rec / "speech_map.json").write_text(
        json.dumps({"duration_sec": 1, "speech_sec": 0.6, "spans": [{"start": 0.0, "end": 0.6}]}),
        encoding="utf-8",
    )
    clips = rec / "clips"
    clips.mkdir()
    _sine_wav(clips / "0001_00000-00000.wav", 180)
    (rec / "transcript.txt").write_text(
        "=== С таймкодами исходника ===\n[00:00:00.00–00:00:00.60] hi\n",
        encoding="utf-8",
    )
    import_recording(tmp_path, "kids", rec)
    match_recording(tmp_path, "kids", "2026.08.18.163513")
    assign_speakers(tmp_path, "kids", "2026.08.18.163513", n=1, speaker_id=mama["id"])
    before = len(get_speaker(tmp_path, mama["id"])["samples"])
    out = harvest_samples(tmp_path, "kids", "2026.08.18.163513", ns=[1])
    assert out["added"] == 1
    assert len(get_speaker(tmp_path, mama["id"])["samples"]) == before + 1


def test_assign_line_overrides(tmp_path):
    from backend.app.match import assign_speakers, match_recording, read_map

    create_project(tmp_path, "Kids")
    mama = create_speaker(tmp_path, "Mama")
    papa = create_speaker(tmp_path, "Papa")
    add_sample_path(tmp_path, mama["id"], _sine_wav(tmp_path / "mama.wav", 180))
    add_sample_path(tmp_path, papa["id"], _sine_wav(tmp_path / "papa.wav", 900))
    rec = tmp_path / "incoming" / "2026.08.18.163513"
    rec.mkdir(parents=True)
    (rec / "speech_map.json").write_text(
        json.dumps({"duration_sec": 1, "speech_sec": 0.6, "spans": [{"start": 0.0, "end": 0.6}]}),
        encoding="utf-8",
    )
    clips = rec / "clips"
    clips.mkdir()
    _sine_wav(clips / "0001_00000-00000.wav", 180)
    (rec / "transcript.txt").write_text(
        "=== С таймкодами исходника ===\n[00:00:00.00–00:00:00.60] hi\n",
        encoding="utf-8",
    )
    import_recording(tmp_path, "kids", rec)
    match_recording(tmp_path, "kids", "2026.08.18.163513")
    assign_speakers(tmp_path, "kids", "2026.08.18.163513", n=1, speaker_id=papa["id"])
    row = read_map(tmp_path / "kids" / "2026.08.18.163513")["lines"]["1"]
    assert row["speaker_id"] == papa["id"]
    assert row["status"] == "manual"
