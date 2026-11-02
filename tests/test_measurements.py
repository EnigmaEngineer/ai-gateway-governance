import hashlib
import json
from pathlib import Path

WALL_TIME_KEYS = ("seconds", "ms", "millis", "elapsed", "duration", "wall", "latency")


def test_there_are_measurements_to_grade(manifests):
    assert len(manifests) >= 4, "expected the baseline, both pattern slices and the evasion pass"


def test_each_scored_manifest_names_a_corpus_that_still_hashes_the_same(manifests, root):
    """A figure measured against a corpus that has since changed is not a figure."""
    graded = 0
    for stem, m in manifests.items():
        if "corpus_sha256" not in m:
            continue
        graded += 1
        path = root / m["corpus_path"]
        assert path.exists(), "%s names a corpus that is not here: %s" % (stem, path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == m["corpus_sha256"], (
            "%s was measured against a different %s, rerun the scorer"
            % (stem, m["corpus_path"]))
    assert graded >= 3, "only %d manifests carried a corpus hash" % graded


def test_no_manifest_carries_a_wall_time(manifests):
    """A count reproduces on another machine. A millisecond does not."""
    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                low = k.lower()
                assert not any(t in low for t in WALL_TIME_KEYS), (
                    "%s carries a timing key %r" % (where, k))
                walk(v, where)
        elif isinstance(node, list):
            for v in node:
                walk(v, where)
    for stem, m in manifests.items():
        walk(m, stem)


def test_every_rate_is_published_as_two_counts(manifests):
    for stem, m in manifests.items():
        for section in ("phi", "injection"):
            block = m.get(section, {})
            for key in ("precision", "recall"):
                if key in block:
                    assert set(block[key]) == {"numerator", "denominator"}, (
                        "%s %s %s is not a pair of counts" % (stem, section, key))


def test_the_evasion_pass_controls_fired(manifests):
    """Every attempt getting through is also what a broken harness looks like."""
    controls = manifests["evasion"]["controls"]
    assert all(controls.values()), "a control did not fire, so the pass graded nothing: %r" % controls


def test_the_baseline_is_worse_than_the_patterns(manifests):
    """If it is not, the patterns are not doing anything the floor does not."""
    base = manifests["holdout-baseline"]["phi"]["recall"]
    real = manifests["holdout-patterns"]["phi"]["recall"]
    assert base["denominator"] == real["denominator"]
    assert real["numerator"] > base["numerator"]


def test_the_scored_slice_is_not_the_slice_the_patterns_were_written_against(manifests):
    dev = manifests["development-patterns"]["corpus_sha256"]
    hold = manifests["holdout-patterns"]["corpus_sha256"]
    assert dev != hold, "both slices hash the same, so nothing is held out"
