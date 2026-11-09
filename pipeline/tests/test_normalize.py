import copy
import json

from pipeline.normalize import content_hash, normalize_study


def test_normalizes_core_fields(raw_study):
    r = normalize_study(raw_study)
    assert r["nct_id"] == "NCT90000099"
    assert r["brief_title"] == "A Study of Exampleglutide in Adults With Obesity"  # trimmed
    assert r["overall_status"] == "RECRUITING"
    assert r["phases"] == ["PHASE2", "PHASE3"]
    assert r["enrollment"] == {"count": 600, "type": "ESTIMATED"}
    assert r["start_date"] == {"date": "2025-09-01", "type": "ACTUAL"}
    assert r["completion_date"] == {"date": "2027-12", "type": None}  # older records omit the type
    assert r["first_posted"] == "2025-08-15"
    assert r["has_results"] is False and r["results_first_posted"] is None
    assert r["lead_sponsor"] == {"name": "Hoffmann-La Roche", "class": "INDUSTRY"}
    assert r["collaborators"] == ["Example University"]  # blank names dropped
    assert r["conditions"] == ["Obesity", "Overweight"]
    assert r["design"]["masking"] == "QUADRUPLE"
    assert r["primary_outcomes"] == [{"measure": "Percent change in body weight", "time_frame": "Baseline to week 52"}]
    assert r["eligibility"]["minimum_age"] == "18 Years" and r["eligibility"]["healthy_volunteers"] is False
    assert r["interventions"][0] == {"type": "DRUG", "name": "Exampleglutide", "other_names": ["EX-123"]}


def test_no_personal_data_survives(raw_study):
    """Hard rule: no personal data. Contact names, phones, e-mails and investigator
    names in the raw record must not reach the normalized record."""
    blob = json.dumps(normalize_study(raw_study))
    for needle in ("Jane Example", "Sam Coordinator", "555-01", "@example.com", "Study Desk"):
        assert needle not in blob
    for key in ("contacts", "phone", "email", "centralContacts", "overallOfficials", "responsibleParty"):
        assert f'"{key}"' not in blob


def test_sites_are_sorted_and_minimal(raw_study):
    sites = normalize_study(raw_study)["sites"]
    assert [s["country"] for s in sites] == ["France", "United States"]
    assert sites[1] == {
        "facility": "Example Hospital",
        "city": "Seattle",
        "state": "Washington",
        "country": "United States",
        "status": "RECRUITING",
        "lat": 47.60621,
        "lon": -122.33207,
    }


def test_deterministic_regardless_of_site_order(raw_study):
    shuffled = copy.deepcopy(raw_study)
    shuffled["protocolSection"]["contactsLocationsModule"]["locations"].reverse()
    assert normalize_study(raw_study) == normalize_study(shuffled)


def test_tolerates_sparse_records():
    r = normalize_study({"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}})
    assert r["nct_id"] == "NCT00000001"
    assert r["enrollment"] is None and r["sites"] == [] and r["phases"] == [] and r["lead_sponsor"] is None


def test_content_hash_ignores_bookkeeping_only(raw_study):
    base = normalize_study(raw_study)
    bumped = dict(base, last_update_posted="2030-01-01")
    edited = dict(base, overall_status="COMPLETED")
    assert content_hash(base) == content_hash(bumped)
    assert content_hash(base) != content_hash(edited)
