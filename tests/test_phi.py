import pytest

from corpus.generate import HARD_NEGATIVES
from gateway import phi, policy

# One payload per enforced class, written here rather than taken from the
# corpus so a generator change cannot quietly stop exercising a class.
SAMPLES = {
    "names": "Patient: Rosa Delacroix is scheduled.",
    "geographic_subdivisions": "Send it to 4120 Willow Creek Drive, Tacoma WA 98402.",
    "dates_and_ages_over_89": "Date of birth 03/14/1952 per the chart.",
    "telephone_numbers": "Daytime number is (415) 555-0132.",
    "fax_numbers": "Fax 415-555-0190 for the authorisation.",
    "email_addresses": "Email care.team@example.org with the result.",
    "social_security_numbers": "SSN 123-45-6789 on the referral.",
    "medical_record_numbers": "MRN 884213 was merged twice.",
    "health_plan_beneficiary_numbers": "Member ID HP44821903 is active.",
    "account_numbers": "Account number 766836989 has a balance.",
    "certificate_or_license_numbers": "Prescriber NPI 1457829 is not enrolled.",
    "vehicle_identifiers": "Logged against VIN 1HGCM82633A004352 today.",
    "device_identifiers": "Pump serial number PX4471-A alarmed twice.",
    "web_urls": "Result is at https://portal.example.org/u/88412 now.",
    "ip_addresses": "Session came from 10.0.4.17 this morning.",
}


def test_every_enforced_class_has_a_working_detector():
    """Membership in ENFORCED is a claim and this is what checks it."""
    missing = sorted(policy.ENFORCED - set(SAMPLES))
    assert not missing, "enforced with no sample exercising it: %s" % missing
    for category, text in SAMPLES.items():
        found = {s.category for s in phi.detect(text)}
        assert category in found, (
            "%s is listed as enforced and found nothing in %r, it found %s"
            % (category, text, sorted(found) or "nothing")
        )


def test_no_detector_exists_for_a_declared_omission():
    """An omission with a detector behind it is a documentation error."""
    for text in SAMPLES.values():
        for span in phi.detect(text):
            assert span.category not in policy.DECLARED_OMISSIONS


def test_redaction_removes_the_identifier_and_keeps_the_sentence():
    text = "SSN 123-45-6789 for Dr. Petrov, call (415) 555-0132."
    out = phi.redact(text)
    assert "123-45-6789" not in out
    assert "555-0132" not in out
    assert out.startswith("SSN ") and out.endswith(".")


def test_hard_negatives_produce_nothing():
    """Precision is decided on these, so a regression here must fail loudly."""
    for text in HARD_NEGATIVES:
        found = [(s.category, s.text) for s in phi.detect(text)]
        assert not found, "false positive on %r: %s" % (text, found)


def test_both_address_forms_are_found_whole():
    """The compressed form was previously split in two at the double colon."""
    for text, want in (
        ("Session came from 2001:db8:85a3::8a2e:370:7334 now.", "2001:db8:85a3::8a2e:370:7334"),
        ("Session came from fe80::1 now.", "fe80::1"),
        ("Session came from 10.0.4.17 now.", "10.0.4.17"),
    ):
        spans = [x for x in phi.detect(text) if x.category == "ip_addresses"]
        assert len(spans) == 1, "%r produced %d spans" % (text, len(spans))
        assert spans[0].text == want


def test_a_timestamp_is_not_an_address():
    """A clinical note is mostly timestamps, so this is the expensive mistake."""
    for text in ("Infusion started at 12:34:56 and ran for two hours.",
                 "Shift cover runs 08:15:00 to 20:15:00 on the roster."):
        assert not phi.detect(text), "redacted a timestamp in %r" % text


def test_spans_never_overlap():
    text = "Account number 123-45-6789 and MRN 884213 on file."
    spans = phi.detect(text)
    for a, b in zip(spans, spans[1:]):
        assert a.end <= b.start


def test_none_is_a_caller_bug_and_not_an_empty_payload():
    with pytest.raises(ValueError):
        phi.detect(None)
