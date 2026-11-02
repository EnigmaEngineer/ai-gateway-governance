import pytest

from gateway import policy


def test_every_class_sits_in_exactly_one_set():
    assert policy.unclassified() == (), (
        "these classes are in neither the enforced set nor the declared omissions, "
        "so the service passes them silently: %s" % (policy.unclassified(),)
    )
    assert policy.double_classified() == ()


def test_no_invented_class_names():
    """A typo would look covered and leave the real class unclassified."""
    assert policy.unknown() == (), (
        "named but not in the regulation's list: %s" % (policy.unknown(),))


def test_the_enumeration_is_the_regulation_and_not_the_coverage():
    assert len(policy.SAFE_HARBOR) == 18
    assert len(policy.ENFORCED) + len(policy.DECLARED_OMISSIONS) == 18


def test_every_omission_carries_a_reason_that_says_something():
    for name, reason in policy.DECLARED_OMISSIONS.items():
        assert len(reason) > 80, "%s has a reason too short to be one" % name
        assert "hard" != reason.strip().lower()


def test_validate_refuses_an_empty_enumeration(monkeypatch):
    """A check over zero items must fail rather than report no problems."""
    monkeypatch.setattr(policy, "SAFE_HARBOR", ())
    with pytest.raises(policy.PolicyError, match="graded nothing"):
        policy.validate()


def test_validate_catches_a_class_nobody_mentioned(monkeypatch):
    monkeypatch.setattr(policy, "SAFE_HARBOR", policy.SAFE_HARBOR + ("new_identifier_class",))
    with pytest.raises(policy.PolicyError, match="unclassified"):
        policy.validate()
