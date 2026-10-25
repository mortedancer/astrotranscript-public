import json
from pathlib import Path

from backend.app import catalog
from backend.app.projects import (
    slugify,
    unique_slug,
    validate_name,
    BadRequest,
    ensure_layout,
    INBOX_ID,
    ARCHIVE_NAME,
    create_project,
    get_project,
    list_projects,
    rename_project,
    NotFound,
    archive_project,
    delete_project,
    import_recording,
    move_recording,
    restore_project,
    Conflict,
    Forbidden,
)


def test_slugify_spaces_and_punctuation():
    assert slugify("Kids Aug 2026") == "kids-aug-2026"


def test_slugify_cyrillic_lowercases():
    assert slugify("Дети") == "дети"


def test_slugify_empty_or_symbols_becomes_project():
    assert slugify("@@@") == "project"
    assert slugify("   ") == "project"


def test_slugify_strips_and_collapses():
    assert slugify("  Hello___World  ") == "hello-world"


def test_slugify_max_60_before_suffix():
    long = "a" * 80
    assert len(slugify(long)) == 60


def test_validate_name_rejects_blank_and_long():
    try:
        validate_name("   ")
        raise AssertionError("expected BadRequest")
    except BadRequest as e:
        assert e.status == 400
    try:
        validate_name("x" * 81)
        raise AssertionError("expected BadRequest")
    except BadRequest as e:
        assert e.status == 400


def test_validate_name_trims():
    assert validate_name("  Kids  ") == "Kids"


def test_unique_slug_reserved_inbox(tmp_path):
    assert unique_slug(tmp_path, "Inbox") == "inbox-2"


def test_unique_slug_collision_suffix(tmp_path):
    (tmp_path / "kids").mkdir()
    assert unique_slug(tmp_path, "Kids") == "kids-2"
    (tmp_path / ".archive" / "kids-2").mkdir(parents=True)
    assert unique_slug(tmp_path, "Kids") == "kids-3"


def _write_map(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "speech_map.json").write_text("{}", encoding="utf-8")


def test_ensure_layout_creates_inbox_and_archive(tmp_path):
    ensure_layout(tmp_path)
    meta = json.loads((tmp_path / INBOX_ID / "project.json").read_text(encoding="utf-8"))
    assert meta["id"] == "inbox"
    assert meta["name"] == "Inbox"
    assert meta["archived"] is False
    assert "created_at" in meta
    assert (tmp_path / ARCHIVE_NAME).is_dir()


def test_ensure_layout_moves_root_recording_into_inbox(tmp_path):
    _write_map(tmp_path / "2026.08.18.163513")
    ensure_layout(tmp_path)
    dest = tmp_path / INBOX_ID / "2026.08.18.163513"
    assert dest.is_dir()
    assert (dest / "speech_map.json").exists()
    assert not (tmp_path / "2026.08.18.163513").exists()


def test_ensure_layout_is_idempotent(tmp_path):
    _write_map(tmp_path / "2026.08.18.163513")
    ensure_layout(tmp_path)
    ensure_layout(tmp_path)
    assert (tmp_path / INBOX_ID / "2026.08.18.163513" / "speech_map.json").exists()


def test_ensure_layout_skips_real_projects_and_other_dirs(tmp_path):
    other = tmp_path / "notes"
    other.mkdir()
    (other / "readme.txt").write_text("x", encoding="utf-8")
    ensure_layout(tmp_path)
    proj = tmp_path / "kids"
    proj.mkdir()
    (proj / "project.json").write_text(
        json.dumps({"id": "kids", "name": "Kids", "created_at": "2026-01-01T00:00:00+00:00", "archived": False}),
        encoding="utf-8",
    )
    ensure_layout(tmp_path)
    assert other.exists()
    assert (proj / "project.json").exists()
    assert not (tmp_path / INBOX_ID / "notes").exists()
    assert not (tmp_path / INBOX_ID / "kids").exists()


def test_ensure_layout_does_not_overwrite_inbox_clash(tmp_path):
    _write_map(tmp_path / INBOX_ID / "dup")
    (tmp_path / INBOX_ID / "project.json").write_text(
        json.dumps({"id": "inbox", "name": "Inbox", "created_at": "2026-01-01T00:00:00+00:00", "archived": False}),
        encoding="utf-8",
    )
    _write_map(tmp_path / "dup")
    ensure_layout(tmp_path)
    assert (tmp_path / "dup" / "speech_map.json").exists()
    assert (tmp_path / INBOX_ID / "dup" / "speech_map.json").exists()


def test_create_project_writes_folder_and_meta(tmp_path):
    p = create_project(tmp_path, "Kids Aug 2026")
    assert p["id"] == "kids-aug-2026"
    assert p["name"] == "Kids Aug 2026"
    assert p["archived"] is False
    assert p["protected"] is False
    assert p["recordings"] == 0
    assert (tmp_path / "kids-aug-2026" / "project.json").exists()


def test_create_project_second_same_name_gets_suffix(tmp_path):
    create_project(tmp_path, "Kids")
    p2 = create_project(tmp_path, "Kids")
    assert p2["id"] == "kids-2"


def test_list_projects_inbox_first_then_newest(tmp_path):
    create_project(tmp_path, "Alpha")
    create_project(tmp_path, "Beta")
    ids = [p["id"] for p in list_projects(tmp_path)]
    assert ids[0] == "inbox"
    assert set(ids) == {"inbox", "alpha", "beta"}


def test_rename_changes_name_not_folder(tmp_path):
    create_project(tmp_path, "Kids")
    p = rename_project(tmp_path, "kids", "Дети")
    assert p["name"] == "Дети"
    assert p["id"] == "kids"
    assert (tmp_path / "kids" / "project.json").exists()


def test_get_project_404(tmp_path):
    ensure_layout(tmp_path)
    try:
        get_project(tmp_path, "nope")
        raise AssertionError("expected NotFound")
    except NotFound as e:
        assert e.status == 404


def test_import_recording_moves_folder(tmp_path):
    create_project(tmp_path, "Kids")
    src = tmp_path / "incoming" / "2026.08.18.163513"
    _write_map(src)
    rec = import_recording(tmp_path, "kids", src)
    assert rec["id"] == "2026.08.18.163513"
    assert (tmp_path / "kids" / "2026.08.18.163513" / "speech_map.json").exists()
    assert not src.exists()
    detail = get_project(tmp_path, "kids")
    assert detail["recordings"] == 1


def test_import_duplicate_stem_conflicts(tmp_path):
    create_project(tmp_path, "Kids")
    create_project(tmp_path, "Other")
    _write_map(tmp_path / "kids" / "2026.08.18.163513")
    src = tmp_path / "incoming" / "2026.08.18.163513"
    _write_map(src)
    try:
        import_recording(tmp_path, "other", src)
        raise AssertionError("expected Conflict")
    except Conflict as e:
        assert e.status == 409
        assert "kids" in e.detail


def test_import_requires_speech_map(tmp_path):
    create_project(tmp_path, "Kids")
    src = tmp_path / "empty-dir"
    src.mkdir()
    try:
        import_recording(tmp_path, "kids", src)
        raise AssertionError("expected BadRequest")
    except BadRequest:
        pass


def test_move_recording_between_projects(tmp_path):
    create_project(tmp_path, "Kids")
    src = tmp_path / "incoming" / "2026.08.18.163513"
    _write_map(src)
    import_recording(tmp_path, "inbox", src)
    move_recording(tmp_path, "inbox", "2026.08.18.163513", "kids")
    assert (tmp_path / "kids" / "2026.08.18.163513" / "speech_map.json").exists()
    assert not (tmp_path / "inbox" / "2026.08.18.163513").exists()


def test_archive_and_restore_change_parent(tmp_path):
    create_project(tmp_path, "Old")
    archive_project(tmp_path, "old")
    assert (tmp_path / ARCHIVE_NAME / "old" / "project.json").exists()
    assert not (tmp_path / "old").exists()
    assert get_project(tmp_path, "old")["archived"] is True
    restore_project(tmp_path, "old")
    assert (tmp_path / "old" / "project.json").exists()
    assert get_project(tmp_path, "old")["archived"] is False


def test_list_projects_hides_archived_by_default(tmp_path):
    create_project(tmp_path, "Old")
    archive_project(tmp_path, "old")
    ids = [p["id"] for p in list_projects(tmp_path)]
    assert "old" not in ids
    assert "old" in [p["id"] for p in list_projects(tmp_path, archived=True)]


def test_cannot_archive_or_delete_inbox(tmp_path):
    ensure_layout(tmp_path)
    try:
        archive_project(tmp_path, "inbox")
        raise AssertionError("expected Forbidden")
    except Forbidden as e:
        assert e.detail == "Inbox cannot be archived or deleted"
    try:
        delete_project(tmp_path, "inbox")
        raise AssertionError("expected Forbidden")
    except Forbidden as e:
        assert e.detail == "Inbox cannot be archived or deleted"


def test_delete_project_moves_recordings_to_inbox(tmp_path):
    create_project(tmp_path, "Kids")
    src = tmp_path / "incoming" / "2026.08.18.163513"
    _write_map(src)
    import_recording(tmp_path, "kids", src)
    delete_project(tmp_path, "kids")
    assert not (tmp_path / "kids").exists()
    assert (tmp_path / "inbox" / "2026.08.18.163513" / "speech_map.json").exists()


def test_delete_project_clash_leaves_source(tmp_path):
    create_project(tmp_path, "Kids")
    _write_map(tmp_path / "kids" / "2026.08.18.163513")
    _write_map(tmp_path / "inbox" / "2026.08.18.163513")
    try:
        delete_project(tmp_path, "kids")
        raise AssertionError("expected Conflict")
    except Conflict:
        pass
    assert (tmp_path / "kids" / "2026.08.18.163513" / "speech_map.json").exists()


def test_delete_project_clash_prechecks_all_stems(tmp_path):
    create_project(tmp_path, "Kids")
    _write_map(tmp_path / "kids" / "2026.08.18.111111")
    _write_map(tmp_path / "kids" / "2026.08.18.163513")
    _write_map(tmp_path / "inbox" / "2026.08.18.163513")
    try:
        delete_project(tmp_path, "kids")
        raise AssertionError("expected Conflict")
    except Conflict:
        pass
    assert (tmp_path / "kids" / "2026.08.18.111111" / "speech_map.json").exists()
    assert (tmp_path / "kids" / "2026.08.18.163513" / "speech_map.json").exists()


def test_delete_project_rmdir_fail_keeps_metadata(tmp_path):
    create_project(tmp_path, "Kids")
    (tmp_path / "kids" / "notes.txt").write_text("stuck", encoding="utf-8")
    try:
        delete_project(tmp_path, "kids")
        raise AssertionError("expected Conflict")
    except Conflict:
        pass
    assert (tmp_path / "kids" / "project.json").exists()
    assert any(p["id"] == "kids" for p in list_projects(tmp_path))


def test_cannot_import_into_archived(tmp_path):
    create_project(tmp_path, "Old")
    archive_project(tmp_path, "old")
    src = tmp_path / "incoming" / "2026.08.19.010000"
    _write_map(src)
    try:
        import_recording(tmp_path, "old", src)
        raise AssertionError("expected Forbidden")
    except Forbidden as e:
        assert e.detail == "Project is archived"


def test_catalog_lists_recordings_inside_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRO_DATA_ROOT", str(tmp_path))
    create_project(tmp_path, "Kids")
    src = tmp_path / "incoming" / "2026.08.18.163513"
    _write_map(src)
    (src / "speech_map.json").write_text(
        '{"duration_sec": 10, "speech_sec": 2, "spans": [], "source": ""}',
        encoding="utf-8",
    )
    import_recording(tmp_path, "kids", src)
    items = catalog.list_recordings("kids", tmp_path)
    assert len(items) == 1
    assert items[0]["id"] == "2026.08.18.163513"
    full = catalog.load_project("2026.08.18.163513", "kids", tmp_path)
    assert full["project_id"] == "kids"
    assert full["project_name"] == "Kids"
