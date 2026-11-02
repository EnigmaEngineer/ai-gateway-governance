"""The decision one request gets on the way through the gateway.

Redaction and injection detection are separate concerns and this is the only
place they meet. Keeping the join here means the two detectors can be scored
independently, which is the whole reason the measurements are per class.

The order matters and it is the opposite of the obvious one. Injection is
assessed on the ORIGINAL text, before redaction, because redacting first
replaces an attacker's identifiers with tags and can destroy the evidence a
reviewer needs. Redaction then runs on the text that gets forwarded.
"""

from dataclasses import dataclass, field

from . import injection, phi, policy

# Fail at import rather than at the first request. A deployment whose policy
# sets do not partition the regulation should not start.
policy.validate()

BLOCK = "block"
REDACT = "redact"
PASS = "pass"


@dataclass
class Decision:
    action: str
    forwarded_text: str
    redacted_classes: list = field(default_factory=list)
    redaction_count: int = 0
    injection_classes: list = field(default_factory=list)
    reads_as_quotation: bool = False
    reasons: list = field(default_factory=list)


def apply(text, block_on_injection=True):
    """Assess one payload and return what the gateway would forward.

    `block_on_injection` is a deployment choice and not a safety claim. A team
    running this in front of an internal summariser may well prefer to flag and
    forward, because a false block on a clinician's note costs more than a
    flagged one. The default blocks, so the safer setting is the one you get
    without reading the argument list.
    """
    if not isinstance(text, str):
        raise TypeError("the gateway reads text payloads, got %r" % type(text).__name__)

    verdict = injection.assess(text)
    spans = phi.detect(text)
    reasons = []

    if verdict["flagged"] and block_on_injection:
        reasons.append("injection classes fired: " + ", ".join(verdict["classes"]))
        if verdict["reads_as_quotation"]:
            # Still blocked. A payload that fires and also reads as a
            # description of an attack is reported with the hedge so a reviewer
            # can clear it, because the alternative is a filter an attacker
            # switches off by writing the words for instance.
            reasons.append("payload also reads as a description of an attack, review before clearing")
        return Decision(
            action=BLOCK,
            forwarded_text="",
            injection_classes=verdict["classes"],
            reads_as_quotation=verdict["reads_as_quotation"],
            reasons=reasons,
        )

    forwarded = phi.redact(text, spans)
    if spans:
        reasons.append("redacted %d span(s)" % len(spans))
    if verdict["flagged"]:
        reasons.append("injection flagged and forwarded by configuration")

    return Decision(
        action=REDACT if spans else PASS,
        forwarded_text=forwarded,
        redacted_classes=sorted({s.category for s in spans}),
        redaction_count=len(spans),
        injection_classes=verdict["classes"],
        reads_as_quotation=verdict["reads_as_quotation"],
        reasons=reasons,
    )
