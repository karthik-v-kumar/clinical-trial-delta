"""Daily refresh: fetch -> normalize -> snapshot -> diff -> write data/.

    python -m pipeline.run                 # today's snapshot (UTC date)
    python -m pipeline.run --dry-run       # fetch and diff, write nothing

One snapshot per day. If today's snapshot already exists the run is a no-op,
so re-running the workflow is always safe. Any fetch failure or count mismatch
aborts before anything is written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .config import WATCHLIST_PATH, Watchlist, load_watchlist
from .ctgov import Client
from .diff import diff_snapshots
from .normalize import normalize_study
from .snapshot import CollectionState, Snapshot, SnapshotStore
from .writers import update_changes, write_collections, write_meta, write_trials

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_snapshot(wl: Watchlist, client: Client, snapshot_date: str, log=print) -> tuple[Snapshot, list[str]]:
    warnings: list[str] = []
    version_before = client.version()
    raw_by_nct: dict[str, dict] = {}
    collections: dict[str, CollectionState] = {}
    for c in wl.collections:
        queries, totals = {}, {}
        for q in c.queries:
            result = client.fetch_query(q)  # raises on failure or count mismatch
            ids = []
            for study in result.studies:
                nct = study["protocolSection"]["identificationModule"]["nctId"]
                raw_by_nct.setdefault(nct, study)
                ids.append(nct)
            queries[q.id] = sorted(ids)
            totals[q.id] = result.total_count
            log(f"  {q.id:28s} {len(ids):6d} trials  (API totalCount {result.total_count}, {result.pages} page(s))")
        collections[c.id] = CollectionState(query_hash=c.query_hash(), queries=queries, api_totals=totals)
    version_after = client.version()
    if version_before.get("dataTimestamp") != version_after.get("dataTimestamp"):
        warnings.append(
            f"registry data refreshed during the run ({version_before.get('dataTimestamp')} -> "
            f"{version_after.get('dataTimestamp')}); queries may straddle two registry versions"
        )
    trials = {nct: normalize_study(raw) for nct, raw in sorted(raw_by_nct.items())}
    snap = Snapshot(
        snapshot_date=snapshot_date,
        created_at=_now(),
        source={
            "api_version": version_after.get("apiVersion"),
            "data_timestamp": version_after.get("dataTimestamp"),
        },
        collections=collections,
        trials=trials,
    )
    return snap, warnings


def run(
    snapshot_date: str | None = None,
    data_dir: Path = DATA_DIR,
    watchlist_path: Path = WATCHLIST_PATH,
    client: Client | None = None,
    dry_run: bool = False,
    log=print,
) -> dict:
    started_at = _now()
    snapshot_date = snapshot_date or dt.datetime.now(dt.UTC).date().isoformat()
    wl = load_watchlist(watchlist_path)
    store = SnapshotStore(data_dir / "snapshots")

    if store.exists(snapshot_date):
        log(f"Snapshot for {snapshot_date} already exists; nothing to do (snapshots are never overwritten).")
        return {"status": "noop", "snapshot_date": snapshot_date}

    client = client or Client()
    log(f"Fetching {len(wl.queries)} queries for {snapshot_date} ...")
    snap, warnings = build_snapshot(wl, client, snapshot_date, log=log)

    prev_date = store.latest_before(snapshot_date)
    prev = store.read(prev_date) if prev_date else None
    diff = diff_snapshots(prev, snap)

    summary = {
        "status": "dry_run" if dry_run else "ok",
        "snapshot_date": snapshot_date,
        "compared_against": prev_date,
        "baseline": diff.baseline,
        "trials": len(snap.trials),
        "events": len(diff.events),
        "records_changed": len(diff.changed_records),
        "rebaselined_collections": diff.rebaselined_collections,
        "left_watchlist": {k: len(v) for k, v in diff.left_watchlist.items()},
        "api_requests": client.requests_made,
        "warnings": warnings,
    }
    if dry_run:
        return summary

    # Site data first, snapshot manifest last: the manifest is the commit point, so
    # an interrupted run is simply re-done (event ids are deterministic and de-duplicated).
    events = update_changes(data_dir, diff.events)
    trial_stats = write_trials(data_dir, snap, wl, events)
    collections = write_collections(data_dir, snap, wl)
    write_meta(
        data_dir,
        snap,
        wl,
        diff,
        collections,
        events,
        {"started_at": started_at, "finished_at": _now(), "api_requests": client.requests_made, "warnings": warnings},
    )
    summary["snapshot"] = store.write(snap)
    summary["trial_files"] = trial_stats
    return summary


def _print_summary(s: dict) -> None:
    if s["status"] == "noop":
        return
    print(f"\nSnapshot {s['snapshot_date']}: {s['trials']} unique trials, {s['api_requests']} API requests")
    if s["baseline"]:
        print("Baseline run (no earlier snapshot): no change events emitted.")
    else:
        print(f"Compared against {s['compared_against']}: {s['events']} events, {s['records_changed']} records changed")
    if s["rebaselined_collections"]:
        print(f"Re-baselined (queries edited or new): {', '.join(s['rebaselined_collections'])}")
    for cid, n in s["left_watchlist"].items():
        print(f"{n} trial(s) no longer match {cid}")
    for w in s["warnings"]:
        print(f"WARNING: {w}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="snapshot date YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--watchlist", type=Path, default=WATCHLIST_PATH)
    parser.add_argument("--dry-run", action="store_true", help="fetch and diff but write nothing")
    args = parser.parse_args(argv)
    summary = run(args.date, args.data_dir, args.watchlist, dry_run=args.dry_run)
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
