"""Static checks that run from a clone with nothing built.

Hashing an artifact that is already on disk grades the artifact. It says
nothing about whether anything can produce one, which is how a published
figure outlives the script that made it.
"""

import re
from pathlib import Path


def test_every_measurement_is_named_by_a_script_that_exists(root):
    scripts = list((root / "scripts").glob("*.py"))
    assert scripts, "no scripts, so this check graded nothing"
    sources = {p.name: p.read_text(encoding="utf-8") for p in scripts}
    produced = set()
    for name, src in sources.items():
        for m in re.finditer(r"measurements/([A-Za-z0-9\-]+)\.json", src):
            produced.add(m.group(1))
    files = sorted(p.stem for p in (root / "measurements").glob("*.json"))
    assert files, "no measurement files to account for"
    # The scorer takes its output path as an argument, so the names live in a
    # committed command rather than inside the script. Every published document
    # counts, because which one carries the command is not part of the question.
    docs = [root / "README.md"] + sorted((root / "docs").glob("*.md"))
    for path in docs:
        for m in re.finditer(r"measurements/([A-Za-z0-9\-]+)\.json",
                             path.read_text(encoding="utf-8")):
            produced.add(m.group(1))
    orphans = [f for f in files if f not in produced]
    assert not orphans, (
        "these measurement files are not produced by any committed command: %s" % orphans)


def test_every_script_a_published_document_runs_is_in_the_repository(root):
    """The README and the documents together.

    The measurement sections and their commands moved into docs/, so the
    front page runs the service and the documents run the measurements.
    Anchoring this to one filename is what kept them on one page.
    """
    docs = [root / "README.md"] + sorted((root / "docs").glob("*.md"))
    named = set()
    for path in docs:
        named |= set(re.findall(r"(?:python|python3)\s+(?:-I\s+)?((?:scripts|corpus)/[A-Za-z0-9_]+\.py)",
                                path.read_text(encoding="utf-8")))
    assert named, "nothing published names a command, so nothing here is runnable from it"
    for rel in sorted(named):
        assert (root / rel).exists(), "a document runs %s and it is not here" % rel


def test_the_marker_file_carries_the_repository_name(root):
    marker = root / ".portfolio-repo"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8").strip() == root.name


def test_the_corpus_is_committed_so_every_figure_can_be_rederived(root):
    for name in ("labelled-dev.jsonl", "labelled-holdout.jsonl"):
        p = root / "corpus" / "data" / name
        assert p.exists() and p.stat().st_size > 0
