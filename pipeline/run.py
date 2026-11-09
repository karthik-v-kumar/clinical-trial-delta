"""Pipeline entrypoint: fetch -> normalize -> snapshot -> diff -> write data/.

    python -m pipeline.run <mode> [--dry-run] [--date YYYY-MM-DD]

Modes
-----
daily
    The full refresh, run once a day by .github/workflows/refresh.yml. Fetches
    every watchlist query from ClinicalTrials.gov, writes today's snapshot
    (Los Angeles calendar date), diffs it against the most recent earlier
    snapshot, appends change events to data/changes.json and rewrites the site's
    data files (data/meta.json, data/collections/*.json, data/trials/*.json).
    If today's snapshot already exists it does nothing, so re-running is safe.

rebuild
    Re-writes the site's data files (data/trials, data/collections, data/sites,
    data/meta.json) from the most recent snapshot, without contacting the
    registry. Use it after changing a writer, the eligibility parser or display
    metadata in watchlist.json, so the site picks the change up without waiting
    for the next daily run. It never touches data/snapshots or data/changes.json.

The mode is recorded in meta.json (run.mode for the run that fetched the
snapshot, rebuild.mode for a later rebuild) and in the snapshot manifest, so
provenance is always visible. New modes are new entries in MODES.

Options
-------
--dry-run   fetch and diff, but write nothing
--date      override the snapshot date (testing, backfills)

Run discipline
--------------
A failure in one entity never kills the run.
* A query that cannot be fetched, or whose pages don't add up to the API's own
  totalCount, is marked failed. Its trial list is carried forward from the
  previous snapshot, its collection is marked partial, and the run continues
  with every other query.
* A trial record that cannot be normalized keeps its previous version; if it
  has none, it is left out of today's snapshot.
* All of this is logged, recorded in meta.json (run.partial, run.failed_queries,
  collections[].partial) and raised as a warning annotation on the Actions run.
Only when every query fails, or the API itself is unreachable, does the run stop
without writing anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .clock import now_iso, today
from .config import WATCHLIST_PATH, Watchlist, load_watchlist
from .diff import diff_snapshots
from .fetch.ctgov import Client, FetchError
from .normalize import normalize_study
from .snapshot import CollectionState, Snapshot, SnapshotStore
from .writers import (
    read_events,
    read_meta,
    update_changes,
    write_collections,
    write_meta,
    write_sites,
    write_trials,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

MODES = {
    "daily": "full refresh of every watchlist query (the scheduled run)",
    "rebuild": "re-write the site's data files from the latest snapshot, without fetching",
}


class RunFailed(RuntimeError):
    """Nothing usable was fetched; the run wrote nothing."""


def build_snapshot(
    wl: Watchlist,
    client: Client,
    snapshot_date: str,
    mode: str,
    prev: Snapshot | None,
    log=print,
) -> tuple[Snapshot, dict]:
    """Fetch and normalize everything. Returns the snapshot and a problems report."""
    problems: dict = {"failed_queries": {}, "kept_previous_records": [], "skipped_records": [], "warnings": []}
    version_before = client.version()  # if the API is down entirely, this raises and the run stops
    raw_by_nct: dict[str, dict] = {}
    carried: set[str] = set()
    collections: dict[str, CollectionState] = {}

    for c in wl.collections:
        prev_state = prev.collections.get(c.id) if prev else None
        queries: dict[str, list[str]] = {}
        totals: dict[str, int] = {}
        failed_here: list[str] = []
        carried_everything = True
        for q in c.queries:
            try:
                result = client.fetch_query(q)
            except FetchError as exc:
                problems["failed_queries"][q.id] = str(exc)[:300]
                failed_here.append(q.id)
                previous_ids = prev_state.queries.get(q.id) if prev_state else None
                if previous_ids is None:
                    carried_everything = False
                    queries[q.id] = []
                    log(f"  {q.id:28s} FAILED (nothing to carry forward): {exc}")
                else:
                    queries[q.id] = list(previous_ids)
                    carried.update(previous_ids)
                    if q.id in prev_state.api_totals:
                        totals[q.id] = prev_state.api_totals[q.id]
                    log(
                        f"  {q.id:28s} FAILED, carrying forward {len(previous_ids)} trials "
                        f"from {prev.snapshot_date}: {exc}"
                    )
                continue
            ids = []
            for study in result.studies:
                nct = study["protocolSection"]["identificationModule"]["nctId"]
                raw_by_nct.setdefault(nct, study)
                ids.append(nct)
            queries[q.id] = sorted(ids)
            totals[q.id] = result.total_count
            log(f"  {q.id:28s} {len(ids):6d} trials  (API totalCount {result.total_count}, {result.pages} page(s))")

        rules = c.query_hash()
        query_hash = rules
        if failed_here:
            # Membership is only comparable with yesterday's if every failed query was
            # carried forward under unchanged rules; otherwise force a re-baseline so a
            # gap is never reported as a burst of "new trials".
            same_rules = prev_state is not None and prev_state.query_hash == rules
            if not (same_rules and carried_everything):
                query_hash = f"incomplete:{rules}"
        collections[c.id] = CollectionState(
            query_hash=query_hash, queries=queries, api_totals=totals, failed_queries=failed_here
        )

    if len(problems["failed_queries"]) == len(wl.queries):
        raise RunFailed(
            f"every query failed; nothing written. First error: {next(iter(problems['failed_queries'].values()))}"
        )

    version_after = client.version()
    if version_before.get("dataTimestamp") != version_after.get("dataTimestamp"):
        problems["warnings"].append(
            f"registry data refreshed during the run ({version_before.get('dataTimestamp')} -> "
            f"{version_after.get('dataTimestamp')}); queries may straddle two registry versions"
        )

    trials: dict[str, dict] = {}
    for nct, raw in sorted(raw_by_nct.items()):
        try:
            trials[nct] = normalize_study(raw)
        except Exception as exc:  # one malformed record must not kill the run
            previous = prev.trials.get(nct) if prev else None
            if previous is not None:
                trials[nct] = previous
                problems["kept_previous_records"].append(nct)
            else:
                problems["skipped_records"].append(nct)
            log(
                f"  {nct}: could not normalize ({type(exc).__name__}: {exc}); "
                f"{'kept previous record' if previous is not None else 'skipped'}"
            )
    for nct in sorted(carried - trials.keys()):
        previous = prev.trials.get(nct) if prev else None
        if previous is not None:
            trials[nct] = previous
        else:
            problems["skipped_records"].append(nct)
    if problems["skipped_records"]:
        skipped = set(problems["skipped_records"])
        for state in collections.values():
            state.queries = {qid: [n for n in ids if n not in skipped] for qid, ids in state.queries.items()}

    snap = Snapshot(
        snapshot_date=snapshot_date,
        mode=mode,
        created_at=now_iso(),
        source={
            "api_version": version_after.get("apiVersion"),
            "data_timestamp": version_after.get("dataTimestamp"),
        },
        collections=collections,
        trials=trials,
    )
    return snap, problems


def _problem_warnings(problems: dict) -> list[str]:
    out = list(problems["warnings"])
    for qid, err in problems["failed_queries"].items():
        out.append(f"query {qid} failed and was carried forward: {err}")
    if problems["kept_previous_records"]:
        out.append(f"{len(problems['kept_previous_records'])} record(s) failed to normalize; kept previous versions")
    if problems["skipped_records"]:
        out.append(f"{len(problems['skipped_records'])} record(s) failed to normalize and were skipped")
    return out


def run(
    mode: str = "daily",
    snapshot_date: str | None = None,
    data_dir: Path = DATA_DIR,
    watchlist_path: Path = WATCHLIST_PATH,
    client: Client | None = None,
    dry_run: bool = False,
    log=print,
) -> dict:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose from {sorted(MODES)}")
    started_at = now_iso()
    wl = load_watchlist(watchlist_path)
    store = SnapshotStore(data_dir / "snapshots")
    if mode == "rebuild":
        return rebuild(wl, store, data_dir, started_at, snapshot_date, dry_run, log)

    snapshot_date = snapshot_date or today()
    if store.exists(snapshot_date):
        log(f"Snapshot for {snapshot_date} already exists; nothing to do (snapshots are never overwritten).")
        return {"status": "noop", "mode": mode, "snapshot_date": snapshot_date}

    prev_date = store.latest_before(snapshot_date)
    prev = store.read(prev_date) if prev_date else None

    client = client or Client()
    log(f"[{mode}] Fetching {len(wl.queries)} queries for {snapshot_date} ...")
    snap, problems = build_snapshot(wl, client, snapshot_date, mode, prev, log=log)
    diff = diff_snapshots(prev, snap)
    partial = bool(problems["failed_queries"] or problems["kept_previous_records"] or problems["skipped_records"])
    warnings = _problem_warnings(problems)

    summary = {
        "status": "dry_run" if dry_run else ("partial" if partial else "ok"),
        "mode": mode,
        "snapshot_date": snapshot_date,
        "compared_against": prev_date,
        "baseline": diff.baseline,
        "trials": len(snap.trials),
        "events": len(diff.events),
        "records_changed": len(diff.changed_records),
        "rebaselined_collections": diff.rebaselined_collections,
        "left_watchlist": {k: len(v) for k, v in diff.left_watchlist.items()},
        "partial": partial,
        "failed_queries": problems["failed_queries"],
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
    write_sites(data_dir, snap, wl)
    finished_at = now_iso()
    write_meta(
        data_dir,
        snap,
        wl,
        collections,
        events,
        run={
            "mode": mode,
            "started_at": started_at,
            "finished_at": finished_at,
            "compared_against": diff.compared_against,
            "baseline": diff.baseline,
            "partial": partial,
            "failed_queries": problems["failed_queries"],
            "records_kept_from_previous": problems["kept_previous_records"],
            "records_skipped": problems["skipped_records"],
            "events_detected": len(diff.events),
            "records_changed": len(diff.changed_records),
            "rebaselined_collections": diff.rebaselined_collections,
            "left_watchlist": diff.left_watchlist,
            "api_requests": client.requests_made,
            "warnings": warnings,
        },
        generated_at=finished_at,
    )
    summary["snapshot"] = store.write(snap)
    summary["trial_files"] = trial_stats
    return summary


def rebuild(
    wl: Watchlist,
    store: SnapshotStore,
    data_dir: Path,
    started_at: str,
    snapshot_date: str | None,
    dry_run: bool,
    log=print,
) -> dict:
    """Re-write the site's data files from an existing snapshot. No network, no new events."""
    dates = store.dates()
    if not dates:
        raise RunFailed("no snapshot to rebuild from; run `python -m pipeline.run daily` first")
    snapshot_date = snapshot_date or dates[-1]
    if snapshot_date != dates[-1]:
        raise RunFailed(f"rebuild only works from the latest snapshot ({dates[-1]}), not {snapshot_date}")
    snap = store.read(snapshot_date)
    missing = [c.id for c in wl.collections if c.id not in snap.collections]
    warnings = [
        f"collection {cid} is not in snapshot {snapshot_date}; it appears after the next daily run" for cid in missing
    ]
    summary = {
        "status": "dry_run" if dry_run else "ok",
        "mode": "rebuild",
        "snapshot_date": snapshot_date,
        "trials": len(snap.trials),
        "warnings": warnings,
    }
    log(f"[rebuild] Re-writing site data from snapshot {snapshot_date} ({len(snap.trials)} trials) ...")
    if dry_run:
        return summary
    events = read_events(data_dir)
    summary["trial_files"] = write_trials(data_dir, snap, wl, events)
    collections = write_collections(data_dir, snap, wl)
    summary["sites"] = write_sites(data_dir, snap, wl)
    previous = read_meta(data_dir) or {}
    finished_at = now_iso()
    write_meta(
        data_dir,
        snap,
        wl,
        collections,
        events,
        run=previous.get("run") or {"mode": snap.mode, "finished_at": snap.created_at},
        generated_at=finished_at,
        rebuild={"mode": "rebuild", "started_at": started_at, "finished_at": finished_at},
    )
    return summary


def _print_summary(s: dict) -> None:
    if s["status"] == "noop":
        return
    if s["mode"] == "rebuild":
        changed = s.get("trial_files", {}).get("files_changed")
        print(f"[rebuild] Snapshot {s['snapshot_date']}: {s['trials']} trials, {changed} trial files changed")
        for w in s["warnings"]:
            print(f"WARNING: {w}")
        return
    print(
        f"\n[{s['mode']}] Snapshot {s['snapshot_date']}: {s['trials']} unique trials, {s['api_requests']} API requests"
    )
    if s["baseline"]:
        print("Baseline run (no earlier snapshot): no change events emitted.")
    else:
        print(f"Compared against {s['compared_against']}: {s['events']} events, {s['records_changed']} records changed")
    if s["rebaselined_collections"]:
        print(f"Re-baselined (queries edited, new or incomplete): {', '.join(s['rebaselined_collections'])}")
    for cid, n in s["left_watchlist"].items():
        print(f"{n} trial(s) no longer match {cid}")
    on_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for w in s["warnings"]:
        print(f"::warning title=Refresh::{w}" if on_actions else f"WARNING: {w}")
    if s["partial"]:
        print("Run completed PARTIALLY; see meta.json run.failed_queries.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mode", choices=sorted(MODES), help="which refresh to run")
    parser.add_argument("--date", help="snapshot date YYYY-MM-DD (default: today in Los Angeles)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--watchlist", type=Path, default=WATCHLIST_PATH)
    parser.add_argument("--dry-run", action="store_true", help="fetch and diff but write nothing")
    args = parser.parse_args(argv)
    try:
        summary = run(args.mode, args.date, args.data_dir, args.watchlist, dry_run=args.dry_run)
    except (RunFailed, FetchError) as exc:
        print(f"Run failed: {exc}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
