import json
from pathlib import Path

import pytest

from pipeline.snapshot import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def day1() -> Snapshot:
    return Snapshot.from_dict(load_fixture("snapshot_2026-01-05.json"))


@pytest.fixture
def day2() -> Snapshot:
    return Snapshot.from_dict(load_fixture("snapshot_2026-01-06.json"))


@pytest.fixture
def raw_study() -> dict:
    return load_fixture("raw_study.json")
