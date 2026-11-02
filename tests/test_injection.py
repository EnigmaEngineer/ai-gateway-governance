import pytest

from corpus.generate import ATTACK_TEMPLATES, INJECTION_NEGATIVES
from gateway import injection


def test_every_class_in_the_taxonomy_has_a_pattern():
    assert set(injection.CLASSES) == set(injection._PATTERNS)
    for cls in injection.CLASSES:
        assert injection._PATTERNS[cls], "%s has no pattern" % cls


def test_the_first_phrasing_of_each_class_fires():
    """The plainest form of each attack. A miss here is a broken pattern."""
    for cls, templates in ATTACK_TEMPLATES.items():
        verdict = injection.assess(templates[0])
        assert verdict["flagged"], "%s missed its plainest phrasing: %r" % (cls, templates[0])
        assert cls in verdict["classes"], (
            "%s fired as %s instead of itself" % (cls, verdict["classes"]))


def test_a_quoted_attack_is_flagged_and_hedged_rather_than_cleared():
    """Clearing on a quoting phrase would hand attackers the off switch."""
    text = INJECTION_NEGATIVES[0]
    verdict = injection.assess(text)
    assert verdict["flagged"]
    assert verdict["reads_as_quotation"]


def test_ordinary_traffic_does_not_fire():
    text = "Please summarise the attached discharge note for the care team."
    assert injection.assess(text)["flagged"] is False


def test_quotation_hedge_is_only_reported_when_something_fired():
    verdict = injection.assess("Please ignore the previous email, wrong attachment.")
    if not verdict["flagged"]:
        assert verdict["reads_as_quotation"] is False


def test_none_is_a_caller_bug():
    with pytest.raises(ValueError):
        injection.classify(None)
