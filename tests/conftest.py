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


def _published_paths():
    """The README and every document, which together are what the repo states.

    The measurement sections were split out of the README late in the build.
    Before that every figure lived on the front page and four checks read it
    by filename, which is an argument for keeping every number on one page. The question these checks
    ask is whether a published figure was derived rather than typed, and which
    file a reader finds it in is not part of that question.
    """
    return [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))


@pytest.fixture(scope="session")
def published_raw():
    """Every published document, concatenated, exactly as written.

    For a check comparing a printed table row character for character.
    """
    parts = [p.read_text(encoding="utf-8") for p in _published_paths()]
    assert parts, "nothing is published, so every check reading this graded nothing"
    return "\n".join(parts)


@pytest.fixture(scope="session")
def published(published_raw):
    """The same text with its hard wrapping removed.

    Every file here is wrapped, so a figure can land with its numerator on one
    line and its denominator on the next. A line by line search misses it and
    reports a published number as absent.
    """
    import re
    return re.sub(r"[ \t]*\n[ \t]*", " ", published_raw)

