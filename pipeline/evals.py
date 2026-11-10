"""Eval runner for the AI summary layer and the eligibility parser.

    python -m pipeline.evals

Golden set: evals/golden.json (hand-written). Three case types:
    summary      a frozen trial record + questions whose answers are in the record
                 (phase, sponsor, enrollment, status). The summary must state them exactly.
    eligibility  a criteria block + the correct checklist. Deterministic, no LLM.
    refusal      a frozen record + a question it cannot answer. The answer must say so,
                 and must not contain any of the case's blocklisted specifics (patterns
                 checked to be absent from the record, so a match means invention).

Assertions are free and always run. The LLM judge (faithfulness and clarity, 1-5,
with a one-line rationale) runs only when LLM_API_KEY is set; otherwise every judge
field is reported as "not run", never estimated. Model outputs and judge scores
are cached under data/evals/cache and reused while their inputs are unchanged.

Also checks every published production summary against its current record.
Writes data/evals/latest.json and appends to data/evals/history.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .ai import (
    ASK_PROMPT_VERSION,
    JUDGE_PROMPT_VERSION,
    SUMMARY_PROMPT_VERSION,
    answer,
    assert_answer,
    assert_summary,
    expected_facts,
    input_hash,
    judge,
    prompt_record,
    summarize,
)
from .clock import now_iso
from .eligibility import parse_criteria
from .llm import PROVIDER_NAME, LLMClient, LLMConfig, LLMError, LLMUnavailable
from .normalize import content_hash
from .run import DATA_DIR, REPO_ROOT
from .snapshot import SnapshotStore
from .writers import read_eval_cache, read_summaries, write_eval_cache, write_eval_report

GOLDEN_PATH = REPO_ROOT / "evals" / "golden.json"
CASE_TYPES = ("summary", "eligibility", "refusal")
TYPE_LABELS = {
    "summary": "Summary faithfulness",
    "eligibility": "Eligibility parsing",
    "refusal": "Refusal and grounding",
}
QUESTION_FIELDS = ("phase", "sponsor", "enrollment", "status")
DEFAULT_GENERATION_BUDGET = 40
DEFAULT_JUDGE_BUDGET = 30
SUMMARY_TASK = "Write a plain-language summary of this trial for a public website."


class GoldenSetError(ValueError):
    pass


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    cases = data.get("cases")
    if data.get("schema_version") != 1 or not isinstance(cases, list):
        raise GoldenSetError("golden.json needs schema_version 1 and a list of cases")
    seen = set()
    for i, case in enumerate(cases):
        where = f"cases[{i}]"
        cid, ctype = case.get("id"), case.get("type")
        if not cid or cid in seen:
            raise GoldenSetError(f"{where}: missing or duplicate id")
        seen.add(cid)
        if ctype not in CASE_TYPES:
            raise GoldenSetError(f"{where}: type must be one of {CASE_TYPES}")
        if not case.get("title"):
            raise GoldenSetError(f"{where}: needs a title")
        if ctype in ("summary", "refusal") and not isinstance(case.get("record"), dict):
            raise GoldenSetError(f"{where}: needs a frozen record")
        if ctype == "summary":
            questions = case.get("questions") or []
            if not questions or any(q.get("field") not in QUESTION_FIELDS or "expected" not in q for q in questions):
                raise GoldenSetError(f"{where}: questions need a field in {QUESTION_FIELDS} and an expected answer")
        if ctype == "eligibility" and not ("criteria" in case and isinstance(case.get("expected"), dict)):
            raise GoldenSetError(f"{where}: needs criteria and an expected checklist")
        if ctype == "refusal" and not (case.get("question") and isinstance(case.get("blocklist"), list)):
            raise GoldenSetError(f"{where}: needs a question and a blocklist")
    return cases


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def checklist_problems(expected: dict, got: dict) -> list[str]:
    """Differences between the expected and parsed checklists, item by item."""
    problems = []
    if bool(expected.get("parsed")) != bool(got.get("parsed")):
        problems.append(f"parsed is {got.get('parsed')}, expected {expected.get('parsed')}")
    for key in ("inclusion", "exclusion"):
        want, have = expected.get(key, []), got.get(key, [])
        for i in range(max(len(want), len(have))):
            w = want[i] if i < len(want) else None
            g = have[i] if i < len(have) else None
            if w is None:
                problems.append(f"{key} item {i + 1}: unexpected extra item {g.get('text')!r}")
            elif g is None:
                problems.append(f"{key} item {i + 1}: missing {w.get('text')!r}")
            else:
                for field in ("text", "group", "sub_items"):
                    if w.get(field) != g.get(field):
                        problems.append(f"{key} item {i + 1} {field}: expected {w.get(field)!r}, got {g.get(field)!r}")
    if expected.get("notes", []) != got.get("notes", []):
        problems.append(f"notes: expected {expected.get('notes', [])!r}, got {got.get('notes', [])!r}")
    return problems


def record_excerpt(record: dict) -> dict:
    """What the failure gallery shows as "the record": the facts the AI saw, minus long text."""
    shown = prompt_record(record, text_limit=600)
    return {
        k: shown[k] for k in ("nct_id", "display", "title", "conditions", "interventions", "dates") if k in shown
    } | {
        "results_posted_on_clinicaltrials_gov": shown["results_posted_on_clinicaltrials_gov"],
        "why_stopped": shown.get("why_stopped"),
    }


class Runner:
    def __init__(
        self, data_dir: Path, config: LLMConfig, client: LLMClient | None, gen_budget: int, judge_budget: int, log
    ):
        self.data_dir = data_dir
        self.config = config
        self.client = client if client is not None else (LLMClient(config) if config.configured else None)
        self.gen_budget = gen_budget
        self.judge_budget = judge_budget
        self.log = log
        self.outputs = read_eval_cache(data_dir, "outputs")
        self.judged = read_eval_cache(data_dir, "judge")
        self.generator_down: str | None = None if self.client else "no LLM provider configured (LLM_API_KEY is not set)"
        self.judge_down: str | None = self.generator_down
        self.consecutive_errors = 0

    def _requests(self, model: str) -> int:
        return self.client.requests_made.get(model, 0) if self.client else 0

    def generate(self, key: str, prompt_version: str, call) -> tuple[dict | None, dict]:
        """Cached output for `key`, or a fresh one if the provider is available."""
        cached = self.outputs.get(key)
        if cached:
            return cached["output"], {
                "model": cached["model"],
                "prompt_version": cached["prompt_version"],
                "generated_at": cached["generated_at"],
                "cached": True,
            }
        if self.generator_down:
            return None, {"model": None, "prompt_version": prompt_version, "cached": False, "note": self.generator_down}
        if self._requests(self.config.model) >= self.gen_budget:
            return None, {
                "model": None,
                "prompt_version": prompt_version,
                "cached": False,
                "note": "generation budget reached",
            }
        try:
            output = call()
        except LLMUnavailable as exc:
            self.generator_down = str(exc)
            return None, {"model": None, "prompt_version": prompt_version, "cached": False, "note": str(exc)}
        except LLMError as exc:
            # The provider failed, the model never answered: that is "no output", not a failed check.
            self.consecutive_errors += 1
            if self.consecutive_errors >= 3:
                self.generator_down = f"provider errors in a row (last: {exc})"
            return None, {
                "model": None,
                "prompt_version": prompt_version,
                "cached": False,
                "note": f"provider error: {exc}",
            }
        self.consecutive_errors = 0
        stamp = now_iso()
        self.outputs[key] = {
            "key": key,
            "output": output,
            "model": self.config.model,
            "prompt_version": prompt_version,
            "generated_at": stamp,
        }
        return output, {
            "model": self.config.model,
            "prompt_version": prompt_version,
            "generated_at": stamp,
            "cached": False,
        }

    def score(self, case_id: str, record: dict, task: str, text: str) -> dict:
        key = _key(case_id, text, self.config.judge_model, JUDGE_PROMPT_VERSION)
        cached = self.judged.get(key)
        if cached:
            return {
                "status": "scored",
                "faithfulness": cached["faithfulness"],
                "clarity": cached["clarity"],
                "rationale": cached["rationale"],
                "model": cached["model"],
                "prompt_version": cached["prompt_version"],
            }
        if self.judge_down:
            return {"status": "not_run", "error": self.judge_down}
        if self._requests(self.config.judge_model) >= self.judge_budget:
            return {"status": "not_run", "error": "judge budget for this run reached"}
        try:
            scores = judge(self.client, record, task, text)
        except LLMUnavailable as exc:
            self.judge_down = str(exc)
            return {"status": "not_run", "error": str(exc)}
        except LLMError as exc:
            return {"status": "error", "error": str(exc)}
        self.judged[key] = {
            "key": key,
            **scores,
            "model": self.config.judge_model,
            "prompt_version": JUDGE_PROMPT_VERSION,
            "judged_at": now_iso(),
        }
        return {"status": "scored", **scores, "model": self.config.judge_model, "prompt_version": JUDGE_PROMPT_VERSION}

    # --- cases -----------------------------------------------------------------

    def run_case(self, case: dict, known_ids: set[str]) -> dict:
        base = {"id": case["id"], "type": case["type"], "title": case["title"], "nct_id": case.get("nct_id")}
        if case["type"] == "eligibility":
            got = parse_criteria(case["criteria"])
            problems = checklist_problems(case["expected"], got)
            return {
                **base,
                "input": {"criteria": case["criteria"]},
                "expected": case["expected"],
                "output": got,
                "output_meta": {"model": "pipeline/eligibility.py (deterministic)"},
                "status": "fail" if problems else "pass",
                "problems": problems,
                "judge": None,
            }

        record = case["record"]
        if case["type"] == "summary":
            key = _key("summary", input_hash(record), self.config.model, SUMMARY_PROMPT_VERSION)
            output, meta = self.generate(key, SUMMARY_PROMPT_VERSION, lambda: summarize(self.client, record))
            expected = {q["field"]: q["expected"] for q in case["questions"]}
            entry = {
                **base,
                "input": {"record": record_excerpt(record), "questions": [q["question"] for q in case["questions"]]},
                "expected": expected,
            }
            if output is None:
                return {
                    **entry,
                    "output": None,
                    "output_meta": meta,
                    "status": "no_output",
                    "problems": [],
                    "judge": None,
                }
            problems = assert_summary(record, output, known_ids)
            facts = output.get("facts") if isinstance(output.get("facts"), dict) else {}
            for q in case["questions"]:
                if facts.get(q["field"]) != q["expected"]:
                    problems.append(f"{q['question']} Expected {q['expected']!r}, stated {facts.get(q['field'])!r}")
            text = output.get("summary") if isinstance(output.get("summary"), str) else None
            verdict = self.score(case["id"], record, SUMMARY_TASK, text) if text else None
            return {
                **entry,
                "output": output,
                "output_meta": meta,
                "status": "fail" if problems else "pass",
                "problems": sorted(set(problems), key=problems.index),
                "judge": verdict,
            }

        # refusal
        question = case["question"]
        key = _key("ask", input_hash(record), question, self.config.model, ASK_PROMPT_VERSION)
        output, meta = self.generate(key, ASK_PROMPT_VERSION, lambda: answer(self.client, record, question))
        entry = {
            **base,
            "input": {"record": record_excerpt(record), "question": question},
            "expected": {"answerable": False, "must_not_contain": case["blocklist"], "why": case.get("why")},
        }
        if output is None:
            return {**entry, "output": None, "output_meta": meta, "status": "no_output", "problems": [], "judge": None}
        problems = assert_answer(output, expect_answerable=False, blocklist=case["blocklist"], known_ids=known_ids)
        text = output.get("answer") if isinstance(output.get("answer"), str) else None
        verdict = self.score(case["id"], record, f"Answer this question: {question}", text) if text else None
        return {
            **entry,
            "output": output,
            "output_meta": meta,
            "status": "fail" if problems else "pass",
            "problems": problems,
            "judge": verdict,
        }


def production_check(data_dir: Path, trials: dict[str, dict], known_ids: set[str]) -> dict:
    """Re-run the summary assertions on every published summary that is still current."""
    summaries, rejected = read_summaries(data_dir)
    checked = passed = stale = 0
    failures = []
    for nct, summary in sorted(summaries.items()):
        record = trials.get(nct)
        if record is None or summary.get("content_hash") != content_hash(record):
            stale += 1  # the trial page hides these until they are regenerated
            continue
        checked += 1
        problems = assert_summary(record, {"summary": summary.get("summary"), "facts": summary.get("facts")}, known_ids)
        if problems:
            failures.append({"nct_id": nct, "problems": problems, "summary": summary.get("summary")})
        else:
            passed += 1
    for nct, row in sorted(rejected.items()):
        failures.append(
            {
                "nct_id": nct,
                "problems": row.get("problems", []),
                "summary": (row.get("output") or {}).get("summary"),
                "rejected": True,
            }
        )
    return {
        "published": len(summaries),
        "checked": checked,
        "passed": passed,
        "failed": checked - passed,
        "stale": stale,
        "rejected": len(rejected),
        "failures": failures,
    }


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def run(
    data_dir: Path = DATA_DIR,
    golden_path: Path = GOLDEN_PATH,
    config: LLMConfig | None = None,
    client: LLMClient | None = None,
    gen_budget: int = DEFAULT_GENERATION_BUDGET,
    judge_budget: int = DEFAULT_JUDGE_BUDGET,
    log=print,
) -> dict:
    config = config or LLMConfig.from_env()
    cases = load_golden(golden_path)
    store = SnapshotStore(data_dir / "snapshots")
    dates = store.dates()
    trials = store.read(dates[-1]).trials if dates else {}
    known_ids = set(trials)
    runner = Runner(data_dir, config, client, gen_budget, judge_budget, log)
    results = [runner.run_case(case, known_ids) for case in cases]

    by_type = []
    for ctype in CASE_TYPES:
        rows = [r for r in results if r["type"] == ctype]
        passed = sum(r["status"] == "pass" for r in rows)
        failed = sum(r["status"] == "fail" for r in rows)
        scored = [r["judge"] for r in rows if r.get("judge") and r["judge"].get("status") == "scored"]
        if ctype == "eligibility":
            judge_summary = {"status": "not_applicable", "scored": 0, "faithfulness_mean": None, "clarity_mean": None}
        else:
            judge_summary = {
                "status": "scored" if scored else "not_run",
                "scored": len(scored),
                "faithfulness_mean": _mean([s["faithfulness"] for s in scored]),
                "clarity_mean": _mean([s["clarity"] for s in scored]),
            }
        graded = passed + failed
        by_type.append(
            {
                "type": ctype,
                "label": TYPE_LABELS[ctype],
                "cases": len(rows),
                "passed": passed,
                "failed": failed,
                "no_output": len(rows) - graded,
                "pass_rate": round(passed / graded, 4) if graded else None,
                "judge": judge_summary,
            }
        )

    llm_rows = [r for r in results if r["type"] != "eligibility"]
    notes = [(r.get("output_meta") or {}).get("note") for r in llm_rows if r["status"] == "no_output"]
    generation_note = next((n for n in notes if n), None)
    scored_any = any(r.get("judge") and r["judge"].get("status") == "scored" for r in llm_rows)
    judge_note = None if scored_any else (runner.judge_down or generation_note or "no responses to score")
    stamp = now_iso()
    report = {
        "run": {
            "run_at": stamp,
            "date": stamp[:10],
            "provider": PROVIDER_NAME,
            "model": config.model,
            "judge_model": config.judge_model,
            "prompt_versions": {"summary": SUMMARY_PROMPT_VERSION, "ask": ASK_PROMPT_VERSION},
            "judge_prompt_version": JUDGE_PROMPT_VERSION,
            "judge_status": "scored" if scored_any else "not_run",
            "judge_note": judge_note,
            "golden_cases": len(results),
            "passed": sum(r["status"] == "pass" for r in results),
            "failed": sum(r["status"] == "fail" for r in results),
            "no_output": sum(r["status"] == "no_output" for r in results),
            "requests": dict(runner.client.requests_made) if runner.client else {},
        },
        "by_type": by_type,
        "production": production_check(data_dir, trials, known_ids),
        "cases": results,
    }
    write_eval_cache(data_dir, "outputs", runner.outputs)
    write_eval_cache(data_dir, "judge", runner.judged)
    write_eval_report(data_dir, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    args = parser.parse_args(argv)
    report = run(
        args.data_dir,
        args.golden,
        gen_budget=int(os.environ.get("EVAL_GENERATION_BUDGET", DEFAULT_GENERATION_BUDGET)),
        judge_budget=int(os.environ.get("EVAL_JUDGE_BUDGET", DEFAULT_JUDGE_BUDGET)),
    )
    run_info = report["run"]
    print(f"Evals: {run_info['passed']} passed, {run_info['failed']} failed, {run_info['no_output']} without output")
    for row in report["by_type"]:
        rate = "n/a" if row["pass_rate"] is None else f"{row['pass_rate']:.0%}"
        judged = row["judge"]
        judge_text = (
            f"judge faithfulness {judged['faithfulness_mean']}, clarity {judged['clarity_mean']}"
            if judged["status"] == "scored"
            else f"judge {judged['status'].replace('_', ' ')}"
        )
        print(f"  {row['label']:24s} {row['passed']}/{row['cases']} pass ({rate}); {judge_text}")
    production = report["production"]
    print(
        f"Production summaries: {production['passed']}/{production['checked']} current summaries pass, "
        f"{production['rejected']} rejected, {production['stale']} stale"
    )
    failures = [c for c in report["cases"] if c["status"] == "fail"]
    on_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for case in failures:
        message = f"Eval case {case['id']} failed: {'; '.join(case['problems'])[:400]}"
        print(f"::warning title=Eval failure::{message}" if on_actions else f"FAIL {message}")
    if production["failed"] or production["rejected"]:
        message = f"{production['failed']} published summaries fail checks; {production['rejected']} rejected"
        print(f"::warning title=Summary checks::{message}" if on_actions else f"WARNING {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["CASE_TYPES", "checklist_problems", "expected_facts", "load_golden", "run"]
