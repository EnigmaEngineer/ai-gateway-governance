"""Every figure the README publishes, re-derived from the manifests.

A number that is typed is a number that drifts. Each assertion here carries the
correct value in its message, so a failure says what the README should say
rather than only that it is wrong.
"""

import re

from gateway import policy


def _pairs(text):
    """Every `a / b` the README publishes, as integer pairs."""
    return set(
        (int(a), int(b))
        for a, b in re.findall(r"(\d+)\s*/\s*(\d+)", text)
    )


def readme(root):
    """The README with its wrapping removed.

    The file is hard wrapped, so a figure can land with its numerator on one
    line and its denominator on the next. A line by line search misses it and
    reports a published number as missing.
    """
    return re.sub(r"[ \t]*\n[ \t]*", " ", (root / "README.md").read_text(encoding="utf-8"))


def test_the_readme_publishes_some_figures_to_grade(root):
    assert _pairs(readme(root)), "no figures found, so this check graded nothing"


def test_every_rate_in_the_table_matches_its_manifest(root, manifests):
    published = _pairs(readme(root))
    expected = []
    for stem, label in (("holdout-patterns", "held out slice"),
                        ("holdout-baseline", "crude floor")):
        m = manifests[stem]
        for section in ("phi", "injection"):
            for key in ("recall", "precision"):
                r = m[section][key]
                expected.append((label, section, key, r["numerator"], r["denominator"]))
    for label, section, key, num, den in expected:
        assert (num, den) in published, (
            "the README does not publish the %s %s %s, which is %d / %d"
            % (label, section, key, num, den))


def test_the_row_count_in_the_table_is_the_row_count_measured(root, manifests):
    text = readme(root)
    rows = manifests["holdout-patterns"]["rows"]
    assert "%d rows" % rows in text, (
        "the README should say %d rows, which is what was scored" % rows)


def test_the_weakest_attack_class_is_named_with_its_real_figure(root, manifests):
    per_class = manifests["holdout-patterns"]["injection"]["per_class_recall"]
    worst = min(per_class.items(), key=lambda kv: kv[1]["caught"] / kv[1]["planted"])
    name, counts = worst
    text = readme(root)
    assert name.replace("_", " ") in text.lower(), (
        "the weakest attack class is %s and the README does not name it" % name)
    assert "%d of %d" % (counts["caught"], counts["planted"]) in text, (
        "%s is %d of %d and the README says otherwise"
        % (name, counts["caught"], counts["planted"]))


def test_the_missed_identifier_count_is_the_measured_one(root, manifests):
    per_class = manifests["holdout-patterns"]["phi"]["per_class"]
    missed = {k: v["missed"] for k, v in per_class.items() if v["missed"]}
    total = sum(missed.values())
    text = readme(root)
    assert len(missed) == 1, (
        "the README says every miss is one case and the misses are %r" % missed)
    assert "%d of the %d missed" % (total, total) in text, (
        "the README should say %d of the %d missed identifiers" % (total, total))


def test_the_evasion_counts_are_the_measured_ones(root, manifests):
    e = manifests["evasion"]
    attempts = e["injection"]["attempts"] + e["phi"]["attempts"]
    through = e["injection"]["got_through"] + e["phi"]["got_through"]
    text = readme(root).lower()
    assert "twenty two evasion attempts" in text and attempts == 22, (
        "the README says twenty two attempts and the manifest says %d" % attempts)
    assert "twenty of them still get through" in text and through == 20, (
        "the README says twenty got through and the manifest says %d" % through)


def test_the_attack_class_count_in_the_diagram_matches_the_detector(root):
    """The diagram publishes a count and the block gate cannot grade a drawing."""
    from gateway import injection
    text = (root / "README.md").read_text(encoding="utf-8")
    assert "%d attack classes" % len(injection.CLASSES) in text


def test_the_class_counts_in_the_diagram_match_the_policy_module(root):
    text = readme(root)
    assert "%d enforced classes" % len(policy.ENFORCED) in text
    assert "%d declared omissions" % len(policy.DECLARED_OMISSIONS) in text
    assert "the eighteen classes" in text and len(policy.SAFE_HARBOR) == 18
