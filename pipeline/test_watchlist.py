import copy
import json

import pytest

from pipeline.config import WATCHLIST_PATH, WatchlistError, load_watchlist, parse_watchlist


@pytest.fixture
def raw():
    with open(WATCHLIST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_seed_watchlist_loads():
    wl = load_watchlist()
    assert [c.id for c in wl.collections] == ["glp1-obesity", "type-2-diabetes", "cholesterol-lipids"]
    assert [c.name for c in wl.collections] == ["GLP-1 & obesity", "Type 2 diabetes", "Cholesterol & lipid disorders"]
    assert all(q.study_type == "INTERVENTIONAL" for q in wl.queries)


def test_seed_terms_from_the_brief_are_present():
    wl = load_watchlist()
    glp1, t2d, lipids = wl.collections
    terms = lambda c, f: {t.casefold() for q in c.queries for t in getattr(q, f)}
    assert {"obesity", "overweight"} <= terms(glp1, "cond")
    assert {"semaglutide", "tirzepatide", "retatrutide", "orforglipron", "glp-1"} <= terms(glp1, "intr")
    assert {"diabetes mellitus, type 2"} <= terms(t2d, "cond")
    assert {"metformin", "insulin", "empagliflozin", "dapagliflozin", "sglt2", "dpp-4"} <= terms(t2d, "intr")
    assert {"hypercholesterolemia", "dyslipidemias", "hypertriglyceridemia"} <= terms(lipids, "cond")
    assert {"statin", "evolocumab", "alirocumab", "pcsk9", "inclisiran", "bempedoic acid", "evinacumab"} <= terms(
        lipids, "intr"
    )
    assert glp1.sponsors_of_note == ("Novo Nordisk", "Eli Lilly", "Amgen", "Viking", "Roche")
    assert t2d.sponsors_of_note == ("Novo Nordisk", "Eli Lilly", "Boehringer Ingelheim", "AstraZeneca")
    assert lipids.sponsors_of_note == ("Amgen", "Novartis", "Regeneron", "Esperion")


def test_every_sponsor_of_note_is_queried_within_its_collection():
    for c in load_watchlist().collections:
        queried = {s for q in c.queries for s in q.spons}
        assert set(c.sponsors_of_note) <= queried, c.id


def test_query_hash_changes_only_with_matching_rules(raw):
    base = parse_watchlist(raw).collections[0].query_hash()
    relabelled = copy.deepcopy(raw)
    relabelled["collections"][0]["queries"][0]["label"] = "new label"
    relabelled["collections"][0]["name"] = "New name"
    assert parse_watchlist(relabelled).collections[0].query_hash() == base
    edited = copy.deepcopy(raw)
    edited["collections"][0]["queries"][0]["intr"].append("mazdutide")
    assert parse_watchlist(edited).collections[0].query_hash() != base


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r["collections"][0].update(id="GLP1 Obesity"), "kebab"),
        (lambda r: r["collections"][1].update(id="glp1-obesity"), "more than once"),
        (lambda r: r["collections"][0]["queries"][1].update(id="glp1-obesity-drugs"), "more than once"),
        (lambda r: r["collections"][0].update(accent="teal"), "accent"),
        (lambda r: r["collections"][0].update(queries=[]), "queries"),
        (lambda r: r["collections"][0]["queries"][0].update(study_type="RCT"), "study_type"),
        (lambda r: r["collections"][0]["queries"][0].update(intervention=["x"]), "unknown keys"),
        (lambda r: r["collections"][0]["queries"][0].update(intr=["semaglutide", "Semaglutide"]), "duplicate"),
        (lambda r: r["collections"][0]["queries"].append({"id": "empty-q", "label": "x"}), "at least one"),
        (lambda r: r.update(schema_version=2), "schema_version"),
    ],
)
def test_validation_errors(raw, mutate, message):
    bad = copy.deepcopy(raw)
    mutate(bad)
    with pytest.raises(WatchlistError, match=message):
        parse_watchlist(bad)
