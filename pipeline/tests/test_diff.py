"""Differ tests against two fixture snapshots with known diffs.

fixtures/snapshot_2026-01-05.json -> fixtures/snapshot_2026-01-06.json contains,
on purpose, one or more instances of every event type plus the cases that must
NOT produce an event (whitespace-only endpoint edits, date type flips, site
status changes, bookkeeping-only updates, watchlist edits).
"""

import copy

import pytest

from pipeline.diff import EVENT_TYPES, diff_snapshots
from pipeline.snapshot import Snapshot

DAY2 = "2026-01-06"
HOUSTON = {"facility": "Research Site", "city": "Houston", "state": "Texas", "country": "United States"}
DALLAS = {"facility": "Research Site", "city": "Dallas", "state": "Texas", "country": "United States"}

# (collection, nct, type, field, old_value, new_value), in emitted order.
EXPECTED = [
    ("glp1-obesity", "NCT90000001", "status_change", "overall_status", "RECRUITING", "ACTIVE_NOT_RECRUITING"),
    ("glp1-obesity", "NCT90000002", "status_change", "overall_status", "ACTIVE_NOT_RECRUITING", "COMPLETED"),
    (
        "glp1-obesity",
        "NCT90000002",
        "enrollment_change",
        "enrollment",
        {"count": 300, "type": "ESTIMATED"},
        {"count": 287, "type": "ACTUAL"},
    ),
    (
        "glp1-obesity",
        "NCT90000002",
        "date_change",
        "completion_date",
        {"date": "2026-10", "type": "ESTIMATED"},
        {"date": "2025-12-20", "type": "ACTUAL"},
    ),
    ("glp1-obesity", "NCT90000005", "site_added", "sites", None, [HOUSTON]),
    ("glp1-obesity", "NCT90000005", "site_removed", "sites", [DALLAS], None),
    (
        "glp1-obesity",
        "NCT90000007",
        "new_trial",
        "trial",
        None,
        {"overall_status": "NOT_YET_RECRUITING", "phases": ["PHASE3"], "first_posted": "2026-01-05"},
    ),
    (
        "glp1-obesity",
        "NCT90000010",
        "new_trial",
        "trial",
        None,
        {"overall_status": "RECRUITING", "phases": ["PHASE2"], "first_posted": "2025-03-01"},
    ),
    (
        "glp1-obesity",
        "NCT90000014",
        "date_change",
        "primary_completion_date",
        {"date": "2027-03", "type": "ESTIMATED"},
        {"date": "2026-09", "type": "ESTIMATED"},
    ),
    ("type-2-diabetes", "NCT90000001", "status_change", "overall_status", "RECRUITING", "ACTIVE_NOT_RECRUITING"),
    ("type-2-diabetes", "NCT90000003", "results_posted", "has_results", False, True),
    (
        "type-2-diabetes",
        "NCT90000004",
        "endpoint_change",
        "primary_outcomes",
        ["Change in HbA1c", "Time in range"],
        ["Change in HbA1c from baseline to week 26", "  time in   Range "],
    ),
    (
        "type-2-diabetes",
        "NCT90000010",
        "enrollment_change",
        "enrollment",
        {"count": 100, "type": "ESTIMATED"},
        {"count": 120, "type": "ESTIMATED"},
    ),
    (
        "type-2-diabetes",
        "NCT90000013",
        "enrollment_change",
        "enrollment",
        {"count": 250, "type": "ESTIMATED"},
        {"count": 250, "type": "ACTUAL"},
    ),
    ("cholesterol-lipids", "NCT90000008", "status_change", "overall_status", "RECRUITING", "TERMINATED"),
]


def as_tuples(events):
    return [(e["collection"], e["nct_id"], e["type"], e["field"], e["old_value"], e["new_value"]) for e in events]


def test_emits_exactly_the_known_diffs(day1, day2):
    result = diff_snapshots(day1, day2)
    assert as_tuples(result.events) == EXPECTED


def test_fixture_exercises_every_event_type(day1, day2):
    assert {e["type"] for e in diff_snapshots(day1, day2).events} == set(EVENT_TYPES)


def test_event_shape(day1, day2):
    required = {
        "id",
        "change_id",
        "type",
        "nct_id",
        "collection",
        "field",
        "old_value",
        "new_value",
        "detected_date",
        "brief_title",
    }
    for e in diff_snapshots(day1, day2).events:
        assert required <= set(e) <= required | {"context"}
        assert e["detected_date"] == DAY2
        assert e["type"] in EVENT_TYPES


def test_titles_come_from_todays_record(day1, day2):
    by_nct = {e["nct_id"]: e for e in diff_snapshots(day1, day2).events}
    assert by_nct["NCT90000007"]["brief_title"] == "Orforglipron once-daily study"


def test_contexts(day1, day2):
    events = {(e["collection"], e["nct_id"], e["type"]): e for e in diff_snapshots(day1, day2).events}
    assert events[("cholesterol-lipids", "NCT90000008", "status_change")]["context"] == {
        "why_stopped": "Sponsor decision"
    }
    assert events[("type-2-diabetes", "NCT90000003", "results_posted")]["context"] == {
        "results_first_posted": "2026-01-05"
    }
    assert events[("glp1-obesity", "NCT90000005", "site_added")]["context"] == {
        "site_count_before": 3,
        "site_count_after": 3,
    }
    assert "context" not in events[("glp1-obesity", "NCT90000001", "status_change")]


def test_one_change_in_two_collections_shares_change_id(day1, day2):
    pair = [e for e in diff_snapshots(day1, day2).events if e["nct_id"] == "NCT90000001"]
    assert [e["collection"] for e in pair] == ["glp1-obesity", "type-2-diabetes"]
    assert pair[0]["change_id"] == pair[1]["change_id"]
    assert pair[0]["id"] != pair[1]["id"]


def test_ids_are_unique_and_deterministic(day1, day2):
    first = diff_snapshots(day1, day2).events
    second = diff_snapshots(day1, day2).events
    assert first == second
    assert len({e["id"] for e in first}) == len(first)


def test_trial_new_to_one_collection_only_reports_there(day1, day2):
    """NCT90000010 joins glp1-obesity (new_trial there) and changes enrollment
    (reported only where it was already tracked: type-2-diabetes)."""
    events = [(e["collection"], e["type"]) for e in diff_snapshots(day1, day2).events if e["nct_id"] == "NCT90000010"]
    assert events == [("glp1-obesity", "new_trial"), ("type-2-diabetes", "enrollment_change")]


def test_watchlist_edit_rebaselines_without_new_trial_flood(day1, day2):
    result = diff_snapshots(day1, day2)
    assert result.rebaselined_collections == ["cholesterol-lipids"]
    assert not any(e["nct_id"] == "NCT90000009" for e in result.events)
    # ...but trials tracked on both days still report real changes.
    assert ("cholesterol-lipids", "NCT90000008", "status_change") in {
        (e["collection"], e["nct_id"], e["type"]) for e in result.events
    }


def test_left_watchlist_is_reported_not_emitted(day1, day2):
    result = diff_snapshots(day1, day2)
    assert result.left_watchlist == {"glp1-obesity": ["NCT90000006"]}
    assert not any(e["nct_id"] == "NCT90000006" for e in result.events)


def test_changed_records_ignores_bookkeeping_fields(day1, day2):
    changed = diff_snapshots(day1, day2).changed_records
    assert "NCT90000011" not in changed  # only last_update_posted moved
    assert "NCT90000012" in changed  # brief summary edited: no event type, but the record changed
    assert "NCT90000007" in changed and "NCT90000009" in changed  # new to the snapshot
    assert changed == sorted(changed)


@pytest.mark.parametrize(
    "description, nct, edit",
    [
        (
            "whitespace/case-only endpoint edit",
            "NCT90000004",
            lambda r: r["primary_outcomes"][0].update(measure="  CHANGE IN hba1c "),
        ),
        ("date type flip without a move", "NCT90000004", lambda r: r["primary_completion_date"].update(type="ACTUAL")),
        ("site status change", "NCT90000005", lambda r: r["sites"][0].update(status="COMPLETED")),
        ("site coordinates change", "NCT90000005", lambda r: r["sites"][0].update(lat=1.0, lon=2.0)),
        ("site list reordered", "NCT90000001", lambda r: r["sites"].reverse()),
        ("outcome list reordered", "NCT90000004", lambda r: r["primary_outcomes"].reverse()),
        ("results withdrawn (true -> false)", "NCT90000014", lambda r: r.update(has_results=False)),
        ("last_update_posted only", "NCT90000001", lambda r: r.update(last_update_posted="2026-01-06")),
    ],
)
def test_non_changes_emit_nothing(day1, description, nct, edit):
    later = copy.deepcopy(day1)
    later.snapshot_date = DAY2
    edit(later.trials[nct])
    assert diff_snapshots(day1, later).events == [], description


def test_identical_snapshots_emit_nothing(day1):
    later = copy.deepcopy(day1)
    later.snapshot_date = DAY2
    result = diff_snapshots(day1, later)
    assert result.events == [] and result.changed_records == [] and result.left_watchlist == {}


def test_duplicate_site_names_are_counted_not_merged(day1):
    """Sponsors often list every location as "Research Site"; two in one city are two sites."""
    later = copy.deepcopy(day1)
    later.snapshot_date = DAY2
    later.trials["NCT90000005"]["sites"].pop(0)  # one of the two Dallas "Research Site" entries
    [event] = diff_snapshots(day1, later).events
    assert event["type"] == "site_removed" and event["old_value"] == [DALLAS]


def test_many_sites_added_is_one_event(day1):
    later = copy.deepcopy(day1)
    later.snapshot_date = DAY2
    later.trials["NCT90000001"]["sites"] += [
        {"facility": f"Site {i}", "city": "Leeds", "state": None, "country": "United Kingdom", "status": "RECRUITING"}
        for i in range(25)
    ]
    events = diff_snapshots(day1, later).events
    assert [(e["collection"], e["type"]) for e in events] == [
        ("glp1-obesity", "site_added"),
        ("type-2-diabetes", "site_added"),
    ]
    assert len(events[0]["new_value"]) == 25


def test_baseline_run_emits_nothing(day1):
    result = diff_snapshots(None, day1)
    assert result.baseline and result.events == [] and result.compared_against is None
    assert result.changed_records == sorted(day1.trials)


def test_new_collection_is_a_baseline_for_that_collection(day1, day2):
    later = copy.deepcopy(day2)
    later.collections["brand-new"] = copy.deepcopy(later.collections["glp1-obesity"])
    result = diff_snapshots(day1, later)
    assert "brand-new" in result.rebaselined_collections
    assert not any(e["collection"] == "brand-new" for e in result.events)


def test_previous_snapshot_must_be_earlier(day1, day2):
    with pytest.raises(ValueError):
        diff_snapshots(day2, day1)


def test_gap_between_snapshots_uses_todays_date(day1, day2):
    later = Snapshot.from_dict(
        {
            "snapshot_date": "2026-01-09",
            "collections": {
                cid: {"query_hash": s.query_hash, "queries": s.queries} for cid, s in day2.collections.items()
            },
            "trials": day2.trials,
        }
    )
    result = diff_snapshots(day1, later)
    assert result.compared_against == "2026-01-05"
    assert {e["detected_date"] for e in result.events} == {"2026-01-09"}
