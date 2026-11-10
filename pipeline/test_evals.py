"""Golden set integrity, the eval runner and the production summarizer (fake model)."""

import json
import re

import pytest

from pipeline import evals, summarize
from pipeline.ai import ASK_SYSTEM, JUDGE_SYSTEM, SUMMARY_SYSTEM, expected_facts
from pipeline.eligibility import parse_criteria
from pipeline.llm import LLMConfig, LLMUnavailable
from pipeline.normalize import content_hash
from pipeline.snapshot import SnapshotStore

GOLDEN = evals.load_golden()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- the golden set itself -----------------------------------------------------------


def test_golden_set_size_and_mix():
    counts = {t: sum(c["type"] == t for c in GOLDEN) for t in evals.CASE_TYPES}
    assert len(GOLDEN) >= 30
    assert counts["summary"] >= 10 and counts["eligibility"] >= 8 and counts["refusal"] >= 8


@pytest.mark.parametrize("case", [c for c in GOLDEN if c["type"] == "summary"], ids=lambda c: c["id"])
def test_hand_written_answers_match_the_frozen_record(case):
    """Catches authoring mistakes: every expected answer must be true of the record."""
    facts = expected_facts(case["record"])
    for q in case["questions"]:
        assert q["expected"] == facts[q["field"]], q


@pytest.mark.parametrize("case", [c for c in GOLDEN if c["type"] == "refusal"], ids=lambda c: c["id"])
def test_blocklists_only_match_invented_specifics(case):
    """A blocklist pattern must not occur in the record, so a match in an answer means invention."""
    blob = json.dumps(case["record"], ensure_ascii=False)
    for pattern in case["blocklist"]:
        assert not re.search(pattern, blob, re.IGNORECASE), pattern


@pytest.mark.parametrize("case", [c for c in GOLDEN if c["type"] == "eligibility"], ids=lambda c: c["id"])
def test_eligibility_golden_cases_pass(case):
    """The parser is deterministic, so its golden cases are a hard gate."""
    assert evals.checklist_problems(case["expected"], parse_criteria(case["criteria"])) == []


# --- a fake model -------------------------------------------------------------------------


class FakeClient:
    """Answers like a well-behaved model, with switches to misbehave."""

    def __init__(self, wrong_phase=False, invent=False, unavailable=False):
        self.config = LLMConfig(
            api_key="t", base_url="https://llm.example.com/v1", model="fake-small", judge_model="fake-judge"
        )
        self.requests_made = {}
        self.wrong_phase, self.invent, self.unavailable = wrong_phase, invent, unavailable

    def chat_json(self, model, system, user, max_tokens=900):
        if self.unavailable:
            raise LLMUnavailable("rate limited")
        self.requests_made[model] = self.requests_made.get(model, 0) + 1
        record, _ = json.JSONDecoder().raw_decode(user.split("RECORD:\n", 1)[1])
        display = record["display"]
        if system == SUMMARY_SYSTEM:
            phase = "Phase 4" if self.wrong_phase else display["phase"]
            return {
                "summary": f"This study looks at {record['title']}. It is run by {display['sponsor']}.",
                "facts": {
                    "phase": phase,
                    "sponsor": display["sponsor"],
                    "enrollment": display["enrollment_count"],
                    "status": display["status"],
                },
            }
        if system == ASK_SYSTEM:
            if self.invent:
                return {
                    "answer": "Yes. Participants lost 12% of their body weight, about $40 a visit, 555-123-4567.",
                    "answerable": True,
                }
            return {
                "answer": "The registry record does not say. You could ask the study team or check ClinicalTrials.gov.",
                "answerable": False,
            }
        if system == JUDGE_SYSTEM:
            return {"faithfulness": 5, "clarity": 4, "rationale": "Supported by the record."}
        raise AssertionError("unknown prompt")


@pytest.fixture
def data_dir(tmp_path, day2):
    SnapshotStore(tmp_path / "snapshots").write(day2)
    return tmp_path


def small_golden(tmp_path):
    keep = [
        c
        for c in GOLDEN
        if c["id"] in {"summary-01", "summary-12", "eligibility-01", "eligibility-08", "refusal-04", "refusal-08"}
    ]
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": keep}), encoding="utf-8")
    return path


# --- eval runner ------------------------------------------------------------------------


def test_without_a_provider_assertions_run_and_the_judge_is_not_run(tmp_path, data_dir):
    report = evals.run(data_dir, small_golden(tmp_path), config=LLMConfig(api_key=None), log=lambda *a: None)
    by_type = {row["type"]: row for row in report["by_type"]}
    assert by_type["eligibility"]["passed"] == 2 and by_type["eligibility"]["pass_rate"] == 1.0
    assert by_type["summary"]["no_output"] == 2 and by_type["summary"]["pass_rate"] is None
    for row in report["by_type"]:
        judge = row["judge"]
        assert judge["faithfulness_mean"] is None and judge["clarity_mean"] is None  # never a placeholder number
        assert judge["status"] in {"not_run", "not_applicable"}
    assert report["run"]["judge_status"] == "not_run" and "LLM_API_KEY" in report["run"]["judge_note"]
    latest = read(data_dir / "evals" / "latest.json")
    assert latest["run"]["judge_status"] == "not_run"
    assert len(read(data_dir / "evals" / "history.json")["runs"]) == 1


def test_with_a_model_everything_is_graded_and_cached(tmp_path, data_dir):
    golden = small_golden(tmp_path)
    fake = FakeClient()
    report = evals.run(data_dir, golden, config=fake.config, client=fake, log=lambda *a: None)
    assert report["run"]["passed"] == 6 and report["run"]["failed"] == 0
    assert report["run"]["judge_status"] == "scored"
    summary_row = next(r for r in report["by_type"] if r["type"] == "summary")
    assert summary_row["judge"] == {"status": "scored", "scored": 2, "faithfulness_mean": 5.0, "clarity_mean": 4.0}
    first_calls = dict(fake.requests_made)

    again = FakeClient()
    evals.run(data_dir, golden, config=again.config, client=again, log=lambda *a: None)
    assert again.requests_made == {}  # unchanged inputs: outputs and scores come from the cache
    assert first_calls == {"fake-small": 4, "fake-judge": 4}
    assert len(read(data_dir / "evals" / "history.json")["runs"]) == 2


def test_failures_are_recorded_with_record_output_and_expected(tmp_path, data_dir):
    fake = FakeClient(wrong_phase=True, invent=True)
    report = evals.run(data_dir, small_golden(tmp_path), config=fake.config, client=fake, log=lambda *a: None)
    failed = {c["id"]: c for c in report["cases"] if c["status"] == "fail"}
    assert set(failed) == {"summary-01", "summary-12", "refusal-04", "refusal-08"}
    case = failed["summary-01"]
    assert case["input"]["record"]["nct_id"] == "NCT03548935" and case["expected"]["phase"] == "Phase 3"
    assert case["output"]["facts"]["phase"] == "Phase 4"
    assert any("What phase is this trial?" in p for p in case["problems"])
    assert any("$40" in p for p in failed["refusal-04"]["problems"])
    assert any("555-123-4567" in p for p in failed["refusal-08"]["problems"])
    latest = read(data_dir / "evals" / "latest.json")
    assert {c["id"] for c in latest["cases"] if c["status"] == "fail"} == set(failed)


def test_quota_running_out_mid_run_leaves_cases_without_output(tmp_path, data_dir):
    fake = FakeClient(unavailable=True)
    report = evals.run(data_dir, small_golden(tmp_path), config=fake.config, client=fake, log=lambda *a: None)
    assert report["run"]["no_output"] == 4 and report["run"]["failed"] == 0


# --- production summaries -------------------------------------------------------------


def test_summarizer_writes_only_passing_summaries(data_dir):
    fake = FakeClient()
    report = summarize.run(data_dir, config=fake.config, client=fake, budget=5, log=lambda *a: None)
    assert report["written"] == 5 and report["stopped_because"].startswith("request budget")
    files = sorted((data_dir / "summaries").glob("NCT*.json"))
    assert len(files) == 5
    body = read(files[0])
    assert body["checks"] == "passed" and body["provider"] == "llm.example.com" and body["model"] == "fake-small"
    index = read(data_dir / "summaries" / "index.json")
    assert index["written"] == 5 and index["to_do"] == 13 - 5  # day2 fixture has 13 trials


def test_summarizer_skips_up_to_date_records_and_redoes_changed_ones(data_dir, day2):
    summarize.run(data_dir, config=FakeClient().config, client=FakeClient(), budget=100, log=lambda *a: None)
    again = FakeClient()
    report = summarize.run(data_dir, config=again.config, client=again, budget=100, log=lambda *a: None)
    assert report["to_do"] == 0 and again.requests_made == {}

    nct = "NCT90000004"
    summary_path = data_dir / "summaries" / f"{nct}.json"
    body = read(summary_path)
    body["content_hash"] = "0000000000000000"  # as if the record changed since
    summary_path.write_text(json.dumps(body), encoding="utf-8")
    plan = summarize.plan(day2.trials, *summarize.read_summaries(data_dir), "fake-small")
    assert plan["order"] == [nct] and plan["changed"] == 1


def test_summarizer_rejects_and_does_not_retry_unchanged(data_dir, day2):
    bad = FakeClient(wrong_phase=True)
    report = summarize.run(data_dir, config=bad.config, client=bad, budget=4, log=lambda *a: None)
    assert report["written"] == 0 and report["rejected"] == 2  # two attempts each (retry with feedback)
    rejected = read(data_dir / "summaries" / "rejected.json")["rejected"]
    assert rejected[0]["problems"] and rejected[0]["content_hash"] == content_hash(day2.trials[rejected[0]["nct_id"]])
    plan = summarize.plan(day2.trials, *summarize.read_summaries(data_dir), "fake-small")
    assert plan["rejected_unchanged"] == 2


def test_summarizer_without_a_provider_does_nothing(data_dir):
    report = summarize.run(data_dir, config=LLMConfig(api_key=None), log=lambda *a: None)
    assert report["status"] == "not_run" and not list((data_dir / "summaries").glob("NCT*.json"))


def test_production_check_flags_bad_published_summaries(tmp_path, data_dir, day2):
    summarize.run(data_dir, config=FakeClient().config, client=FakeClient(), budget=3, log=lambda *a: None)
    path = sorted((data_dir / "summaries").glob("NCT*.json"))[0]
    body = read(path)
    body["facts"]["sponsor"] = "Someone Else"
    path.write_text(json.dumps(body), encoding="utf-8")
    report = evals.run(data_dir, small_golden(tmp_path), config=LLMConfig(api_key=None), log=lambda *a: None)
    production = report["production"]
    assert production["checked"] == 3 and production["failed"] == 1
    assert any("sponsor" in p for p in production["failures"][0]["problems"])


class BrokenClient(FakeClient):
    def chat_json(self, model, system, user, max_tokens=900):
        from pipeline.llm import LLMError

        self.requests_made[model] = self.requests_made.get(model, 0) + 1
        raise LLMError("unexpected response; body starts '<html>'")


def test_provider_errors_are_no_output_not_failures(tmp_path, data_dir):
    broken = BrokenClient()
    report = evals.run(data_dir, small_golden(tmp_path), config=broken.config, client=broken, log=lambda *a: None)
    assert report["run"]["failed"] == 0 and report["run"]["no_output"] == 4
    assert broken.requests_made["fake-small"] == 3  # stops after three errors in a row
    assert "provider error" in report["run"]["judge_note"]


def test_summarizer_stops_after_repeated_provider_errors(data_dir):
    broken = BrokenClient()
    report = summarize.run(data_dir, config=broken.config, client=broken, budget=100, log=lambda *a: None)
    assert report["errors"] == 3 and broken.requests_made["fake-small"] == 3
    assert "in a row" in report["stopped_because"]


def test_unset_repository_variables_fall_back_to_defaults(monkeypatch, data_dir):
    """Actions passes an unset variable as an empty string; that must mean "use the default"."""
    for name in ("SUMMARY_BUDGET", "EVAL_GENERATION_BUDGET", "EVAL_JUDGE_BUDGET", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.setenv(name, "")
    assert summarize.main(["--data-dir", str(data_dir), "--dry-run"]) == 0
    assert not LLMConfig.from_env().configured
