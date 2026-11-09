"""Compare two snapshots and emit typed change events.

Event types (the brief's list, nothing else):

    new_trial          a trial appears in a collection it was not in yesterday
    status_change      overall recruitment status changed
    enrollment_change  enrollment number or its type (estimated / actual) changed
    endpoint_change    primary outcome measure text changed
    site_added         one or more sites (locations) were added
    site_removed       one or more sites were removed
    date_change        primary completion date or completion date moved
    results_posted     results were posted (has_results went false -> true)

Every event: id, change_id, type, nct_id, collection, field, old_value,
new_value, detected_date, brief_title, and an optional context dict.

A registry change to a trial that sits in two collections yields two events
(one per collection) sharing a change_id, so collection feeds are simple
filters and the global feed de-duplicates on change_id.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field

from .normalize import content_hash
from .snapshot import Snapshot

EVENT_TYPES = (
    "new_trial",
    "status_change",
    "enrollment_change",
    "endpoint_change",
    "site_added",
    "site_removed",
    "date_change",
    "results_posted",
)
_TYPE_ORDER = {t: i for i, t in enumerate(EVENT_TYPES)}
DATE_FIELDS = ("primary_completion_date", "completion_date")
SITE_KEY_FIELDS = ("facility", "city", "state", "country")

_WS = re.compile(r"\s+")


@dataclass
class DiffResult:
    events: list[dict]
    baseline: bool  # True when there was no earlier snapshot to compare against
    compared_against: str | None
    rebaselined_collections: list[str] = field(default_factory=list)
    left_watchlist: dict[str, list[str]] = field(default_factory=dict)  # collection -> NCT ids that stopped matching
    changed_records: list[str] = field(default_factory=list)  # NCT ids that are new or whose content changed


def _clean(text: str | None) -> str:
    """Comparison form of free text: whitespace and letter case are not changes."""
    return _WS.sub(" ", text or "").strip().casefold()


def site_key(site: dict) -> tuple[str, ...]:
    """Identity of a site. Excludes recruitment status and coordinates, which change in place."""
    return tuple(_clean(site.get(k)) for k in SITE_KEY_FIELDS)


def _public_site(site: dict) -> dict:
    return {k: site.get(k) for k in SITE_KEY_FIELDS}


def _date_of(value: dict | None) -> str | None:
    return (value or {}).get("date")


def _field_changes(old: dict, new: dict) -> list[dict]:
    """Changes between two versions of one trial, independent of collection."""
    out: list[dict] = []

    def add(etype, fld, old_value, new_value, context=None):
        out.append({"type": etype, "field": fld, "old_value": old_value, "new_value": new_value, "context": context})

    if old.get("overall_status") != new.get("overall_status"):
        ctx = {"why_stopped": new["why_stopped"]} if new.get("why_stopped") else None
        add("status_change", "overall_status", old.get("overall_status"), new.get("overall_status"), ctx)

    if (old.get("enrollment") or None) != (new.get("enrollment") or None):
        add("enrollment_change", "enrollment", old.get("enrollment"), new.get("enrollment"))

    old_measures = [o.get("measure") for o in old.get("primary_outcomes") or []]
    new_measures = [o.get("measure") for o in new.get("primary_outcomes") or []]
    if Counter(map(_clean, old_measures)) != Counter(map(_clean, new_measures)):
        add("endpoint_change", "primary_outcomes", old_measures, new_measures)

    old_sites = Counter(site_key(s) for s in old.get("sites") or [])
    new_sites = Counter(site_key(s) for s in new.get("sites") or [])
    added, removed = new_sites - old_sites, old_sites - new_sites
    counts = {"site_count_before": sum(old_sites.values()), "site_count_after": sum(new_sites.values())}
    if added:
        add("site_added", "sites", None, _pick_sites(new.get("sites") or [], added), counts)
    if removed:
        add("site_removed", "sites", _pick_sites(old.get("sites") or [], removed), None, counts)

    for fld in DATE_FIELDS:
        if _date_of(old.get(fld)) != _date_of(new.get(fld)):
            add("date_change", fld, old.get(fld), new.get(fld))

    if not old.get("has_results") and new.get("has_results"):
        ctx = {"results_first_posted": new["results_first_posted"]} if new.get("results_first_posted") else None
        add("results_posted", "has_results", False, True, ctx)

    return out


def _pick_sites(sites: list[dict], wanted: Counter) -> list[dict]:
    """The site entries behind a multiset of keys, in record order."""
    remaining = Counter(wanted)
    picked = []
    for s in sites:
        k = site_key(s)
        if remaining[k] > 0:
            remaining[k] -= 1
            picked.append(_public_site(s))
    return picked


def _digest(*parts) -> str:
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _event(change: dict, nct_id: str, collection: str, detected: str, title: str | None) -> dict:
    change_id = _digest(change["type"], nct_id, change["field"], change["old_value"], change["new_value"], detected)
    event = {
        "id": _digest(change_id, collection),
        "change_id": change_id,
        "type": change["type"],
        "nct_id": nct_id,
        "collection": collection,
        "field": change["field"],
        "old_value": change["old_value"],
        "new_value": change["new_value"],
        "detected_date": detected,
        "brief_title": title,
    }
    if change.get("context"):
        event["context"] = change["context"]
    return event


def diff_snapshots(prev: Snapshot | None, curr: Snapshot) -> DiffResult:
    detected = curr.snapshot_date
    if prev is None:
        return DiffResult(events=[], baseline=True, compared_against=None, changed_records=sorted(curr.trials))
    if prev.snapshot_date >= curr.snapshot_date:
        raise ValueError(f"previous snapshot {prev.snapshot_date} is not before {curr.snapshot_date}")

    changes_by_trial: dict[str, list[dict]] = {}
    changed_records = []
    for nct, record in curr.trials.items():
        old = prev.trials.get(nct)
        if old is None:
            changed_records.append(nct)
            continue
        if content_hash(old) != content_hash(record):
            changed_records.append(nct)
            changes_by_trial[nct] = _field_changes(old, record)

    events: list[dict] = []
    rebaselined: list[str] = []
    left: dict[str, list[str]] = {}
    for cid, state in curr.collections.items():
        prev_state = prev.collections.get(cid)
        same_rules = prev_state is not None and prev_state.query_hash == state.query_hash
        if not same_rules:
            rebaselined.append(cid)
        before = prev_state.members if prev_state else set()
        now = state.members

        if same_rules:
            for nct in sorted(now - before):
                rec = curr.trials[nct]
                change = {
                    "type": "new_trial",
                    "field": "trial",
                    "old_value": None,
                    "new_value": {
                        "overall_status": rec.get("overall_status"),
                        "phases": rec.get("phases") or [],
                        "first_posted": rec.get("first_posted"),
                    },
                }
                events.append(_event(change, nct, cid, detected, rec.get("brief_title")))

        # Field changes only for trials that were in this collection on both days.
        for nct in sorted(now & before):
            for change in changes_by_trial.get(nct, []):
                events.append(_event(change, nct, cid, detected, curr.trials[nct].get("brief_title")))

        gone = sorted(before - now)
        if gone:
            left[cid] = gone

    collection_order = {cid: i for i, cid in enumerate(curr.collections)}
    events.sort(key=lambda e: (collection_order[e["collection"]], e["nct_id"], _TYPE_ORDER[e["type"]], e["field"]))
    return DiffResult(
        events=events,
        baseline=False,
        compared_against=prev.snapshot_date,
        rebaselined_collections=rebaselined,
        left_watchlist=left,
        changed_records=sorted(changed_records),
    )
