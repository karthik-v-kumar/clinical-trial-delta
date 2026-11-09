"""LLM client, prompts and deterministic assertions. No network: scripted transports."""

import json
import re

import pytest

from pipeline.ai import (
    SUMMARY_PROMPT_VERSION,
    assert_answer,
    assert_summary,
    expected_facts,
    judge,
    prompt_record,
    record_block,
)
from pipeline.llm import LLMClient, LLMConfig, LLMError, LLMUnavailable, parse_json_object
from pipeline.normalize import normalize_study


@pytest.fixture
def record(raw_study):
    return normalize_study(raw_study)


def good_summary(record, **overrides):
    facts = expected_facts(record)
    output = {
        "summary": "This study tests exampleglutide, a weekly injection, in 600 adults with obesity. "
        "It is a Phase 2/3 trial run by Hoffmann-La Roche and is recruiting now.",
        "facts": dict(facts),
    }
    output.update(overrides)
    return output


# --- client ------------------------------------------------------------------------


class Transport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, json.loads(body)))
        status, payload, hdrs = self.replies.pop(0)
        if isinstance(payload, dict) and "choices" not in payload and status == 200:
            payload = {"choices": [{"message": {"content": json.dumps(payload)}}]}
        return status, hdrs, json.dumps(payload).encode() if not isinstance(payload, bytes) else payload


def client(replies, **config):
    cfg = LLMConfig(api_key="token", **config)
    return LLMClient(cfg, transport=Transport(replies), sleep=lambda s: None, clock=lambda: 0.0)


def test_config_from_env_defaults():
    cfg = LLMConfig.from_env({"LLM_API_KEY": " abc "})
    assert cfg.configured and cfg.api_key == "abc"
    assert cfg.base_url == "https://models.github.ai/inference"
    assert cfg.model == "openai/gpt-4.1-mini" and cfg.judge_model == "openai/gpt-4.1"
    assert not LLMConfig.from_env({}).configured


def test_request_shape_and_json_reply():
    c = client([(200, {"answer": "ok", "answerable": True}, {})])
    assert c.chat_json("openai/gpt-4.1-mini", "sys", "user") == {"answer": "ok", "answerable": True}
    url, headers, body = c.transport.calls[0]
    assert url == "https://models.github.ai/inference/chat/completions"
    assert headers["Authorization"] == "Bearer token"
    assert body["model"] == "openai/gpt-4.1-mini" and body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_unconfigured_client_refuses_to_call():
    c = LLMClient(LLMConfig(api_key=None), transport=Transport([]))
    with pytest.raises(LLMUnavailable):
        c.chat_json("m", "s", "u")


def test_short_rate_limit_waits_and_retries():
    waits = []
    c = client([(429, {"error": "slow"}, {"Retry-After": "20"}), (200, {"x": 1}, {})])
    c.sleep = waits.append
    assert c.chat_json("openai/gpt-4.1-mini", "s", "u") == {"x": 1}
    assert 21 in waits


def test_daily_limit_stops_that_model_for_the_run():
    c = client([(429, {"error": "daily"}, {"Retry-After": "40000"})])
    with pytest.raises(LLMUnavailable):
        c.chat_json("openai/gpt-4.1-mini", "s", "u")
    with pytest.raises(LLMUnavailable):  # no second request is made
        c.chat_json("openai/gpt-4.1-mini", "s", "u")
    assert len(c.transport.calls) == 1


def test_auth_failure_is_unavailable_and_bad_request_is_an_error():
    with pytest.raises(LLMUnavailable):
        client([(403, {"error": "no models permission"}, {})]).chat_json("m", "s", "u")
    with pytest.raises(LLMError):
        client([(400, {"error": "bad"}, {})]).chat_json("m", "s", "u")


def test_pacing_respects_per_minute_limits():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    c = client([(200, {"a": 1}, {}), (200, {"a": 2}, {})])
    c.sleep, c.clock = sleep, lambda: now[0]
    c.chat_json("openai/gpt-4.1-mini", "s", "u")
    c.chat_json("openai/gpt-4.1-mini", "s", "u")
    assert slept == [pytest.approx(4.5)]  # 15 requests a minute on the low tier


@pytest.mark.parametrize(
    "text, expected",
    [('{"a": 1}', {"a": 1}), ('```json\n{"a": 1}\n```', {"a": 1}), ('Sure! {"a": 1} Hope that helps.', {"a": 1})],
)
def test_parse_json_object(text, expected):
    assert parse_json_object(text) == expected


def test_parse_json_object_rejects_non_objects():
    with pytest.raises(LLMError):
        parse_json_object("[1, 2]")
    with pytest.raises(LLMError):
        parse_json_object("no json here")


# --- prompts -------------------------------------------------------------------------


def test_prompt_record_gives_display_values_and_no_sites(record):
    shown = prompt_record(record)
    assert shown["display"] == {
        "phase": "Phase 2/3",
        "status": "Recruiting",
        "sponsor": "Hoffmann-La Roche",
        "enrollment_count": 600,
        "enrollment_type": "target",
    }
    assert "sites" not in shown and "Seattle" not in json.dumps(shown)


def test_record_block_fits_the_request_limit(record):
    record = dict(record)
    record["eligibility"] = dict(record["eligibility"], criteria="x " * 20000)
    record["brief_summary"] = "y " * 20000
    assert len(record_block(record, 8000)) <= 8000


def test_prompt_versions_change_when_prompts_change():
    assert re.fullmatch(r"summary-v1-[0-9a-f]{6}", SUMMARY_PROMPT_VERSION)


def test_judge_validates_scores(record):
    good = client([(200, {"faithfulness": 5, "clarity": "4", "rationale": " Clear and supported. "}, {})])
    assert judge(good, record, "task", "text") == {"faithfulness": 5, "clarity": 4, "rationale": "Clear and supported."}
    with pytest.raises(LLMError):
        judge(client([(200, {"faithfulness": 7, "clarity": 3}, {})]), record, "task", "text")


# --- assertions ------------------------------------------------------------------------


def test_good_summary_passes(record):
    assert assert_summary(record, good_summary(record), known_ids={"NCT90000099"}) == []


@pytest.mark.parametrize(
    "change, problem",
    [
        (
            {"facts": {"phase": "Phase 3", "sponsor": "Hoffmann-La Roche", "enrollment": 600, "status": "Recruiting"}},
            "stated phase",
        ),
        (
            {"facts": {"phase": "Phase 2/3", "sponsor": "Roche", "enrollment": 600, "status": "Recruiting"}},
            "stated sponsor",
        ),
        (
            {
                "facts": {
                    "phase": "Phase 2/3",
                    "sponsor": "Hoffmann-La Roche",
                    "enrollment": 650,
                    "status": "Recruiting",
                }
            },
            "stated enrollment",
        ),
        ({"summary": "A Phase 3 study of exampleglutide in adults with obesity, run by Hoffmann-La Roche."}, "phase 3"),
        (
            {"summary": "This study of exampleglutide will enroll about 2,000 people with obesity across many sites."},
            "2,000 people",
        ),
        (
            {"summary": "Like NCT1234 and other trials, this one studies exampleglutide in adults with obesity."},
            "malformed",
        ),
        (
            {"summary": "This follows NCT12345678, testing exampleglutide in adults living with obesity worldwide."},
            "not in our data",
        ),
        ({"summary": "Too short."}, "characters"),
    ],
)
def test_summary_assertions_catch_wrong_facts(record, change, problem):
    problems = assert_summary(record, good_summary(record, **change), known_ids={"NCT90000099", "NCT00000001"})
    assert any(problem in p for p in problems), problems


def test_phase_mentions_in_words_are_checked(record):
    ok = good_summary(
        record, summary="This Phase II/III study tests exampleglutide, a weekly shot, in adults with obesity."
    )
    assert assert_summary(record, ok) == []
    wrong = good_summary(
        record, summary="This phase I study tests exampleglutide, a weekly shot, in adults with obesity today."
    )
    assert assert_summary(record, wrong)


def test_age_ranges_are_not_mistaken_for_enrollment(record):
    text = "This study is for adults aged 18 to 75 years with obesity. It plans to enroll 600 people and is run by Hoffmann-La Roche."
    assert assert_summary(record, good_summary(record, summary=text)) == []


def test_missing_enrollment_must_be_null(record):
    record = dict(record, enrollment=None)
    out = good_summary(record, summary="A study of exampleglutide for adults with obesity, run by Hoffmann-La Roche.")
    assert assert_summary(record, out) == []
    out["facts"]["enrollment"] = 0
    assert assert_summary(record, out)


def test_refusal_assertions():
    fine = {"answer": "The record does not list any costs or payments. Ask the study team.", "answerable": False}
    assert assert_answer(fine, expect_answerable=False, blocklist=[r"[$€£]\s?\d+"]) == []
    invented = {"answer": "Participants receive $50 per visit.", "answerable": False}
    assert any("$50" in p for p in assert_answer(invented, expect_answerable=False, blocklist=[r"[$€£]\s?\d+"]))
    claims = {"answer": "It costs nothing.", "answerable": True}
    assert any("answerable" in p for p in assert_answer(claims, expect_answerable=False))
