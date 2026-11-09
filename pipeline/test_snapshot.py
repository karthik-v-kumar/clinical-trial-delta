import json

import pytest

from pipeline.snapshot import SnapshotCorruptError, SnapshotExistsError, SnapshotStore


def test_round_trip(tmp_path, day1):
    store = SnapshotStore(tmp_path)
    store.write(day1)
    back = store.read("2026-01-05")
    assert back.trials == day1.trials
    assert {c: s.queries for c, s in back.collections.items()} == {c: s.queries for c, s in day1.collections.items()}
    assert back.collections["glp1-obesity"].query_hash == "glp1-v1"


def test_never_overwrites(tmp_path, day1):
    store = SnapshotStore(tmp_path)
    store.write(day1)
    before = store.manifest_path("2026-01-05").read_bytes()
    with pytest.raises(SnapshotExistsError):
        store.write(day1)
    assert store.manifest_path("2026-01-05").read_bytes() == before


def test_unchanged_records_are_shared_between_days(tmp_path, day1, day2):
    store = SnapshotStore(tmp_path)
    first = store.write(day1)
    second = store.write(day2)
    assert first["records_written"] == len(day1.trials)
    # day 2 only adds files for trials that are new or whose content changed (NCT90000006 dropped out)
    unchanged = sum(1 for n, r in day2.trials.items() if day1.trials.get(n) == r)
    assert second["records_written"] == len(day2.trials) - unchanged
    assert store.read("2026-01-05").trials == day1.trials  # day 1 still fully readable


def test_dates_and_latest_before(tmp_path, day1, day2):
    store = SnapshotStore(tmp_path)
    assert store.latest_before("2026-01-05") is None
    store.write(day1)
    store.write(day2)
    assert store.dates() == ["2026-01-05", "2026-01-06"]
    assert store.latest_before("2026-01-06") == "2026-01-05"
    assert store.latest_before("2026-02-01") == "2026-01-06"


def test_output_is_byte_deterministic(tmp_path, day1):
    a, b = SnapshotStore(tmp_path / "a"), SnapshotStore(tmp_path / "b")
    a.write(day1)
    b.write(day1)
    assert a.manifest_path("2026-01-05").read_bytes() == b.manifest_path("2026-01-05").read_bytes()


def test_detects_tampered_record(tmp_path, day1):
    store = SnapshotStore(tmp_path)
    store.write(day1)
    digest = store.read_manifest("2026-01-05")["trials"]["NCT90000001"]
    path = store.record_path(digest)
    path.write_text(json.dumps({"nct_id": "NCT90000001"}))
    with pytest.raises(SnapshotCorruptError):
        store.read("2026-01-05")


def test_rejects_bad_dates(tmp_path):
    with pytest.raises(ValueError):
        SnapshotStore(tmp_path).manifest_path("2026-1-5")


def test_store_is_plain_json(tmp_path, day1):
    """data/ holds committed JSON only: no compressed or binary files in the store."""
    store = SnapshotStore(tmp_path)
    store.write(day1)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files and all(p.suffix == ".json" for p in files)
    manifest = json.loads(store.manifest_path("2026-01-05").read_text(encoding="utf-8"))
    assert manifest["snapshot_date"] == "2026-01-05"
    record = json.loads(store.record_path(manifest["trials"]["NCT90000001"]).read_text(encoding="utf-8"))
    assert record == day1.trials["NCT90000001"]
