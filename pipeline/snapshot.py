"""Daily snapshots of the watchlist, stored so that none is ever overwritten.

Layout under data/snapshots/:

    YYYY-MM-DD.json.gz            one manifest per day: which trials matched each
                                  query, and the content hash of every trial record
    records/ab/abcdef0123456789.json.gz
                                  normalized trial records, content-addressed
                                  (file name = hash of contents)

A full snapshot for day D is that day's manifest plus the records it points to.
Records are immutable (same content -> same name, so a record file is written
once and never changed), and a manifest is written once and never replaced.
Because unchanged trials share record files across days, the store grows only
by what actually changed; at ~6k trials a full copy per day would add ~10 MB
(gzipped) to git every day.

The ClinicalTrials.gov API has no history endpoint, so this store is the only
history of the registry that exists for our watchlist.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SNAPSHOT_SCHEMA_VERSION = 1
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SnapshotExistsError(FileExistsError):
    """A snapshot for this date already exists; snapshots are never overwritten."""


class SnapshotCorruptError(RuntimeError):
    """A stored record does not match the hash its manifest recorded."""


@dataclass
class CollectionState:
    query_hash: str
    queries: dict[str, list[str]]  # query id -> NCT ids it matched
    api_totals: dict[str, int] = field(default_factory=dict)  # query id -> API totalCount

    @property
    def members(self) -> set[str]:
        return {nct for ids in self.queries.values() for nct in ids}


@dataclass
class Snapshot:
    snapshot_date: str
    collections: dict[str, CollectionState]
    trials: dict[str, dict]  # NCT id -> normalized record
    created_at: str | None = None
    source: dict = field(default_factory=dict)

    def members(self, collection_id: str) -> set[str]:
        state = self.collections.get(collection_id)
        return state.members if state else set()

    def collections_of(self, nct_id: str) -> list[str]:
        return [cid for cid, state in self.collections.items() if nct_id in state.members]

    def queries_of(self, nct_id: str) -> list[str]:
        return [qid for state in self.collections.values() for qid, ids in state.queries.items() if nct_id in ids]

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        """Readable in-memory form (used by test fixtures)."""
        return cls(
            snapshot_date=data["snapshot_date"],
            created_at=data.get("created_at"),
            source=data.get("source", {}),
            collections={
                cid: CollectionState(
                    query_hash=c["query_hash"],
                    queries={qid: sorted(ids) for qid, ids in c["queries"].items()},
                    api_totals=c.get("api_totals", {}),
                )
                for cid, c in data["collections"].items()
            },
            trials=data["trials"],
        )


def _gzip_json(obj) -> bytes:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)  # mtime=0: identical bytes for identical content


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.records_dir = self.root / "records"

    def manifest_path(self, date: str) -> Path:
        if not _DATE.match(date):
            raise ValueError(f"bad snapshot date {date!r}")
        return self.root / f"{date}.json.gz"

    def record_path(self, digest: str) -> Path:
        return self.records_dir / digest[:2] / f"{digest}.json.gz"

    def dates(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name[:10] for p in self.root.glob("*.json.gz") if _DATE.match(p.name[:10]))

    def exists(self, date: str) -> bool:
        return self.manifest_path(date).exists()

    def latest_before(self, date: str) -> str | None:
        earlier = [d for d in self.dates() if d < date]
        return earlier[-1] if earlier else None

    def write(self, snap: Snapshot) -> dict:
        """Write a snapshot. Records first, manifest last (the manifest is the commit point)."""
        manifest_path = self.manifest_path(snap.snapshot_date)
        if manifest_path.exists():
            raise SnapshotExistsError(f"snapshot {snap.snapshot_date} already exists; refusing to overwrite")
        hashes: dict[str, str] = {}
        new_records = 0
        for nct, record in sorted(snap.trials.items()):
            digest = content_hash_full(record)
            hashes[nct] = digest
            path = self.record_path(digest)
            if not path.exists():
                _atomic_write(path, _gzip_json(record))
                new_records += 1
        manifest = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_date": snap.snapshot_date,
            "created_at": snap.created_at,
            "source": snap.source,
            "collections": {
                cid: {
                    "query_hash": state.query_hash,
                    "queries": {qid: sorted(ids) for qid, ids in state.queries.items()},
                    "api_totals": state.api_totals,
                }
                for cid, state in snap.collections.items()
            },
            "trials": hashes,
        }
        _atomic_write(manifest_path, _gzip_json(manifest))
        return {"manifest": str(manifest_path), "records_written": new_records, "records_total": len(hashes)}

    def read(self, date: str) -> Snapshot:
        with gzip.open(self.manifest_path(date), "rt", encoding="utf-8") as fh:
            manifest = json.load(fh)
        if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotCorruptError(f"snapshot {date}: unsupported schema {manifest.get('schema_version')}")
        trials = {}
        for nct, digest in manifest["trials"].items():
            with gzip.open(self.record_path(digest), "rt", encoding="utf-8") as fh:
                record = json.load(fh)
            if content_hash_full(record) != digest:
                raise SnapshotCorruptError(f"snapshot {date}: record for {nct} does not match hash {digest}")
            trials[nct] = record
        return Snapshot(
            snapshot_date=manifest["snapshot_date"],
            created_at=manifest.get("created_at"),
            source=manifest.get("source", {}),
            collections={
                cid: CollectionState(
                    query_hash=c["query_hash"], queries=c["queries"], api_totals=c.get("api_totals", {})
                )
                for cid, c in manifest["collections"].items()
            },
            trials=trials,
        )


def content_hash_full(record: dict) -> str:
    """Storage hash: covers every field, so a stored record is an exact copy.

    (normalize.content_hash ignores bookkeeping fields such as last_update_posted
    and is the one to use for "did the trial change?".)
    """
    blob = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "CollectionState",
    "Snapshot",
    "SnapshotCorruptError",
    "SnapshotExistsError",
    "SnapshotStore",
    "content_hash_full",
]
