import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def manifests():
    """Every measurement file, keyed by stem. Empty is a failure, not a skip."""
    paths = sorted((ROOT / "measurements").glob("*.json"))
    assert paths, "no measurement files, so every check reading them graded nothing"
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in paths}


@pytest.fixture(scope="session")
def holdout():
    path = ROOT / "corpus" / "data" / "labelled-holdout.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows, "the holdout corpus is empty"
    return rows
