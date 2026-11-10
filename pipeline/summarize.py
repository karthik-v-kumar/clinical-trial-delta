"""Plain-language AI summaries for trial pages.

    python -m pipeline.summarize              # within today's request budget
    python -m pipeline.summarize --dry-run    # list the work, call nothing

Runs after the daily refresh, on GitHub Actions only, never at page-view time.
It summarizes only trials whose record is new or has changed since its summary
was written (content hash), or whose summary prompt has changed, so the LLM is
never called for an unchanged record.

Work is done in priority order within a request budget, because the free GitHub
Models tier allows ~150 requests a day: changed records first, then trials that
are open or running, then the rest. Every output must pass the deterministic
assertions in pipeline/ai.py before it is published (data/summaries/<NCT>.json).
Outputs that fail twice are recorded in data/summaries/rejected.json and shown
on the Evals page; they are retried only when the record or prompt changes.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .ai import SUMMARY_PROMPT_VERSION, assert_summary, summarize
from .clock import now_iso
from .llm import PROVIDER_NAME, LLMClient, LLMConfig, LLMError, LLMUnavailable
from .normalize import content_hash
from .run import DATA_DIR
from .snapshot import SnapshotStore
from .writers import read_summaries, write_rejected_summaries, write_summary, write_summary_index

DEFAULT_BUDGET = 100  # requests per run; leaves room under the 150/day free tier for the evals
MAX_CONSECUTIVE_ERRORS = 3  # a provider that keeps failing is not retried 100 times
OPEN_STATUSES = {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING"}


def plan(trials: dict[str, dict], summaries: dict[str, dict], rejected: dict[str, dict], model: str) -> dict:
    """Which trials need a summary, in the order to do them."""
    changed, missing_open, missing_rest, current, blocked = [], [], [], 0, 0
    for nct, record in trials.items():
        digest = content_hash(record)
        existing = summaries.get(nct)
        if (
            existing
            and existing.get("content_hash") == digest
            and existing.get("prompt_version") == SUMMARY_PROMPT_VERSION
        ):
            current += 1
            continue
        failed = rejected.get(nct)
        if (
            failed
            and failed.get("content_hash") == digest
            and failed.get("prompt_version") == SUMMARY_PROMPT_VERSION
            and failed.get("model") == model
        ):
            blocked += 1  # already failed for this exact record, prompt and model
            continue
        if existing:
            changed.append(nct)
        elif record.get("overall_status") in OPEN_STATUSES:
            missing_open.append(nct)
        else:
            missing_rest.append(nct)

    def newest_first(ncts: list[str]) -> list[str]:
        def key(n: str):
            start = (trials[n].get("start_date") or {}).get("date") or ""
            return (start, n)

        return sorted(ncts, key=key, reverse=True)

    order = sorted(changed, reverse=True) + newest_first(missing_open) + newest_first(missing_rest)
    return {
        "order": order,
        "current": current,
        "changed": len(changed),
        "missing": len(missing_open) + len(missing_rest),
        "rejected_unchanged": blocked,
    }


def run(
    data_dir: Path = DATA_DIR,
    config: LLMConfig | None = None,
    client: LLMClient | None = None,
    budget: int = DEFAULT_BUDGET,
    dry_run: bool = False,
    log=print,
) -> dict:
    config = config or LLMConfig.from_env()
    store = SnapshotStore(data_dir / "snapshots")
    dates = store.dates()
    if not dates:
        return {"status": "no_data"}
    snap = store.read(dates[-1])
    known_ids = set(snap.trials)
    summaries, rejected = read_summaries(data_dir)
    work = plan(snap.trials, summaries, rejected, config.model)
    report = {
        "status": "not_run",
        "provider": PROVIDER_NAME,
        "model": config.model,
        "prompt_version": SUMMARY_PROMPT_VERSION,
        "snapshot_date": snap.snapshot_date,
        "trials": len(snap.trials),
        "up_to_date": work["current"],
        "to_do": len(work["order"]),
        "written": 0,
        "rejected": 0,
        "errors": 0,
        "requests": 0,
        "stopped_because": None,
    }
    log(
        f"Summaries: {work['current']} up to date, {work['changed']} for changed records, "
        f"{work['missing']} never written, {work['rejected_unchanged']} previously rejected (not retried)."
    )
    if dry_run:
        report["status"] = "dry_run"
        return report
    if not config.configured:
        report["stopped_because"] = "no LLM provider configured (LLM_API_KEY is not set)"
        log(f"Not run: {report['stopped_because']}.")
        write_summary_index(data_dir, report, now_iso())
        return report

    client = client or LLMClient(config)
    report["status"] = "ok"
    consecutive_errors = 0
    for nct in work["order"]:
        if client.requests_made.get(config.model, 0) >= budget:
            report["stopped_because"] = f"request budget of {budget} for this run reached"
            break
        record = snap.trials[nct]
        try:
            output = summarize(client, record)
            problems = assert_summary(record, output, known_ids)
            if problems and client.requests_made.get(config.model, 0) < budget:
                output = summarize(client, record, feedback=problems)
                problems = assert_summary(record, output, known_ids)
        except LLMUnavailable as exc:
            report["stopped_because"] = str(exc)
            log(f"Stopping: {exc}")
            break
        except LLMError as exc:
            report["errors"] += 1
            consecutive_errors += 1
            log(f"  {nct}: {exc}")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                report["stopped_because"] = f"{consecutive_errors} provider errors in a row (last: {exc})"
                log(f"Stopping: {report['stopped_because']}")
                break
            continue
        consecutive_errors = 0
        stamp = now_iso()
        if problems:
            rejected[nct] = {
                "nct_id": nct,
                "content_hash": content_hash(record),
                "model": config.model,
                "prompt_version": SUMMARY_PROMPT_VERSION,
                "attempted_at": stamp,
                "problems": problems,
                "output": output,
            }
            report["rejected"] += 1
            log(f"  {nct}: rejected ({'; '.join(problems)})")
            continue
        write_summary(
            data_dir,
            {
                "nct_id": nct,
                "content_hash": content_hash(record),
                "summary": output["summary"].strip(),
                "facts": output["facts"],
                "provider": PROVIDER_NAME,
                "model": config.model,
                "prompt_version": SUMMARY_PROMPT_VERSION,
                "generated_at": stamp,
                "checks": "passed",
            },
        )
        rejected.pop(nct, None)
        report["written"] += 1

    report["requests"] = client.requests_made.get(config.model, 0)
    report["to_do"] = len(work["order"]) - report["written"] - report["rejected"]  # left for later runs
    write_rejected_summaries(data_dir, rejected)
    write_summary_index(data_dir, report, now_iso())
    log(
        f"Wrote {report['written']} summaries, rejected {report['rejected']}, {report['errors']} errors, "
        f"{report['requests']} requests to {config.model}."
        + (f" Stopped: {report['stopped_because']}." if report["stopped_because"] else "")
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--budget", type=int, default=int(os.environ.get("SUMMARY_BUDGET", DEFAULT_BUDGET)))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run(args.data_dir, budget=args.budget, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
