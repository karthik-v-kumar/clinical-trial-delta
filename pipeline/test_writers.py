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
    comparators,
    describe_change,
    drug_matcher,
    has_placebo_group,
    interventions_tested,
    key_lab_criteria,
    other_names,
    phase_bucket,
    publish,
    sponsor_matcher,
    trial_history,
    update_changes,
    write_collections,
    write_drugs,
    write_expanding,
    write_feeds,
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


# --- drugs, search names and feeds --------------------------------------------------


def _drugs():
    from pipeline.config import Drug

    return (
        Drug("semaglutide", "Semaglutide", "GLP-1 receptor agonist", ("semaglutide", "Wegovy", "NN9535")),
        Drug("survodutide", "Survodutide", "Glucagon and GLP-1 receptor agonist", ("survodutide", "BI 456906")),
        Drug("bempedoic-acid", "Bempedoic acid", "ACL inhibitor", ("bempedoic acid", "ETC-1002")),
    )


def test_drugs_match_listed_interventions_by_whole_word_and_code():
    match = drug_matcher(_drugs())
    iv = lambda name, *other, kind="DRUG": {"type": kind, "name": name, "other_names": list(other)}
    assert match({"interventions": [iv("Semaglutide 2.4 mg")]}) == ["semaglutide"]
    assert match({"interventions": [iv("Study drug", "Wegovy")]}) == ["semaglutide"]
    # Sponsor codes match with a space, a hyphen or neither.
    assert match({"interventions": [iv("BI-456906")]}) == ["survodutide"]
    assert match({"interventions": [iv("BI456906")]}) == ["survodutide"]
    assert match({"interventions": [iv("Bempedoic Acid 180 mg")]}) == ["bempedoic-acid"]
    # A placebo made to match the drug is not the drug.
    assert match({"interventions": [iv("Placebo matching semaglutide"), iv("Diet")]}) == []
    # Whole words only; and free text such as the title never counts.
    assert match({"interventions": [iv("NN95351")], "brief_title": "Semaglutide study"}) == []
    assert match({}) == []


def test_other_names_skip_placebo_and_repeats():
    record = {
        "interventions": [
            {"name": "Tirzepatide", "other_names": ["LY3298176", "Mounjaro", "tirzepatide"]},
            {"name": "Placebo", "other_names": ["Placebo for tirzepatide"]},
            {"name": "Semaglutide", "other_names": ["Wegovy", "LY3298176", "matching placebo"]},
        ]
    }
    assert other_names(record) == ["LY3298176", "Mounjaro", "Wegovy"]


def test_drug_files_have_the_collection_shape(tmp_path, day2):
    from pipeline.config import Watchlist

    day2.trials["NCT90000010"]["interventions"] = [{"type": "DRUG", "name": "Semaglutide", "other_names": []}]
    day2.trials["NCT90000014"]["interventions"] = [{"type": "DRUG", "name": "NN9535", "other_names": []}]
    day2.trials["NCT90000009"]["interventions"] = [{"type": "DRUG", "name": "Placebo for Wegovy", "other_names": []}]
    wl = Watchlist(collections=load_watchlist().collections, drugs=_drugs())
    (tmp_path / "drugs").mkdir()
    (tmp_path / "drugs" / "retired-drug.json").write_text("{}")
    stats = write_drugs(tmp_path, day2, wl)
    assert stats == {"semaglutide": 2, "survodutide": 0, "bempedoic-acid": 0}
    assert not (tmp_path / "drugs" / "retired-drug.json").exists()

    index = read(tmp_path / "drugs" / "index.json")
    first = index["drugs"][0]
    assert first == {
        "id": "semaglutide",
        "name": "Semaglutide",
        "drug_class": "GLP-1 receptor agonist",
        "also_listed_as": ["Wegovy", "NN9535"],
        "collections": ["glp1-obesity", "type-2-diabetes"],
        "trial_count": 2,
        "recruiting_count": 2,
        "not_yet_recruiting_count": 0,
    }
    drug = read(tmp_path / "drugs" / "semaglutide.json")
    assert [t["nct_id"] for t in drug["trials"]] == ["NCT90000014", "NCT90000010"]  # newest first
    assert set(drug["trials"][0]) == set(ROW_FIELDS)
    assert drug["by_status"] == [{"status": "RECRUITING", "count": 2}]
    assert drug["accent"] == load_watchlist().collections[0].accent
    assert read(tmp_path / "drugs" / "survodutide.json")["trials"] == []


def test_feeds_list_each_change_once_newest_first(tmp_path):
    import xml.etree.ElementTree as ET

    site = [{"facility": "A & B Clinic", "city": "Boston", "state": "Massachusetts", "country": "United States"}]
    events = [
        _event("NCT90000014", "status_change", "RECRUITING", "2026-01-05", "c1"),
        _event("NCT90000010", "site_added", site, "2026-01-06", "c2"),
        _event("NCT90000010", "site_added", site, "2026-01-06", "c2", collection="type-2-diabetes"),
    ]
    events[0]["old_value"] = "NOT_YET_RECRUITING"
    events[0]["brief_title"] = "Retatrutide <obesity> study"
    counts = write_feeds(tmp_path, load_watchlist(), events)
    assert counts == {"all": 2, "glp1-obesity": 2, "type-2-diabetes": 1, "cholesterol-lipids": 0}

    channel = ET.parse(tmp_path / "feeds" / "all.xml").getroot().find("channel")
    items = channel.findall("item")
    assert [i.findtext("guid") for i in items] == ["c2", "c1"]
    assert items[0].findtext("title") == "1 site added: NCT90000010"
    assert items[0].findtext("link").endswith("/t/NCT90000010")
    assert "Boston, Massachusetts" in items[0].findtext("description")
    assert items[1].findtext("title") == "Status: Not yet recruiting → Recruiting: Retatrutide <obesity> study"
    assert items[1].findtext("pubDate") == "Mon, 05 Jan 2026 12:00:00 +0000"
    assert channel.findtext("pubDate") == "Tue, 06 Jan 2026 12:00:00 +0000"
    # An empty collection still gets a valid feed, and an unchanged feed is not rewritten.
    assert ET.parse(tmp_path / "feeds" / "cholesterol-lipids.xml").getroot().find("channel").findall("item") == []
    before = (tmp_path / "feeds" / "all.xml").stat().st_mtime_ns
    write_feeds(tmp_path, load_watchlist(), events)
    assert (tmp_path / "feeds" / "all.xml").stat().st_mtime_ns == before


def test_change_descriptions():
    date = lambda d: {"date": d, "type": "ESTIMATED"}
    event = {
        "type": "date_change",
        "field": "primary_completion_date",
        "old_value": date("2027-06"),
        "new_value": date("2027-12-15"),
    }
    assert describe_change(event) == ("Primary completion: Jun 2027 → Dec 15, 2027", "")
    enrollment = {
        "type": "enrollment_change",
        "old_value": {"count": 600, "type": "ESTIMATED"},
        "new_value": {"count": 1200, "type": "ESTIMATED"},
    }
    assert describe_change(enrollment)[0] == "Enrollment: 600 people (target) → 1,200 people (target)"
    stopped = {
        "type": "status_change",
        "old_value": "RECRUITING",
        "new_value": "TERMINATED",
        "context": {"why_stopped": "Sponsor decision"},
    }
    assert describe_change(stopped) == ("Status: Recruiting → Terminated", "Reason given: Sponsor decision")


# --- clinician view and trials adding sites --------------------------------------


def test_key_lab_criteria_are_verbatim_and_need_a_number():
    long = (
        "Laboratory abnormalities at screening judged clinically significant by the investigator, including "
        "serum alanine aminotransferase (ALT) above twice the upper limit of normal, or estimated glomerular "
        "filtration rate (eGFR) <30 mL/min/1.73 m² using the CKD-EPI formula, or any other value that in the "
        "opinion of the investigator would interfere with participation in the trial or the interpretation of data"
    )
    checklist = {
        "parsed": True,
        "inclusion": [
            {"text": "HbA1c ≥7.0% and ≤10.5% at screening"},
            {"text": "Laboratory values:", "sub_items": ["BMI ≥ 27 kg/m²", "Stable body weight"]},
            {"text": "HbA1c measured by the central laboratory"},  # the 1 in "HbA1c" is not a cut-off
            {"text": "HbA1c tested in people with type 2 diabetes"},  # nor is "type 2"
        ],
        "exclusion": [{"text": long}, {"text": "Fasting triglycerides >500 mg/dL or LDL-C <70 mg/dL"}],
    }
    found = key_lab_criteria(checklist)
    assert found[0] == {"labs": ["HbA1c"], "kind": "inclusion", "text": "HbA1c ≥7.0% and ≤10.5% at screening"}
    assert found[1] == {"labs": ["BMI"], "kind": "inclusion", "text": "BMI ≥ 27 kg/m²"}
    excerpt = found[2]
    assert excerpt["labs"] == ["eGFR"] and excerpt["kind"] == "exclusion"
    assert excerpt["text"].startswith("…") and excerpt["text"].endswith("…") and "<30 mL/min/1.73 m²" in excerpt["text"]
    assert len(excerpt["text"]) < len(long)
    assert found[3]["labs"] == ["LDL-C", "Triglycerides"]
    assert len(found) == 4
    assert key_lab_criteria({"parsed": False}) == [] and key_lab_criteria(None) == []


def test_comparators_name_active_arms_then_placebo():
    record = {
        "arm_groups": [
            {"type": "EXPERIMENTAL", "label": "Drug X", "intervention_names": ["Drug: X"]},
            {
                "type": "ACTIVE_COMPARATOR",
                "label": "Sema",
                "intervention_names": ["Drug: Semaglutide", "Drug: Placebo for X"],
            },
            {"type": "PLACEBO_COMPARATOR", "label": "Placebo", "intervention_names": ["Drug: Placebo"]},
            {"type": "ACTIVE_COMPARATOR", "label": "Usual care", "intervention_names": []},
        ]
    }
    assert comparators(record) == ["Semaglutide", "Placebo", "Usual care"]
    assert comparators({}) == []


def test_clinical_file_covers_every_finder_trial(tmp_path, day2):
    day2.trials["NCT90000014"].update(
        {
            "eligibility": {
                "criteria": "Inclusion Criteria:\n\n* BMI ≥30 kg/m²\n\nExclusion Criteria:\n\n* HbA1c >10%"
            },
            "design": {"allocation": "RANDOMIZED", "masking": "DOUBLE", "intervention_model": "PARALLEL"},
            "primary_outcomes": [{"measure": "Change in body weight " * 20, "time_frame": "Week 72"}],
            "enrollment": {"count": 600, "type": "ESTIMATED"},
        }
    )
    write_finder(tmp_path, day2, load_watchlist(), [])
    finder = read(tmp_path / "finder.json")
    clinical = read(tmp_path / "clinical.json")
    assert set(clinical["trials"]) == {t["nct_id"] for t in finder["trials"]}
    trial = clinical["trials"]["NCT90000014"]
    assert [(c["labs"], c["kind"]) for c in trial["criteria"]] == [(["BMI"], "inclusion"), (["HbA1c"], "exclusion")]
    assert trial["design"] == {"allocation": "RANDOMIZED", "masking": "DOUBLE", "model": "PARALLEL", "purpose": None}
    assert trial["endpoints"][0]["measure"].endswith("…") and len(trial["endpoints"][0]["measure"]) <= 220
    assert trial["enrollment"] == {"count": 600, "type": "ESTIMATED"}
    assert "sites" not in trial  # institutions come from the trial files, for the results on screen


def test_expanding_lists_trials_with_a_signal(tmp_path, day2):
    added = [{"facility": "A", "city": "Lyon", "state": None, "country": "France"}]
    events = [
        _event("NCT90000010", "site_added", added, "2026-01-06", "s1"),
        _event("NCT90000010", "site_added", added, "2026-01-06", "s1", collection="type-2-diabetes"),
        {
            **_event("NCT90000012", "enrollment_change", {"count": 900, "type": "ESTIMATED"}, "2026-01-05", "e1"),
            "old_value": {"count": 600, "type": "ESTIMATED"},
        },
        # A lowered target is not a signal.
        {
            **_event("NCT90000004", "enrollment_change", {"count": 100, "type": "ESTIMATED"}, "2026-01-05", "e2"),
            "old_value": {"count": 300, "type": "ESTIMATED"},
        },
        # Older than the window.
        _event("NCT90000009", "site_added", added, "2025-09-01", "s2"),
    ]
    day2.trials["NCT90000007"]["start_date"] = {"date": "2026-04", "type": "ESTIMATED"}
    day2.trials["NCT90000014"]["first_posted"] = "2025-12-20"
    stats = write_expanding(tmp_path, day2, load_watchlist(), events)
    body = read(tmp_path / "expanding.json")
    by_id = {t["nct_id"]: t for t in body["trials"]}
    assert set(by_id) == {"NCT90000007", "NCT90000010", "NCT90000012", "NCT90000014"}
    assert stats["trials"] == 4 and stats["sites_added"] == 1 and stats["enrollment_raised"] == 1
    assert by_id["NCT90000010"]["signals"] == [
        {"type": "sites_added", "date": "2026-01-06", "count": 1, "countries": ["France"]}
    ]
    assert by_id["NCT90000012"]["signals"][0] == {
        "type": "enrollment_raised",
        "date": "2026-01-05",
        "from": 600,
        "to": 900,
    }
    planned = by_id["NCT90000007"]
    # The fixture posted it on 2026-01-05, so it is also newly registered.
    assert planned["signals"] == [
        {"type": "not_yet_recruiting", "date": "2026-04"},
        {"type": "newly_registered", "date": "2026-01-05"},
    ]
    assert planned["planned_site_count"] == 1 and planned["site_count"] == 0
    assert planned["countries"] == [["United States", 1]]
    assert by_id["NCT90000014"]["signals"] == [{"type": "newly_registered", "date": "2025-12-20"}]
    # Newest signal first.
    assert [t["nct_id"] for t in body["trials"]][:2] == ["NCT90000010", "NCT90000012"]
    assert body["window_days"] == 60 and body["registered_days"] == 90
