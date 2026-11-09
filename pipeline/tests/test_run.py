"""End-to-end: two daily runs against a fake registry, in a temp data dir."""

import copy
import json

import pytest

from pipeline.config import load_watchlist
from pipeline.ctgov import FetchIntegrityError, QueryResult
from pipeline.run import run


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, by_query: dict[str, list[dict]], data_timestamp="2026-01-05T10:00:00", fail_query=None):
        self.by_query = by_query
        self.data_timestamp = data_timestamp
        self.fail_query = fail_query
        self.requests_made = 0

    def version(self):
        self.requests_made += 1
        return {"apiVersion": "2.0.5", "dataTimestamp": self.data_timestamp}

    def fetch_query(self, q):
        self.requests_made += 1
        if q.id == self.fail_query:
            raise FetchIntegrityError("simulated count mismatch")
        studies = self.by_query.get(q.id, [])
        return QueryResult(query_id=q.id, total_count=len(studies), studies=studies, pages=1)


@pytest.fixture
def registry(raw_study):
    def make(nct, **status):
        s = copy.deepcopy(raw_study)
        s["protocolSection"]["identificationModule"]["nctId"] = nct
        s["protocolSection"]["statusModule"].update(status)
        return s

    day1 = {
        "glp1-obesity-drugs": [make("NCT10000001"), make("NCT10000002")],
        "glp1-obesity-sponsors": [make("NCT10000002")],
        "t2d-drugs": [make("NCT10000001"), make("NCT10000003")],
        "lipids-drugs": [make("NCT10000004")],
    }
    day2 = copy.deepcopy(day1)
    for s in day2["glp1-obesity-drugs"] + day2["t2d-drugs"]:
        if s["protocolSection"]["identificationModule"]["nctId"] == "NCT10000001":
            s["protocolSection"]["statusModule"]["overallStatus"] = "COMPLETED"
    day2["lipids-drugs"].append(make("NCT10000005"))
    return day1, day2


def test_two_day_run(tmp_path, registry):
    day1, day2 = registry
    quiet = lambda *a, **k: None

    first = run("2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=quiet)
    assert first["baseline"] and first["events"] == 0 and first["trials"] == 4
    assert (tmp_path / "snapshots" / "2026-01-05.json.gz").exists()
    assert read(tmp_path / "changes.json")["events"] == []
    meta = read(tmp_path / "meta.json")
    assert meta["app"] == "Clinical Trial Delta" and meta["run"]["baseline"] is True
    assert [c["trial_count"] for c in meta["collections"]] == [2, 2, 1]
    assert {q["id"]: q["matched"] for q in meta["queries"]}["glp1-obesity-sponsors"] == 1

    second = run("2026-01-06", data_dir=tmp_path, client=FakeClient(day2), log=quiet)
    assert second["compared_against"] == "2026-01-05"
    events = read(tmp_path / "changes.json")["events"]
    assert sorted((e["collection"], e["nct_id"], e["type"]) for e in events) == [
        ("cholesterol-lipids", "NCT10000005", "new_trial"),
        ("glp1-obesity", "NCT10000001", "status_change"),
        ("type-2-diabetes", "NCT10000001", "status_change"),
    ]
    trial = read(tmp_path / "trials" / "NCT10000001.json")
    assert trial["overall_status"] == "COMPLETED"
    assert trial["history"][0]["collections"] == ["glp1-obesity", "type-2-diabetes"]
    meta = read(tmp_path / "meta.json")
    assert meta["snapshot_date"] == "2026-01-06"
    assert [c["changes_recent"] for c in meta["collections"]] == [1, 1, 1]
    assert trial["sponsors_of_note"] == ["Roche"]  # fixture's lead sponsor is Hoffmann-La Roche


def test_same_day_rerun_is_a_no_op(tmp_path, registry):
    day1, _ = registry
    run("2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=lambda *a: None)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    again = run("2026-01-05", data_dir=tmp_path, client=FakeClient({}), log=lambda *a: None)
    assert again["status"] == "noop"
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_fetch_failure_writes_nothing(tmp_path, registry):
    day1, _ = registry
    with pytest.raises(FetchIntegrityError):
        run("2026-01-05", data_dir=tmp_path, client=FakeClient(day1, fail_query="t2d-drugs"), log=lambda *a: None)
    assert not any(tmp_path.iterdir())


def test_dry_run_writes_nothing(tmp_path, registry):
    day1, _ = registry
    result = run("2026-01-05", data_dir=tmp_path, client=FakeClient(day1), dry_run=True, log=lambda *a: None)
    assert result["status"] == "dry_run" and result["trials"] == 4
    assert not any(tmp_path.iterdir())


def test_registry_refresh_mid_run_is_flagged(tmp_path, registry):
    day1, _ = registry

    class Refreshing(FakeClient):
        calls = 0

        def version(self):
            self.calls += 1
            return {"apiVersion": "2.0.5", "dataTimestamp": f"2026-01-05T1{self.calls}:00:00"}

    result = run("2026-01-05", data_dir=tmp_path, client=Refreshing(day1), log=lambda *a: None)
    assert result["warnings"] and "refreshed during the run" in result["warnings"][0]
    assert read(tmp_path / "meta.json")["run"]["warnings"] == result["warnings"]


def test_watchlist_queries_all_covered_by_fixture_names(registry):
    """Guard: the fake registry above uses real query ids from the watchlist."""
    ids = {q.id for q in load_watchlist().queries}
    assert set(registry[0]) <= ids
