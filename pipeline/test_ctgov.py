import json
import urllib.parse

import pytest

from pipeline.config import Query
from pipeline.fetch.ctgov import (
    Client,
    FetchError,
    FetchIntegrityError,
    api_params,
    essie_or,
    web_params,
    web_url,
)

Q = Query(
    id="q",
    label="test",
    collection_id="c",
    cond=("Obesity", "Overweight"),
    intr=("semaglutide", "GLP-1"),
    spons=("Novo Nordisk",),
    study_type="INTERVENTIONAL",
)


def study(nct):
    return {"protocolSection": {"identificationModule": {"nctId": nct}}}


class FakeTransport:
    """Scripted responses; records every URL requested."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        status, body, headers = self.responses.pop(0)
        return status, headers, json.dumps(body).encode() if not isinstance(body, bytes) else body


def client(transport):
    return Client(transport=transport, min_interval=0, sleep=lambda s: None)


def params_of(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def test_essie_quoting():
    assert essie_or(["semaglutide", "GLP-1", "Diabetes Mellitus, Type 2", "SGLT2"]) == (
        'semaglutide OR "GLP-1" OR "Diabetes Mellitus, Type 2" OR SGLT2'
    )


def test_api_and_web_params_describe_the_same_search():
    assert api_params(Q) == {
        "query.cond": "Obesity OR Overweight",
        "query.intr": 'semaglutide OR "GLP-1"',
        "query.spons": '"Novo Nordisk"',
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
    }
    assert web_params(Q) == {
        "cond": "Obesity OR Overweight",
        "intr": 'semaglutide OR "GLP-1"',
        "spons": '"Novo Nordisk"',
        "aggFilters": "studyType:int",
    }
    url = web_url(Q)
    assert url.startswith("https://clinicaltrials.gov/search?cond=Obesity%20OR%20Overweight")
    assert params_of(url) == web_params(Q)


def test_pages_until_no_token_and_checks_total():
    t = FakeTransport(
        [
            (200, {"totalCount": 3, "studies": [study("NCT1"), study("NCT2")], "nextPageToken": "tok"}, {}),
            (200, {"studies": [study("NCT3")]}, {}),
        ]
    )
    result = client(t).fetch_query(Q)
    assert [s["protocolSection"]["identificationModule"]["nctId"] for s in result.studies] == ["NCT1", "NCT2", "NCT3"]
    assert result.total_count == 3 and result.pages == 2
    first, second = params_of(t.urls[0]), params_of(t.urls[1])
    assert first["countTotal"] == "true" and first["pageSize"] == "1000" and "pageToken" not in first
    assert second["pageToken"] == "tok"
    # Never request personal data: no whole contacts module, no contact/official/responsible-party pieces.
    fields = first["fields"].split(",")
    assert "protocolSection.contactsLocationsModule" not in fields
    assert not [
        f
        for f in fields
        if f.rsplit(".", 1)[-1] in {"contacts", "centralContacts", "overallOfficials", "responsibleParty"}
    ]
    assert "protocolSection.sponsorCollaboratorsModule" not in fields


def test_count_mismatch_retries_then_fails():
    short = (200, {"totalCount": 3, "studies": [study("NCT1"), study("NCT2")]}, {})
    t = FakeTransport([short, short])
    with pytest.raises(FetchIntegrityError):
        client(t).fetch_query(Q)
    assert len(t.urls) == 2


def test_count_mismatch_recovers_on_retry():
    t = FakeTransport(
        [
            (200, {"totalCount": 2, "studies": [study("NCT1")]}, {}),
            (200, {"totalCount": 2, "studies": [study("NCT1"), study("NCT2")]}, {}),
        ]
    )
    assert len(client(t).fetch_query(Q).studies) == 2


def test_duplicates_across_pages_are_collapsed():
    t = FakeTransport(
        [
            (200, {"totalCount": 2, "studies": [study("NCT1"), study("NCT2")], "nextPageToken": "x"}, {}),
            (200, {"studies": [study("NCT2")]}, {}),
        ]
    )
    assert len(client(t).fetch_query(Q).studies) == 2


def test_retries_server_errors_and_rate_limits():
    delays = []
    t = FakeTransport(
        [
            (503, b"busy", {}),
            (429, b"slow down", {"Retry-After": "7"}),
            (200, {"apiVersion": "2.0.5", "dataTimestamp": "2026-01-01T00:00:00"}, {}),
        ]
    )
    c = Client(transport=t, min_interval=0, sleep=delays.append)
    assert c.version()["apiVersion"] == "2.0.5"
    assert c.requests_made == 3
    assert 7 <= delays[1] < 8  # honoured Retry-After


def test_client_errors_fail_fast():
    t = FakeTransport([(400, b"bad query", {})])
    with pytest.raises(FetchError):
        client(t).version()
    assert len(t.urls) == 1


def test_gives_up_after_max_attempts():
    t = FakeTransport([(500, b"", {})] * 5)
    with pytest.raises(FetchError):
        client(t).version()
    assert len(t.urls) == 5


def test_pacing_waits_between_requests():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    ok = (200, {"apiVersion": "x"}, {})
    c = Client(transport=FakeTransport([ok, ok]), min_interval=1.0, sleep=sleep, clock=lambda: now[0])
    c.version()
    now[0] += 0.25
    c.version()
    assert slept == [pytest.approx(0.75)]


def test_web_count_uses_website_parameters():
    t = FakeTransport([(200, {"total": 42, "hits": []}, {})])
    assert client(t).web_count(Q) == 42
    p = params_of(t.urls[0])
    assert t.urls[0].startswith("https://clinicaltrials.gov/api/int/studies?")
    assert p["aggFilters"] == "studyType:int" and p["cond"] == "Obesity OR Overweight"
