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


def test_the_mtls_document_table_matches_the_probe(root, manifests):
    """Seven arms and a verdict each. A table typed by hand goes stale."""
    text = doc(root, "mtls.md")
    arms = manifests["mtls"]["arms"]
    assert len(arms) >= 6, "only %d arms, so the table is shorter than it was" % len(arms)
    for a in arms:
        row = "| %s | %s | %s |" % (a["arm"], a["handshake"], a["exchange"])
        assert row in text, "the table is missing or wrong for %r" % a["arm"]


def test_the_mtls_document_counts_the_late_refusals(root, manifests):
    late = manifests["mtls"]["handshake_completed_but_exchange_refused"]
    text = doc(root, "mtls.md")
    assert "Two of the seven" in text and len(late) == 2, (
        "the document says two and the probe found %d" % len(late))


def test_the_failover_document_names_the_widths_that_were_swept(root, manifests):
    text = doc(root, "failover.md")
    for width in manifests["failover"]["widths_swept"]:
        assert str(width) in text, "%s was swept and the document omits it" % width


def test_the_failover_document_arm_count_is_the_measured_one(root, manifests):
    """Thirty two plus one, and the plus one is the arm that loses requests."""
    m = manifests["failover"]
    text = doc(root, "failover.md").lower()
    assert "sixteen arms" in text, "the document no longer states the arm count"
    assert len(m["arms"]) == 16, "the sweep has %d arms now" % len(m["arms"])
    assert m["both_providers_down"]["requests_lost"] == m["requests_per_arm"]
    assert "forty requests per arm" in text and m["requests_per_arm"] == 40


def test_the_failover_document_breaker_threshold_is_the_measured_one(root, manifests):
    m = manifests["failover"]
    assert m["breaker_threshold"] == 3
    assert "three consecutive failures" in doc(root, "failover.md")


def test_the_versioning_document_carries_the_header_the_gateway_sends(root):
    """The example in the document is the string the registry produces."""
    from gateway import versioning
    text = doc(root, "versioning.md")
    headers = dict(versioning.VERSIONS["v1"].headers())
    assert headers["deprecation"] in text
    assert headers["sunset"] in text


def test_the_versioning_document_names_the_wrong_zone_token(root):
    """The authoritative example writes UTC and the grammar wants GMT."""
    from gateway import versioning
    text = doc(root, "versioning.md")
    assert versioning.RFC9745_SECTION_4_EXAMPLE_SUNSET in text
    assert "rel=\"deprecation\"" in text
