"""The same figure gate, pointed at the documents that are not the README.

A check written against one filename is an argument for keeping every number on
that one page, which is how a front page grows. The question these ask is
whether a published figure was derived rather than typed, and which file a
reader finds it in is not part of that question.
"""

import re

from gateway import policy

WORDS = {
    "three": 3, "fifteen": 15, "eighteen": 18,
    "twenty": 20, "twenty two": 22,
}


def doc(root, name):
    """The document with its wrapping removed.

    Every file here is hard wrapped, so a figure can land with its numerator on
    one line and its denominator on the next. Searching line by line misses it
    and reports a published number as absent.
    """
    raw = (root / "docs" / name).read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", raw)


def test_the_evasion_document_grades_something(root):
    text = doc(root, "evasion.md")
    assert re.search(r"\d+\s+of\s+\d+", text), "no figures here, so this check graded nothing"


def test_the_evasion_document_carries_the_corpus_figures(root, manifests):
    text = doc(root, "evasion.md")
    m = manifests["holdout-patterns"]
    for section, label in (("phi", "identifier recall"), ("injection", "attack recall")):
        r = m[section]["recall"]
        want = "%d of %d" % (r["numerator"], r["denominator"])
        assert want in text, "%s should read %s and does not" % (label, want)


def test_the_evasion_document_names_the_weakest_class_correctly(root, manifests):
    per_class = manifests["holdout-patterns"]["injection"]["per_class_recall"]
    counts = per_class["instruction_override"]
    want = "%d of %d" % (counts["caught"], counts["planted"])
    assert want in doc(root, "evasion.md"), (
        "instruction override is %s on the scored slice" % want)


def test_the_evasion_document_counts_match_the_manifest(root, manifests):
    text = doc(root, "evasion.md").lower()
    e = manifests["evasion"]
    attempts = e["injection"]["attempts"] + e["phi"]["attempts"]
    through = e["injection"]["got_through"] + e["phi"]["got_through"]
    assert WORDS["twenty two"] == attempts, "the document says twenty two attempts"
    assert "twenty two attempts" in text
    assert WORDS["twenty"] == through, "the document says twenty got through"
    assert "twenty of twenty two" in text


def test_the_omissions_document_counts_match_the_policy_module(root):
    text = doc(root, "policy-omissions.md")
    assert "has fifteen members" in text and WORDS["fifteen"] == len(policy.ENFORCED)
    assert "eighteen classes" in text and WORDS["eighteen"] == len(policy.SAFE_HARBOR)
    assert "has three" in text and WORDS["three"] == len(policy.DECLARED_OMISSIONS)


def test_every_omission_reason_appears_in_the_document(root):
    """A reason that lives only in the code is a reason a reviewer never reads."""
    text = doc(root, "policy-omissions.md").lower()
    for name in policy.DECLARED_OMISSIONS:
        words = name.split("_")
        assert any(w in text for w in words if len(w) > 4), (
            "%s is omitted and the document does not discuss it" % name)


def test_the_scoring_document_names_both_seeds(root):
    text = doc(root, "scoring.md")
    for seed in ("11", "23"):
        assert "seed %s" % seed in text.lower()
