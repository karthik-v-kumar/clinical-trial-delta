"""Load and validate the watchlist (pipeline/watchlist.json).

The watchlist is the only thing a non-programmer should need to edit. Validation
is strict so a typo fails the daily run loudly instead of silently tracking the
wrong trials.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

WATCHLIST_PATH = Path(__file__).with_name("watchlist.json")

STUDY_TYPES = ("INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS")
TERM_FIELDS = ("cond", "intr", "spons")

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class WatchlistError(ValueError):
    """The watchlist file is malformed."""


@dataclass(frozen=True)
class Query:
    id: str
    label: str
    collection_id: str
    cond: tuple[str, ...] = ()
    intr: tuple[str, ...] = ()
    spons: tuple[str, ...] = ()
    term: str | None = None
    study_type: str | None = None

    def definition(self) -> dict:
        """The parts of the query that decide which trials match."""
        return {
            "cond": list(self.cond),
            "intr": list(self.intr),
            "spons": list(self.spons),
            "term": self.term,
            "study_type": self.study_type,
        }


@dataclass(frozen=True)
class Collection:
    id: str
    name: str
    why_it_matters: str
    accent: str
    sponsors_of_note: tuple[str, ...]
    queries: tuple[Query, ...]

    def query_hash(self) -> str:
        """Fingerprint of the collection's matching rules.

        Stored in each snapshot so the differ can tell "a trial newly matches
        because the registry changed" from "it matches because we edited the
        watchlist" (the latter must not be reported as new_trial).
        """
        payload = json.dumps([{"id": q.id, **q.definition()} for q in self.queries], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Watchlist:
    collections: tuple[Collection, ...]

    @property
    def queries(self) -> tuple[Query, ...]:
        return tuple(q for c in self.collections for q in c.queries)

    def collection(self, collection_id: str) -> Collection:
        for c in self.collections:
            if c.id == collection_id:
                return c
        raise KeyError(collection_id)

    @property
    def all_sponsors_of_note(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for c in self.collections:
            for s in c.sponsors_of_note:
                seen.setdefault(s, None)
        return tuple(seen)


def _require_str(obj: dict, key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WatchlistError(f"{where}: '{key}' must be a non-empty string")
    return value.strip()


def _str_list(obj: dict, key: str, where: str) -> tuple[str, ...]:
    value = obj.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise WatchlistError(f"{where}: '{key}' must be a list of non-empty strings")
    terms = tuple(v.strip() for v in value)
    if len(set(t.casefold() for t in terms)) != len(terms):
        raise WatchlistError(f"{where}: '{key}' has duplicate terms")
    return terms


def parse_watchlist(raw: dict) -> Watchlist:
    if raw.get("schema_version") != 1:
        raise WatchlistError("schema_version must be 1")
    defaults = raw.get("defaults", {})
    default_study_type = defaults.get("study_type")
    if default_study_type is not None and default_study_type not in STUDY_TYPES:
        raise WatchlistError(f"defaults.study_type must be one of {STUDY_TYPES}")

    raw_collections = raw.get("collections")
    if not isinstance(raw_collections, list) or not raw_collections:
        raise WatchlistError("'collections' must be a non-empty list")

    seen_ids: set[str] = set()

    def claim(identifier: str, where: str) -> None:
        if not _SLUG.match(identifier):
            raise WatchlistError(f"{where}: id '{identifier}' must be lowercase-kebab-case")
        if identifier in seen_ids:
            raise WatchlistError(f"{where}: id '{identifier}' is used more than once")
        seen_ids.add(identifier)

    collections = []
    for i, rc in enumerate(raw_collections):
        where = f"collections[{i}]"
        cid = _require_str(rc, "id", where)
        claim(cid, where)
        accent = _require_str(rc, "accent", where)
        if not _HEX.match(accent):
            raise WatchlistError(f"{where}: accent must be a #RRGGBB colour")
        raw_queries = rc.get("queries")
        if not isinstance(raw_queries, list) or not raw_queries:
            raise WatchlistError(f"{where}: 'queries' must be a non-empty list")

        queries = []
        for j, rq in enumerate(raw_queries):
            qwhere = f"{where}.queries[{j}]"
            qid = _require_str(rq, "id", qwhere)
            claim(qid, qwhere)
            unknown = set(rq) - {"id", "label", "term", "study_type", *TERM_FIELDS}
            if unknown:
                raise WatchlistError(f"{qwhere}: unknown keys {sorted(unknown)}")
            term = rq.get("term")
            if term is not None and (not isinstance(term, str) or not term.strip()):
                raise WatchlistError(f"{qwhere}: 'term' must be a non-empty string if set")
            study_type = rq.get("study_type", default_study_type)
            if study_type is not None and study_type not in STUDY_TYPES:
                raise WatchlistError(f"{qwhere}: study_type must be one of {STUDY_TYPES}")
            q = Query(
                id=qid,
                label=_require_str(rq, "label", qwhere),
                collection_id=cid,
                cond=_str_list(rq, "cond", qwhere),
                intr=_str_list(rq, "intr", qwhere),
                spons=_str_list(rq, "spons", qwhere),
                term=term.strip() if term else None,
                study_type=study_type,
            )
            if not (q.cond or q.intr or q.spons or q.term):
                raise WatchlistError(f"{qwhere}: needs at least one of cond, intr, spons, term")
            queries.append(q)

        collections.append(
            Collection(
                id=cid,
                name=_require_str(rc, "name", where),
                why_it_matters=_require_str(rc, "why_it_matters", where),
                accent=accent,
                sponsors_of_note=_str_list(rc, "sponsors_of_note", where),
                queries=tuple(queries),
            )
        )
    return Watchlist(collections=tuple(collections))


def load_watchlist(path: Path = WATCHLIST_PATH) -> Watchlist:
    with open(path, encoding="utf-8") as fh:
        return parse_watchlist(json.load(fh))
