from datetime import datetime

import pytest

from backend.app.astro_time import (
    format_astro,
    parse_recording_start,
    parse_rel_point,
    parse_wall_datetime,
    relative_to_absolute,
    start_from_anchor,
)


def test_filename_is_wall_clock_not_sidereal():
    start = parse_recording_start("2026.08.18.163513")
    assert start == datetime(2026, 8, 18, 16, 35, 13)
    abs_t = relative_to_absolute(start, 31 * 60 + 59.08)
    assert format_astro(abs_t) == "2026-08-18 17:07:12.080"


def test_dictaphone_stem_r_yyyymmdd_hhmmss():
    start = parse_recording_start("R20250827-115520")
    assert start == datetime(2025, 8, 27, 11, 55, 20)


def test_anchor_inverts_relative_to_absolute():
    at = datetime(2026, 8, 18, 17, 0, 0)
    rel = 31 * 60 + 59.08
    start = start_from_anchor(at, rel)
    assert relative_to_absolute(start, rel) == at


def test_parse_wall_keeps_clock_face_and_fraction():
    assert parse_wall_datetime("2026-08-18T17:00:00") == datetime(2026, 8, 18, 17, 0, 0)
    assert parse_wall_datetime("2026-08-18 17:00") == datetime(2026, 8, 18, 17, 0, 0)
    assert parse_wall_datetime("2026-08-18T17:00:00.080") == datetime(2026, 8, 18, 17, 0, 0, 80000)
    assert parse_wall_datetime("2026-08-18T17:00:00+03:00") == datetime(2026, 8, 18, 17, 0, 0)


def test_parse_wall_rejects_garbage():
    with pytest.raises(ValueError):
        parse_wall_datetime("yesterday")
    with pytest.raises(ValueError):
        parse_wall_datetime("2026-02-31T10:00:00")


def test_parse_rel_point_accepts_seconds_and_hms():
    assert parse_rel_point(12.5) == 12.5
    assert parse_rel_point("00:41:17.76") == pytest.approx(41 * 60 + 17.76)
    assert parse_rel_point("1:02.5") == pytest.approx(62.5)
    with pytest.raises(ValueError):
        parse_rel_point("-2")
    with pytest.raises(ValueError):
        parse_rel_point("nope")
