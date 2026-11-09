import json

import pytest

from pipeline.config import load_watchlist
from pipeline.diff import diff_snapshots
from pipeline.normalize import normalize_study
from pipeline.writers import (
    ROW_FIELDS,
    TRIAL_EXCLUDED,
    TRIAL_FIELDS,
    UnpublishedFieldError,
    phase_bucket,
    publish,
    sponsor_matcher,
    trial_history,
    update_changes,
    write_collections,
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


def test_publish_rejects_unknown_keys():
    with pytest.raises(UnpublishedFieldError, match="surprise"):
        publish({"a": 1, "surprise": 2}, allowed=("a",))
    assert publish({"a": 1, "b": 2}, allowed=("a",), excluded=("b",)) == {"a": 1}


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
