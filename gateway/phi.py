"""Detectors for the Safe Harbor classes this service enforces.

Every pattern here was written from the class definition in the regulation and
from the shape of the identifier itself. None of them was written by looking at
a false positive in the scored corpus, which matters because a pattern derived
from the sample it is graded on turns the score into a property of the tuning.
Where a pattern was changed after looking at a corpus, it was the development
slice and never the scored one, and docs/scoring.md records which figures moved.

A span is a half open character range plus the class it belongs to. Detectors
return spans so the caller can redact, count or audit without re-running them.
"""

import re
from collections import namedtuple

Span = namedtuple("Span", "start end category text")

# Several classes only exist in free text when something labels them. An account
# number is nine digits and so is a part number, so the label is the signal and
# the digits are the payload. Patterns in this group carry their own label.
_LABELLED = (
    ("medical_record_numbers", r"(?:MRN|medical\s+record\s+(?:number|no\.?|#))"),
    ("health_plan_beneficiary_numbers",
     r"(?:member\s+(?:id|number|#)|beneficiary\s+(?:id|number)|policy\s+(?:number|no\.?|#)|subscriber\s+id)"),
    ("account_numbers", r"(?:account\s+(?:number|no\.?|#)|acct\.?\s*#?)"),
    ("certificate_or_license_numbers",
     r"(?:NPI|DEA(?:\s+(?:number|#))?|license\s+(?:number|no\.?|#)|cert(?:ificate)?\s+(?:number|no\.?|#))"),
    ("device_identifiers",
     r"(?:(?:(?:device|pump|implant|monitor)\s+)?serial(?:\s+(?:number|no\.?|#))?|device\s+id|implant\s+id)"),
    ("vehicle_identifiers", r"(?:VIN|plate\s+(?:number|#)|license\s+plate)"),
)

# The value that follows a label. Loose on purpose, because the label has
# already done the work of saying there is an identifier here. Stops at
# punctuation that ends a clause so a redaction cannot swallow the sentence.
# A label word must not be eaten as the value it introduces. Regex alternation
# returns the leftmost match rather than the longest, so without this guard
# "pump serial number PX4471" captured the word number and reported a hit for a
# class that had in fact found nothing.
_LABEL_WORDS = r"(?!(?:number|numbers|no|num|id|ids|code|is|the|for|and)\b)"
_AFTER_LABEL = r"[:\s#=]{0,4}" + _LABEL_WORDS + r"([A-Za-z0-9][A-Za-z0-9\-/]{3,24})"

_LABELLED_RE = tuple(
    (cat, re.compile(label + _AFTER_LABEL, re.IGNORECASE))
    for cat, label in _LABELLED
)

# A social security number has a fixed shape and does not need a label. The
# unseparated form is only taken when something labels it, because nine bare
# digits are far more often an order number.
# The dot form is as common as the hyphen on a scanned intake sheet. Both
# separators have to be the same character class or the pattern is enforcing a
# house style rather than finding an identifier.
_SSN_SEPARATED = re.compile(r"\b(?!000|666|9\d\d)(\d{3})[-.\s](?!00)(\d{2})[-.\s](?!0000)(\d{4})\b")
_SSN_LABELLED = re.compile(r"(?:SSN|social\s+security(?:\s+(?:number|no\.?|#))?)[:\s#=]{0,4}(\d{9}|\d{3}[-\s]?\d{2}[-\s]?\d{4})", re.IGNORECASE)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# NANP shapes. The optional country code is there because support transcripts
# carry both forms and a caller writing +1 is not writing a different number.
_PHONE = re.compile(
    r"(?:(?<![\d\-])(?:\+?1[\s.\-]?)?\(?\b[2-9]\d{2}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b)"
)

# Fax is the same ten digits as a phone. The only thing that makes it class five
# rather than class four is a word next to it, so that is what is matched.
_FAX = re.compile(
    r"fax[:\s#=]{0,6}((?:\+?1[\s.\-]?)?\(?\b[2-9]\d{2}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b)",
    re.IGNORECASE,
)

_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")
# Three or more colon separated groups is the shape of a timestamp as well as
# the shape of an address, and a clinical note is mostly timestamps. The first
# version of this took 12:34:56 as an address and split a real one in half at
# the double colon, so it was wrong in both directions at once. Full form or a
# compressed form carrying the double colon, and nothing in between.
_IPV6 = re.compile(
    r"\b(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}:){0,5}[0-9A-Fa-f]{1,4}"
    r"|::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}"
    r")\b"
)

_URL = re.compile(r"\b(?:https?://[^\s<>\"')]+|www\.[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?:/[^\s<>\"')]*)?)")

# A VIN is seventeen characters and excludes I, O and Q so it cannot be confused
# with digits. That exclusion is what makes an unlabelled match safe here.
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)

# Dates related to an individual. The regulation allows the year alone, so a
# bare four digit year is deliberately not matched.
_DATE_NUMERIC = re.compile(r"\b(?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])[/\-.](?:19|20)?\d{2}\b")
_DATE_ISO = re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")
_MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE_WORD = re.compile(r"\b" + _MONTHS + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\b", re.IGNORECASE)

# The age tail of class three. Ninety and above is an identifier and anything
# below it is not, which is one of the few bright lines in the whole list.
_AGE_OVER_89 = re.compile(
    r"\b(?:(9\d|1\d{2})[\s\-]*(?:year|yr)s?[\s\-]*old|age[d]?[:\s]{1,3}(9\d|1\d{2})\b)",
    re.IGNORECASE,
)

_ZIP_LABELLED = re.compile(r"(?:zip|postal)(?:\s+code)?[:\s#=]{0,4}(\d{5}(?:-\d{4})?)", re.IGNORECASE)
# A five digit number is only a ZIP when a state sits in front of it. Without
# that anchor the pattern matches invoice totals and dosage counts, which is
# why the bare form is not taken at all.
_ZIP_AFTER_STATE = re.compile(
    r"\b(?:A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|OH|OK|OR|P[AR]|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])[\s,]+(\d{5}(?:-\d{4})?)\b"
)

_STREET_TYPES = r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Place|Pl\.?|Terrace|Ter\.?|Way|Circle|Cir\.?)"
_STREET = re.compile(r"\b\d{1,5}\s+(?:[A-Z][A-Za-z.]*\s+){1,3}" + _STREET_TYPES + r"\b")

# Names are the class with no shape at all, so only the labelled form is taken.
# An unlabelled capitalised bigram catches a name and also catches every product
# and hospital and drug in the payload, and a redactor with that precision gets
# switched off. The cost of this choice is recall and it is measured per class.
_TITLES = r"(?:Mr|Mrs|Ms|Miss|Dr|Doctor|Prof|Rev)"
_NAME_TITLED = re.compile(r"\b" + _TITLES + r"\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")
_NAME_LABELLED = re.compile(
    r"(?:patient|member|subscriber|guardian|insured|caller|beneficiary|provider|physician|nurse)"
    r"(?:\s+(?:name|is))?[:\s]{1,3}([A-Z][a-z]+(?:\s+[A-Z][a-z'\-]+){1,2})\b",
    re.IGNORECASE,
)


def _add(spans, match, category, group=0):
    start, end = match.span(group)
    if end > start:
        spans.append(Span(start, end, category, match.string[start:end]))


# TODO: normalise the payload before matching. A Greek capital iota in place of
# a Latin I walks through every pattern here and no amount of widening fixes it.
# Needs a confusable fold and a zero width strip, and both of them change what
# the recorded offsets mean, so the span contract has to be settled first.
# TODO: detect will run every pattern over the whole payload, and the only
# bound on payload size is MAX_BODY in the HTTP layer. Nothing has measured it.
def detect(text):
    """Every span this service claims to find, sorted and de-overlapped.

    Overlap resolution keeps the longer span and then the earlier one. An SSN
    inside a labelled account field should redact once as the more specific
    class rather than twice.
    """
    if text is None:
        raise ValueError("detect was handed None, which is a caller bug and not an empty payload")
    spans = []

    for m in _SSN_SEPARATED.finditer(text):
        _add(spans, m, "social_security_numbers")
    for m in _SSN_LABELLED.finditer(text):
        _add(spans, m, "social_security_numbers", 1)
    for m in _EMAIL.finditer(text):
        _add(spans, m, "email_addresses")
    for m in _FAX.finditer(text):
        _add(spans, m, "fax_numbers", 1)
    for m in _PHONE.finditer(text):
        _add(spans, m, "telephone_numbers")
    for m in _URL.finditer(text):
        _add(spans, m, "web_urls")
    for m in _IPV4.finditer(text):
        _add(spans, m, "ip_addresses")
    for m in _IPV6.finditer(text):
        _add(spans, m, "ip_addresses")
    for m in _VIN.finditer(text):
        _add(spans, m, "vehicle_identifiers")
    for rx in (_DATE_NUMERIC, _DATE_ISO, _DATE_WORD):
        for m in rx.finditer(text):
            _add(spans, m, "dates_and_ages_over_89")
    for m in _AGE_OVER_89.finditer(text):
        _add(spans, m, "dates_and_ages_over_89")
    for m in _ZIP_LABELLED.finditer(text):
        _add(spans, m, "geographic_subdivisions", 1)
    for m in _ZIP_AFTER_STATE.finditer(text):
        _add(spans, m, "geographic_subdivisions", 1)
    for m in _STREET.finditer(text):
        _add(spans, m, "geographic_subdivisions")
    for m in _NAME_TITLED.finditer(text):
        _add(spans, m, "names", 1)
    for m in _NAME_LABELLED.finditer(text):
        _add(spans, m, "names", 1)
    for cat, rx in _LABELLED_RE:
        for m in rx.finditer(text):
            _add(spans, m, cat, 1)

    return _dedupe(spans)


# TODO: on overlap this keeps the longer span, so a labelled account field
# holding a value shaped like a social security number redacts once under the
# wider class. Which of the two a reviewer would rather see is not decided and
# nothing measures the case either way.
def _dedupe(spans):
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    kept = []
    for s in spans:
        if kept and s.start < kept[-1].end:
            # Overlapping. Keep whichever covers more characters so a
            # redaction never leaves half an identifier behind.
            if (s.end - s.start) > (kept[-1].end - kept[-1].start):
                kept[-1] = s
            continue
        kept.append(s)
    return kept


def redact(text, spans=None):
    """Replace every detected span with a tag naming the class it was."""
    spans = detect(text) if spans is None else spans
    out = []
    cursor = 0
    for s in spans:
        out.append(text[cursor:s.start])
        out.append("[" + s.category.upper() + "]")
        cursor = s.end
    out.append(text[cursor:])
    return "".join(out)
