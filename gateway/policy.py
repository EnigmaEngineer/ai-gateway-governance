"""The set of identifier classes this gateway is accountable for.

The enumeration is the Safe Harbor list in 45 CFR 164.514(b)(2). It is fixed
by regulation and not by what the detectors happen to cover, which is the
whole point of keeping it here. A redaction service built as a pile of regexes
refuses what it names and passes everything else, so the list of things it does
NOT handle has to exist somewhere a check can read it.
"""

# The regulation's eighteen classes, in its order. Nothing is added and nothing
# is renamed, because a local rename is how a category quietly leaves the list.
SAFE_HARBOR = (
    "names",
    "geographic_subdivisions",
    "dates_and_ages_over_89",
    "telephone_numbers",
    "fax_numbers",
    "email_addresses",
    "social_security_numbers",
    "medical_record_numbers",
    "health_plan_beneficiary_numbers",
    "account_numbers",
    "certificate_or_license_numbers",
    "vehicle_identifiers",
    "device_identifiers",
    "web_urls",
    "ip_addresses",
    "biometric_identifiers",
    "full_face_photographs",
    "other_unique_identifying_code",
)

# Classes with a detector behind them. Membership here is a claim that something
# in gateway/phi.py tries, not a claim that it succeeds. The measured recall per
# class is the thing that says how well, and it lives in measurements/.
ENFORCED = {
    "names",
    "geographic_subdivisions",
    "dates_and_ages_over_89",
    "telephone_numbers",
    "fax_numbers",
    "email_addresses",
    "social_security_numbers",
    "medical_record_numbers",
    "health_plan_beneficiary_numbers",
    "account_numbers",
    "certificate_or_license_numbers",
    "vehicle_identifiers",
    "device_identifiers",
    "web_urls",
    "ip_addresses",
}

# Classes deliberately left alone, each with the reason written down. A reason
# of "hard" is not a reason. Every entry here says what would have to change.
DECLARED_OMISSIONS = {
    "biometric_identifiers": (
        "A fingerprint or voice template reaches a chat API as an opaque base64 "
        "blob inside an attachment field. This guardrail reads text payloads and "
        "does not decode attachments, so it cannot tell a voice print from any "
        "other blob. Closing this needs attachment handling and a content type "
        "allow list, not another pattern."
    ),
    "full_face_photographs": (
        "Same shape as the biometric case and the same reason. An image is a blob "
        "to this service. A deployment that must cover it belongs behind an "
        "upstream content filter that can open the image."
    ),
    "other_unique_identifying_code": (
        "The regulation's catch-all is unbounded by construction, so no pattern "
        "list satisfies it. It is named here because it is the reason this service "
        "redacts the enumerated classes and does not claim de-identification. A "
        "caller who needs that claim needs a human review step as well."
    ),
}


class PolicyError(Exception):
    """Raised when the three sets above do not partition the enumeration."""


def unclassified():
    """Classes in the regulation that are in neither set.

    This is the failure the 45 CFR list is here to make visible. A class that
    nobody mentioned is not refused by default, it is permitted by default.
    """
    return tuple(
        c for c in SAFE_HARBOR
        if c not in ENFORCED and c not in DECLARED_OMISSIONS
    )


def double_classified():
    """Classes claimed as enforced and omitted at once."""
    return tuple(c for c in SAFE_HARBOR if c in ENFORCED and c in DECLARED_OMISSIONS)


def unknown():
    """Entries in either set that the regulation does not list.

    A typo in a category name would otherwise look like a covered class and
    leave the real one unclassified. Checking both directions catches that.
    """
    named = set(ENFORCED) | set(DECLARED_OMISSIONS)
    return tuple(sorted(n for n in named if n not in SAFE_HARBOR))


def validate():
    """Fail unless every listed class sits in exactly one set.

    Called at import time by the service so a deployment cannot start while the
    partition is broken. The gate script calls it too, for the case where
    nothing imported the service.
    """
    if not SAFE_HARBOR:
        raise PolicyError("the enumeration is empty, so this check graded nothing")
    problems = []
    if unclassified():
        problems.append("unclassified: %s" % ", ".join(unclassified()))
    if double_classified():
        problems.append("enforced and omitted at once: %s" % ", ".join(double_classified()))
    if unknown():
        problems.append("not in 45 CFR 164.514(b)(2): %s" % ", ".join(unknown()))
    if problems:
        raise PolicyError(" | ".join(problems))
    return {
        "enumerated": len(SAFE_HARBOR),
        "enforced": len(ENFORCED),
        "declared_omissions": len(DECLARED_OMISSIONS),
    }
