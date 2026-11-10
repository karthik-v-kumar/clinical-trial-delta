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


BASE = "https://llm.example.com/v1"
MODEL = "writer-small"


def client(replies, **config):
    cfg = LLMConfig(api_key="token", base_url=BASE, model=MODEL, **config)
    return LLMClient(cfg, transport=Transport(replies), sleep=lambda s: None, clock=lambda: 0.0)


def test_config_comes_only_from_settings():
    env = {"LLM_API_KEY": " abc ", "LLM_BASE_URL": "https://api.example.org/openai/", "LLM_MODEL": "m1"}
    cfg = LLMConfig.from_env(env)
    assert cfg.configured and cfg.api_key == "abc" and cfg.base_url == "https://api.example.org/openai"
    assert cfg.model == "m1" and cfg.judge_model == "m1"  # the judge defaults to the writing model
    assert cfg.provider_name == "api.example.org" and cfg.min_interval == 6.0
    named = LLMConfig.from_env(env | {"JUDGE_MODEL": "m2", "LLM_PROVIDER": "Example AI", "LLM_MIN_INTERVAL": "2"})
    assert named.judge_model == "m2" and named.provider_name == "Example AI" and named.min_interval == 2.0


def test_nothing_is_assumed_when_settings_are_missing():
    """No built-in provider: without a key, a URL and a model, every LLM step is "not run"."""
    assert not LLMConfig.from_env({}).configured
    only_key = LLMConfig.from_env({"LLM_API_KEY": "abc"})
    assert not only_key.configured
    assert only_key.not_configured_reason == "no LLM provider configured (LLM_BASE_URL, LLM_MODEL not set)"
    assert "LLM_API_KEY" in LLMConfig.from_env({}).not_configured_reason


def test_request_shape_and_json_reply():
    c = client([(200, {"answer": "ok", "answerable": True}, {})])
    assert c.chat_json(MODEL, "sys", "user") == {"answer": "ok", "answerable": True}
    url, headers, body = c.transport.calls[0]
    assert url == "https://llm.example.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer token"
    assert body["model"] == MODEL and body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_reasoning_effort_is_sent_only_when_set():
    c = client([(200, {"a": 1}, {})], reasoning_effort="low")
    c.chat_json(MODEL, "s", "u")
    assert c.transport.calls[0][2]["reasoning_effort"] == "low"


def test_unconfigured_client_refuses_to_call():
    c = LLMClient(LLMConfig(api_key=None), transport=Transport([]))
    with pytest.raises(LLMUnavailable):
        c.chat_json("m", "s", "u")


def test_short_rate_limit_waits_and_retries():
    waits = []
    c = client([(429, {"error": "slow"}, {"Retry-After": "20"}), (200, {"x": 1}, {})])
    c.sleep = waits.append
    assert c.chat_json(MODEL, "s", "u") == {"x": 1}
    assert 21 in waits


def test_retry_delay_in_the_error_body_is_honoured():
    waits = []
    body = {"error": {"code": 429, "details": [{"retryDelay": "12s"}]}}
    c = client([(429, body, {}), (200, {"x": 1}, {})])
    c.sleep = waits.append
    assert c.chat_json(MODEL, "s", "u") == {"x": 1}
    assert 13 in waits


def test_daily_limit_stops_that_model_for_the_run():
    c = client([(429, {"error": "daily"}, {"Retry-After": "40000"})])
    with pytest.raises(LLMUnavailable):
        c.chat_json(MODEL, "s", "u")
    with pytest.raises(LLMUnavailable):  # no second request is made
        c.chat_json(MODEL, "s", "u")
    assert len(c.transport.calls) == 1


def test_auth_failure_is_unavailable_and_bad_request_is_an_error():
    with pytest.raises(LLMUnavailable):
        client([(403, {"error": "no models permission"}, {})]).chat_json("m", "s", "u")
    with pytest.raises(LLMError):
        client([(400, {"error": "bad"}, {})]).chat_json("m", "s", "u")


@pytest.mark.parametrize("status", [404, 410])
def test_missing_endpoint_or_model_stops_the_run(status):
    with pytest.raises(LLMUnavailable, match="not available"):
        client([(status, {"error": "gone"}, {})]).chat_json("m", "s", "u")


def test_a_retired_endpoint_answering_ok_stops_the_run():
    """GitHub Models, since its retirement, answers HTTP 200 with a plain-text "OK"."""
    c = client([(200, b"OK\r\n", {"Content-Type": "text/plain"})])
    with pytest.raises(LLMUnavailable, match="not a chat completion.*text/plain.*OK"):
        c.chat_json(MODEL, "s", "u")
    assert len(c.transport.calls) == 1


def test_json_mode_is_dropped_if_the_provider_rejects_it():
    c = client([(400, {"error": "response_format json_object is not supported"}, {}), (200, {"a": 1}, {})])
    assert c.chat_json(MODEL, "s", "u") == {"a": 1}
    first, second = (call[2] for call in c.transport.calls)
    assert "response_format" in first and "response_format" not in second


def test_a_reply_cut_off_at_max_tokens_says_so():
    reply = {"choices": [{"message": {"content": '{"summary": "This study'}, "finish_reason": "length"}]}
    with pytest.raises(LLMError, match="cut off"):
        client([(200, reply, {})]).chat_json(MODEL, "s", "u")


def test_pacing_respects_per_minute_limits():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    c = client([(200, {"a": 1}, {}), (200, {"a": 2}, {})])
    c.sleep, c.clock = sleep, lambda: now[0]
    c.chat_json(MODEL, "s", "u")
    c.chat_json(MODEL, "s", "u")
    assert slept == [pytest.approx(6.0)]  # 10 requests a minute unless LLM_MIN_INTERVAL says otherwise


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


def test_event_stream_and_gzip_bodies_are_understood():
    import gzip as gz

    from pipeline.llm import _decode, extract_content

    stream = b'data: {"choices":[{"delta":{"content":"{\\"a\\":"}}]}\n\ndata: {"choices":[{"delta":{"content":" 1}"}}]}\n\ndata: [DONE]\n'
    assert parse_json_object(extract_content({}, stream)) == {"a": 1}
    body = json.dumps({"choices": [{"message": {"content": '{"b": 2}'}}]}).encode()
    assert parse_json_object(extract_content({}, _decode({"Content-Encoding": "gzip"}, gz.compress(body)))) == {"b": 2}


def test_unreadable_body_error_says_what_came_back():
    from pipeline.llm import NotAChatCompletion, extract_content

    with pytest.raises(NotAChatCompletion, match="text/html.*<html>"):
        extract_content({"Content-Type": "text/html"}, b"<html>Sign in</html>")


def test_redirect_is_reported_not_followed():
    c = client([(301, b"", {"Location": "https://example.com/elsewhere"})])
    with pytest.raises(LLMUnavailable, match="redirected"):
        c.chat_json("m", "s", "u")


class ProbeTransport:
    """GET /models and POST /chat/completions, as an OpenAI-compatible provider answers them."""

    def __init__(self, models_status=200):
        self.models_status = models_status
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, body))
        if body is None:
            payload = {"data": [{"id": "models/writer-small"}, {"id": "models/judge-large"}]}
            return self.models_status, {}, json.dumps(payload).encode()
        reply = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}], "usage": {}}
        return 200, {"Content-Type": "application/json"}, json.dumps(reply).encode()


def test_probe_lists_models_when_no_model_is_chosen_yet(capsys):
    from pipeline.llm import probe

    transport = ProbeTransport()
    assert probe(LLMConfig(api_key="k", base_url=BASE), transport) == 0
    out = capsys.readouterr().out
    assert "writer-small" in out and "judge-large" in out and "set the LLM_MODEL variable" in out
    assert transport.calls == [(f"{BASE}/models", None)]  # no chat request without a model


def test_probe_checks_both_models(capsys):
    from pipeline.llm import probe

    transport = ProbeTransport()
    assert (
        probe(
            LLMConfig(api_key="secret-key", base_url=BASE, model="writer-small", judge_model="judge-large"), transport
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "OK: writer-small works." in out and "OK: judge-large works." in out and "secret-key" not in out
    assert len(transport.calls) == 3


def test_probe_needs_a_key_and_a_url(capsys):
    from pipeline.llm import probe

    assert probe(LLMConfig(api_key=None), ProbeTransport()) == 1
    assert "LLM_API_KEY" in capsys.readouterr().out


def test_overloaded_model_is_retried_with_longer_waits():
    waits = []
    c = client([(503, {"error": "high demand"}, {}), (503, {"error": "high demand"}, {}), (200, {"a": 1}, {})])
    c.sleep = waits.append
    assert c.chat_json(MODEL, "s", "u") == {"a": 1}
    backoffs = [round(w) for w in waits if round(w) != 6]  # 6 s waits are the normal pacing
    assert backoffs == [10, 20]


def test_persistent_overload_error_says_what_the_provider_said():
    c = client([(503, {"error": "This model is currently experiencing high demand."}, {})] * 4)
    with pytest.raises(LLMError, match="503.*high demand"):
        c.chat_json(MODEL, "s", "u")


def test_probe_tries_candidate_models_without_changing_settings(capsys, monkeypatch):
    from pipeline.llm import probe

    monkeypatch.setenv("LLM_PROBE_MODELS", "candidate-a  candidate-b writer-small")
    transport = ProbeTransport()
    assert probe(LLMConfig(api_key="k", base_url=BASE, model="writer-small"), transport) == 0
    tested = [json.loads(body)["model"] for _, body in transport.calls if body]
    assert tested == ["writer-small", "candidate-a", "candidate-b"]
