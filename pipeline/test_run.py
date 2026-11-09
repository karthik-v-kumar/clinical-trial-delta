"""End-to-end: two daily runs against a fake registry, in a temp data dir."""

import copy
import json

import pytest

import pipeline.run as run_module
from pipeline.config import load_watchlist
from pipeline.fetch.ctgov import FetchIntegrityError, QueryResult
from pipeline.run import RunFailed, main, run
from pipeline.writers import META_RUN_FIELDS

QUIET = lambda *a, **k: None  # noqa: E731


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, by_query: dict[str, list[dict]], data_timestamp="2026-01-05T10:00:00", fail=()):
        self.by_query = by_query
        self.data_timestamp = data_timestamp
        self.fail = set(fail)  # query ids to fail, or {"*"} for all
        self.requests_made = 0

    def version(self):
        self.requests_made += 1
        return {"apiVersion": "2.0.5", "dataTimestamp": self.data_timestamp}

    def fetch_query(self, q):
        self.requests_made += 1
        if q.id in self.fail or "*" in self.fail:
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
    quiet = QUIET

    first = run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=quiet)
    assert first["baseline"] and first["events"] == 0 and first["trials"] == 4
    assert (tmp_path / "snapshots" / "2026-01-05.json").exists()
    assert read(tmp_path / "changes.json")["events"] == []
    meta = read(tmp_path / "meta.json")
    assert meta["app"] == "Clinical Trial Delta" and meta["run"]["baseline"] is True
    assert [c["trial_count"] for c in meta["collections"]] == [2, 2, 1]
    assert {q["id"]: q["matched"] for q in meta["queries"]}["glp1-obesity-sponsors"] == 1

    second = run("daily", "2026-01-06", data_dir=tmp_path, client=FakeClient(day2), log=quiet)
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
    run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=lambda *a: None)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    again = run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient({}), log=lambda *a: None)
    assert again["status"] == "noop"
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_failed_query_is_carried_forward_and_the_run_continues(tmp_path, registry):
    """Per-entity failures never kill the run: t2d-drugs fails on day 2, its trial list
    is carried forward, its collection is marked partial, everything else refreshes."""
    day1, day2 = registry
    run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=QUIET)
    result = run("daily", "2026-01-06", data_dir=tmp_path, client=FakeClient(day2, fail={"t2d-drugs"}), log=QUIET)
    assert result["status"] == "partial" and list(result["failed_queries"]) == ["t2d-drugs"]

    meta = read(tmp_path / "meta.json")
    assert meta["run"]["partial"] is True and "t2d-drugs" in meta["run"]["failed_queries"]
    assert [(c["id"], c["partial"]) for c in meta["collections"]] == [
        ("glp1-obesity", False),
        ("type-2-diabetes", True),
        ("cholesterol-lipids", False),
    ]
    t2d = read(tmp_path / "collections" / "type-2-diabetes.json")
    assert t2d["partial"] is True and t2d["trial_count"] == 2  # carried forward from 2026-01-05
    assert [q["failed"] for q in t2d["queries"]] == [True, False]

    events = sorted((e["collection"], e["nct_id"], e["type"]) for e in read(tmp_path / "changes.json")["events"])
    # NCT10000001 was re-fetched through the GLP-1 query, so its status change is still
    # seen, including in the partial collection it was already tracked in. No trial is
    # reported as new or as having left the partial collection.
    assert events == [
        ("cholesterol-lipids", "NCT10000005", "new_trial"),
        ("glp1-obesity", "NCT10000001", "status_change"),
        ("type-2-diabetes", "NCT10000001", "status_change"),
    ]
    assert read(tmp_path / "trials" / "NCT10000003.json")["in_watchlist"] is True


def test_failed_query_with_nothing_to_carry_rebaselines_its_collection(tmp_path, registry):
    day1, day2 = registry
    first = run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1, fail={"t2d-drugs"}), log=QUIET)
    assert first["status"] == "partial"
    assert read(tmp_path / "collections" / "type-2-diabetes.json")["trial_count"] == 0
    second = run("daily", "2026-01-06", data_dir=tmp_path, client=FakeClient(day2), log=QUIET)
    assert "type-2-diabetes" in second["rebaselined_collections"]
    events = read(tmp_path / "changes.json")["events"]
    assert not any(e["collection"] == "type-2-diabetes" and e["type"] == "new_trial" for e in events)


def test_bad_record_keeps_its_previous_version(tmp_path, registry):
    day1, day2 = registry
    run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=QUIET)
    for s in day2["lipids-drugs"]:
        if s["protocolSection"]["identificationModule"]["nctId"] == "NCT10000004":
            s["protocolSection"]["designModule"] = "not a module"  # malformed record
    result = run("daily", "2026-01-06", data_dir=tmp_path, client=FakeClient(day2), log=QUIET)
    assert result["status"] == "partial"
    meta = read(tmp_path / "meta.json")
    assert meta["run"]["records_kept_from_previous"] == ["NCT10000004"]
    assert read(tmp_path / "trials" / "NCT10000004.json")["phases"] == ["PHASE2", "PHASE3"]


def test_bad_record_with_no_history_is_skipped(tmp_path, registry):
    day1, _ = registry
    day1["lipids-drugs"][0]["protocolSection"]["designModule"] = "not a module"
    result = run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=QUIET)
    assert result["trials"] == 3
    assert read(tmp_path / "meta.json")["run"]["records_skipped"] == ["NCT10000004"]
    assert read(tmp_path / "collections" / "cholesterol-lipids.json")["trial_count"] == 0


def test_every_query_failing_writes_nothing(tmp_path, registry):
    day1, _ = registry
    with pytest.raises(RunFailed):
        run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1, fail={"*"}), log=QUIET)
    assert not any(tmp_path.iterdir())


def test_meta_records_mode_and_la_timestamps(tmp_path, registry):
    day1, _ = registry
    run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=QUIET)
    meta = read(tmp_path / "meta.json")
    assert meta["run"]["mode"] == "daily"
    assert meta["generated_at"][-6:] in {"-08:00", "-07:00"}  # America/Los_Angeles offset


def test_every_run_info_key_reaches_meta(tmp_path, registry, monkeypatch):
    """Beacon guard for meta.json: whatever run.py hands the writer must be allowlisted."""
    day1, _ = registry
    seen = {}
    real = run_module.write_meta

    def spy(*args, **kwargs):
        seen["run_info"] = kwargs["run"]
        return real(*args, **kwargs)

    monkeypatch.setattr(run_module, "write_meta", spy)
    run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=QUIET)
    missing = set(seen["run_info"]) - set(META_RUN_FIELDS)
    assert not missing, f"add {missing} to META_RUN_FIELDS in pipeline/writers.py"


def test_unknown_mode_is_rejected():
    with pytest.raises(SystemExit):
        main(["hourly"])
    with pytest.raises(ValueError):
        run("hourly")


def test_dry_run_writes_nothing(tmp_path, registry):
    day1, _ = registry
    result = run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), dry_run=True, log=lambda *a: None)
    assert result["status"] == "dry_run" and result["trials"] == 4
    assert not any(tmp_path.iterdir())


def test_registry_refresh_mid_run_is_flagged(tmp_path, registry):
    day1, _ = registry

    class Refreshing(FakeClient):
        calls = 0

        def version(self):
            self.calls += 1
            return {"apiVersion": "2.0.5", "dataTimestamp": f"2026-01-05T1{self.calls}:00:00"}

    result = run("daily", "2026-01-05", data_dir=tmp_path, client=Refreshing(day1), log=lambda *a: None)
    assert result["warnings"] and "refreshed during the run" in result["warnings"][0]
    assert read(tmp_path / "meta.json")["run"]["warnings"] == result["warnings"]


def test_watchlist_queries_all_covered_by_fixture_names(registry):
    """Guard: the fake registry above uses real query ids from the watchlist."""
    ids = {q.id for q in load_watchlist().queries}
    assert set(registry[0]) <= ids


def test_rebuild_rewrites_site_files_without_fetching(tmp_path, registry):
    day1, day2 = registry
    run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=QUIET)
    run("daily", "2026-01-06", data_dir=tmp_path, client=FakeClient(day2), log=QUIET)
    meta_before = read(tmp_path / "meta.json")
    changes_before = (tmp_path / "changes.json").read_bytes()
    snapshots_before = {p: p.read_bytes() for p in (tmp_path / "snapshots").rglob("*.json")}

    # Simulate site files written by an older writer: drop a derived field everywhere.
    for path in (tmp_path / "trials").glob("NCT*.json"):
        body = read(path)
        del body["eligibility_checklist"]
        path.write_text(json.dumps(body), encoding="utf-8")
    (tmp_path / "sites" / "glp1-obesity.json").unlink()

    class NoNetwork:
        requests_made = 0

        def __getattr__(self, name):
            raise AssertionError("rebuild must not contact the registry")

    result = run("rebuild", data_dir=tmp_path, client=NoNetwork(), log=QUIET)
    assert result["mode"] == "rebuild" and result["snapshot_date"] == "2026-01-06"
    assert result["trial_files"]["files_changed"] == 5

    trial = read(tmp_path / "trials" / "NCT10000001.json")
    assert trial["eligibility_checklist"]["parsed"] is True
    assert trial["history"][0]["type"] == "status_change"  # history survives a rebuild
    assert (tmp_path / "sites" / "glp1-obesity.json").exists()

    meta = read(tmp_path / "meta.json")
    assert meta["snapshot_date"] == "2026-01-06"
    assert meta["run"] == meta_before["run"]  # the fetch run's provenance is kept
    assert meta["rebuild"]["mode"] == "rebuild"
    assert meta["collections"] == meta_before["collections"]
    assert (tmp_path / "changes.json").read_bytes() == changes_before
    assert {p: p.read_bytes() for p in (tmp_path / "snapshots").rglob("*.json")} == snapshots_before


def test_daily_run_after_a_rebuild_clears_the_rebuild_note(tmp_path, registry):
    day1, day2 = registry
    run("daily", "2026-01-05", data_dir=tmp_path, client=FakeClient(day1), log=QUIET)
    run("rebuild", data_dir=tmp_path, log=QUIET)
    assert "rebuild" in read(tmp_path / "meta.json")
    run("daily", "2026-01-06", data_dir=tmp_path, client=FakeClient(day2), log=QUIET)
    assert "rebuild" not in read(tmp_path / "meta.json")


def test_rebuild_needs_a_snapshot(tmp_path):
    with pytest.raises(RunFailed):
        run("rebuild", data_dir=tmp_path, log=QUIET)
