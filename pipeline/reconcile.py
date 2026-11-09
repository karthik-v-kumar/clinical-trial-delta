"""Acceptance check: do our trial counts match ClinicalTrials.gov's own counts?

    python -m pipeline.reconcile             # check the latest snapshot
    python -m pipeline.reconcile --no-web    # API v2 counts only

For every watchlist query this compares three numbers:
    pipeline  trials the pipeline stored in the snapshot for that query
    api       API v2 totalCount for the same parameters, asked now
    web       the count clinicaltrials.gov/search shows for the same search
              (Condition / Intervention / Sponsor boxes + the "Interventional"
              study-type checkbox), read from the search page's own backend

Writes data/reconciliation.json and prints a table. Exits 1 if a count differs
and the registry has NOT been refreshed since the snapshot (i.e. the difference
cannot be explained by newer registry data).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .clock import now_iso
from .config import WATCHLIST_PATH, load_watchlist
from .fetch.ctgov import Client, FetchError, web_url
from .run import DATA_DIR
from .snapshot import SnapshotStore
from .writers import write_json


def _read_manifest(data_dir: Path, date: str | None) -> dict:
    store = SnapshotStore(data_dir / "snapshots")
    dates = store.dates()
    if not dates:
        raise SystemExit("no snapshots yet; run `python -m pipeline.run daily` first")
    return store.read_manifest(date or dates[-1])


def reconcile(
    data_dir: Path = DATA_DIR, date: str | None = None, web: bool = True, client: Client | None = None
) -> dict:
    wl = load_watchlist(WATCHLIST_PATH)
    manifest = _read_manifest(data_dir, date)
    client = client or Client()
    now_ts = client.version().get("dataTimestamp")
    snap_ts = manifest.get("source", {}).get("data_timestamp")
    refreshed = now_ts != snap_ts

    rows, collections = [], []
    for c in wl.collections:
        state = manifest["collections"].get(c.id)
        if state is None:
            continue
        for q in c.queries:
            pipeline_n = len(state["queries"].get(q.id, []))
            api_n = client.count(q)
            web_n, web_error = None, None
            if web:
                try:
                    web_n = client.web_count(q)
                except (FetchError, KeyError, ValueError) as exc:
                    web_error = str(exc)[:200]
            reference = web_n if web_n is not None else api_n
            rows.append(
                {
                    "query": q.id,
                    "collection": c.id,
                    "label": q.label,
                    "pipeline": pipeline_n,
                    "api_at_fetch": state.get("api_totals", {}).get(q.id),
                    "api_now": api_n,
                    "web_now": web_n,
                    "web_error": web_error,
                    "delta_vs_web": None if web_n is None else pipeline_n - web_n,
                    "match": pipeline_n == reference,
                    "web_url": web_url(q),
                }
            )
        per_query = [set(ids) for ids in state["queries"].values()]
        union = set().union(*per_query) if per_query else set()
        collections.append(
            {
                "collection": c.id,
                "trials": len(union),
                "sum_of_query_counts": sum(len(s) for s in per_query),
                "overlap": sum(len(s) for s in per_query) - len(union),
            }
        )

    mismatches = [r for r in rows if not r["match"]]
    if not mismatches:
        verdict = "all counts match"
    elif refreshed:
        verdict = (
            f"{len(mismatches)} count(s) differ; the registry was refreshed since the snapshot "
            f"({snap_ts} -> {now_ts}), which explains differences until the next daily run"
        )
    else:
        verdict = f"{len(mismatches)} count(s) differ with no registry refresh since the snapshot: investigate"
    return {
        "checked_at": now_iso(),
        "snapshot_date": manifest["snapshot_date"],
        "snapshot_data_timestamp": snap_ts,
        "registry_data_timestamp_now": now_ts,
        "registry_refreshed_since_snapshot": refreshed,
        "verdict": verdict,
        "unexplained_mismatch": bool(mismatches) and not refreshed,
        "queries": rows,
        "collections": collections,
    }


def as_markdown(report: dict) -> str:
    lines = [
        f"Snapshot {report['snapshot_date']} (registry data {report['snapshot_data_timestamp']}), "
        f"checked {report['checked_at']} (registry data {report['registry_data_timestamp_now']})",
        "",
        "| Query | Pipeline | ClinicalTrials.gov website | API v2 totalCount | Delta |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in report["queries"]:
        web = r["web_now"] if r["web_now"] is not None else f"n/a ({r['web_error']})"
        delta = r["delta_vs_web"] if r["delta_vs_web"] is not None else r["pipeline"] - r["api_now"]
        lines.append(f"| [{r['query']}]({r['web_url']}) | {r['pipeline']} | {web} | {r['api_now']} | {delta:+d} |")
    lines += [
        "",
        "| Collection | Trials (deduplicated) | Sum of query counts | In both queries |",
        "|---|---:|---:|---:|",
    ]
    for c in report["collections"]:
        lines.append(f"| {c['collection']} | {c['trials']} | {c['sum_of_query_counts']} | {c['overlap']} |")
    lines += ["", f"Verdict: {report['verdict']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="snapshot date to check (default: latest)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--no-web", action="store_true", help="skip the website count")
    args = parser.parse_args(argv)
    report = reconcile(args.data_dir, args.date, web=not args.no_web)
    write_json(args.data_dir / "reconciliation.json", report, indent=2)
    table = as_markdown(report)
    print(table)
    # On GitHub Actions: show the table on the run page and flag mismatches.
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("## Count reconciliation\n\n" + table + "\n")
    if os.environ.get("GITHUB_ACTIONS") == "true" and report["verdict"] != "all counts match":
        print(f"::warning title=Count reconciliation::{report['verdict']}")
    return 1 if report["unexplained_mismatch"] else 0


if __name__ == "__main__":
    sys.exit(main())
