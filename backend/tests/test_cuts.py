from pathlib import Path

import pytest

from backend.app.cuts import fx_filter_af, list_cuts, render_loop_cut
from backend.app.projects import BadRequest, create_project, import_recording
from backend.app.process import ffmpeg_bin
import wave


def _silence(path: Path, seconds: float = 0.4, sr: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(seconds * sr))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
    return path


def test_fx_filter_includes_eq_and_compressor():
    af = fx_filter_af(
        {
            "on": True,
            "eq": [0] * 7 + [3] + [0] * 8,
            "lfHz": 120,
            "lfDb": -6,
            "hfHz": 8000,
            "hfDb": -3,
            "thresh": -20,
            "ratio": 4,
            "attack": 10,
            "release": 150,
            "knee": 8,
            "makeup": 4,
        }
    )
    assert "highpass=f=120" in af
    assert "equalizer=f=1600" in af
    assert "acompressor=" in af
    assert fx_filter_af({"on": False}) == "anull"
    comp = next(p for p in af.split(",") if p.startswith("acompressor="))
    assert "dB" not in comp
    assert "threshold=0.1" in comp
    assert "makeup=1.58489" in comp or "makeup=1.5848" in comp


def test_fx_filter_deecho_is_fixed_comb_not_adaptive():
    base = {
        "on": True,
        "eq": [0] * 16,
        "lfHz": 90,
        "hfHz": 9000,
        "thresh": -22,
        "ratio": 3,
        "makeup": 0,
        "deecho": True,
        "deechoAmt": 70,
    }
    af = fx_filter_af(base)
    assert "adelay=" in af
    assert "volume=-" in af
    assert "anlms" not in af
    assert "afftdn" not in af
    off = fx_filter_af({**base, "deecho": False})
    assert "adelay=" not in off
    dry = fx_filter_af({**base, "deechoAmt": 0})
    assert "adelay=" not in dry


def test_render_loop_cut_writes_mp3(tmp_path):
    try:
        ffmpeg_bin()
    except FileNotFoundError:
        pytest.skip("ffmpeg missing")
    create_project(tmp_path, "Kids")
    wav = _silence(tmp_path / "incoming" / "2026.08.18.163513.WAV", seconds=0.5)
    import_recording(tmp_path, "kids", wav)
    cut = render_loop_cut(
        tmp_path,
        "kids",
        stem="2026.08.18.163513",
        start=0.05,
        end=0.35,
        source="original",
        fx={"on": True, "eq": [0] * 16, "lfHz": 80, "hfHz": 10000, "thresh": -18, "ratio": 2, "makeup": 1},
        name="test-cut",
    )
    assert cut["id"]
    assert cut["name"] == "test-cut"
    assert cut["fx"] is True
    mp3 = tmp_path / "kids" / "cuts" / f"{cut['id']}.mp3"
    assert mp3.is_file() and mp3.stat().st_size > 0
    listed = list_cuts(tmp_path, "kids")
    assert listed[0]["id"] == cut["id"]
    with pytest.raises(BadRequest):
        render_loop_cut(
            tmp_path,
            "kids",
            stem="2026.08.18.163513",
            start=0.1,
            end=0.15,
            source="original",
        )
