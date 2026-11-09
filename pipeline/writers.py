"""Write the site's data files from a snapshot and the change feed.

Writers copy ONLY allowlisted keys into data/ (the site reads nothing else).
The Beacon lesson: a new pipeline output used to be silently dropped because
the writer allowlist was not updated in the same change. Here the opposite
happens: any key that is neither allowlisted nor explicitly excluded raises
UnpublishedFieldError and fails the run, and a unit test checks that every
normalized field is accounted for.

Output (all JSON, all committed):
    data/meta.json                  run info + per-collection counts (home page)
    data/changes.json               every change event ever detected (append-only)
    data/collections/<id>.json      collection metadata, landscape counts, trial table rows
    data/trials/<NCT>.json          one trial: record + collections + its change history
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .config import Watchlist
from .ctgov import API_BASE, registry_url, web_url
from .diff import EVENT_TYPES, DiffResult
from .normalize import SCHEMA_VERSION, content_hash
from .snapshot import Snapshot

APP_NAME = "Clinical Trial Delta"

# --- allowlists -------------------------------------------------------------

# Normalized-record keys published in data/trials/<NCT>.json.
TRIAL_FIELDS = (
    "nct_id",
    "brief_title",
    "official_title",
    "acronym",
    "overall_status",
    "last_known_status",
    "why_stopped",
    "study_type",
    "phases",
    "design",
    "enrollment",
    "start_date",
    "primary_completion_date",
    "completion_date",
    "first_posted",
    "last_update_posted",
    "results_first_posted",
    "has_results",
    "lead_sponsor",
    "collaborators",
    "conditions",
    "interventions",
    "arm_groups",
    "primary_outcomes",
    "brief_summary",
    "eligibility",
    "sites",
)
# Normalized keys deliberately NOT published, with the reason. Empty for now;
# a key belongs here only by a deliberate decision, never by omission.
TRIAL_EXCLUDED: dict[str, str] = {}
# Keys the writer itself adds to a trial file.
TRIAL_DERIVED = (
    "schema_version",
    "registry_url",
    "content_hash",
    "collections",
    "matched_queries",
    "sponsors_of_note",
    "in_watchlist",
    "left_watchlist_date",
    "history",
)

EVENT_FIELDS = (
    "id",
    "change_id",
    "type",
    "nct_id",
    "collection",
    "field",
    "old_value",
    "new_value",
    "detected_date",
    "brief_title",
    "context",
)
# Key order of a trial file: identity and record first, pipeline additions after.
TRIAL_FILE_KEYS = ("schema_version",) + TRIAL_FIELDS + tuple(k for k in TRIAL_DERIVED if k != "schema_version")

HISTORY_FIELDS = ("change_id", "type", "field", "old_value", "new_value", "detected_date", "context", "collections")

# Rows of the collection page's trial table (sortable: phase, status, enrollment, start date).
ROW_FIELDS = (
    "nct_id",
    "brief_title",
    "acronym",
    "phases",
    "overall_status",
    "enrollment_count",
    "enrollment_type",
    "start_date",
    "primary_completion_date",
    "lead_sponsor",
    "sponsors_of_note",
    "has_results",
    "site_count",
    "recruiting_site_count",
    "countries",
    "matched_queries",
)

PHASE_ORDER = (
    "EARLY_PHASE1",
    "PHASE1",
    "PHASE1/PHASE2",
    "PHASE2",
    "PHASE2/PHASE3",
    "PHASE3",
    "PHASE4",
    "NA",
)
STATUS_ORDER = (
    "NOT_YET_RECRUITING",
    "RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
    "SUSPENDED",
    "TERMINATED",
    "COMPLETED",
    "WITHDRAWN",
    "UNKNOWN",
)
RECENT_DAYS = 7


class UnpublishedFieldError(KeyError):
    """A record has a key that no writer allowlist accounts for."""


def publish(record: dict, allowed, excluded=(), where: str = "record") -> dict:
    unknown = set(record) - set(allowed) - set(excluded)
    if unknown:
        raise UnpublishedFieldError(
            f"{where}: keys {sorted(unknown)} are not in the writer allowlist. Add them to the "
            f"allowlist (to publish) or the excluded list (to drop on purpose) in pipeline/writers.py."
        )
    return {k: record[k] for k in allowed if k in record}


# --- helpers ----------------------------------------------------------------


def phase_bucket(phases: list[str] | None) -> str:
    phases = [p for p in phases or [] if p != "NA"]
    return "/".join(phases) if phases else "NA"


def _ordered_counts(counter: Counter, order: tuple[str, ...], key: str) -> list[dict]:
    known = [v for v in order if counter.get(v)]
    extra = sorted(v for v in counter if v not in order)
    return [{key: v, "count": counter[v]} for v in known + extra]


def sponsor_matcher(names: tuple[str, ...]):
    patterns = [(n, re.compile(rf"\b{re.escape(n)}\b", re.IGNORECASE)) for n in names]

    def match(record: dict) -> list[str]:
        orgs = [(record.get("lead_sponsor") or {}).get("name") or ""] + list(record.get("collaborators") or [])
        return [n for n, pat in patterns if any(pat.search(o) for o in orgs)]

    return match


def write_json(path: Path, obj, indent: int | None = 1) -> bool:
    """Write JSON if the content differs; returns True when the file changed."""
    text = json.dumps(obj, indent=indent, ensure_ascii=False, separators=(",", ":") if indent is None else None)
    text += "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --- writers ----------------------------------------------------------------


def update_changes(data_dir: Path, new_events: list[dict]) -> list[dict]:
    """Append events to data/changes.json (full history, oldest first, de-duplicated by id)."""
    path = data_dir / "changes.json"
    existing = _read_json(path, {"events": []})["events"]
    seen = {e["id"] for e in existing}
    appended = []
    for event in new_events:
        if event["type"] not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event['type']!r}")
        clean = publish(event, EVENT_FIELDS, where=f"event {event.get('id')}")
        if clean["id"] not in seen:
            seen.add(clean["id"])
            appended.append(clean)
    events = existing + appended
    write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "event_types": list(EVENT_TYPES),
            "event_count": len(events),
            "events": events,
        },
    )
    return events


def trial_history(events: list[dict]) -> dict[str, list[dict]]:
    """Per-trial change history, one entry per registry change (collections merged)."""
    by_trial: dict[str, dict[str, dict]] = defaultdict(dict)
    for e in events:
        entry = by_trial[e["nct_id"]].get(e["change_id"])
        if entry is None:
            entry = {k: e.get(k) for k in HISTORY_FIELDS if k != "collections"}
            entry["collections"] = []
            by_trial[e["nct_id"]][e["change_id"]] = entry
        if e["collection"] not in entry["collections"]:
            entry["collections"].append(e["collection"])
    out = {}
    for nct, entries in by_trial.items():
        items = sorted(entries.values(), key=lambda x: x["detected_date"], reverse=True)  # newest first
        out[nct] = [{k: v for k, v in item.items() if not (k == "context" and v is None)} for item in items]
    return out


def write_trials(data_dir: Path, snap: Snapshot, wl: Watchlist, events: list[dict]) -> dict:
    trials_dir = data_dir / "trials"
    history = trial_history(events)
    match_sponsors = sponsor_matcher(wl.all_sponsors_of_note)
    written = 0
    for nct, record in snap.trials.items():
        body = {"schema_version": SCHEMA_VERSION}
        body.update(publish(record, TRIAL_FIELDS, TRIAL_EXCLUDED, where=f"trial {nct}"))
        body.update(
            {
                "registry_url": registry_url(nct),
                "content_hash": content_hash(record),
                "collections": snap.collections_of(nct),
                "matched_queries": snap.queries_of(nct),
                "sponsors_of_note": match_sponsors(record),
                "in_watchlist": True,
                "history": history.get(nct, []),
            }
        )
        written += write_json(trials_dir / f"{nct}.json", publish(body, TRIAL_FILE_KEYS, where=f"trial file {nct}"))

    # Trials that stopped matching keep their page (feed links must not break),
    # marked as no longer tracked. Their record is the last one we saw.
    left = 0
    if trials_dir.exists():
        for path in sorted(trials_dir.glob("NCT*.json")):
            nct = path.stem
            if nct in snap.trials:
                continue
            body = _read_json(path, None)
            if body is None:
                continue
            if body.get("in_watchlist", True):
                body["in_watchlist"] = False
                body["left_watchlist_date"] = snap.snapshot_date
            body["history"] = history.get(nct, [])
            left += 1
            written += write_json(path, publish(body, TRIAL_FILE_KEYS, where=f"trial file {nct}"))
    return {"trial_files": len(snap.trials), "files_changed": written, "untracked_trial_files": left}


def collection_row(record: dict, snap: Snapshot, match_sponsors) -> dict:
    enrollment = record.get("enrollment") or {}
    sites = record.get("sites") or []
    row = {
        "nct_id": record["nct_id"],
        "brief_title": record.get("brief_title"),
        "acronym": record.get("acronym"),
        "phases": record.get("phases") or [],
        "overall_status": record.get("overall_status"),
        "enrollment_count": enrollment.get("count"),
        "enrollment_type": enrollment.get("type"),
        "start_date": (record.get("start_date") or {}).get("date"),
        "primary_completion_date": (record.get("primary_completion_date") or {}).get("date"),
        "lead_sponsor": (record.get("lead_sponsor") or {}).get("name"),
        "sponsors_of_note": match_sponsors(record),
        "has_results": bool(record.get("has_results")),
        "site_count": len(sites),
        "recruiting_site_count": sum(1 for s in sites if s.get("status") == "RECRUITING"),
        "countries": sorted({s["country"] for s in sites if s.get("country")}),
        "matched_queries": snap.queries_of(record["nct_id"]),
    }
    return publish(row, ROW_FIELDS, where=f"row {record['nct_id']}")


def write_collections(data_dir: Path, snap: Snapshot, wl: Watchlist) -> dict:
    match_sponsors = sponsor_matcher(wl.all_sponsors_of_note)
    summary = {}
    for c in wl.collections:
        state = snap.collections[c.id]
        members = sorted(state.members, reverse=True)  # newest NCT numbers first
        records = [snap.trials[n] for n in members]
        statuses = Counter(r.get("overall_status") for r in records)
        body = {
            "schema_version": SCHEMA_VERSION,
            "id": c.id,
            "name": c.name,
            "why_it_matters": c.why_it_matters,
            "accent": c.accent,
            "sponsors_of_note": list(c.sponsors_of_note),
            "queries": [
                {
                    "id": q.id,
                    "label": q.label,
                    "registry_count": state.api_totals.get(q.id),
                    "matched": len(state.queries.get(q.id, [])),
                    "web_url": web_url(q),
                }
                for q in c.queries
            ],
            "trial_count": len(records),
            "recruiting_count": statuses.get("RECRUITING", 0),
            "not_yet_recruiting_count": statuses.get("NOT_YET_RECRUITING", 0),
            "by_phase": _ordered_counts(Counter(phase_bucket(r.get("phases")) for r in records), PHASE_ORDER, "phase"),
            "by_status": _ordered_counts(statuses, STATUS_ORDER, "status"),
            "trials": [collection_row(r, snap, match_sponsors) for r in records],
        }
        write_json(data_dir / "collections" / f"{c.id}.json", body)
        summary[c.id] = body
    return summary


def write_meta(
    data_dir: Path,
    snap: Snapshot,
    wl: Watchlist,
    diff: DiffResult,
    collections: dict[str, dict],
    events: list[dict],
    run_info: dict,
) -> dict:
    window_start = (dt.date.fromisoformat(snap.snapshot_date) - dt.timedelta(days=RECENT_DAYS - 1)).isoformat()
    recent = defaultdict(set)
    for e in events:
        if window_start <= e["detected_date"] <= snap.snapshot_date:
            recent[e["collection"]].add(e["change_id"])
    meta = {
        "schema_version": SCHEMA_VERSION,
        "app": APP_NAME,
        "snapshot_date": snap.snapshot_date,
        "generated_at": run_info["finished_at"],
        "run": {
            "started_at": run_info["started_at"],
            "finished_at": run_info["finished_at"],
            "compared_against": diff.compared_against,
            "baseline": diff.baseline,
            "events_detected": len(diff.events),
            "records_changed": len(diff.changed_records),
            "rebaselined_collections": diff.rebaselined_collections,
            "left_watchlist": diff.left_watchlist,
            "api_requests": run_info.get("api_requests"),
            "warnings": run_info.get("warnings", []),
        },
        "source": {"name": "ClinicalTrials.gov API v2", "url": API_BASE, **snap.source},
        "totals": {
            "trials": len(snap.trials),
            "events_all_time": len(events),
            "changes_all_time": len({e["change_id"] for e in events}),
        },
        "recent_window_days": RECENT_DAYS,
        "collections": [
            {
                "id": c.id,
                "name": c.name,
                "why_it_matters": c.why_it_matters,
                "accent": c.accent,
                "trial_count": collections[c.id]["trial_count"],
                "recruiting_count": collections[c.id]["recruiting_count"],
                "changes_recent": len(recent.get(c.id, ())),
            }
            for c in wl.collections
        ],
        "queries": [
            {
                "id": q.id,
                "collection": q.collection_id,
                "label": q.label,
                "matched": len(snap.collections[q.collection_id].queries.get(q.id, [])),
                "registry_count": snap.collections[q.collection_id].api_totals.get(q.id),
                "web_url": web_url(q),
            }
            for q in wl.queries
        ],
    }
    write_json(data_dir / "meta.json", meta, indent=2)
    return meta
