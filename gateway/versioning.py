"""Which API versions this gateway serves, and what it tells a caller about them.

Two things live here because they are the same decision. A version that is
still routed is a version a caller can keep using, and a version that is
routed while being on its way out has to say so in the response rather than in
a changelog nobody reads.

The registry is the allow list. A path whose version segment is not a key in
it is refused, which matters more than it sounds: the earlier handler matched
`/v1/guard` as a literal string, so adding a second version by stripping a
prefix would have made every unlisted spelling route to the current code. An
omission from an allow list is a grant.

The header serialisation is pedantic on purpose. `Deprecation` and `Sunset`
almost always appear together and they do NOT share a date format, which is
the one thing about this pair worth knowing.
"""

import calendar
import email.utils
import time

# Every normative fact the serialisation below rests on, with the sentence it
# came from and where to read it. The point is that a reader can check the
# claim without taking anybody's word for it, and that nothing here was
# written from memory.
SOURCED = {
    "deprecation-is-an-sf-date": {
        "value": "Item Structured Header Field whose value is a Date",
        "quote": "Deprecation is an Item Structured Header Field; its value "
                 "MUST be a Date as per Section 3.3.7 of [RFC9651].",
        "url": "https://www.rfc-editor.org/rfc/rfc9745.html#section-2.1",
        "note": "An sf-date serialises as '@' followed by whole seconds since "
                "the epoch. The sigil is what separates it from an integer.",
    },
    "deprecation-may-be-in-the-future": {
        "value": "past and future both permitted",
        "quote": "It conveys the deprecation date, which may be in the future "
                 "(the resource in context will be deprecated at that date) or "
                 "in the past (the resource in context was deprecated at that date).",
        "url": "https://www.rfc-editor.org/rfc/rfc9745.html#section-2.1",
        "note": "So neither clamping nor refusing a future date is correct.",
    },
    "sunset-is-an-http-date": {
        "value": "HTTP-date",
        "quote": "The Sunset value is an HTTP-date timestamp, as defined in "
                 "Section 7.1.1.1 of [RFC7231], and SHOULD be a timestamp in "
                 "the future.",
        "url": "https://www.rfc-editor.org/rfc/rfc8594.html#section-3",
        "note": "RFC 7231 is obsoleted by RFC 9110 and the production moved to "
                "section 5.6.7. The format did not change.",
    },
    "http-date-zone-is-the-literal-gmt": {
        "value": "GMT",
        "quote": "When a sender generates a field that contains one or more "
                 "timestamps defined as HTTP-date, the sender MUST generate "
                 "those timestamps in the IMF-fixdate format.",
        "url": "https://www.rfc-editor.org/rfc/rfc9110.html#section-5.6.7",
        "note": "IMF-fixdate ends in GMT = %s\"GMT\", which is case sensitive "
                "ABNF, so UTC and +0000 and Z are all wrong.",
    },
    "sunset-not-before-deprecation": {
        "value": "sunset >= deprecation",
        "quote": "The timestamp given in the Sunset HTTP header field MUST NOT "
                 "be earlier than the one given in the Deprecation header field.",
        "url": "https://www.rfc-editor.org/rfc/rfc9745.html#section-4",
        "note": "The only MUST NOT in RFC 9745 and the only one of these a "
                "registry can get wrong by itself.",
    },
    "the-only-relation-rfc9745-defines": {
        "value": "deprecation",
        "quote": "Refers to documentation (intended for human consumption) "
                 "about the deprecation of the link's context.",
        "url": "https://www.rfc-editor.org/rfc/rfc9745.html#section-6.2",
        "note": "RFC 9745 registers this one relation and no other. It names "
                "no successor relation anywhere.",
    },
    "successor-version-comes-from-rfc5829": {
        "value": "successor-version",
        "quote": "When included on a versioned resource, this link points to a "
                 "resource containing the successor version in the version history.",
        "url": "https://www.rfc-editor.org/rfc/rfc5829.html#section-3.6",
        "note": "Earlier drafts of the deprecation header recommended this and "
                "publication dropped it, so a gateway emitting it is on RFC "
                "5829 and must not cite RFC 9745 for it.",
    },
}

# The pairing the one worked example in RFC 9745 section 4 shows, copied as it
# is printed there. It is kept because it is wrong and the wrongness is the
# reason this module does not use strftime.
RFC9745_SECTION_4_EXAMPLE_SUNSET = "Sun, 30 Jun 2024 23:59:59 UTC"


def sourced(key):
    """A normative fact, or an error. Never a default nobody read.

    Mirrors apigee.semantics.fact for the same reason. A rule resting on a
    row that does not exist should not quietly rest on None.
    """
    if key not in SOURCED:
        raise KeyError("no sourced fact for %r, go and read the spec" % key)
    return SOURCED[key]


def epoch(stamp):
    """Whole seconds since the epoch for a UTC instant written as 2027-04-01T00:00:00Z.

    timegm rather than mktime. mktime reads the struct as local time, which is
    a no-op on a box set to UTC and six hours of error on a laptop.
    """
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def sf_date(seconds):
    """An sf-date, which is the format Deprecation takes."""
    return "@%d" % int(seconds)


def http_date(seconds):
    """An IMF-fixdate, which is the format Sunset takes.

    `email.utils.formatdate(..., usegmt=True)` is the stdlib spelling that gets
    this right. The two spellings that look equally reasonable do not:
    `strftime` with `%Z` over `localtime` emits the zone name, which is UTC on
    a machine set to UTC, and `formatdate` without `usegmt` emits -0000. Both
    are refused by IMF-fixdate and on a UTC machine both differ from the
    correct answer only in the last few characters.
    """
    return email.utils.formatdate(int(seconds), usegmt=True)


class Version:
    """One API version and what the gateway says about it.

    `deprecation` and `sunset` are UTC instants as strings, because an epoch
    integer in a source file is a number nobody can check by eye. They are
    chosen policy rather than anything measured.
    """

    def __init__(self, name, status, deprecation=None, sunset=None,
                 successor=None, doc_url=None):
        if status not in ("current", "deprecated"):
            raise ValueError("unknown version status %r" % status)
        if status == "deprecated" and deprecation is None:
            raise ValueError("%s is deprecated with no deprecation date, which "
                             "is the state the header exists to report" % name)
        if status == "current" and deprecation is not None:
            raise ValueError("%s carries a deprecation date and claims to be "
                             "current" % name)
        if sunset is not None and deprecation is None:
            raise ValueError("%s has a sunset and no deprecation date, so the "
                             "ordering rule has nothing to compare" % name)
        self.name = name
        self.status = status
        self.deprecation = None if deprecation is None else epoch(deprecation)
        self.sunset = None if sunset is None else epoch(sunset)
        self.successor = successor
        self.doc_url = doc_url
        if self.sunset is not None and self.sunset < self.deprecation:
            raise ValueError(
                "%s sunsets before it is deprecated, which RFC 9745 section 4 "
                "refuses: %s" % (name, sourced("sunset-not-before-deprecation")["quote"]))

    def headers(self):
        """The response fields for this version, in the order they are sent.

        A current version gets nothing. A header announcing that a resource is
        fine is noise on every response forever.
        """
        out = []
        if self.deprecation is None:
            return out
        out.append(("deprecation", sf_date(self.deprecation)))
        if self.sunset is not None:
            out.append(("sunset", http_date(self.sunset)))
        if self.doc_url:
            out.append(("link", '<%s>; rel="deprecation"; type="text/html"' % self.doc_url))
        if self.successor:
            # rel="successor-version" is RFC 5829 and not RFC 9745, which
            # registers no successor relation. Emitting it is fine. Citing the
            # deprecation RFC for it is not.
            out.append(("link", '</%s/chat>; rel="successor-version"' % self.successor))
        return out


DOC_URL = "https://github.com/EnigmaEngineer/ai-gateway-governance/blob/main/docs/versioning.md"

# v1 is on its way out and v2 is where the work goes. The dates are a policy
# choice and six months is the window a caller gets, which is long enough to
# be credible and short enough that somebody has to act.
VERSIONS = {
    "v1": Version("v1", "deprecated", deprecation="2026-10-01T00:00:00Z",
                  sunset="2027-04-01T00:00:00Z", successor="v2", doc_url=DOC_URL),
    "v2": Version("v2", "current"),
}

CURRENT = "v2"

# Paths the gateway answers outside any version. A health check that moves
# with the API version is a health check nobody can configure once.
UNVERSIONED = frozenset(("/healthz",))


def split(path):
    """Pull the version segment off a request path.

    Returns the version name and the rest, or None for the version when the
    path carries no segment this registry knows. A caller asking for /v9/chat
    gets a refusal, not v2's behaviour, and a caller asking for /chat with no
    version at all gets the same, because guessing which version somebody
    meant is how a client ends up pinned to whatever was current the day it
    was written.
    """
    if not path.startswith("/"):
        return None, path
    parts = path.split("/", 2)
    if len(parts) < 3:
        return None, path
    name = parts[1]
    if name not in VERSIONS:
        return None, path
    return name, "/" + parts[2]

