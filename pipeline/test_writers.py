import json

import pytest

from pipeline.config import load_watchlist
from pipeline.diff import diff_snapshots
from pipeline.eligibility import parse_criteria
from pipeline.normalize import normalize_study
from pipeline.writers import (
    CHECKLIST_FIELDS,
    CHECKLIST_ITEM_FIELDS,
    EVENT_FIELDS,
    HISTORY_FIELDS,
    ROW_FIELDS,
    TRIAL_DERIVED,
    TRIAL_EXCLUDED,
    TRIAL_FIELDS,
    TRIAL_FILE_KEYS,
    age_in_years,
    has_placebo_group,
    interventions_tested,
    phase_bucket,
    publish,
    sponsor_matcher,
    trial_history,
    update_changes,
    write_collections,
    write_finder,
    write_sites,
    write_trials,
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_normalized_field_is_allowlisted_or_excluded(raw_study):
    """The Beacon lesson: a new normalized field must be published or dropped on
    purpose in the same change; it can never vanish silently."""
    produced = set(normalize_study(raw_study))
    accounted = set(TRIAL_FIELDS) | set(TRIAL_EXCLUDED)
    assert produced - accounted == set(), "add new fields to TRIAL_FIELDS or TRIAL_EXCLUDED in writers.py"
    assert accounted - produced == set(), "allowlist names a field the normalizer no longer produces"


def test_every_differ_event_key_is_allowlisted(day1, day2):
    """Same guard for change events: a key the differ adds must reach changes.json."""
    produced = {k for e in diff_snapshots(day1, day2).events for k in e}
    assert produced <= set(EVENT_FIELDS), f"add {produced - set(EVENT_FIELDS)} to EVENT_FIELDS in writers.py"
    history_keys = {k for e in diff_snapshots(day1, day2).events for k in e} - {"id", "nct_id", "brief_title"}
    assert history_keys - {"collection"} <= set(HISTORY_FIELDS)


def test_publish_copies_only_the_allowlist_in_order():
    """As in financial-research-hub: anything not allowlisted is dropped, silently."""
    assert publish({"b": 2, "internal": 3, "a": 1}, allowed=("a", "b", "missing")) == {"a": 1, "b": 2}
    assert list(publish({"b": 2, "a": 1}, allowed=("a", "b"))) == ["a", "b"]


def test_phase_bucket():
    assert phase_bucket(["PHASE2", "PHASE3"]) == "PHASE2/PHASE3"
    assert phase_bucket(["NA"]) == "NA" and phase_bucket([]) == "NA" and phase_bucket(None) == "NA"


def test_sponsor_matching_uses_word_boundaries():
    match = sponsor_matcher(("Roche", "Eli Lilly", "Amgen", "Viking"))
    assert match({"lead_sponsor": {"name": "Hoffmann-La Roche"}, "collaborators": []}) == ["Roche"]
    assert match({"lead_sponsor": {"name": "Eli Lilly and Company"}, "collaborators": ["Amgen"]}) == [
        "Eli Lilly",
        "Amgen",
    ]
    assert match({"lead_sponsor": {"name": "Rochester General Hospital"}, "collaborators": ["Pamgen Inc"]}) == []
    assert match({"lead_sponsor": None, "collaborators": []}) == []


def test_changes_feed_appends_and_deduplicates(tmp_path, day1, day2):
    events = diff_snapshots(day1, day2).events
    update_changes(tmp_path, events[:5])
    all_events = update_changes(tmp_path, events)  # overlapping re-run
    feed = read(tmp_path / "changes.json")
    assert feed["event_count"] == len(events) == len(all_events)
    assert [e["id"] for e in feed["events"]] == [e["id"] for e in events]


def test_changes_feed_rejects_unknown_event_types(tmp_path, day1, day2):
    event = dict(diff_snapshots(day1, day2).events[0], type="vibes_change")
    with pytest.raises(ValueError):
        update_changes(tmp_path, [event])


def test_history_merges_collections_per_change(day1, day2):
    history = trial_history(diff_snapshots(day1, day2).events)
    [entry] = history["NCT90000001"]
    assert entry["type"] == "status_change"
    assert entry["collections"] == ["glp1-obesity", "type-2-diabetes"]


def test_trial_files(tmp_path, day1, day2):
    wl = load_watchlist()
    write_trials(tmp_path, day1, wl, [])
    events = update_changes(tmp_path, diff_snapshots(day1, day2).events)
    stats = write_trials(tmp_path, day2, wl, events)
    t1 = read(tmp_path / "trials" / "NCT90000001.json")
    assert t1["registry_url"] == "https://clinicaltrials.gov/study/NCT90000001"
    assert t1["collections"] == ["glp1-obesity", "type-2-diabetes"]
    assert t1["matched_queries"] == ["glp1-obesity-drugs", "t2d-drugs"]
    assert t1["sponsors_of_note"] == ["Novo Nordisk"]
    assert t1["in_watchlist"] is True and len(t1["history"]) == 1
    assert list(t1)[:2] == ["schema_version", "nct_id"]
    assert set(TRIAL_DERIVED) - {"left_watchlist_date"} <= set(t1)  # nothing the writer built was dropped
    assert set(t1) <= set(TRIAL_FILE_KEYS)
    # A trial that stopped matching keeps its page, marked as no longer tracked.
    gone = read(tmp_path / "trials" / "NCT90000006.json")
    assert gone["in_watchlist"] is False and gone["left_watchlist_date"] == "2026-01-06"
    assert stats["untracked_trial_files"] == 1


def test_rewriting_unchanged_trial_files_is_a_no_op(tmp_path, day1):
    wl = load_watchlist()
    write_trials(tmp_path, day1, wl, [])
    assert write_trials(tmp_path, day1, wl, [])["files_changed"] == 0


def test_collection_files(tmp_path, day2):
    summary = write_collections(tmp_path, day2, load_watchlist())
    glp1 = read(tmp_path / "collections" / "glp1-obesity.json")
    assert glp1 == summary["glp1-obesity"]
    assert glp1["trial_count"] == 6
    assert glp1["recruiting_count"] == 3  # NCT90000005, 10, 14
    assert sum(b["count"] for b in glp1["by_phase"]) == glp1["trial_count"]
    assert sum(b["count"] for b in glp1["by_status"]) == glp1["trial_count"]
    assert glp1["by_phase"] == [{"phase": "PHASE2", "count": 1}, {"phase": "PHASE3", "count": 5}]
    assert all(set(row) == set(ROW_FIELDS) for row in glp1["trials"])
    assert glp1["queries"][0]["web_url"].startswith("https://clinicaltrials.gov/search?")


def test_every_checklist_key_is_allowlisted(raw_study):
    """Beacon guard for the eligibility checklist the parser produces."""
    rich = parse_criteria(
        "Intro\n\nInclusion Criteria:\n\nPart A:\n\n* One of:\n  * this\n  * that\n\nExclusion Criteria:\n\n* none\n"
    )
    assert set(rich) <= set(CHECKLIST_FIELDS), "add new checklist keys to CHECKLIST_FIELDS in writers.py"
    item_keys = {k for item in rich["inclusion"] + rich["exclusion"] for k in item}
    assert item_keys == set(CHECKLIST_ITEM_FIELDS), "CHECKLIST_ITEM_FIELDS must match what the parser emits"


def test_trial_file_carries_the_checklist(tmp_path, day1):
    record = dict(day1.trials["NCT90000001"])
    record["eligibility"] = {"criteria": "Inclusion Criteria:\n\n* Age 18+\n\nExclusion Criteria:\n\n* Pregnancy\n"}
    day1.trials["NCT90000001"] = record
    write_trials(tmp_path, day1, load_watchlist(), [])
    checklist = read(tmp_path / "trials" / "NCT90000001.json")["eligibility_checklist"]
    assert checklist == {
        "parsed": True,
        "inclusion": [{"text": "Age 18+"}],
        "exclusion": [{"text": "Pregnancy"}],
        "notes": [],
    }
    # A record with no criteria text still gets an honest, unparsed checklist.
    assert read(tmp_path / "trials" / "NCT90000002.json")["eligibility_checklist"]["parsed"] is False


def test_sites_index_lists_recruiting_cities_only(tmp_path, day2):
    stats = write_sites(tmp_path, day2, load_watchlist())
    glp1 = read(tmp_path / "sites" / "glp1-obesity.json")
    # NCT90000005 (recruiting) has a recruiting Dallas site and a recruiting Houston
    # site without coordinates; its Austin site is completed. NCT90000001 has sites
    # with coordinates but is no longer recruiting overall.
    by_trial = {t["nct_id"]: [glp1["cities"][i]["city"] for i in t["cities"]] for t in glp1["trials"]}
    assert "NCT90000001" not in by_trial
    assert by_trial["NCT90000010"] == ["Boston"] and by_trial["NCT90000014"] == ["Boston"]
    assert glp1["cities"] == [
        {"city": "Boston", "state": "Massachusetts", "country": "United States", "lat": 42.36, "lon": -71.06}
    ]
    assert glp1["sites_without_coordinates"] == 2 and glp1["recruiting_site_count"] == 4
    assert stats["glp1-obesity"] == {"cities": 1, "trials": 2}
    assert glp1["snapshot_date"] == "2026-01-06" and glp1["collection"] == "glp1-obesity"


# --- trial finder -------------------------------------------------------------


def test_age_limits_are_read_in_years():
    assert age_in_years("18 Years") == 18 and age_in_years("65 years") == 65
    assert age_in_years("6 Months") == 0.5 and age_in_years("2 Weeks") == 0.04
    assert age_in_years("N/A") is None and age_in_years(None) is None and age_in_years("") is None


def test_tested_interventions_leave_out_placebo_and_group_kinds():
    record = {
        "interventions": [
            {"type": "DRUG", "name": "Tirzepatide"},
            {"type": "DRUG", "name": "Placebo"},
            {"type": "OTHER", "name": "Sham device"},
            {"type": "BEHAVIORAL", "name": "Lifestyle counselling"},
            {"type": "DRUG", "name": "tirzepatide"},
            {"type": "PROCEDURE", "name": None},
        ]
    }
    assert interventions_tested(record) == (
        ["Tirzepatide", "Lifestyle counselling"],
        ["medicine", "lifestyle", "procedure"],
    )
    assert interventions_tested({}) == ([], [])


def test_placebo_group_from_arms_or_intervention_names():
    assert has_placebo_group({"arm_groups": [{"type": "PLACEBO_COMPARATOR"}]})
    assert has_placebo_group({"arm_groups": [{"type": "SHAM_COMPARATOR"}]})
    assert has_placebo_group({"interventions": [{"type": "DRUG", "name": "Matching placebo"}]})
    assert not has_placebo_group({"arm_groups": [{"type": "EXPERIMENTAL"}], "interventions": [{"name": "Semaglutide"}]})


def _event(nct, kind, new_value, date, change_id, collection="glp1-obesity"):
    return {
        "id": f"{change_id}-{collection}",
        "change_id": change_id,
        "type": kind,
        "nct_id": nct,
        "collection": collection,
        "field": "sites" if kind == "site_added" else "overall_status",
        "old_value": None,
        "new_value": new_value,
        "detected_date": date,
        "brief_title": None,
    }


def test_finder_lists_open_and_not_yet_open_trials_with_a_mappable_site(tmp_path, day2):
    day2.trials["NCT90000014"].update(
        {
            "conditions": ["Obesity", "Overweight"],
            "interventions": [{"type": "DRUG", "name": "Retatrutide"}, {"type": "DRUG", "name": "Placebo"}],
            "arm_groups": [{"type": "EXPERIMENTAL"}, {"type": "PLACEBO_COMPARATOR"}],
            "eligibility": {"sex": "ALL", "minimum_age": "18 Years", "maximum_age": "75 Years"},
            "lead_sponsor": {"name": "Eli Lilly and Company", "class": "INDUSTRY"},
        }
    )
    boston = {"facility": "Site 1", "city": "Boston", "state": "Massachusetts", "country": "United States"}

    def site(city, status, lat=42.0, lon=-71.5):
        return {**boston, "city": city, "status": status, "lat": lat, "lon": lon}

    # NCT90000007 has not started: the registry gives its sites no status of their own.
    day2.trials["NCT90000007"]["start_date"] = {"date": "2026-03", "type": "ESTIMATED"}
    day2.trials["NCT90000007"]["sites"] = [
        site("Boston", None, 42.36, -71.06),
        site("Cambridge", None),
        site("Lowell", "WITHDRAWN"),
        site("Salem", None, lat=None),
    ]
    # NCT90000009 is recruiting in Boston; its Worcester site has not opened, and a
    # second Boston site that has not opened does not make Boston "not open yet".
    day2.trials["NCT90000009"]["sites"] += [
        site("Worcester", "NOT_YET_RECRUITING"),
        site("Boston", "NOT_YET_RECRUITING", 42.36, -71.06),
        site("Springfield", "COMPLETED"),
    ]
    events = [
        _event("NCT90000014", "status_change", "RECRUITING", "2026-01-06", "c1"),
        # The same registry change seen from a second collection counts once.
        _event("NCT90000010", "site_added", [boston], "2026-01-06", "c2"),
        _event("NCT90000010", "site_added", [boston], "2026-01-06", "c2", collection="type-2-diabetes"),
        # Outside the 30-day window: ignored.
        _event("NCT90000012", "status_change", "RECRUITING", "2025-11-01", "c3", collection="type-2-diabetes"),
        # A site added in a city the trial lists no open or planned site in: no news.
        _event(
            "NCT90000004",
            "site_added",
            [{**boston, "city": "Austin"}],
            "2026-01-05",
            "c4",
            collection="type-2-diabetes",
        ),
        # A planned site that was just added is news too.
        _event(
            "NCT90000009",
            "site_added",
            [{**boston, "city": "Worcester"}],
            "2026-01-06",
            "c5",
            collection="cholesterol-lipids",
        ),
    ]
    stats = write_finder(tmp_path, day2, load_watchlist(), events)
    finder = read(tmp_path / "finder.json")
    by_id = {t["nct_id"]: t for t in finder["trials"]}
    city = [c["city"] for c in finder["cities"]]

    # Recruiting overall: 04, 05, 09, 10, 12, 14. NCT90000005's recruiting sites
    # have no coordinates, so it is counted but not listed. Not yet recruiting: 07.
    assert sorted(by_id) == [
        "NCT90000004",
        "NCT90000007",
        "NCT90000009",
        "NCT90000010",
        "NCT90000012",
        "NCT90000014",
    ]
    assert finder["recruiting_trials"] == 6 and finder["trials_without_location"] == 1
    assert finder["not_yet_recruiting_trials"] == 1 and finder["not_yet_without_location"] == 0
    assert stats == {"trials": 6, "cities": 3, "without_location": 1, "not_yet_recruiting": 1}
    assert city == ["Boston", "Cambridge", "Worcester"]
    assert finder["cities"][0] == {
        "city": "Boston",
        "state": "Massachusetts",
        "country": "United States",
        "lat": 42.36,
        "lon": -71.06,
    }
    assert [c["id"] for c in finder["collections"]] == ["glp1-obesity", "type-2-diabetes", "cholesterol-lipids"]
    assert by_id["NCT90000010"]["collections"] == ["glp1-obesity", "type-2-diabetes"]

    trial = by_id["NCT90000014"]
    assert trial["status"] == "RECRUITING"
    assert trial["tested"] == ["Retatrutide"] and trial["kinds"] == ["medicine"] and trial["placebo"] is True
    assert (trial["sex"], trial["min_age"], trial["max_age"]) == ("ALL", 18, 75)
    assert trial["conditions"] == ["Obesity", "Overweight"] and trial["sponsor"] == "Eli Lilly and Company"
    assert trial["cities"] == [0] and trial["site_count"] == 1
    assert trial["soon"] == [] and trial["planned_site_count"] == 0 and "start_date" not in trial
    assert trial["opened"] == "2026-01-06" and "new_sites" not in trial

    # Not started: no recruiting cities; every site that is not closed is a planned one
    # (the one without coordinates is counted but cannot be placed).
    planned = by_id["NCT90000007"]
    assert planned["status"] == "NOT_YET_RECRUITING" and planned["start_date"] == "2026-03"
    assert planned["cities"] == [] and planned["site_count"] == 0
    assert [city[i] for i in planned["soon"]] == ["Boston", "Cambridge"] and planned["planned_site_count"] == 3

    # Recruiting, with a site that has not opened.
    mixed = by_id["NCT90000009"]
    assert [city[i] for i in mixed["cities"]] == ["Boston"] and mixed["site_count"] == 1
    assert [city[i] for i in mixed["soon"]] == ["Worcester"] and mixed["planned_site_count"] == 2
    assert mixed["new_sites"] == [{"date": "2026-01-06", "cities": [2]}]

    assert by_id["NCT90000010"]["new_sites"] == [{"date": "2026-01-06", "cities": [0]}]
    assert "opened" not in by_id["NCT90000012"] and "new_sites" not in by_id["NCT90000004"]
    assert finder["snapshot_date"] == "2026-01-06" and finder["news_window_days"] == 30
